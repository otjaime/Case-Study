"""Claude API calls and prompt logic for generating the business case."""

import anthropic
from rich.console import Console

console = Console()

SYSTEM_PROMPT = """You are a senior growth strategist preparing an open business case document to accompany \
a job application. The goal is NOT to present a finished plan — it's to demonstrate that you've done real \
research, you understand the company's context deeply, and you have sharp questions and hypotheses worth \
discussing in an interview.

The document should feel like something a thoughtful candidate would bring to a first conversation with \
the hiring manager — grounded in data, honest about what you don't know, and designed to start a \
strategic conversation rather than end one.

Rules:
- Reference specific data points when available (funding, competitors, channels, news). Never invent data.
- Frame observations as hypotheses, not conclusions. Use "likely", "suggests", "I'd want to validate".
- Show your reasoning. The hiring manager should see HOW you think, not just WHAT you think.
- Be direct. No filler, no generic frameworks. Every sentence should demonstrate real research or sharp thinking.
- If data is missing or unclear, name the gap explicitly — that's a strength, not a weakness."""


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

Generate an **open business case** document — a strategic discussion starter, NOT a finished plan. \
The candidate will use this as a conversation piece in their interview. Structure it as follows:

1. **Company Snapshot** (3-4 sentences)
   - What they do, who they serve, where they are in their growth trajectory
   - Reference specific data: funding, competitors, market position. Only cite data you actually have.

2. **What I Found** (the growth landscape)
   - 4-6 bullet points of factual observations from the research
   - Separate facts from inferences. Label each: "[Data]" for things sourced from research, "[Signal]" for reasonable inferences
   - Include what's missing — "I couldn't find public data on X, which is itself a signal"

3. **How I Read This Role**
   - 2-3 sentences interpreting what problem this hire is meant to solve
   - Frame it as "My read is..." not "This role is about..."
   - End with: "I'd want to validate this interpretation in our conversation."

4. **Questions I'd Bring to the Table** (5-6 questions)
   - Specific, strategic questions that show depth of thinking
   - NOT generic ("What's your budget?") — but pointed ("Given your [X], I'd want to understand how you're currently thinking about [Y]")
   - Each question should implicitly reveal a hypothesis or insight

5. **Initial Hypotheses** (3-4 hypotheses)
   - Format: "**Hypothesis:** [statement]. **Why I think this:** [reasoning from data]. **How I'd test it:** [specific experiment or metric]. **What I'd need to know first:** [open question]."
   - These should be genuinely testable, not safe platitudes
   - At least one should be contrarian or non-obvious

6. **90-Day Skeleton** (not a plan — a framework)
   - Week 1-2: What I'd listen for and audit (specific to this company)
   - Month 1: Likely first experiments (framed as "pending validation of [hypothesis]")
   - Month 2-3: Directional bets I'd want to explore (explicitly conditional)
   - End with: "This is a starting framework. The real plan emerges from the data and our conversations."

Do NOT include a "Why Me" section — the entire document IS the demonstration of capability."""


async def generate_case_study(context: dict) -> str:
    """Call Claude API to generate the business case."""
    console.print("\n[bold blue]Generating business case with Claude...[/bold blue]")

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
