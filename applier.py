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
Your job is to extract the BUSINESS PROBLEMS the company faces, not just the homework tasks.

CASE STUDY:
{case_study}

Respond ONLY with valid JSON matching this schema exactly:
{{
  "empresa": "company name",
  "rol": "role title from the case",
  "business_problems": [
    {{
      "problem": "1-sentence description of the core business problem",
      "evidence": ["specific data point or metric that proves this problem exists", "..."],
      "root_cause": "what's actually driving this problem (the systemic issue, not the symptom)",
      "consequence_if_ignored": "what happens in 6-12 months if no one fixes this",
      "related_tasks": [1, 2]
    }}
  ],
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
  "metricas_clave": ["metric or data point from Data & Context section — include actual numbers"],
  "competitive_context": ["key competitive dynamic or threat"],
  "constraints": {{
    "budget": "budget range if mentioned",
    "team": "team size if mentioned",
    "timeline": "timeline if mentioned",
    "supply": "supply or capacity constraints if mentioned",
    "other": "any other constraints"
  }}
}}

EXTRACTION RULES:
- Extract business_problems from "The Challenge" section — these are the REAL issues, not the tasks.
  Each problem should be a genuine business tension (e.g., "channel cannibalization," "attribution blindness").
- For evidence: pull specific numbers or data points that prove the problem exists.
- For root_cause: identify the systemic driver — NOT just "we need to fix X."
- For related_tasks: which task numbers address this problem?
- Extract ALL tasks from "Your Task" section — they inform what skills to demonstrate.
- For kpi_objetivo: identify WHICH metric this task aims to move.
- Extract metrics from "Data & Context" — include actual numbers.
- Extract competitive threats as separate items.
- Extract constraints from "Constraints" section — budget, team, timeline, supply."""


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
            result["_ok"] = bool(result.get("business_problems") or result.get("tasks"))
            return result
    except Exception as exc:
        console.print(f"  [yellow]Case extraction error: {exc}[/yellow]")

    return {"empresa": "", "rol": "", "business_problems": [],
            "tasks": [], "metricas_clave": [], "competitive_context": [],
            "constraints": {}, "_ok": False}


# ---------------------------------------------------------------------------
# Step 0C — Map experience to tasks
# ---------------------------------------------------------------------------

MAP_EXPERIENCE_PROMPT = """Map the candidate's experience to each BUSINESS PROBLEM in the case.

The case contains business problems (the real challenges the company faces) and tasks (the homework
deliverables). Your job is to map the candidate's experience to the PROBLEMS, not the tasks.
Use the tasks' required skills as context for what capabilities matter.

CANDIDATE PROFILE:
{profile_json}

BUSINESS PROBLEMS:
{problems_json}

TASKS (for skill context only):
{tasks_json}

For each business problem, find the most relevant experience from the candidate's work history.
Consider THREE types of match — not just domain, but METHODOLOGY and PROBLEM TYPE:

Respond ONLY with a JSON array:
[
  {{
    "problem": "1-sentence problem description",
    "experiencia_relevante": {{
      "empresa": "company name where they dealt with something analogous",
      "que_hice": "what they did that's analogous — be specific about the METHOD, not just the domain",
      "resultado": "metric or outcome — use exact numbers from CV, append [NO METRIC] if none"
    }},
    "nivel_match": "alto | medio | bajo | ninguno",
    "razonamiento": "1-2 sentences explaining WHY this experience transfers to this specific business problem",
    "skills_demonstrated": ["which skills from the related tasks this experience proves"]
  }}
]

MATCHING RULES:
- "alto": Direct match — same methodology AND similar problem, with measurable results.
  Example: Problem is "channel cannibalization between DTC and retail." Candidate managed DTC-to-retail transition → alto.
- "medio": Methodological match — different domain but same analytical approach or framework.
  Example: Problem is "no cross-channel attribution." Candidate built B2B attribution → medio (same method, different channel).
  Example: Problem is "DTC retention declining." Candidate optimized repeat purchase rate in a different category → medio.
