# Job Hunter AI

An automated, AI-powered job search and qualification pipeline built in Python. It scrapes job postings from major international job boards and local Portuguese tech portals, enriches hidden job requirements, evaluates each listing against a customizable candidate profile using a multi-provider LLM fallback chain, and delivers curated matches via Markdown reports and batched Telegram notifications.

---

## Key Features

* **Hybrid Multi-Source Scraping:** Combines `python-jobspy` (LinkedIn & Indeed) with a custom `httpx` + `BeautifulSoup` crawler for local tech portals and company career pages (e.g., Teamlyzer, ITJobs.pt, Devs.com.pt, Net-Empregos).
* **LinkedIn Requirements Enrichment:** Automatically fetches public job pages to extract the *"Requirements added by the job poster"* section often omitted by standard scrapers, preventing false positives on experience or language constraints.
* **Pre-Filtering & Deduplication:** Filters out aggregator pages, navigation links, blacklisted titles/languages, and previously evaluated URLs (`seen_jobs.json`) before spending any LLM tokens.
* **Resilient Multi-LLM Fallback Chain:** Supports up to 4 sequential OpenAI-compatible endpoints (e.g., Groq models + OpenRouter) with automatic failover on rate limits (`429`) or parsing errors, structured Pydantic validation, and low-reasoning token optimization.
* **Automated Reporting & Alerts:** Appends daily high-scoring matches (`>= 72/100`) to `vagas_YYYY-MM-DD.md` and dispatches batched, Markdown-formatted alerts to Telegram without exceeding message length limits.

---

## Architecture & Workflow

```text
[Custom Portals (httpx/BS4)] + [JobSpy (LinkedIn/Indeed)]
                         │
                         ▼
       [URL & Keyword Pre-Filter + Deduplication]
                         │
                         ▼
      [LinkedIn Extra Requirements Enrichment]
                         │
                         ▼
  [LLM Evaluation Chain (Principal -> Fallback 1..3)]
         (Strict Red-Flag & Score Validation)
                         │
            ┌────────────┴────────────┐
            ▼                         ▼
  [vagas_YYYY-MM-DD.md]      [Telegram Bot Batches]
```

---

## Tech Stack

* **Language & Package Manager:** Python 3.11+, [`uv`](https://github.com/astral-sh/uv)
* **Scraping & Parsing:** `python-jobspy`, `httpx`, `beautifulsoup4`
* **AI & Validation:** `openai` SDK (compatible with Groq, OpenRouter, Ollama, etc.), `pydantic`
* **Configuration & Alerts:** `python-dotenv`, Telegram Bot API

---

## Project Structure

```text
job-hunter-ai/
├── main.py              # Core scraping, filtering, LLM evaluation, and notification pipeline
├── perfil.md            # Candidate profile, target stack, bonus criteria, and strict red flags
├── portais.json         # Search keywords, JobSpy boards, and custom portal URL rules
├── seen_jobs.json       # Persistent cache of already-evaluated job URLs
├── .env.example         # Environment variables template for LLM providers & Telegram
└── pyproject.toml       # Project dependencies managed by uv
```

---

## Setup & Installation

### 1. Clone the repository
```bash
git clone [https://github.com/clarenceaguiar/job-hunter-ai-python.git](https://github.com/clarenceaguiar/job-hunter-ai-python.git)
cd job-hunter-ai-python
```

### 2. Install dependencies with `uv`
```bash
uv sync
```

### 3. Configure environment variables
Create a `.env` file in the root directory based on the template below:

```env
# Primary LLM Provider (e.g., Groq)
LLM_API_KEY="your-groq-api-key"
LLM_BASE_URL="[https://api.groq.com/openai/v1](https://api.groq.com/openai/v1)"
LLM_MODEL="openai/gpt-oss-120b"

# Fallback 1 (e.g., Groq secondary model with independent quota)
FALLBACK1_API_KEY="your-groq-api-key"
FALLBACK1_BASE_URL="[https://api.groq.com/openai/v1](https://api.groq.com/openai/v1)"
FALLBACK1_MODEL="openai/gpt-oss-20b"

# Fallback 2 (e.g., OpenRouter)
FALLBACK2_API_KEY="your-openrouter-api-key"
FALLBACK2_BASE_URL="[https://openrouter.ai/api/v1](https://openrouter.ai/api/v1)"
FALLBACK2_MODEL="openrouter/free"

# Optional Fallback 3
FALLBACK3_API_KEY=""
FALLBACK3_BASE_URL=""
FALLBACK3_MODEL=""

# Telegram Notifications (Optional)
TELEGRAM_BOT_TOKEN="your-telegram-bot-token"
TELEGRAM_CHAT_ID="your-telegram-chat-id"
```

### 4. Customize your profile and portals
* Edit `perfil.md` to define your target roles, tech stack, location/remote rules, language proficiency, and disqualifying red flags.
* Edit `portais.json` to adjust search terms or enable/disable specific custom portals.

---

## Usage

Run the pipeline with the default **24-hour** lookback window:
```bash
uv run main.py
```

Or specify a custom lookback window in hours (e.g., **72 hours** after a weekend):
```bash
uv run main.py --hours 72
```

### Example Output (`vagas_YYYY-MM-DD.md`)
```markdown
### [90/100] Junior Developer Java • Lisbon (Hybrid) — Teamlyzer
- **Fonte:** Teamlyzer | **Local:** Verificar na descrição
- **Stack/Match:** Java, Spring Boot, SQL
- **Resumo:** Junior Java Developer role in a hybrid model in Lisbon, aligned with entry-level experience and technical stack requirements.
- **Link para Candidatura:** [https://pt.teamlyzer.com/companies/](https://pt.teamlyzer.com/companies/)...
```

---
