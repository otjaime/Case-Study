# Job Case Study Generator

## What this is

A web tool that takes a job description and company name, deeply researches the company using AI-powered search and web crawling, then generates a realistic take-home business case challenge — the kind companies send during late-stage screening. The candidate solves it with their own expertise and attaches it proactively to their application, getting 3 steps ahead of other applicants.

## Core flow

1. User pastes a job description + company name in the web UI
2. Tool researches the company via Exa (semantic search) and Firecrawl (website crawl)
3. Research covers: funding, competitors, marketing channels, recent news, website deep dive
4. Claude generates a realistic business case challenge tailored to the role and company
5. Output is rendered in the browser with a "Copy Markdown" button

## What it generates

The output reads as if it came from the company's hiring team:

- **Background** — Company context presented as internal brief
- **The Challenge** — Specific growth problem to solve
- **Your Task** — 3-4 concrete deliverables (channel mix, positioning, experiments)
- **Data & Context** — Real data points as if shared by the company
- **Evaluation Criteria** — What the hiring manager is looking for
- **Constraints** — Budget, team size, timeline forcing tradeoffs
- **Format & Submission** — Expected length and format

---

## Tech stack

- Python 3.11+
- `fastapi` + `uvicorn` for web server
- `jinja2` for HTML templates
- `exa-py` for AI semantic search (funding, news, competitors, marketing)
- `firecrawl-py` for deep website crawling
- `anthropic` SDK for Claude API calls (Sonnet)
- `httpx` + `beautifulsoup4` + `playwright` for fallback scraping
- `python-dotenv` for env vars
- `rich` for CLI output formatting
- `weasyprint` for PDF export (optional)

---

## Project structure

```
├── app.py              # FastAPI web server (GET /, POST /generate)
├── main.py             # CLI entry point (--url, --name, --pdf)
├── research.py         # Exa + Firecrawl company research
├── scraper.py          # Basic web scraping (fallback)
├── analyzer.py         # Processes research into structured context
├── generator.py        # Claude API prompt and generation
├── output.py           # Markdown + PDF file output
├── templates/
│   └── index.html      # Single-page web frontend
├── outputs/            # Generated case studies (CLI mode)
├── Dockerfile          # Railway deployment with Playwright
├── Procfile            # Railway web process
├── requirements.txt    # Python dependencies
├── .env.example        # Required API keys template
└── CLAUDE.md           # This file
```

---

## Environment variables

```
ANTHROPIC_API_KEY=sk-ant-...     # Required — Claude API
EXA_API_KEY=...                  # Required — Exa semantic search
FIRECRAWL_API_KEY=...            # Required — Firecrawl website crawl
ANTHROPIC_BASE_URL=...           # Optional — defaults to api.anthropic.com
```

Without Exa/Firecrawl keys, the tool falls back to basic scrapers (much weaker output).

---

## Usage

### Web (primary)

```bash
source venv/bin/activate
uvicorn app:app --reload
# Open http://localhost:8000
```

### CLI

```bash
source venv/bin/activate
python main.py --url "https://jobs.lever.co/company/job-id"
python main.py --url "https://..." --name "stripe-growth-lead" --pdf
```

---

## Deployment

Deployed on Railway via Dockerfile. Auto-deploys from GitHub (`otjaime/Case-Study`).
Set `ANTHROPIC_API_KEY`, `EXA_API_KEY`, `FIRECRAWL_API_KEY` in Railway variables.
