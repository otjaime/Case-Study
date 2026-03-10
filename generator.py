"""Claude API calls and prompt logic for generating the business case challenge."""

import anthropic
from rich.console import Console

console = Console()

# ---------------------------------------------------------------------------
# Business model context templates (Improvement 5)
# ---------------------------------------------------------------------------

BUSINESS_MODEL_CONTEXT = {
    "B2B SaaS": """
Key metrics to reference: ARR, NRR, CAC payback, pipeline velocity, ACV, churn rate.
Common challenges: long sales cycles, ICP clarity, PLG vs SLG motion, expansion revenue.
Relevant channels: outbound, content/SEO, product virality, partnerships.
    """,
    "DTC ecommerce": """
Key metrics to reference: CAC, LTV, ROAS, repeat purchase rate, AOV, contribution margin.
Common challenges: rising paid CAC, retention vs acquisition balance, creative fatigue, attribution.
Relevant channels: Meta/TikTok paid, email/SMS, influencer, SEO.
    """,
    "marketplace": """
Key metrics to reference: GMV, take rate, liquidity, supply/demand balance, CAC by side.
Common challenges: cold start, disintermediation, trust, geographic expansion.
Relevant channels: supply-side direct sales, demand-side paid, community, SEO.
    """,
    "fintech": """
Key metrics to reference: MAU, transaction volume, activation rate, card spend, CAC vs LTV.
Common challenges: regulatory constraints, trust building, activation funnel, cross-sell.
Relevant channels: referral programs, employer partnerships, content, paid social.
    """,
    "institutional B2B": """
Key metrics to reference: qualified pipeline, ACV, deal cycle length, win rate, ARR, NRR.
Common challenges: founder-dependent sales, long trust-building cycles, competitive incumbents,
  no brand awareness in target market, pipeline consistency.
Relevant channels: prime broker relationships, fund administrator networks, allocator introductions,
  conference-based pipeline, community-led content (NOT paid digital, NOT SEO).
Buyer dynamics: buying committees (PM/CIO champion + COO/CFO budget holder),
  3-9 month deal cycles, relationship-first — cold outbound rarely works.
Anti-patterns to call out: generic AI messaging fails with CIOs, booth sponsorships ROI-negative,
  standard B2B SaaS playbooks don't apply.
    """,
}


def _get_model_context(business_model: str) -> str:
    for key in BUSINESS_MODEL_CONTEXT:
        if key.lower() in business_model.lower():
            return BUSINESS_MODEL_CONTEXT[key]
    return ""


# ---------------------------------------------------------------------------
# Required task types per business model (Fix 4)
# ---------------------------------------------------------------------------

REQUIRED_TASK_TYPES = {
    "institutional B2B": [
        "channel-market fit analysis (why standard B2B SaaS channels don't work here)",
        "positioning framework vs established incumbents",
        "90-day pipeline generation plan from near-zero",
    ],
    "DTC ecommerce": [
        "attribution architecture (multi-touch, cross-channel)",
        "retention funnel redesign (activation + repeat purchase)",
        "paid channel mix and creative strategy",
    ],
    "B2B SaaS": [
        "ICP definition and segmentation",
        "pipeline generation and channel prioritization",
        "PLG vs SLG motion analysis",
    ],
    "fintech": [
        "activation funnel optimization",
        "trust and regulatory positioning",
        "referral and partnership channel design",
    ],
    "marketplace": [
        "supply-side vs demand-side acquisition strategy",
        "liquidity and cold-start problem",
        "unit economics by market/geography",
    ],
}


FINANCIAL_MODEL_TASKS = [
    "operating plan", "financial model", "p&l", "revenue model",
    "budget allocation", "aop", "contribution margin", "unit economics",
]


def _requires_table_format(task_text: str) -> bool:
    return any(term in task_text.lower() for term in FINANCIAL_MODEL_TASKS)


