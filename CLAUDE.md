# Job Case Study Generator

## What this is

A web tool that takes a job description and company name, deeply researches the company using AI-powered search and web crawling, then generates a realistic take-home business case challenge — the kind companies send during late-stage screening. The candidate solves it with their own expertise and attaches it proactively to their application, getting 3 steps ahead of other applicants.

## Core flow (V2 pipeline)

```
JD text + company name
  ↓
[decomposer.py] → company_profile + requirements_map   (Claude Haiku)
  ↓
[research.py] → research_data   (Exa + Firecrawl, guided by profile)
  ↓
[analyzer.py] → full_context + coverage_gaps   (validates requirements coverage)
  ↓
[generator.py] → Stage 1: diagnosis → Stage 2: case   (Claude Opus, streamed)
  ↓
[score_case_quality] → quality scores   (Claude Haiku)
  ↓
Output rendered in browser with streaming + quality bar
  ↓  (optional)
[applier.py] → personalized application document   (Claude Opus, streamed)
```

Key architectural principle: gaps in case quality are **parsing problems**, not prompt problems. If the decomposer misses a skill (e.g. Amplitude, AI applied to performance), the case will never cover it. The decomposer ensures every tool, task, and KPI from the JD flows through to generation.

## What it generates

The output reads as if it came from the company's hiring team:

- **Background** — Company context presented as internal brief
- **The Challenge** — Specific growth problem to solve
- **Your Task** — 4-5 concrete deliverables, each testing a different skill from the JD
- **Data & Context** — Real data points as if shared by the company
- **Evaluation Criteria** — What the hiring manager is looking for
- **Constraints** — Budget, team size, timeline forcing tradeoffs
- **Format & Submission** — Expected length and format

---

## Tech stack

- Python 3.11+
- `fastapi` + `uvicorn` for web server (SSE streaming)
- `jinja2` for HTML templates
- `exa-py` for AI semantic search (funding, news, competitors, marketing)
- `firecrawl-py` for deep website crawling
- `anthropic` SDK for Claude API calls (Opus for generation/apply, Haiku for extraction/scoring)
- `httpx` + `beautifulsoup4` + `playwright` for fallback scraping
- `python-dotenv` for env vars
- `rich` for CLI output formatting
- `weasyprint` for PDF export (optional)

---

## Project structure

```
├── app.py              # FastAPI web server (GET /, POST /generate, POST /generate-stream, POST /apply-stream)
├── main.py             # CLI entry point (--url, --name, --pdf)
├── decomposer.py       # JD decomposition → company_profile + requirements_map
├── research.py         # Exa + Firecrawl company research (industry-guided, cached)
├── scraper.py          # Basic web scraping (fallback)
├── analyzer.py         # Processes research into structured context + coverage validation
├── generator.py        # Two-stage Claude generation + quality scoring
├── applier.py          # Transforms case study into personalized application document
├── output.py           # Markdown + PDF file output
├── templates/
│   └── index.html      # Single-page web frontend (SSE streaming, quality bar, apply flow)
├── outputs/            # Generated case studies (CLI mode)
├── Dockerfile          # Railway deployment with Playwright
├── Procfile            # Railway web process
├── requirements.txt    # Python dependencies
├── .env.example        # Required API keys template
└── CLAUDE.md           # This file
```

---

## Module details

### decomposer.py
- Runs BEFORE research — extracts structured `company_profile` and `requirements_map` from raw JD text
- Uses Claude Haiku for fast, cheap extraction
- `company_profile`: industry, business_model, product_type, company_stage, market, seniority, reports_to, team_size, role_type
- `requirements_map`: tools_required, core_tasks, primary_kpis, secondary_kpis, emerging_skills, methodologies, leadership_signals
- Retry logic + fallback if parsing fails

### research.py
- Guided by `company_profile` (industry-specific and stage-specific Exa queries)
- Domain-level file cache with 7-day TTL (`/tmp/case_study_cache/`)
- Secondary Exa searches per competitor (max 3) for deeper intel
- `research_all()` returns: funding, news, marketing, competitors, website, industry_intel

### analyzer.py
- `build_context()` accepts company_profile + requirements_map from decomposer
- `validate_coverage()` checks every tool, task, KPI against collected context
- Uncovered items become `coverage_gaps` that force the generator to address them
- `_infer_challenges()` uses Claude Haiku (not rule-based) for company-specific challenges

### generator.py
- Two-stage generation: Stage 1 (diagnosis) → Stage 2 (case construction)
- Coverage-aware: prompt includes requirements_map + coverage_gaps
- Business model templates inject model-specific metrics (B2B SaaS, DTC, marketplace, fintech)
- `generate_case_study_streaming()` streams Stage 2 via async generator for SSE
- `score_case_quality()` post-generation scoring via Haiku (specificity, realism, difficulty)

### applier.py
- Transforms a generated case study into a personalized application document
- Inputs: case study markdown + JD + company info + applicant's CV/resume + relevant experiences
- Extracts applicant name, background, and expertise from CV to personalize voice
- 5-section structure: Opening → Diagnosis → What I'd Do → Non-obvious Insight → Close + Email
- `generate_application_streaming()` streams via async generator for SSE
- Uses Claude Opus for generation

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
