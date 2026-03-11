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
      "logros": ["achievement WITH quantified result", "..."],
      "skills_demostrados": ["skill1", "skill2"]
    }}
  ],
  "skills_tecnicos": ["tools", "platforms", "software"],
  "skills_funcionales": ["growth", "paid acquisition", "lifecycle", "etc."],
  "industrias": ["SaaS", "fintech", "etc."],
  "seniority": "junior | mid | senior | lead | director | VP | C-level",
  "contacto": "email or LinkedIn if found, otherwise empty string"
}}

METRIC EXTRACTION RULES:
- For each achievement, extract the QUANTIFIED RESULT as a number or percentage.
  Good: "Reduced CAC by 35% over 6 months" → "Reduced CAC by 35% over 6 months"
  Good: "Grew revenue from $2M to $5M ARR" → "Grew revenue from $2M to $5M ARR"
  Bad: "Improved marketing performance" → "Improved marketing performance [NO METRIC]"
- If a metric is implied but not stated, append [NO METRIC] so downstream steps know.
- NEVER fabricate or estimate metrics that aren't in the CV.

Include ALL companies from the CV — do not stop at 3 or 4.
If something is not available, use empty string or empty list."""


async def _extract_profile(client: anthropic.AsyncAnthropic, cv_text: str) -> dict:
    """Step 0A: Extract structured profile from CV using Haiku."""
    console.print("  [dim]Extracting profile from CV...[/dim]")
    try:
        message = await client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=3000,
            messages=[{"role": "user", "content": EXTRACT_PROFILE_PROMPT.format(
                cv_text=cv_text[:15000]
            )}],
        )
        text = message.content[0].text.strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            result = json.loads(text[start:end])
            result["_ok"] = bool(result.get("empresas"))
            return result
    except Exception as exc:
        console.print(f"  [yellow]Profile extraction error: {exc}[/yellow]")

    return {"nombre": "", "rol_actual": "", "empresas": [], "skills_tecnicos": [],
            "skills_funcionales": [], "industrias": [], "seniority": "", "contacto": "",
            "_ok": False}


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
      "herramientas_mencionadas": ["tool1", "tool2"],
      "kpi_objetivo": "the specific metric or KPI this task aims to improve",
      "entregable": "what the candidate must produce (memo, model, framework, etc.)"
    }}
  ],
  "metricas_clave": ["metric or data point from Data & Context section"],
  "evaluation_criteria": ["criterion 1", "criterion 2"],
  "constraints": {{
    "budget": "budget range if mentioned",
    "team": "team size if mentioned",
    "timeline": "timeline if mentioned",
    "other": "any other constraints"
  }}
}}

EXTRACTION RULES:
- Extract challenges from "The Challenge" section.
- For each task in "Your Task": extract the title, required skills, tools, AND the KPI it measures.
- For kpi_objetivo: identify WHICH metric this task is supposed to move (e.g., "CAC", "retention rate", "pipeline velocity").
- Extract metrics from "Data & Context" — include actual numbers.
- Extract constraints from "Constraints" section — budget, team, timeline.
- Criteria from "Evaluation Criteria".
- Include ALL tasks from "Your Task" section."""


async def _extract_case(client: anthropic.AsyncAnthropic, case_study: str) -> dict:
    """Step 0B: Extract structured case data using Haiku."""
    console.print("  [dim]Analyzing case structure...[/dim]")
    try:
        message = await client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=3000,
            messages=[{"role": "user", "content": EXTRACT_CASE_PROMPT.format(
                case_study=case_study[:12000]
            )}],
        )
        text = message.content[0].text.strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            result = json.loads(text[start:end])
            result["_ok"] = bool(result.get("tasks"))
            return result
    except Exception as exc:
        console.print(f"  [yellow]Case extraction error: {exc}[/yellow]")

    return {"empresa": "", "rol": "", "challenges_principales": [],
            "tasks": [], "metricas_clave": [], "evaluation_criteria": [],
            "constraints": {}, "_ok": False}


# ---------------------------------------------------------------------------
# Step 0C — Map experience to tasks
# ---------------------------------------------------------------------------