def _get_task_guidance(business_model: str, context: dict | None = None) -> str:
    required_types = REQUIRED_TASK_TYPES.get(business_model, [])
    parts = []

    if required_types:
        types_str = "\n".join(f"  - {t}" for t in required_types)
        parts.append(f"""
REQUIRED TASK TYPES FOR THIS BUSINESS MODEL ({business_model}):
The "Your Task" section MUST include tasks covering:
{types_str}
These are non-negotiable for this business model context.""")

    # Check if any required tasks or core tasks involve financial modeling
    all_tasks = list(required_types)
    if context:
        req_map = context.get("requirements_map", {})
        all_tasks.extend(req_map.get("core_tasks", []))
    if any(_requires_table_format(t) for t in all_tasks):
        parts.append("""
FORMATTING REQUIREMENT: Any task involving financial modeling, P&L, or budget allocation \
MUST include a structured table with explicit columns (channel, metric, value, assumption). \
Do not describe the model in prose — show the structure.""")

    return "\n".join(parts) if parts else ""


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

DIAGNOSIS_SYSTEM_PROMPT = """You are a senior growth strategist. Your task is to deeply analyze a company \
and diagnose their most likely growth challenges given their stage, model, and the role \
they are hiring for. Be specific, opinionated, and grounded in the data provided."""

CASE_SYSTEM_PROMPT = """You are an experienced hiring manager designing a take-home business case for a final-round \
candidate. Your job is to create a realistic, challenging assignment that tests whether the candidate \
truly understands the company's growth problems and can think strategically about solving them.

The business case should feel indistinguishable from a real one — the kind top companies send during \
late-stage screening. It should be specific to this company's actual situation, not a generic exercise.

The case MUST test every skill, tool, and objective listed in the requirements map.
A case that doesn't test a required skill has failed its purpose.

Rules:
- Write in the voice of the hiring manager / company, not the candidate
- Use real data from the research to ground the scenario (funding, competitors, channels, market position)
- The challenge should be hard enough to separate great candidates from good ones
- Include enough context that a strong candidate can produce impressive work, but don't give away the answer
- Calibrate difficulty and scope to the seniority level of the role
- Make the deliverables concrete and specific — not vague "create a strategy"
- Include realistic constraints (budget, team size, timeline) that force tradeoffs
- Each task in "Your Task" must test a DIFFERENT skill from the requirements map"""


# ---------------------------------------------------------------------------
# Context builder (shared between stages)
# ---------------------------------------------------------------------------

