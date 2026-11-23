import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import List
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from jobspy import scrape_jobs
from openai import OpenAI
from pydantic import BaseModel, Field

load_dotenv()

parser = argparse.ArgumentParser(description="Job Hunter AI")
parser.add_argument("--hours", type=int, default=24, help="Janela de busca em horas (padrão: 24)")
args = parser.parse_args()

HOURS_OLD = args.hours
LOCATION = "Portugal"
MIN_SCORE = 72

TECH_KEYWORDS = [
    "java", "spring", "python", "backend", "back-end", "frontend", "front-end",
    "fullstack", "full-stack", "devops", "linux", "sysadmin", "system administrator",
    "qa", "tester", "testing", "automation", "sdet", "junior", "júnior", "trainee",
    "graduate", "academia", "software", "engineer", "developer", "programmer",
    "programador", "analista", "support", "suporte", "cloud", ".net", "c#",
    "sap", "abap", "dados", "data"
]

BLACKLIST_TITLES = [
    "senior", "sénior", "sr.", "lead", "principal", "architect", "staff",
    "manager", "director", "head of", "vp ", "chief", "estágio curricular",
    "mid-level", "mid level", "pleno", "intermediate", "software engineer ii",
    "french", "francês", "german", "alemão", "dutch", "holandês", "spanish", "espanhol"
]

IGNORE_LINKS = [
    "subscribe", "login", "signup", "sign up", "jobs in ", "remote jobs in",
    "privacy", "terms", "empregos ti em", "ofertas por", "ver vaga", "ver todas",
    "pesquisar empregos", "alertas de emprego", "meu currículo", "candidatos",
    "carreiras", "recrutamento", "talent", "view as markdown", "about ",
    "join remote ok", "hire the top", "spontaneous application", "build with revolut",
    "enviar currículo", "trabalho remoto em ti", "programas para trainees",
    "vagas de ", "empregos em alta", "resultado(s)", "empregos em "
]

IGNORED_URL_PATTERNS = [
    "/city/", "/cidade/", "/category/", "/categoria/", "/distrito/",
    "/tag/", "/search", "/pesquisar", "/alertas", "/login", "/register",
    "/about", "/sobre", "/blog", "/contact", "/contacto"
]

SEEN_FILE = Path("seen_jobs.json")
PROFILE_FILE = Path("perfil.md")
PORTALS_FILE = Path("portais.json")
OUTPUT_FILE = Path(f"vagas_{datetime.now().strftime('%Y-%m-%d')}.md")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8"
}


class JobEvaluation(BaseModel):
    score: int = Field(description="Nota de 0 a 100 de aderência rigorosa ao perfil")
    match_stack: List[str] = Field(description="Tecnologias/competências compatíveis")
    red_flags: List[str] = Field(description="Critérios eliminatórios violados ou pontos de atenção")
    summary: str = Field(description="Resumo objetivo em 2 frases sobre a vaga e regime de trabalho")


def load_seen() -> set:
    if SEEN_FILE.exists():
        content = SEEN_FILE.read_text(encoding="utf-8").strip()
        if content:
            return set(json.loads(content))
    return set()


def save_seen(seen_urls: set):
    SEEN_FILE.write_text(json.dumps(list(seen_urls), indent=2), encoding="utf-8")


