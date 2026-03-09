# Job Case Study Generator

## What this is

A CLI tool that takes a job posting URL and generates a personalized case study document that a marketing/growth candidate can use as part of their job application. The goal is to demonstrate deep understanding of the company's specific growth challenges before the interview.

## Core flow

1. User runs the script with a job posting URL
2. Tool scrapes and analyzes the job posting + company website
3. Tool pulls additional public data (Meta Ads Library, SimilarWeb-equivalent, LinkedIn company page)
4. Claude generates a structured case study tailored to the role and company
5. Output is a clean markdown file (and optionally PDF) ready to attach or send

---

## Tech stack

- Python 3.11+
- `httpx` for HTTP
- `beautifulsoup4` for scraping
- `playwright` for JS-rendered pages (install with `playwright install chromium`)
- `anthropic` SDK for Claude API calls
- `python-dotenv` for env vars
- `rich` for CLI output formatting
- `weasyprint` for PDF export (optional)

---

## CLI usage

```bash
# Basic
python main.py --url "https://jobs.lever.co/company/job-id"

# With custom output name
python main.py --url "https://..." --name "stripe-growth-lead"

# With PDF export
python main.py --url "https://..." --pdf
```
