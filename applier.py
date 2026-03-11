"""Transforms a generated case study into a personalized application document.

Takes the case study output, JD text, the applicant's CV/resume, and their
relevant experiences, then generates a proactive application document in first
person — the kind you send WITHOUT being asked to get the first interview.
"""

import anthropic
from rich.console import Console

console = Console()

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

APPLY_SYSTEM = """You are ghostwriting a proactive application document for a specific person.

You will receive the applicant's CV/resume. Extract their name, current role, background,
and areas of expertise from it. Use this to write in THEIR voice — first person, with
the depth and specificity that matches their actual experience level.

THE DOCUMENT'S PURPOSE:
This is NOT a cover letter. NOT a case study answer.
It is a 4-6 page document the applicant sends WITHOUT BEING ASKED to get the first interview.
The implicit message: "I diagnosed your problem, already solved it in another context, \
and here's what I'd do in yours."

VOICE AND TONE RULES:
- First person, direct. Opinions with clear positions ("I'd do X, not Y, because...")
- No excessive hedging ("maybe", "perhaps", "it could be that")
- No management speak ("synergy", "holistic", "strategic alignment")
- No cover letter phrases ("I'm pleased to present...", "With great enthusiasm...", \
  "I have the ideal profile...")
- No listing skills like a CV
- No speaking about the applicant in third person

FORMAT RULES:
- No generic headers like "Introduction" or "Conclusion"
- Tables only where the original case has them and they're necessary
- Max 3 bullets per section
- Bold only for key terms, not decorative emphasis
- Total length: 4-6 pages (~2000-2500 words)

CRITICAL: If the applicant has no analogous experience for a task, do NOT invent one.
Instead, articulate the reasoning with market data or benchmarks from the case."""

APPLY_USER = """Generate a proactive application document using the structure below.

GENERATED CASE STUDY (from the company's perspective):
{case_study}

JOB DESCRIPTION:
{jd_text}

COMPANY: {company_name}
ROLE: {job_title}

APPLICANT'S CV/RESUME:
{cv_text}

APPLICANT'S RELEVANT EXPERIENCES (provided by the applicant):
{experiences}

---

DOCUMENT STRUCTURE:

**Section 1 — Opening (half page)**
Template pattern (adapt, don't copy literally):
"[Company] is at a point where [diagnosis in 1 sentence]. I've seen this pattern before — \
at [analogous context from experiences] — and what I learned there is directly applicable.
I prepared this analysis without being asked because I believe there's a specific \
opportunity worth articulating."

**Section 2 — Diagnosis (1-1.5 pages)**
Take the Background and Challenge from the case study.
Rewrite in the applicant's voice — third person for the company, first person for the analysis.
Structure:
- 1 paragraph: company's current situation (real data from the case)
- 2-3 bullets: most critical challenges, ordered by impact
- 1 closing paragraph: the central tension the new hire must resolve

IMPORTANT: Do NOT copy the case text directly. Rewrite with ~30% wording change. \
The diagnosis must sound like the applicant analyzed it, not like the company wrote it.

**Section 3 — What I'd Do (2-3 pages)**
For each of the top 3 most relevant tasks from the case:
- 1 paragraph: specific recommendation with logic
- 1 explicit reference to analogous experience from the provided experiences
- 1 concrete metric or outcome (real or projected with clear assumptions)

Format for each:
**[Challenge name]**
What I'd do: [recommendation in 2-3 sentences, with specific tools]
Why it works: [logic, not generic — reference data from the case]
What I've already tested: [analogous experience with real result]

RULE: If no analogous experience exists for a task, skip "What I've already tested" \
and instead use market data or benchmarks to support the reasoning.

**Section 4 — The insight they probably don't have (half page)**
A single point. The most counterintuitive or non-obvious finding from the analysis.
Something that shows understanding of the company at a deeper level than someone \
who just read the JD. This must come from the actual case analysis + experiences. \
Do NOT generate it generically.

**Section 5 — Close (3-4 lines)**
No fluff. No "I look forward to your response."
Pattern: "If this resonates, happy to go deeper on [most relevant specific topic]. \
[Contact info placeholder]"

---

After the document, also generate:

### Email (separate from the document)
3-4 lines max. Include subject line.
Use the applicant's first name (from their CV) as the signature.

Subject: [Company] — unsolicited analysis
[Hiring manager name or "Hi [company] team"],
I put together a diagnosis of the challenges [company] faces in [area] \
and how I'd approach them in the first 90 days. Attaching in case it's useful.
[Applicant first name]
[LinkedIn placeholder]

---

Output the full document as clean markdown. No meta-commentary, no explanations, \
just the document followed by the email section."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def generate_application(case_study: str, jd_text: str,
                                company_name: str, job_title: str,
                                cv_text: str, experiences: str) -> str:
    """Generate the full application document (non-streaming)."""
    console.print("\n[bold blue]Generating application document...[/bold blue]")

    client = anthropic.AsyncAnthropic()
    prompt = APPLY_USER.format(
        case_study=case_study[:8000],
        jd_text=jd_text[:4000],
        company_name=company_name,
        job_title=job_title,
        cv_text=cv_text[:4000],
        experiences=experiences[:3000],
    )

    message = await client.messages.create(
        model="claude-opus-4-6",
        max_tokens=6000,
        system=APPLY_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    console.print("[green]Application document generated.[/green]")
    return message.content[0].text


async def generate_application_streaming(case_study: str, jd_text: str,
                                          company_name: str, job_title: str,
                                          cv_text: str, experiences: str):
    """Stream the application document via async generator.

    Yields dicts: {"stage": "applying"} at start,
    {"chunk": text} for each token, {"stage": "done"} at end.
    """
    client = anthropic.AsyncAnthropic()
    prompt = APPLY_USER.format(
        case_study=case_study[:8000],
        jd_text=jd_text[:4000],
        company_name=company_name,
        job_title=job_title,
        cv_text=cv_text[:4000],
        experiences=experiences[:3000],
    )

    yield {"stage": "applying"}

    async with client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=6000,
        system=APPLY_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        async for text in stream.text_stream:
            yield {"chunk": text}

    yield {"stage": "done"}