def create_llm_providers() -> list[tuple[str, OpenAI, str]]:
    """Monta a cadeia de contingência: Principal -> Fallback 1 -> Fallback 2 -> Fallback 3."""
    providers = []
    configs = [
        ("Principal", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"),
        ("Fallback 1", "FALLBACK1_API_KEY", "FALLBACK1_BASE_URL", "FALLBACK1_MODEL"),
        ("Fallback 2", "FALLBACK2_API_KEY", "FALLBACK2_BASE_URL", "FALLBACK2_MODEL"),
        ("Fallback 3", "FALLBACK3_API_KEY", "FALLBACK3_BASE_URL", "FALLBACK3_MODEL"),
    ]
    for label, key_var, url_var, model_var in configs:
        api_key = os.getenv(key_var, "").strip()
        base_url = os.getenv(url_var, "").strip()
        model = os.getenv(model_var, "").strip()
        if api_key and base_url and model:
            providers.append((f"{label} ({model})", OpenAI(api_key=api_key, base_url=base_url), model))
    return providers


def is_valid_portal_link(title: str, url: str, base_url: str, seen_urls: set) -> bool:
    t = title.strip().lower()
    u = url.strip().lower()

    if len(t) < 6 or url == base_url or url in seen_urls:
        return False
    if t.endswith(" jobs"):
        return False
    if any(ign in t for ign in IGNORE_LINKS):
        return False
    if any(bad in t for bad in BLACKLIST_TITLES):
        return False
    if any(pattern in u for pattern in IGNORED_URL_PATTERNS):
        return False
    if u.rstrip("/").endswith(("/jobs", "/job", "/empregos", "/vagas", "/careers")):
        return False

    return True


def enrich_linkedin_requirements(jobs: List[dict], seen_urls: set) -> List[dict]:
    """Busca o bloco extra 'Requirements added by the job poster' no HTML público da vaga do LinkedIn."""
    with httpx.Client(headers=HEADERS, timeout=10.0, follow_redirects=True) as client:
        for job in jobs:
            url = job["url"].split("?")[0]
            if "linkedin.com/jobs/view/" not in url or url in seen_urls:
                continue
            try:
                time.sleep(0.8)
                resp = client.get(url)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    full_page_text = " ".join(soup.get_text(" ", strip=True).split())
                    marker = "Requirements added by the job poster"
                    if marker in full_page_text:
                        idx = full_page_text.index(marker)
                        extra_block = full_page_text[idx:idx + 700]
                        job["description"] = f"{job['description']}\n\n=== {extra_block}"
            except Exception:
                continue
    return jobs


def fetch_jobspy_vacancies(sites: List[str], terms: List[str]) -> List[dict]:
    collected = []
    for idx, term in enumerate(terms):
        if idx > 0:
            time.sleep(3.5)
        print(f"[JobSpy] Buscando '{term}' em {sites}...")
        try:
            df = scrape_jobs(
                site_name=sites,
                search_term=term,
                location=LOCATION,
                results_wanted=20,
                hours_old=HOURS_OLD,
                country_indeed="Portugal",
                fetch_description=True,
            )
            for _, row in df.iterrows():
                collected.append({
                    "source": str(row.get("site", "jobspy")),
                    "title": str(row.get("title", "")),
                    "company": str(row.get("company", "N/A")),
                    "location": str(row.get("location", "")),
                    "url": str(row.get("job_url", "")),
                    "description": str(row.get("description", ""))
                })
        except Exception as e:
            print(f"  -> Aviso no JobSpy para '{term}': {e}")
    return collected


def fetch_custom_portals(portals: List[dict], seen_urls: set) -> List[dict]:
    collected = []
    with httpx.Client(headers=HEADERS, timeout=15.0, follow_redirects=True) as client:
        for portal in portals:
            if not portal.get("ativo", True):
                continue

            name = portal["nome"]
            base_url = portal["url"]
            url_filter = portal.get("filtro_url", "")
            print(f"[Portal] Varrendo {name}...")

            try:
                resp = client.get(base_url)
                if resp.status_code != 200:
                    print(f"  -> Status {resp.status_code} em {name} (ignorado)")
                    continue

                soup = BeautifulSoup(resp.text, "html.parser")
                candidate_links = {}

                for a in soup.find_all("a", href=True):
                    href = urljoin(base_url, a["href"]).split("#")[0].split("?")[0]
                    title = a.get_text(" ", strip=True)
                    title_lower = title.lower()

                    if not is_valid_portal_link(title, href, base_url, seen_urls):
                        continue

                    has_url_match = url_filter and (url_filter.lower() in href.lower())
                    has_tech_word = any(kw in title_lower for kw in TECH_KEYWORDS)

                    if has_url_match or has_tech_word:
                        candidate_links[href] = title

                for job_url, job_title in list(candidate_links.items())[:15]:
                    try:
                        time.sleep(0.6)
                        job_resp = client.get(job_url)
                        if job_resp.status_code == 200:
                            job_soup = BeautifulSoup(job_resp.text, "html.parser")

                            h1_tag = job_soup.find("h1")
                            if h1_tag:
                                h1_text = " ".join(h1_tag.get_text(" ", strip=True).split())
                                if 6 <= len(h1_text) <= 120 and not any(ign in h1_text.lower() for ign in IGNORE_LINKS):
                                    job_title = h1_text

                            for tag in job_soup(["script", "style", "nav", "footer", "header"]):
                                tag.decompose()
                            clean_text = " ".join(job_soup.get_text(" ", strip=True).split())

                            if len(clean_text) > 200:
                                collected.append({
                                    "source": name,
                                    "title": job_title[:120],
                                    "company": name,
                                    "location": "Verificar na descrição",
                                    "url": job_url,
                                    "description": clean_text
                                })
                    except Exception:
                        continue

            except Exception as e:
                print(f"  -> Erro ao acessar {name}: {e}")

    return collected


def passes_prefilter(job: dict, seen_urls: set) -> bool:
    url = job["url"].split("?")[0]
    if not url or url in seen_urls:
        return False

    title_lower = job["title"].strip().lower()
    if title_lower.endswith(" jobs"):
        return False
    if any(bad_word in title_lower for bad_word in BLACKLIST_TITLES):
        return False
    if any(ign in title_lower for ign in IGNORE_LINKS):
        return False
    if any(pattern in url.lower() for pattern in IGNORED_URL_PATTERNS):
        return False

    if len(job["description"].strip()) < 120:
        return False

    return True


def evaluate_with_fallback(providers: list, profile_text: str, job: dict) -> JobEvaluation:
    last_error = None

    for label, client, model in providers:
        try:
            prompt = f"""
            Avalie a compatibilidade da vaga abaixo com o perfil e os critérios do candidato.
            Aplique rigorosamente os CRITÉRIOS ELIMINATÓRIOS do perfil (se violar algum, dê nota <= 35).

            Responda EXCLUSIVAMENTE com um objeto JSON válido neste formato exato (sem blocos de código markdown):
            {{
              "score": 85,
              "match_stack": ["Java", "Spring Boot"],
              "red_flags": [],
              "summary": "Resumo objetivo em 2 frases sobre a vaga e regime de trabalho."
            }}

            === PERFIL E REGRAS DO CANDIDATO ===
            {profile_text}

            === DADOS DA VAGA ===
            Título: {job['title']}
            Empresa/Portal: {job['company']} ({job['source']})
            Localização informada: {job['location']}
            Texto da vaga:
            {job['description'][:3200]}
            """

            is_groq = "groq.com" in str(client.base_url).lower()
            extra_params = {"extra_body": {"reasoning_effort": "low"}} if is_groq else {}

            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": "Você é um especialista sênior em recrutamento técnico em Portugal. Responda EXCLUSIVAMENTE com um objeto JSON válido, sem markdown ou texto extra."
                    },
                    {"role": "user", "content": prompt}
                ],
                max_tokens=900,
                temperature=0.1,
                timeout=30.0,
                **extra_params,
            )
            raw_content = response.choices[0].message.content or ""
            start, end = raw_content.find("{"), raw_content.rfind("}")
            if start == -1 or end == -1:
                raise ValueError(f"Resposta sem JSON válido: {raw_content[:120]}")
            return JobEvaluation.model_validate_json(raw_content[start:end + 1])

        except Exception as e:
            print(f"  -> Falha em {label}: {e}")
            last_error = e
            time.sleep(1.5)

    raise RuntimeError(f"Todos os provedores falharam. Último erro: {last_error}")


