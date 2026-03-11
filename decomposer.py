"""JD Decomposer — extracts structured company_profile and requirements_map from raw job description text.

Runs BEFORE research.py and analyzer.py. Uses Claude Haiku for fast, cheap extraction.
"""

import json

import anthropic
from rich.console import Console

console = Console()

DECOMPOSE_SYSTEM = """You are a talent analyst. Extract structured information from job descriptions.
Respond ONLY with valid JSON. No preamble, no markdown, no explanation.
Be exhaustive on tools and tasks — missing one means the case won't test it."""

DECOMPOSE_USER = """Extract company_profile and requirements_map from this job description.

Business model classification guide:
- "B2B SaaS": software sold to businesses, subscription, demos, enterprise sales
- "institutional B2B": hedge funds, asset managers, family offices, portfolio analytics
- "fintech": payments, neobanks, lending, wallets, financial services for consumers
- "DTC ecommerce": direct-to-consumer physical products, subscription boxes, CPG online
- "CPG": consumer packaged goods with retail distribution (Target, Walmart, Costco)
- "consumer app": mobile/web apps for individual consumers (fitness, food delivery, etc.)
- "marketplace": two-sided platforms connecting buyers and sellers
- "B2C": direct-to-consumer digital services (not physical goods)

JD:
{jd_text}

Return exactly this JSON structure (fill every field, use "unknown" if not inferable):
{{
  "company_profile": {{
    "company_name": "string",
    "industry": "string (e.g. fintech, retail, SaaS, marketplace, CPG, energy, edtech, institutional finance)",
    "business_model": "B2B SaaS | B2C | B2B2C | marketplace | DTC ecommerce | institutional B2B | fintech | consumer app | CPG",
    "product_type": "app | web platform | physical + digital | SaaS",
    "company_stage": "startup | scaleup | corporate | enterprise",
    "headcount_estimate": "1-50 | 51-200 | 201-1000 | 1000+",
    "market": "string (e.g. Chile, LatAm, Global, US, Europe)",
    "role_title": "string",
    "seniority": "junior | senior | lead | manager | head | director | vp",
    "reports_to": "string (e.g. CMO, CEO, VP Growth, unknown)",
    "team_size": "string (number of direct reports if mentioned, else unknown)",
    "role_type": "individual contributor | player-coach | pure manager"
  }},
  "requirements_map": {{
    "tools_required": ["list of tools/platforms explicitly mentioned"],
    "certifications": ["list of certifications if any"],
    "core_tasks": ["list of functional tasks the person will do"],
    "primary_kpis": ["list of primary success metrics"],
    "secondary_kpis": ["list of secondary metrics"],
    "emerging_skills": ["new/specific requirements that signal what they care about"],
    "methodologies": ["agile, OKRs, data-driven, etc."],
    "leadership_signals": ["manage team, develop people, etc."]
  }}
}}"""


async def decompose_jd(jd_text: str, company_name: str = "") -> tuple[dict, dict]:
    """Extract company_profile and requirements_map from raw JD text.

    Returns (company_profile, requirements_map).
    """
    console.print("\n[bold]Decomposing job description...[/bold]")

    prompt = DECOMPOSE_USER.format(jd_text=jd_text[:8000])

    try:
        client = anthropic.AsyncAnthropic()
        message = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            system=DECOMPOSE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()

        # Parse JSON — find the outermost braces
        start = text.find("{")
        end = text.rfind("}") + 1
        if start < 0 or end <= start:
            console.print("  [yellow]Decomposer: JSON parse failed, retrying...[/yellow]")
            return await _retry_decompose(client, jd_text, company_name)

        parsed = json.loads(text[start:end])
        profile = parsed.get("company_profile", {})
        req_map = parsed.get("requirements_map", {})

        # Override company_name if provided by user (more reliable than JD extraction)
        if company_name:
            profile["company_name"] = company_name

        # Ensure required keys exist with defaults
        profile = _fill_profile_defaults(profile)
        req_map = _fill_reqmap_defaults(req_map)

        console.print(f"  [dim]Decomposed: {profile['role_title']} at {profile['company_name']}[/dim]")
        console.print(f"  [dim]  Industry: {profile['industry']}, Stage: {profile['company_stage']}, Model: {profile['business_model']}[/dim]")
        console.print(f"  [dim]  Tools: {len(req_map['tools_required'])}, Tasks: {len(req_map['core_tasks'])}, KPIs: {len(req_map['primary_kpis'])}[/dim]")

        return profile, req_map

    except json.JSONDecodeError:
        console.print("  [yellow]Decomposer: JSON parse failed, retrying...[/yellow]")
        client = anthropic.AsyncAnthropic()
        return await _retry_decompose(client, jd_text, company_name)
    except Exception as e:
        console.print(f"  [yellow]Decomposer failed: {e}[/yellow]")
        return _fallback_decompose(jd_text, company_name)


async def _retry_decompose(client: anthropic.AsyncAnthropic, jd_text: str, company_name: str) -> tuple[dict, dict]:
    """Retry with a stricter prompt."""
    try:
        message = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            system="You are a JSON extraction machine. Output ONLY valid JSON. No text before or after.",
            messages=[{"role": "user", "content": DECOMPOSE_USER.format(jd_text=jd_text[:6000])}],
        )
        text = message.content[0].text.strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        parsed = json.loads(text[start:end])
        profile = _fill_profile_defaults(parsed.get("company_profile", {}))
        req_map = _fill_reqmap_defaults(parsed.get("requirements_map", {}))
        if company_name:
            profile["company_name"] = company_name
        return profile, req_map
    except Exception:
        return _fallback_decompose(jd_text, company_name)


def _fallback_decompose(jd_text: str, company_name: str) -> tuple[dict, dict]:
    """Minimal fallback if Claude calls fail entirely."""
    console.print("  [yellow]Using fallback decomposition[/yellow]")
    profile = _fill_profile_defaults({"company_name": company_name or "Unknown"})
    req_map = _fill_reqmap_defaults({})
    return profile, req_map


def _fill_profile_defaults(profile: dict) -> dict:
    defaults = {
        "company_name": "Unknown",
        "industry": "unknown",
        "business_model": "unknown",
        "product_type": "unknown",
        "company_stage": "unknown",
        "headcount_estimate": "unknown",
        "market": "unknown",
        "role_title": "Growth Role",
        "seniority": "senior",
        "reports_to": "unknown",
        "team_size": "unknown",
        "role_type": "unknown",
    }
    for k, v in defaults.items():
        if k not in profile or not profile[k]:
            profile[k] = v
    return profile


def _fill_reqmap_defaults(req_map: dict) -> dict:
    defaults = {
        "tools_required": [],
        "certifications": [],
        "core_tasks": [],
        "primary_kpis": [],
        "secondary_kpis": [],
        "emerging_skills": [],
        "methodologies": [],
        "leadership_signals": [],
    }
    for k, v in defaults.items():
        if k not in req_map or not isinstance(req_map[k], list):
            req_map[k] = v
    return req_map