def _build_context_block(context: dict) -> str:
    """Build the research context block used by both stages."""
    skills = "\n".join(f"- {s}" for s in context["key_skills_required"]) if context["key_skills_required"] else "not extracted"
    challenges = "\n".join(f"- {c}" for c in context["inferred_challenges"]) if context["inferred_challenges"] else "none inferred"
    notable = "\n".join(f"- {c}" for c in context["notable_claims"]) if context["notable_claims"] else "none found"

    investors_str = ", ".join(context.get("investors", [])) if context.get("investors") else "unknown"
    revenue_str = "\n".join(f"- {h}" for h in context.get("revenue_hints", [])) if context.get("revenue_hints") else "none found"

    channels_str = ", ".join(context.get("marketing_channels", [])) if context.get("marketing_channels") else "none detected"
    strategy_str = ", ".join(context.get("strategy_signals", [])) if context.get("strategy_signals") else "none detected"

    competitors_str = ", ".join(context.get("competitors", [])) if context.get("competitors") else "none identified"

    news_str = context.get("recent_news", "none found") or "none found"

    pricing = context.get("pricing_details", "")
    pricing_section = f"\n**Pricing Page Content:**\n{pricing[:1500]}" if pricing else ""

    product = context.get("product_details", "")
    product_section = f"\n**Product/About Details:**\n{product[:1500]}" if product else ""

    careers = context.get("careers_page", "")
    careers_section = f"\n**Careers Page Signals:**\n{careers[:1000]}" if careers else ""

    marketing_raw = context.get("marketing_raw", "")
    marketing_intel = f"\n**Marketing Intelligence (raw research):**\n{marketing_raw[:2500]}" if marketing_raw else ""

    competitive_raw = context.get("competitive_raw", "")
    competitive_intel = f"\n**Competitive Intelligence (raw research):**\n{competitive_raw[:2000]}" if competitive_raw else ""

    news_raw = context.get("news_raw", "")
    news_intel = f"\n**Recent Press & News (raw):**\n{news_raw[:2500]}" if news_raw else ""

    model_context = _get_model_context(context.get("business_model", ""))
    model_section = f"\n**Business Model Reference:**\n{model_context}" if model_context else ""

    competitors_detail = context.get("competitors_detail", "")
    competitors_detail_section = f"\n**Competitor Deep Dive:**\n{competitors_detail}" if competitors_detail else ""

    industry_intel = context.get("industry_intel", "")
    industry_section = f"\n**Industry Intelligence:**\n{industry_intel[:2000]}" if industry_intel else ""

    # Requirements map section (from decomposer)
    req_map = context.get("requirements_map", {})
    req_section = ""
    if req_map and any(req_map.get(k) for k in ["tools_required", "core_tasks", "primary_kpis"]):
        tools = ", ".join(req_map.get("tools_required", [])) or "none specified"
        core_tasks = "\n".join(f"  - {t}" for t in req_map.get("core_tasks", [])) or "  - none extracted"
        primary_kpis = ", ".join(req_map.get("primary_kpis", [])) or "none specified"
        secondary_kpis = ", ".join(req_map.get("secondary_kpis", [])) or "none specified"
        emerging = ", ".join(req_map.get("emerging_skills", [])) or "none detected"
        methodologies = ", ".join(req_map.get("methodologies", [])) or "none specified"
        leadership = ", ".join(req_map.get("leadership_signals", [])) or "none specified"

        req_section = f"""

---

**WHAT THIS ROLE MUST DEMONSTRATE (from JD decomposition):**
Required tools: {tools}
Core tasks:
{core_tasks}
Primary KPIs: {primary_kpis}
Secondary KPIs: {secondary_kpis}
Emerging skills required: {emerging}
Methodologies: {methodologies}
Leadership signals: {leadership}"""

    # Coverage gaps section
    coverage_gaps = context.get("coverage_gaps", [])
    coverage_section = ""
    if coverage_gaps:
        gaps_str = "\n".join(f"  - {g}" for g in coverage_gaps)
        coverage_section = f"""

---

**COVERAGE REQUIREMENTS (items NOT found in research — case MUST address these):**
{gaps_str}"""

    return f"""**COMPANY PROFILE:**
Company: {context['company_name']}
Industry: {context.get('industry', 'unknown')}
Stage: {context.get('company_stage', 'unknown')}
Market: {context.get('market', 'unknown')}
Product type: {context.get('product_type', 'unknown')}
Headcount: {context.get('headcount_estimate', 'unknown')}

**ROLE:**
Role: {context['job_title']} reporting to {context.get('reports_to', 'unknown')}
Seniority: {context['seniority']}
Team: {context.get('team_size', 'unknown')} direct reports
Role type: {context.get('role_type', 'unknown')}
{req_section}

---

**Job Description:**
{context['job_description']}

**Key Skills Required:**
{skills}

---

**Company Context:**
{context['company_description'][:5000]}

Business model: {context['business_model']}
Growth stage: {context['growth_stage']}
{model_section}
{pricing_section}
{product_section}
{careers_section}
{industry_section}

**Notable Public Claims:**
{notable}

---

**Funding & Financials:**
Funding stage: {context.get('funding_stage', 'unknown')}
Total raised: {context.get('total_raised', 'unknown')}
Key investors: {investors_str}
Revenue signals:
{revenue_str}

---

**Marketing & Growth Channels:**
Channels detected: {channels_str}
Strategy signals: {strategy_str}
{marketing_intel}

---

**Competitive Landscape:**
Known competitors: {competitors_str}
{competitors_detail_section}
{competitive_intel}

---

**Recent News & Events:**
{news_str}
{news_intel}

---

**Pre-analysis — likely challenges:**
{challenges}
{coverage_section}"""


