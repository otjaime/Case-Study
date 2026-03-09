"""Claude API calls and prompt logic for generating the business case challenge."""

import anthropic
from rich.console import Console

console = Console()

SYSTEM_PROMPT = """You are an experienced hiring manager designing a take-home business case for a final-round \
candidate. Your job is to create a realistic, challenging assignment that tests whether the candidate \
truly understands the company's growth problems and can think strategically about solving them.

The business case should feel indistinguishable from a real one — the kind top companies send during \
late-stage screening. It should be specific to this company's actual situation, not a generic exercise.

Rules:
- Write in the voice of the hiring manager / company, not the candidate
- Use real data from the research to ground the scenario (funding, competitors, channels, market position)
- The challenge should be hard enough to separate great candidates from good ones
- Include enough context that a strong candidate can produce impressive work, but don't give away the answer
- Calibrate difficulty and scope to the seniority level of the role
- Make the deliverables concrete and specific — not vague "create a strategy"
- Include realistic constraints (budget, team size, timeline) that force tradeoffs"""


def _build_user_prompt(context: dict) -> str:
    """Build the user prompt from the enriched context object."""
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

    return f"""Design a take-home business case for this role. Use the research below to make it specific and realistic.

**Role:** {context['job_title']} at {context['company_name']}
**Seniority:** {context['seniority']}

**Job Description:**
{context['job_description']}

**Key Skills Required:**
{skills}

---

**Company Context:**
{context['company_description'][:5000]}

Business model: {context['business_model']}
Growth stage: {context['growth_stage']}
{pricing_section}
{product_section}
{careers_section}

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
{competitive_intel}

---

**Recent News & Events:**
{news_str}
{news_intel}

---

**Pre-analysis — likely challenges:**
{challenges}

---

Now generate a realistic take-home business case with this structure:

# [Company Name] — [Role Title] Business Case

## Background

A 2-3 paragraph scenario brief written as the hiring manager. Set the stage: what the company does, \
where you are in your growth journey, and what specific challenge you're facing that led to this hire. \
Use real data points from the research but present them as internal context the company is sharing with the candidate. \
Make it feel like a real internal brief — direct, specific, a bit raw.

## The Challenge

A clear, specific problem statement. Not vague ("improve growth") but pointed: a specific constraint, \
a specific metric under pressure, a specific strategic decision that needs to be made. \
This should be the kind of problem the person in this role would actually face in their first 90 days.

## Your Task

3-4 concrete deliverables the candidate must produce. Each should be specific and actionable:
- Not "create a strategy" but "propose a channel mix with estimated budget allocation across Q1"
- Not "analyze the market" but "identify our 3 strongest positioning angles against [specific competitors] and explain why"
- Include at least one deliverable that requires creative/strategic thinking (not just analysis)
- Include at least one that requires quantitative reasoning (metrics, projections, budget)

## Data & Context

Present key data points as if the company is sharing them for the exercise. Include:
- Market/competitive data from the research
- Channel performance hints (from marketing research)
- Company metrics that can be inferred from public data
- Anything from their website, pricing, or news that's relevant
- Frame gaps honestly: "We don't have reliable attribution data yet" or "Our current CRM doesn't track X"

## Evaluation Criteria

4-5 bullet points describing what you're looking for in a strong response. \
Calibrate to the seniority level. For senior roles, emphasize strategic thinking and prioritization. \
For more junior roles, emphasize analytical rigor and creativity. \
Include at least one criterion that reveals whether the candidate actually understands THIS company vs. giving a generic answer.

## Constraints

Realistic constraints that force interesting tradeoffs:
- Budget range (infer from company stage/size)
- Team size (current marketing/growth team size, infer from careers page or stage)
- Timeline (first 90 days)
- Any technical or organizational constraints implied by the JD or company stage

## Format & Submission

Specify: expected length (2-4 pages), format (deck vs. doc vs. memo), and any structural requirements. \
Keep it practical — what a real company would ask for.

---

IMPORTANT: The output should read as if it came directly from the company's hiring team. \
Do NOT include tips, hints, or guidance for the candidate. Do NOT reveal what the "right answer" is. \
The candidate should have to do real strategic work to produce a strong response."""


async def generate_case_study(context: dict) -> str:
    """Call Claude API to generate the business case challenge."""
    console.print("\n[bold blue]Generating business case challenge with Claude...[/bold blue]")

    client = anthropic.AsyncAnthropic()

    user_prompt = _build_user_prompt(context)

    message = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=6000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    text = message.content[0].text
    console.print("[green]Business case generated successfully.[/green]")
    return text