MAP_EXPERIENCE_PROMPT = """Map the candidate's experience to each task in the business case.

CANDIDATE PROFILE:
{profile_json}

CASE TASKS:
{tasks_json}

For each task, find the most relevant experience from the candidate's work history.
Consider THREE types of match — not just domain, but METHODOLOGY and PROBLEM TYPE:

Respond ONLY with a JSON array:
[
  {{
    "task": "Task N — [title]",
    "experiencia_relevante": {{
      "empresa": "company name where they did something similar",
      "que_hice": "what they did that's analogous — be specific about the METHOD, not just the domain",
      "resultado": "metric or outcome — use exact numbers from CV, append [NO METRIC] if none"
    }},
    "nivel_match": "alto | medio | bajo | ninguno",
    "razonamiento": "1-2 sentences explaining WHY this experience transfers to this task"
  }}
]

MATCHING RULES:
- "alto": Direct match — same methodology AND similar problem, with measurable results.
  Example: Task asks for "attribution model." Candidate built attribution models before → alto.
- "medio": Methodological match — different domain but same analytical approach or framework.
  Example: Task asks for "DTC attribution." Candidate built B2B attribution → medio (same method, different domain).
  Example: Task asks for "reduce churn." Candidate optimized repeat purchase rate → medio (both retention problems).
- "bajo": Adjacent problem — candidate worked in the space but on different problems.
- "ninguno": No transferable experience found — be honest.

CRITICAL:
- A candidate who did "ROAS evaluation" at a DTC brand has a DIRECT match to an "attribution" task — not just "medio."
- A candidate who "optimized activation funnels" matches "improve onboarding completion" — same problem, different words.
- Look at the KPI the task aims to improve (kpi_objetivo) and check if the candidate moved similar KPIs.
- "razonamiento" must explain the TRANSFER LOGIC, not just restate the match level.

If nivel_match is "ninguno", set experiencia_relevante to {{"empresa": "", "que_hice": "", "resultado": ""}} \
and explain in razonamiento what adjacent experience exists (if any)."""


async def _map_experience(client: anthropic.AsyncAnthropic, profile: dict, caso: dict) -> list:
    """Step 0C: Map candidate experience to case tasks using Haiku."""
    console.print("  [dim]Mapping experience to case tasks...[/dim]")
    try:
        message = await client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=3000,
            messages=[{"role": "user", "content": MAP_EXPERIENCE_PROMPT.format(
                profile_json=json.dumps(profile, indent=2, ensure_ascii=False)[:6000],
                tasks_json=json.dumps(caso.get("tasks", []), indent=2, ensure_ascii=False)[:3000],
            )}],
        )
        text = message.content[0].text.strip()
        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    except Exception as exc:
        console.print(f"  [yellow]Mapping error: {exc}[/yellow]")

    return []


def _mapping_quality(mapping: list) -> dict:
    """Compute mapping quality summary for diagnostics."""
    counts = {"alto": 0, "medio": 0, "bajo": 0, "ninguno": 0}
    for item in mapping:
        level = item.get("nivel_match", "ninguno")
        if level in counts:
            counts[level] += 1
    return counts


def _build_work_history_reference(profile: dict) -> str:
    """Build a flat work history reference for the generation prompt.

    This gives Opus a clear list of citable experiences so it can only
    reference real companies and achievements.
    """
    lines = []
    for emp in profile.get("empresas", []):
        name = emp.get("empresa", "?")
        role = emp.get("rol", "?")
        period = emp.get("periodo", "")
        achievements = emp.get("logros", [])
        ach_str = "; ".join(achievements[:4]) if achievements else "no specific metrics listed"
        lines.append(f"- **{name}** ({role}, {period}): {ach_str}")
    return "\n".join(lines) if lines else "No work history extracted."


# ---------------------------------------------------------------------------
# Final generation prompt (Opus)
# ---------------------------------------------------------------------------

APPLY_SYSTEM = """You are ghostwriting a proactive application document for a specific person.

You will receive:
1. The applicant's structured profile (extracted from their CV)
2. A work history reference — the ONLY experiences you may cite
3. The full case study and its extracted structure
4. An experience-to-task mapping with match levels and transfer reasoning
5. Case constraints (budget, team, timeline)

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

INTEGRITY RULES:
- You may ONLY cite companies and achievements that appear in CANDIDATE'S WORK HISTORY below.
- If a mapping shows "ninguno", do NOT invent experience. Use reasoning + market benchmarks.
- An honest "I haven't done this but here's my reasoning" is more credible than fabricated experience.
- Do NOT exaggerate results. If an achievement says [NO METRIC], do not add numbers.
- Use the exact company names from the work history — never approximate or rename them."""