# ---------------------------------------------------------------------------
# Stage 1: Business Diagnosis
# ---------------------------------------------------------------------------

async def _run_diagnosis(client: anthropic.AsyncAnthropic, context: dict) -> str:
    """Stage 1: Diagnose the company's growth challenges."""
    console.print("  [dim]Stage 1: Diagnosing business challenges...[/dim]")

    context_block = _build_context_block(context)

    message = await client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1500,
        system=DIAGNOSIS_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"""{context_block}

---

What are the 5 most specific growth challenges this company is facing RIGHT NOW?

For each one:
1. Name it concisely
2. Explain why you believe it exists based on the data above
3. Rate its severity (high/medium/low)

Be specific and opinionated. Reference actual data points, not generic business platitudes."""
        }],
    )

    return message.content[0].text


# ---------------------------------------------------------------------------
# Stage 2: Case Construction
# ---------------------------------------------------------------------------

async def _run_case_construction(client: anthropic.AsyncAnthropic, context: dict, diagnosis: str) -> str:
    """Stage 2: Build the business case challenge on top of the diagnosis."""
    console.print("  [dim]Stage 2: Constructing business case...[/dim]")

    context_block = _build_context_block(context)
    task_guidance = _get_task_guidance(context.get("business_model", ""), context)

    message = await client.messages.create(
        model="claude-opus-4-6",
        max_tokens=6000,
        system=CASE_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"""{context_block}

---

**Expert Diagnosis (from senior growth strategist):**

{diagnosis}

---

Now generate a realistic take-home business case BUILT ON the diagnosed challenges above. Use the most severe and specific challenges as the foundation for the case.

CRITICAL: If a COVERAGE REQUIREMENTS section is listed above, the case MUST include tasks that test those items. \
Each task should test a different skill from the requirements map. \
At least one task must explicitly require the listed tools. \
At least one task must reference emerging skills.
{task_guidance}
Structure:

# [Company Name] — [Role Title] Business Case

## Background

1 paragraph describing the company's current situation. Include specific metrics where available. \
Reference their stage, market position, and the digital/growth challenge they face. \
Use real data points from the research but present them as internal context the company is sharing with the candidate. \
Make it feel like a real internal brief — direct, specific, a bit raw.

## The Challenge

The specific problem the new hire inherits. Frame it as a real operational problem, \
not a strategy exercise. Include data that makes the problem tangible. \
This should be the kind of problem the person in this role would actually face in their first 90 days.

## Your Task

4-5 numbered deliverables the candidate must produce. Each task must test a DIFFERENT skill from the requirements map:
- At least one task must explicitly require the listed tools (e.g. "Build a measurement framework using [specific tool]")
- At least one task must reference emerging skills listed in the requirements
- At least one deliverable that requires creative/strategic thinking (not just analysis)
- At least one that requires quantitative reasoning (metrics, projections, budget)
- Each task should be answerable in 300-500 words by the candidate

## Data & Context

Provide enough data for the candidate to build quantitative answers. Include:
- Market/competitive data from the research
- Channel performance hints (from marketing research)
- Company metrics that can be inferred from public data
- Include metrics relevant to the primary KPIs from the requirements map
- Include constraints that reflect the company stage reality
- Frame gaps honestly: "We don't have reliable attribution data yet" or "Our current CRM doesn't track X"

## Evaluation Criteria

5 bullet points describing what you're looking for in a strong response. \
Mirror the language of the job description requirements. \
Include explicit criteria for emerging skills listed in the requirements map. \
Calibrate to the seniority level. For senior roles, emphasize strategic thinking and prioritization. \
For more junior roles, emphasize analytical rigor and creativity.

## Constraints

Realistic constraints that force interesting tradeoffs:
- Budget range (infer from company stage/size)
- Team size and reporting structure from the role profile
- Timeline (first 90 days)
- Any technical or organizational constraints implied by the JD or company stage

## Format & Submission

Specify format appropriate for the seniority level. \
Expected length (2-4 pages), format (deck vs. doc vs. memo), and any structural requirements. \
Keep it practical — what a real company would ask for.

---

IMPORTANT: The output should read as if it came directly from the company's hiring team. \
Do NOT include tips, hints, or guidance for the candidate. Do NOT reveal what the "right answer" is. \
The candidate should have to do real strategic work to produce a strong response."""
        }],
    )

    return message.content[0].text


