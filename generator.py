"""Claude API calls and prompt logic for generating the case study."""

import anthropic
from rich.console import Console

console = Console()

SYSTEM_PROMPT = """You are a senior growth strategist preparing a targeted case study to accompany a job application. \
Your goal is to demonstrate deep understanding of the company's specific growth challenges and show \
how you would approach them in the first 90 days in the role.

The output must feel like it was written by someone who has genuinely thought about this company — \
not a generic framework. Use the data provided to make specific, grounded observations.

Be direct and opinionated. Avoid filler. Every sentence should add signal."""


def _build_user_prompt(context: dict) -> str:
    """Build the user prompt from the context object."""
    ad_themes = ", ".join(context["ad_themes"]) if context["ad_themes"] else "none observed"
    notable = "\n".join(f"- {c}" for c in context["notable_claims"]) if context["notable_claims"] else "none found"
    skills = "\n".join(f"- {s}" for s in context["key_skills_required"]) if context["key_skills_required"] else "not extracted"
    challenges = "\n".join(f"- {c}" for c in context["inferred_challenges"]) if context["inferred_challenges"] else "none inferred"

    return f"""Here is the context for the application:

**Role:** {context['job_title']} at {context['company_name']}
**Seniority:** {context['seniority']}

**Job Description:**
{context['job_description']}

**Key Skills Required:**
{skills}

**Company Context:**
{context['company_description']}
Business model: {context['business_model']}
Growth stage: {context['growth_stage']}
Paid ads activity: {context['paid_ads_activity']}
Ad themes observed: {ad_themes}
Estimated web traffic: {context['estimated_traffic']}
Notable public claims:
{notable}

**Pre-analysis — likely challenges:**
{challenges}

---

Generate a case study document with the following sections:

1. **Company Snapshot** (3-4 sentences)
   - What they do, who they serve, where they appear to be in their growth trajectory
   - One specific observation that shows you've actually looked at their business

2. **Growth Diagnosis** (4-6 bullet points)
   - The most likely growth constraints this company is facing right now given stage, model, and role
   - Be specific — not "they need more leads" but "their paid CAC is likely under pressure because X"

3. **What the Role is Really About**
   - Your interpretation of what problem this hire is meant to solve, reading between the lines of the JD
   - What success looks like in 12 months if the hire goes well

4. **My 90-Day Plan**
   - Week 1-2: Listen and audit (specific things you'd audit)
   - Month 1: Quick wins (specific hypotheses you'd test first and why)
   - Month 2-3: Structural bets (bigger initiatives you'd build toward)

5. **One Specific Hypothesis**
   - A single, concrete growth hypothesis you'd want to test in the first 60 days
   - Format: "I believe that [action] will [result] because [reason]. I'd measure it by [metric]."

6. **Why Me** (optional, 2-3 sentences)
   - [CANDIDATE TO FILL THIS IN]"""


async def generate_case_study(context: dict) -> str:
    """Call Claude API to generate the case study."""
    console.print("\n[bold blue]Generating case study with Claude...[/bold blue]")

    client = anthropic.AsyncAnthropic()

    user_prompt = _build_user_prompt(context)

    message = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    text = message.content[0].text
    console.print("[green]Case study generated successfully.[/green]")
    return text
