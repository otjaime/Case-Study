"""Claude API calls and prompt logic for generating the case study."""

import anthropic
from rich.console import Console

console = Console()

SYSTEM_PROMPT = """You are a senior growth strategist preparing a targeted case study to accompany a job application. \
Your goal is to demonstrate deep understanding of the company's specific growth challenges and show \
how you would approach them in the first 90 days in the role.

The output must feel like it was written by someone who has genuinely thought about this company — \
not a generic framework. Use the data provided to make specific, grounded observations. \
Reference real numbers, real competitors, real channels, and real news when available.

Be direct and opinionated. Avoid filler. Every sentence should add signal."""


def _build_user_prompt(context: dict) -> str:
    """Build the user prompt from the enriched context object."""
    # Basic role info
    skills = "\n".join(f"- {s}" for s in context["key_skills_required"]) if context["key_skills_required"] else "not extracted"
    challenges = "\n".join(f"- {c}" for c in context["inferred_challenges"]) if context["inferred_challenges"] else "none inferred"
    notable = "\n".join(f"- {c}" for c in context["notable_claims"]) if context["notable_claims"] else "none found"

    # Funding & financials
    investors_str = ", ".join(context.get("investors", [])) if context.get("investors") else "unknown"
    revenue_str = "\n".join(f"- {h}" for h in context.get("revenue_hints", [])) if context.get("revenue_hints") else "none found"

    # Marketing
    channels_str = ", ".join(context.get("marketing_channels", [])) if context.get("marketing_channels") else "none detected"
    strategy_str = ", ".join(context.get("strategy_signals", [])) if context.get("strategy_signals") else "none detected"

    # Competitors
    competitors_str = ", ".join(context.get("competitors", [])) if context.get("competitors") else "none identified"

    # News
    news_str = context.get("recent_news", "none found") or "none found"

    # Pricing details
    pricing = context.get("pricing_details", "")
    pricing_section = f"\n**Pricing Page Content:**\n{pricing[:1500]}" if pricing else ""

    # Product / about details
    product = context.get("product_details", "")
    product_section = f"\n**Product/About Details:**\n{product[:1500]}" if product else ""

    # Careers page
    careers = context.get("careers_page", "")
    careers_section = f"\n**Careers Page Signals:**\n{careers[:1000]}" if careers else ""

    # Raw research for Claude to synthesize
    marketing_raw = context.get("marketing_raw", "")
    marketing_intel = f"\n**Marketing Intelligence (raw research):**\n{marketing_raw[:2500]}" if marketing_raw else ""

    competitive_raw = context.get("competitive_raw", "")
    competitive_intel = f"\n**Competitive Intelligence (raw research):**\n{competitive_raw[:2000]}" if competitive_raw else ""

    news_raw = context.get("news_raw", "")
    news_intel = f"\n**Recent Press & News (raw):**\n{news_raw[:2500]}" if news_raw else ""

    return f"""Here is the context for the application:

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

Generate a case study document with the following sections:

1. **Company Snapshot** (3-4 sentences)
   - What they do, who they serve, where they appear to be in their growth trajectory
   - Reference specific data points: funding, traffic, competitors, or claims where available
   - One specific observation that shows you've actually looked at their business

2. **Growth Diagnosis** (4-6 bullet points)
   - The most likely growth constraints this company is facing right now given stage, model, and role
   - Be specific — not "they need more leads" but "their paid CAC is likely under pressure because X"
   - Reference actual competitors, channels, or news when you can

3. **What the Role is Really About**
   - Your interpretation of what problem this hire is meant to solve, reading between the lines of the JD
   - What success looks like in 12 months if the hire goes well

4. **My 90-Day Plan**
   - Week 1-2: Listen and audit (specific things you'd audit based on the data above)
   - Month 1: Quick wins (specific hypotheses you'd test first and why)
   - Month 2-3: Structural bets (bigger initiatives you'd build toward)

5. **One Specific Hypothesis**
   - A single, concrete growth hypothesis you'd want to test in the first 60 days
   - Format: "I believe that [action] will [result] because [reason]. I'd measure it by [metric]."
   - Ground this in the actual company data — reference their channels, competitors, or product.

6. **Why Me** (optional, 2-3 sentences)
   - [CANDIDATE TO FILL THIS IN]"""


async def generate_case_study(context: dict) -> str:
    """Call Claude API to generate the case study."""
    console.print("\n[bold blue]Generating case study with Claude...[/bold blue]")

    client = anthropic.AsyncAnthropic()

    user_prompt = _build_user_prompt(context)

    message = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=6000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    text = message.content[0].text
    console.print("[green]Case study generated successfully.[/green]")
    return text
