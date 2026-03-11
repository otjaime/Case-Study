"""Transforms a generated case study into a personalized application document.

V2 pipeline:
  CV text  →  [extract_profile]  →  PERFIL (Haiku)
  Case md  →  [extract_case]     →  CASO   (Haiku)
  PERFIL + CASO  →  [map_experience]  →  MAPPING (Haiku)
  All structured context  →  [generate_document]  →  streamed markdown (Opus)
"""

import json
import anthropic
from rich.console import Console

console = Console()

HAIKU_MODEL = "claude-haiku-4-5-20251001"
OPUS_MODEL = "claude-opus-4-6"

# ---------------------------------------------------------------------------
# Step 0A — Extract structured profile from CV
# ---------------------------------------------------------------------------

EXTRACT_PROFILE_PROMPT = """Extract a structured profile from this CV/resume.

CV TEXT:
{cv_text}

Respond ONLY with valid JSON matching this schema exactly:
{{
  "nombre": "full name",
  "rol_actual": "current or most recent role + company",
  "empresas": [
    {{
      "empresa": "company name",
      "rol": "role title",
      "periodo": "dates or duration",
      "logros": ["achievement with metric if available", "..."],
      "skills_demostrados": ["skill1", "skill2"]
    }}
  ],
  "skills_tecnicos": ["tools", "platforms", "software"],
  "skills_funcionales": ["growth", "paid acquisition", "lifecycle", "etc."],
  "industrias": ["SaaS", "fintech", "etc."],
  "seniority": "junior | mid | senior | lead | director | VP | C-level",
  "contacto": "email or LinkedIn if found, otherwise empty string"
}}

Include ALL companies from the CV. Extract real metrics from achievements where possible.
If something is not available, use empty string or empty list."""


async def _extract_profile(client: anthropic.AsyncAnthropic, cv_text: str) -> dict:
    """Step 0A: Extract structured profile from CV using Haiku."""
    console.print("  [dim]Extracting profile from CV...[/dim]")
    message = await client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": EXTRACT_PROFILE_PROMPT.format(
            cv_text=cv_text[:6000]
        )}],
    )
    text = message.content[0].text.strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        return json.loads(text[start:end])
    return {"nombre": "", "rol_actual": "", "empresas": [], "skills_tecnicos": [],
            "skills_funcionales": [], "industrias": [], "seniority": "", "contacto": ""}


# ---------------------------------------------------------------------------
# Step 0B — Extract structured case data
# ---------------------------------------------------------------------------

EXTRACT_CASE_PROMPT = """Extract structured data from this business case study.

CASE STUDY:
{case_study}

Respond ONLY with valid JSON matching this schema exactly:
{{
  "empresa": "company name",
  "rol": "role title from the case",
  "challenges_principales": ["challenge 1", "challenge 2", "challenge 3"],
  "tasks": [
    {{
      "numero": 1,
      "titulo": "task title or summary",
      "skills_requeridos": ["skill1", "skill2"],
      "herramientas_mencionadas": ["tool1", "tool2"]
    }}
  ],
  "metricas_clave": ["metric or data point from Data & Context section"],
  "evaluation_criteria": ["criterion 1", "criterion 2"]
}}

Extract challenges from "The Challenge" section. Tasks from "Your Task" section.
Metrics from "Data & Context". Criteria from "Evaluation Criteria"."""


async def _extract_case(client: anthropic.AsyncAnthropic, case_study: str) -> dict:
    """Step 0B: Extract structured case data using Haiku."""
    console.print("  [dim]Analyzing case structure...[/dim]")
    message = await client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": EXTRACT_CASE_PROMPT.format(
            case_study=case_study[:8000]
        )}],
    )
    text = message.content[0].text.strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        return json.loads(text[start:end])
    return {"empresa": "", "rol": "", "challenges_principales": [],
            "tasks": [], "metricas_clave": [], "evaluation_criteria": []}


# ---------------------------------------------------------------------------
# Step 0C — Map experience to tasks
# ---------------------------------------------------------------------------