- "bajo": Adjacent problem — candidate worked in the space but on different problems.
- "ninguno": No transferable experience found — be honest.

CRITICAL:
- Match against the PROBLEM, not the task deliverable. A candidate who managed channel conflict has
  experience with the cannibalization PROBLEM even if they never built a specific P&L model.
- Look at the root cause — if the problem's root cause is "no unified commercial planning," match
  against experience building cross-functional planning processes, not just the surface symptom.
- "razonamiento" must explain the TRANSFER LOGIC, not just restate the match level.

If nivel_match is "ninguno", set experiencia_relevante to {{"empresa": "", "que_hice": "", "resultado": ""}} \
and explain in razonamiento what adjacent experience exists (if any)."""


async def _map_experience(client: anthropic.AsyncAnthropic, profile: dict, caso: dict) -> list:
    """Step 0C: Map candidate experience to business problems using Haiku."""
    console.print("  [dim]Mapping experience to business problems...[/dim]")
    try:
        message = await client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=3000,
            messages=[{"role": "user", "content": MAP_EXPERIENCE_PROMPT.format(
                profile_json=json.dumps(profile, indent=2, ensure_ascii=False)[:6000],
                problems_json=json.dumps(caso.get("business_problems", []), indent=2, ensure_ascii=False)[:4000],
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
3. The full case study (used as a source of company intelligence — NOT as homework to answer)
4. Extracted business problems with evidence, root causes, and consequences
5. An experience-to-problem mapping with match levels and transfer reasoning
6. Competitive context and constraints

THE DOCUMENT'S PURPOSE:
This is NOT a cover letter. NOT a case study answer. NOT homework.
It is a 4-6 page diagnostic document the applicant sends WITHOUT BEING ASKED to get the first interview.
The implicit message: "I see what's happening at your company, I've dealt with this before, \
and here's specifically what I'd do about it."

The document should read as if the candidate independently researched the company and developed \
an original analysis — NOT as if they're responding to a case study or assignment.

VOICE AND TONE RULES:
- First person of the candidate, direct
- Clear positions ("I'd do X, not Y, because...")
- No excessive hedging ("maybe", "perhaps")
- No management speak ("synergy", "holistic", "strategic alignment")
- No cover letter phrases ("I'm pleased to present...", "With great enthusiasm...")
- No listing skills like a CV
- No speaking about the candidate in third person
- No references to "the case," "the assignment," "the task," or anything that reveals \
  this was generated from a structured case study

FORMAT RULES:
- No generic headers like "Introduction" or "Conclusion"
- Section headers should be the BUSINESS PROBLEM, not "Task 1" or "Deliverable A"
- Tables only where they genuinely add analytical value (P&L models, channel comparisons)
- Max 3 bullets per section
- Bold only for key terms, not decorative emphasis
- Total length: 4-6 pages (~2000-2500 words)

INTEGRITY RULES:
- You may ONLY cite companies and achievements that appear in CANDIDATE'S WORK HISTORY below.
- If a mapping shows "ninguno", do NOT invent experience. Use reasoning + market benchmarks.
- An honest "I haven't done this but here's my reasoning" is more credible than fabricated experience.
- Do NOT exaggerate results. If an achievement says [NO METRIC], do not add numbers.
- Use the exact company names from the work history — never approximate or rename them."""

