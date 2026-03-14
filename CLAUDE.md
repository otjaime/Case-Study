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
  ↓  (optional)
[deck.py] → presentation-style PDF deck   (WeasyPrint, no API cost)
  ↓  (optional)
[pitch.py] → audio pitch narration   (Haiku script + ElevenLabs TTS, ~$0.02)
  ↓  (optional)
[video.py] → AI video explainer   (Haiku script + ElevenLabs TTS + HeyGen lip-sync)
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
├── app.py              # FastAPI web server (GET /, POST /generate, POST /generate-stream, POST /apply-stream, POST /export-deck, POST /generate-pitch, POST /generate-video, GET /video-status)
├── main.py             # CLI entry point (--url, --name, --pdf)
├── decomposer.py       # JD decomposition → company_profile + requirements_map
├── research.py         # Exa + Firecrawl company research (industry-guided, cached)
├── scraper.py          # Basic web scraping (fallback)
├── analyzer.py         # Processes research into structured context + coverage validation
├── generator.py        # Two-stage Claude generation + quality scoring
├── applier.py          # Transforms case study into personalized application document
├── deck.py             # Generates presentation-style PDF deck from diagnostic markdown
├── pitch.py            # Audio pitch: slide-aligned narration (Haiku script + ElevenLabs TTS)
├── video.py            # AI video explainer: script (Haiku) → audio (ElevenLabs) → lip-sync (HeyGen)
├── output.py           # Markdown + PDF file output (CLI)
├── templates/
│   ├── index.html      # Single-page web frontend (SSE streaming, quality bar, apply flow, deck, video)
│   └── deck.html       # Jinja2 template for landscape PDF deck (rendered server-side by WeasyPrint)
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
- 3 deep multi-skill tasks (not 4-5 shallow ones), ordered by business impact severity
- Data integrity rules: metrics must come from research, tagged [PUBLIC]/[INFERRED]/[UNKNOWN]
- `generate_case_study_streaming()` streams Stage 2 via async generator for SSE
- `score_case_quality()` post-generation scoring via Haiku (specificity, realism, difficulty)

### applier.py
- V3 pipeline: diagnostic framing — case used as intelligence source, not homework to answer
- Step 0A: `_extract_profile()` — Haiku extracts structured profile from CV (name, companies, skills, achievements with metrics)
- Step 0B: `_extract_case()` — Haiku extracts business problems (with evidence, root causes, consequences), tasks, competitive context, constraints
- Step 0C: `_map_experience()` — Haiku maps candidate experience to each business problem (alto/medio/bajo/ninguno) with transfer reasoning
- Final: `generate_application_streaming()` — Opus generates diagnostic document organized by business problems, streamed via SSE
- 5-section structure: Opening → What I See (diagnosis) → What I'd Do (solutions by problem) → Non-obvious Insight → Close + Email
- Document reads as candidate's original analysis, NOT as a case study response
- Depth distribution: 50/30/20 weighted by experience match strength
- Problems with no matching experience use reasoning + benchmarks instead of fabricated experience

### deck.py
- Generates a presentation-style landscape A4 PDF deck from the diagnostic markdown
- `_parse_diagnostic_sections()` splits markdown into: opening, what_i_see, solutions (per problem), insight, first_30_days, close, email
- `_extract_key_metrics()` pulls bold text, percentages, dollar amounts for stat cards
- `generate_deck_pdf()` renders `templates/deck.html` via Jinja2 + WeasyPrint → PDF bytes
- 5-7 page deck: cover (dark bg), diagnosis with stat cards, one page per problem, experience match visualization, close
- CSS-only charts: horizontal bars, stat cards, colored match-level indicators
- Zero API cost — uses data already computed during apply pipeline

### pitch.py
- Audio pitch generator: slide-aligned narration for the PDF deck
- Reuses `condense_for_slides()` from deck.py and `generate_audio()` from video.py
- `generate_pitch_script()` — Haiku generates 6-paragraph script (~290 words) from slide_data JSON
- `generate_pitch()` — full pipeline: condense → script → TTS audio, returns {script, audio_b64, audio_available}
- Prompt structured per-slide: cover (~20w), diagnosis (~60w), action 1 (~60w), action 2 (~60w), insight (~40w), close (~40w)
- Graceful degradation: if ELEVENLABS_API_KEY missing, returns script-only (no audio)
- Cost: ~$0.001 (Haiku) + ~$0.02 (ElevenLabs) = ~$0.021 per pitch

### video.py
- AI video explainer pipeline: script → audio → lip-sync video
- Step 1: `generate_script()` — Haiku condenses diagnostic into ~300 word spoken script (2 minutes)
- Step 2: `generate_audio()` — ElevenLabs TTS API converts script to audio (pre-made voices: Rachel/Josh)
- Step 3: `generate_video_heygen()` — HeyGen lip-syncs candidate photo with audio
- `run_video_pipeline()` orchestrates all 3 steps as async background task
- In-memory job store with `create_video_job()` / `get_video_job()` + 1-hour TTL cleanup
- Frontend polls `GET /video-status/{job_id}` every 3 seconds
- Cost: ~$0.16-0.28 per video (Haiku ~$0.001 + ElevenLabs ~$0.02 + HeyGen ~$0.20)

---

## Environment variables

```
ANTHROPIC_API_KEY=sk-ant-...     # Required — Claude API
EXA_API_KEY=...                  # Required — Exa semantic search
FIRECRAWL_API_KEY=...            # Required — Firecrawl website crawl
ANTHROPIC_BASE_URL=...           # Optional — defaults to api.anthropic.com
ELEVENLABS_API_KEY=...           # Optional — for video explainer (ElevenLabs TTS)
HEYGEN_API_KEY=...               # Optional — for video explainer (HeyGen lip-sync)
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
For video feature: also set `ELEVENLABS_API_KEY` and `HEYGEN_API_KEY`.