# ---------------------------------------------------------------------------
# Coverage patching
# ---------------------------------------------------------------------------

async def _patch_coverage(
    client: anthropic.AsyncAnthropic,
    context: dict,
    case_text: str,
    missing_items: list[str],
) -> str:
    """Patch a generated case to explicitly cover missing tools/skills/KPIs."""
    console.print("  [dim]Patching coverage gaps...[/dim]")
    gaps_str = "\n".join(f"- {g}" for g in missing_items)
    message = await client.messages.create(
        model="claude-opus-4-6",
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": f"""The following business case is missing explicit coverage of required items.
Revise the relevant tasks in "Your Task" and "Evaluation Criteria" sections to explicitly
require and evaluate these items. Do not change the Background or Challenge sections.
Keep the same overall structure and length.

MISSING ITEMS (must appear explicitly in the revised case):
{gaps_str}

ORIGINAL CASE:
{case_text}

Return ONLY the revised case, no preamble."""
        }],
    )
    return message.content[0].text


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def generate_case_study(context: dict) -> tuple[str, str]:
    """Two-stage case generation: diagnosis first, then case construction.

    Returns (case_text, diagnosis_text).
    """
    console.print("\n[bold blue]Generating business case challenge with Claude...[/bold blue]")

    client = anthropic.AsyncAnthropic()

    # Stage 1: Diagnosis
    diagnosis = await _run_diagnosis(client, context)

    # Stage 2: Case construction built on top of diagnosis
    case_text = await _run_case_construction(client, context, diagnosis)

    # Validate coverage against generated case, not input context
    coverage_gaps = context.get("coverage_gaps", [])
    if coverage_gaps:
        missing = []
        for gap in coverage_gaps:
            item = gap.split(":", 1)[1].lower() if ":" in gap else gap.lower()
            if item not in case_text.lower():
                missing.append(gap)

        if missing:
            console.print(f"[yellow]Coverage gaps in generated case: {missing}[/yellow]")
            case_text = await _patch_coverage(client, context, case_text, missing)

    console.print("[green]Business case generated successfully.[/green]")
    return case_text, diagnosis


async def generate_case_study_streaming(context: dict):
    """Two-stage generation with Stage 2 streamed via async generator.

    Yields dicts: {"stage": "diagnosis"} at start, {"stage": "generating"} when
    Stage 2 begins, {"chunk": text} for each streamed token, {"stage": "done"}
    at the end.

    Also yields {"diagnosis": text} after Stage 1 completes so caller can
    forward it if needed.
    """
    client = anthropic.AsyncAnthropic()

    # Stage 1: Diagnosis (fast, non-streaming)
    yield {"stage": "diagnosis"}
    diagnosis = await _run_diagnosis(client, context)
    yield {"diagnosis": diagnosis}

    # Stage 2: Case construction (streamed)
    yield {"stage": "generating"}

    context_block = _build_context_block(context)
    task_guidance = _get_task_guidance(context.get("business_model", ""), context)

    async with client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=6000,
        system=CASE_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"""{context_block}

---

**Expert Diagnosis (from senior growth strategist):**

{diagnosis}

---

Now generate a realistic take-home business case BUILT ON the diagnosed challenges above. Use the most severe and specific challenges as the foundation for the case.

CRITICAL: If a COVERAGE REQUIREMENTS section is listed above, the case MUST include tasks that test those items. \
Each task should test a different skill from the requirements map. \
At least one task must explicitly require the listed tools. \
At least one task must reference emerging skills.
{task_guidance}
Structure:

# [Company Name] — [Role Title] Business Case

## Background

1 paragraph describing the company's current situation. Include specific metrics where available. \
Reference their stage, market position, and the digital/growth challenge they face. \
Use real data points from the research but present them as internal context the company is sharing with the candidate. \
Make it feel like a real internal brief — direct, specific, a bit raw.

## The Challenge

The specific problem the new hire inherits. Frame it as a real operational problem, \
not a strategy exercise. Include data that makes the problem tangible. \
This should be the kind of problem the person in this role would actually face in their first 90 days.

## Your Task

4-5 numbered deliverables the candidate must produce. Each task must test a DIFFERENT skill from the requirements map:
- At least one task must explicitly require the listed tools (e.g. "Build a measurement framework using [specific tool]")
- At least one task must reference emerging skills listed in the requirements
- At least one deliverable that requires creative/strategic thinking (not just analysis)
- At least one that requires quantitative reasoning (metrics, projections, budget)
- Each task should be answerable in 300-500 words by the candidate

## Data & Context

Provide enough data for the candidate to build quantitative answers. Include:
- Market/competitive data from the research
- Channel performance hints (from marketing research)
- Company metrics that can be inferred from public data
- Include metrics relevant to the primary KPIs from the requirements map
- Include constraints that reflect the company stage reality
- Frame gaps honestly: "We don't have reliable attribution data yet" or "Our current CRM doesn't track X"

## Evaluation Criteria

5 bullet points describing what you're looking for in a strong response. \
Mirror the language of the job description requirements. \
Include explicit criteria for emerging skills listed in the requirements map. \
Calibrate to the seniority level. For senior roles, emphasize strategic thinking and prioritization. \
For more junior roles, emphasize analytical rigor and creativity.

## Constraints

Realistic constraints that force interesting tradeoffs:
- Budget range (infer from company stage/size)
- Team size and reporting structure from the role profile
- Timeline (first 90 days)
- Any technical or organizational constraints implied by the JD or company stage

## Format & Submission

Specify format appropriate for the seniority level. \
Expected length (2-4 pages), format (deck vs. doc vs. memo), and any structural requirements. \
Keep it practical — what a real company would ask for.

---

IMPORTANT: The output should read as if it came directly from the company's hiring team. \
Do NOT include tips, hints, or guidance for the candidate. Do NOT reveal what the "right answer" is. \
The candidate should have to do real strategic work to produce a strong response."""
        }],
    ) as stream:
        async for text in stream.text_stream:
            yield {"chunk": text}

    yield {"stage": "done"}


# ---------------------------------------------------------------------------
# Quality scoring (Improvement 7)
# ---------------------------------------------------------------------------

async def score_case_quality(case_text: str, company_name: str) -> dict:
    """Score the generated case on specificity, realism, and difficulty.

    Returns dict with keys: specificity, realism, difficulty (1-10 each),
    and flags (list of generic phrases detected).
    """
    prompt = f"""Score this business case on 3 dimensions (1-10 each):
1. specificity: Does it reference company-specific details or is it generic?
2. realism: Are the numbers and challenges plausible for this company?
3. difficulty: Would this case challenge a senior marketer or is it too easy?

Company: {company_name}
Case: {case_text[:2000]}

Respond ONLY with JSON: {{"specificity": X, "realism": X, "difficulty": X, "flags": ["list any generic phrases"]}}"""

    try:
        client = anthropic.AsyncAnthropic()
        message = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        import json
        text = message.content[0].text.strip()
        # Try to parse JSON from string
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
        return {"specificity": 0, "realism": 0, "difficulty": 0, "flags": ["parse error"]}
    except Exception:
        return {"specificity": 0, "realism": 0, "difficulty": 0, "flags": ["scoring failed"]}