APPLY_USER = """Generate a proactive diagnostic document using the structured context below.

APPLICANT PROFILE (extracted from CV):
{profile_json}

CANDIDATE'S WORK HISTORY (the ONLY experiences you may cite):
{work_history}

COMPANY INTELLIGENCE (from case study — use as research, NOT as homework to respond to):
{case_study}

BUSINESS PROBLEMS IDENTIFIED:
{problems_json}

COMPETITIVE CONTEXT:
{competitive_json}

CONSTRAINTS:
{constraints_str}

EXPERIENCE-TO-PROBLEM MAPPING (with transfer reasoning):
{mapping_json}

JOB DESCRIPTION:
{jd_text}

---

DOCUMENT STRUCTURE:

**Section 1 — Opening (half page)**
Use the candidate's name from the profile. Reference the company.
Pattern (adapt, don't copy literally):
"[Company] is at an inflection point: [diagnosis in 1 sentence from the most critical business problem].
Having navigated analogous situations — [brief reference to 1 experience with nivel_match \
'alto' from the mapping, citing the specific company and what was learned] — I see patterns here \
that are worth surfacing.
I put this together without being asked because there's a specific set of problems I think \
I can help solve."

Tone: direct, first person, no fluff.
NO "I'm pleased to present...", NO "With great enthusiasm...", NO standard cover letter phrases.
The opening must sound like someone who independently researched the company, NOT someone \
responding to an assignment.

**Section 2 — What I See (1-1.5 pages)**
This is YOUR diagnosis — the candidate's original analysis of the company's situation.
Do NOT paraphrase the case study text. Write this as if the candidate looked at public data, \
talked to people in the industry, and formed their own view.

Structure:
- 1 paragraph: the company's current inflection point with 2-3 data points from metricas_clave. \
  Frame it as "here's what I observe from the outside" not "the case says."
- 2-3 business problems, each as a short paragraph (NOT bullets). For each:
  - State the problem as you see it (use evidence from the case but reframe in your own words)
  - Name the root cause — the systemic issue, not the symptom
  - Briefly note what makes you recognize this pattern (from your own experience — 1 sentence)
- 1 closing paragraph: the single underlying tension that connects these problems. \
  This is the candidate's thesis — the strategic insight that ties the symptoms together.

**Section 3 — What I'd Do (2-3 pages)**
CRITICAL: Organize this section by BUSINESS PROBLEM, not by task number or deliverable.
Each sub-section header should be the problem you're solving (e.g., "Fixing the pricing architecture \
before it collapses the margin stack" not "Task 1: Omnichannel Pricing").

DEPTH DISTRIBUTION: Spend ~50% of Section 3 on the problem with the strongest experience match (alto), \
~30% on the second strongest, ~20% on the remaining. Depth on your strongest match > breadth.
Lead with the most critical problem if your strongest match aligns with it. If not, lead with the \
most critical problem but go deepest on your strongest match.

JD COVERAGE RULE: The case study was built from the job description. Every task in the case represents \
a JD requirement the hiring manager cares about. After writing your deep-dive solutions for each \
business problem, CHECK: are there any task areas from the TASKS list that you haven't addressed \
in your problem-by-problem discussion? If so, weave them in:
- If a task area (e.g., "org design," "paid channel mix," "competitive positioning") is NOT covered \
  by any business problem section, add a brief 2-3 sentence perspective on it within the most \
  relevant problem section.
- You do NOT need equal depth on every task area. But you MUST at least signal awareness and a point \
  of view on every major capability the case tests. A hiring manager who sees zero mention of org \
  design in a CGO document will wonder if the candidate can build a team.
- For leadership roles (Director, VP, C-level, Head of): ALWAYS include your perspective on team \
  structure and first hires, even if it's a single paragraph. This is non-negotiable for senior roles.

COMPETITIVE RESPONSE RULE: If COMPETITIVE CONTEXT is provided above, at least ONE problem section \
MUST directly address how to win against the named competitor(s). Don't just identify the competitive \
threat — propose a specific counter-strategy (positioning, pricing, messaging, or distribution advantage).

For each problem where nivel_match is "alto" or "medio":

**[Problem framed as what you're fixing — active voice, specific]**
The approach: [specific recommendation in 2-3 sentences with concrete tools, frameworks, and logic. \
Must respect the constraints: {constraints_str}.]
Why this works: [reasoning grounded in company data — reference specific numbers from metricas_clave. \
Connect to the root_cause and consequence_if_ignored from the problem extraction.]
What I've already tested: [MUST cite the experience from the mapping — use the exact company name, \
describe what was done, and the concrete result. Use the "razonamiento" from the mapping to \
explain WHY this experience transfers to this specific problem. This is where you prove you're \
not just theorizing — you've done a version of this before.]

For problems where nivel_match is "bajo":

**[Problem framed as what you're fixing]**
The approach: [specific recommendation with reasoning]
Why this works: [logic + company data]
Related experience: [acknowledge the gap — "I haven't solved exactly this, but at [company] I [related work] \
which taught me [transferable principle that applies here]"]

For problems where nivel_match is "ninguno":

**[Problem framed as what you're fixing]**
The approach: [specific recommendation grounded in company data and market benchmarks]
Reasoning: [logic + industry standards — cite benchmarks, not personal experience]
What I'd need to validate: [honest question I'd investigate in the first week — this shows self-awareness \
and signals you know what you don't know]

**Section 4 — The non-obvious insight (half page)**
Identify ONE assumption about the company's situation that your experience contradicts or deepens.

The insight must follow this pattern:
"The conventional view would be [X is the bottleneck / Y is the metric that matters / Z is the right approach].
But when I [specific experience from work history], I discovered [unexpected finding].
Applied to [company], this means [specific practical consequence]."

This must reference BOTH company data AND a real experience from the work history.
A good insight is debatable — reasonable people could disagree.
Do NOT generate a generic observation. It must come from pattern recognition across the \
candidate's experience and this specific company's situation.

If after genuine analysis no insight emerges that meets ALL criteria above, write a single \
paragraph explaining the one thing you'd investigate in the first week and why.

**Section 4B — First 30 Days (3-5 lines)**
List exactly 3 things you'd do in the first 30 days — not vague intentions, but specific actions:
- Decision 1: [concrete action] — because [reasoning tied to the most urgent problem]
- Decision 2: [concrete action] — because [reasoning]
- Decision 3: [concrete action] — because [reasoning]
These should be DECISIONS, not "learn the business" or "meet the team." Show what you'd actually \
change, build, or kill in month one based on the diagnosis above.

**Section 5 — Close (3-4 lines)**
No fluff. No "I look forward to your response."
Pattern: "If any of this resonates, happy to go deeper on [most relevant problem from the diagnosis].
[Contact info from profile, or placeholder if not available]"

---

After the document, generate separately:

### Email
Subject: [Company] — diagnostic + what I'd do in the first 90 days
[Hiring manager name if in JD, or "Hi,"]
I spent some time looking at where [company] is in its [relevant transition — e.g., "DTC-to-retail expansion"] \
and put together a diagnosis of the challenges ahead and how I'd approach them. Attaching in case it's useful.
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
    constraints_parts = []
    if constraints.get("budget"):
        constraints_parts.append(f"Budget: {constraints['budget']}")
    if constraints.get("team"):
        constraints_parts.append(f"Team: {constraints['team']}")
    if constraints.get("timeline"):
        constraints_parts.append(f"Timeline: {constraints['timeline']}")
    if constraints.get("supply"):
        constraints_parts.append(f"Supply: {constraints['supply']}")
    if constraints.get("other"):
        constraints_parts.append(f"Other: {constraints['other']}")
    constraints_str = "\n".join(constraints_parts) if constraints_parts else "Not specified"

    # Build the final prompt with all structured context
    prompt = APPLY_USER.format(
        profile_json=json.dumps(profile, indent=2, ensure_ascii=False)[:6000],
        work_history=work_history,
        case_study=case_study[:10000],
        problems_json=json.dumps(caso.get("business_problems", []), indent=2, ensure_ascii=False)[:5000],
        competitive_json=json.dumps(caso.get("competitive_context", []), indent=2, ensure_ascii=False)[:2000],
        constraints_str=constraints_str,
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