APPLY_USER = """Generate a proactive application document using the structured context below.

APPLICANT PROFILE (extracted from CV):
{profile_json}

CANDIDATE'S WORK HISTORY (the ONLY experiences you may cite):
{work_history}

CASE STUDY (full):
{case_study}

CASE STRUCTURE (extracted):
{caso_json}

CASE CONSTRAINTS:
{constraints_str}

EXPERIENCE-TO-TASK MAPPING (with transfer reasoning):
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
'alto' from the mapping, citing the specific company and what was learned] — and what I \
learned there is directly applicable to what the team faces today.
I prepared this analysis without being asked because I believe there's a specific \
opportunity worth articulating."

Tone: direct, first person, no fluff.
NO "I'm pleased to present...", NO "With great enthusiasm...", NO standard cover letter phrases.

**Section 2 — Diagnosis (1-1.5 pages)**
Based on the case's Background + Challenge sections.
Rewrite in the candidate's voice — NOT copy the case text. Minimum 40% wording change.
Must sound like the candidate's OWN analysis, connecting what they see in the data to what \
they've experienced elsewhere.

Structure:
- 1 paragraph: company's current situation with 2-3 real data points from metricas_clave
- 2-3 bullets: critical challenges ordered by real impact (use challenges_principales)
  For each challenge, briefly note what makes you recognize it (from your own experience)
- 1 closing paragraph: the systemic tension behind the symptoms — the root problem, not the \
  surface-level symptoms the case describes

**Section 3 — What I'd Do (2-3 pages)**
CRITICAL: You MUST use the experience-to-task mapping to ground EVERY solution.

DEPTH DISTRIBUTION: Spend ~50% of Section 3 on the task with the strongest experience match (alto), \
~30% on the second strongest, ~20% on the remaining. Depth on your strongest match > breadth across all tasks.
Tasks are ordered by business impact (first = most critical). If your strongest match aligns with the \
highest-impact task, lead with it. If not, lead with the highest-impact task but go deepest on your strongest match.

For each task where nivel_match is "alto" or "medio":

**[Challenge title — the real problem, not the task number]**
What I'd do: [recommendation in 2-3 sentences with specific tools and logic from case data. \
Must respect the constraints: budget of {constraints_budget}, team of {constraints_team}, \
timeline of {constraints_timeline}.]
Why it works: [reasoning grounded in case metrics — reference specific numbers from metricas_clave]
What I've already tested: [MUST cite the experience from the mapping — use the exact company name, \
describe what was done, and the concrete result. Use the "razonamiento" from the mapping to \
explain WHY this experience transfers to this specific task.]

For tasks where nivel_match is "bajo":

**[Challenge title]**
What I'd do: [specific recommendation]
Why it works: [reasoning + case data]
Related experience: [acknowledge the gap — "I haven't done exactly this, but at [company] I [related work] \
which taught me [transferable principle]"]

For tasks where nivel_match is "ninguno":

**[Challenge title]**
What I'd do: [specific recommendation grounded in case data]
Reasoning: [logic + market benchmark if applicable — cite industry standards, not personal experience]
What I'd need to validate: [honest question I'd ask in the first week — this shows self-awareness]

**Section 4 — The non-obvious insight (half page)**
Identify ONE assumption in the case that your experience contradicts or deepens.

The insight must follow this pattern:
"The case assumes [X is the bottleneck / Y is the metric that matters / Z is the right approach].
But when I [specific experience from work history], I discovered [unexpected finding].
Applied here, this means [specific practical consequence for the company]."

This must reference BOTH case data AND a real experience from the work history.
A good insight is debatable — reasonable people could disagree.
Do NOT generate a generic observation. It must come from pattern recognition across the \
candidate's experience and this specific company's situation.

If after genuine analysis no insight emerges that meets ALL criteria above, write a single \
paragraph explaining the one thing you'd investigate in the first week and why.

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
      {"profile": {...}}     — extracted profile for UI
      {"mapping_quality": {}} — match level counts
      {"warning": "..."}     — quality warnings
      {"stage": "generating"}
      {"chunk": text}        — streamed tokens
      {"stage": "done"}
    """
    client = anthropic.AsyncAnthropic()

    # Step 0A: Extract profile from CV
    yield {"stage": "extracting_profile"}
    profile = await _extract_profile(client, cv_text)
    yield {"profile": profile}

    if not profile.get("_ok"):
        yield {"warning": "Could not extract profile from CV. Document will be generated with limited personalization."}

    # Step 0B: Extract case structure
    yield {"stage": "analyzing_case"}
    caso = await _extract_case(client, case_study)

    if not caso.get("_ok"):
        yield {"warning": "Could not fully extract case structure. Some tasks may be missing."}

    # Step 0C: Map experience to tasks
    yield {"stage": "mapping"}
    mapping = await _map_experience(client, profile, caso)

    # Validate mapping quality
    quality = _mapping_quality(mapping)
    yield {"mapping_quality": quality}

    if not mapping:
        yield {"warning": "Experience mapping failed. Generating with reasoning-based solutions."}
    elif quality["alto"] == 0 and quality["medio"] == 0:
        yield {"warning": "No strong experience matches found. Solutions will rely on reasoning and benchmarks."}

    # Build work history reference (flat, citable list)
    work_history = _build_work_history_reference(profile)

    # Extract constraints
    constraints = caso.get("constraints", {})
    constraints_budget = constraints.get("budget", "not specified")
    constraints_team = constraints.get("team", "not specified")
    constraints_timeline = constraints.get("timeline", "not specified")
    constraints_str = f"Budget: {constraints_budget}\nTeam: {constraints_team}\nTimeline: {constraints_timeline}"
    if constraints.get("other"):
        constraints_str += f"\nOther: {constraints['other']}"

    # Build the final prompt with all structured context
    prompt = APPLY_USER.format(
        profile_json=json.dumps(profile, indent=2, ensure_ascii=False)[:6000],
        work_history=work_history,
        case_study=case_study[:10000],
        caso_json=json.dumps(caso, indent=2, ensure_ascii=False)[:4000],
        constraints_str=constraints_str,
        constraints_budget=constraints_budget,
        constraints_team=constraints_team,
        constraints_timeline=constraints_timeline,
        mapping_json=json.dumps(mapping, indent=2, ensure_ascii=False)[:4000],
        jd_text=jd_text[:6000],
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