MAP_EXPERIENCE_PROMPT = """Map the candidate's experience to each task in the business case.

CANDIDATE PROFILE:
{profile_json}

CASE TASKS:
{tasks_json}

For each task, find the most relevant experience from the candidate's work history.
Respond ONLY with a JSON array:
[
  {{
    "task": "Task N — [title]",
    "experiencia_relevante": {{
      "empresa": "company name where they did something similar",
      "que_hice": "what they did that's analogous",
      "resultado": "metric or outcome if available"
    }},
    "nivel_match": "alto | medio | bajo | ninguno"
  }}
]

Rules:
- "alto": direct experience doing the same type of work with results
- "medio": related experience in adjacent area
- "bajo": vaguely related but different context
- "ninguno": no relevant experience found — be honest, don't stretch

If nivel_match is "ninguno", set experiencia_relevante to {{"empresa": "", "que_hice": "", "resultado": ""}}"""


async def _map_experience(client: anthropic.AsyncAnthropic, profile: dict, caso: dict) -> list:
    """Step 0C: Map candidate experience to case tasks using Haiku."""
    console.print("  [dim]Mapping experience to case tasks...[/dim]")
    message = await client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": MAP_EXPERIENCE_PROMPT.format(
            profile_json=json.dumps(profile, indent=2, ensure_ascii=False)[:3000],
            tasks_json=json.dumps(caso.get("tasks", []), indent=2, ensure_ascii=False)[:2000],
        )}],
    )
    text = message.content[0].text.strip()
    start = text.find("[")
    end = text.rfind("]") + 1
    if start >= 0 and end > start:
        return json.loads(text[start:end])
    return []


# ---------------------------------------------------------------------------
# Final generation prompt (Opus)
# ---------------------------------------------------------------------------

APPLY_SYSTEM = """You are ghostwriting a proactive application document for a specific person.

You will receive the applicant's structured profile (extracted from their CV),
the case study structure, and an experience-to-task mapping.

THE DOCUMENT'S PURPOSE:
This is NOT a cover letter. NOT a case study answer.
It is a 4-6 page document the applicant sends WITHOUT BEING ASKED to get the first interview.
The implicit message: "I diagnosed your problem, already solved it in another context, \
and here's what I'd do in yours."

VOICE AND TONE RULES:
- First person of the candidate, direct
- Clear positions ("I'd do X, not Y, because...")
- No excessive hedging ("maybe", "perhaps")
- No management speak ("synergy", "holistic", "strategic alignment")
- No cover letter phrases ("I'm pleased to present...", "With great enthusiasm...")
- No listing skills like a CV
- No speaking about the candidate in third person

FORMAT RULES:
- No generic headers like "Introduction" or "Conclusion"
- Tables only where the case has them and they add value to the solution
- Max 3 bullets per section
- Bold only for key terms, not decorative emphasis
- Total length: 4-6 pages (~2000-2500 words)

CRITICAL RULES:
- If a task mapping shows "ninguno", do NOT invent experience. Use reasoning + market benchmarks.
- An honest "I haven't done this but here's my reasoning" is more credible than fabricated experience.
- Do NOT exaggerate results from the CV.
- All cited experiences must come from the profile — never invented."""