def notify_telegram_batches(blocks: List[str], batch_size: int = 4):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id or not blocks:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    total_batches = (len(blocks) + batch_size - 1) // batch_size

    for i in range(0, len(blocks), batch_size):
        batch = blocks[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        header = f"📋 *Vagas Compatíveis ({batch_num}/{total_batches})*\n\n"
        message = header + "\n---\n".join(batch)

        resp = httpx.post(
            url,
            json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown", "disable_web_page_preview": True}
        )
        if resp.status_code != 200:
            httpx.post(url, json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True})
        time.sleep(1.0)


def main():
    providers = create_llm_providers()
    if not providers:
        print("ERRO: Nenhum provedor de LLM configurado no arquivo .env!")
        return

    print(f"Provedores ativos na cadeia: {[p[0] for p in providers]}")
    profile_text = PROFILE_FILE.read_text(encoding="utf-8")
    config = json.loads(PORTALS_FILE.read_text(encoding="utf-8"))
    seen_urls = load_seen()

    # Prioriza os portais portugueses antes do JobSpy (LinkedIn/Indeed)
    raw_jobs = fetch_custom_portals(config["portais_customizados"], seen_urls)
    raw_jobs += fetch_jobspy_vacancies(config["jobspy_sites"], config["termos_busca"])
    print(f"\nTotal bruto coletado: {len(raw_jobs)} vagas.")

    candidates = []
    temp_urls = set(seen_urls)
    for job in raw_jobs:
        clean_url = job["url"].split("?")[0]
        job["url"] = clean_url
        if passes_prefilter(job, temp_urls):
            temp_urls.add(clean_url)
            candidates.append(job)

    print("Enriquecendo requisitos extras do LinkedIn...")
    candidates = enrich_linkedin_requirements(candidates, seen_urls)

    print(f"Vagas inéditas enviadas para triagem da IA: {len(candidates)}\n")
    if not candidates:
        return

    approved = []
    for idx, job in enumerate(candidates, 1):
        print(f"[{idx}/{len(candidates)}] Avaliando: {job['title']} ({job['source']})...")
        try:
            time.sleep(0.5)
            eval_result = evaluate_with_fallback(providers, profile_text, job)
            seen_urls.add(job["url"])
            save_seen(seen_urls)

            if eval_result.score >= MIN_SCORE:
                print(f"  -> APROVADA! Nota: {eval_result.score}/100")
                approved.append((eval_result.score, job, eval_result))
            else:
                print(f"  -> Descartada (Nota: {eval_result.score}/100)")
        except Exception as e:
            print(f"  -> Vaga mantida na fila para próxima tentativa: {e}")

    approved.sort(key=lambda x: x[0], reverse=True)
    save_seen(seen_urls)

    if approved:
        timestamp = datetime.now().strftime("%H:%M")
        is_new_file = not OUTPUT_FILE.exists()
        report_lines = []

        if is_new_file:
            report_lines.append(f"# Vagas Compatíveis — {datetime.now().strftime('%d/%m/%Y')}\n")
        else:
            report_lines.append(f"\n---\n## Nova rodada às {timestamp} ({len(approved)} vagas)\n")

        telegram_blocks = []
        for score, job, res in approved:
            block = (
                f"### [{score}/100] {job['title']} — {job['company']}\n"
                f"- **Fonte:** {job['source']} | **Local:** {job['location']}\n"
                f"- **Stack/Match:** {', '.join(res.match_stack)}\n"
                f"- **Resumo:** {res.summary}\n"
                f"- **Link para Candidatura:** {job['url']}\n"
            )
            if res.red_flags:
                block += f"- **Atenção:** {', '.join(res.red_flags)}\n"
            report_lines.append(block)
            telegram_blocks.append(block)

        with OUTPUT_FILE.open("a", encoding="utf-8") as f:
            f.write("\n".join(report_lines) + "\n")

        print(f"\nConcluído! {len(approved)} vagas aprovadas acumuladas em '{OUTPUT_FILE}'.")
        notify_telegram_batches(telegram_blocks, batch_size=4)
    else:
        print(f"\nConcluído! Nenhuma vaga atingiu nota >= {MIN_SCORE} nesta execução.")


if __name__ == "__main__":
    main()