APPLY_USER = """Generate a proactive application document using the structured context below.

APPLICANT PROFILE (extracted from CV):
{profile_json}

CASE STUDY (full):
{case_study}

CASE STRUCTURE (extracted):
{caso_json}

EXPERIENCE-TO-TASK MAPPING:
{mapping_json}

JOB DESCRIPTION:
{jd_text}

---

DOCUMENT STRUCTURE:

**Section 1 — Opening (half page)**
Use the candidate's name from the profile. Reference the company from the case.
Pattern (adapt, don't copy literally):
"[Company] is at a point where [diagnosis in 1 sentence from the main challenge].
I've worked in analogous situations — [brief reference to 1 experience with nivel_match \
'alto' from the mapping] — and what I learned there is directly applicable to what the \
team faces today.
I prepared this analysis without being asked because I believe there's a specific \
opportunity worth articulating."

Tone: direct, first person, no fluff.
NO "I'm pleased to present...", NO "With great enthusiasm...", NO standard cover letter phrases.

**Section 2 — Diagnosis (1-1.5 pages)**
Based on the case's Background + Challenge sections.
Rewrite in the candidate's voice — NOT copy the case text. Minimum 40% wording change.
Must sound like external analysis, not self-description.
Structure:
- 1 paragraph: company's current situation with 2-3 real data points from the case
- 2-3 bullets: critical challenges ordered by real impact (use challenges_principales)
- 1 closing paragraph: the systemic tension behind the symptoms the case describes

**Section 3 — What I'd Do (2-3 pages)**
For each task where nivel_match is "alto" or "medio":

**[Challenge title — the real problem, not the task number]**
What I'd do: [recommendation in 2-3 sentences with specific tools and logic from case data]
Why it works: [reasoning grounded in case data — not generic]
What I've already tested: [experience from the mapping — company, what was done, concrete result]

For tasks where nivel_match is "ninguno":

**[Challenge title]**
What I'd do: [specific recommendation]
Reasoning: [logic + market benchmark if applicable]
What I'd need to validate: [honest question I'd ask in the first week]

**Section 4 — The non-obvious insight (half page)**
A single point. The most counterintuitive finding from the analysis.
Must emerge from the intersection of case data and candidate experience.
Criteria: not something anyone would say reading the JD, has a specific practical \
consequence, could be debatable.
If no genuine insight emerges from the case + profile intersection, OMIT this section entirely.
A missing section is better than a superficial one.

**Section 5 — Close (3-4 lines)**
No fluff. No "I look forward to your response."
Pattern: "If this resonates, happy to go deeper on [most relevant topic from the case].
[Contact info from profile, or placeholder if not available]"

---

After the document, generate separately:

### Email
Subject: [Company] — unsolicited analysis
[Hiring manager name if in JD, or "Hi,"]
I put together a diagnosis of the challenges [company] faces in [role area] \
and how I'd approach them in the first 90 days. Attaching in case it's useful.
[Candidate name from profile]
[Contact from profile]

---

Output the full document as clean markdown, then the email section.
No meta-commentary, no explanations, just the document + email."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def generate_application_streaming(case_study: str, jd_text: str,
                                          company_name: str, job_title: str,
                                          cv_text: str):
    """V2 pipeline: extract → map → generate (streamed).

    Yields dicts with stage updates and streamed chunks:
      {"stage": "extracting_profile"}
      {"stage": "analyzing_case"}
      {"stage": "mapping"}
      {"profile": {...}}   — extracted profile for UI
      {"stage": "generating"}
      {"chunk": text}      — streamed tokens
      {"stage": "done"}
    """
    client = anthropic.AsyncAnthropic()

    # Step 0A: Extract profile from CV
    yield {"stage": "extracting_profile"}
    profile = await _extract_profile(client, cv_text)
    yield {"profile": profile}

    # Step 0B: Extract case structure
    yield {"stage": "analyzing_case"}
    caso = await _extract_case(client, case_study)

    # Step 0C: Map experience to tasks
    yield {"stage": "mapping"}
    mapping = await _map_experience(client, profile, caso)

    # Build the final prompt with all structured context
    prompt = APPLY_USER.format(
        profile_json=json.dumps(profile, indent=2, ensure_ascii=False)[:3000],
        case_study=case_study[:6000],
        caso_json=json.dumps(caso, indent=2, ensure_ascii=False)[:2000],
        mapping_json=json.dumps(mapping, indent=2, ensure_ascii=False)[:2000],
        jd_text=jd_text[:3000],
    )

    # Generate document (streamed)
    yield {"stage": "generating"}

    async with client.messages.stream(
        model=OPUS_MODEL,
        max_tokens=6000,
        system=APPLY_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        async for text in stream.text_stream:
            yield {"chunk": text}

    yield {"stage": "done"}
