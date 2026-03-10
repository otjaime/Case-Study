"""Processes scraped and researched data into a structured context object for Claude."""

import re

import anthropic


def _detect_seniority(job_title: str, job_text: str) -> str:
    """Infer seniority level from job title and description."""
    combined = f"{job_title} {job_text}".lower()
    if any(w in combined for w in ["vp ", "vice president"]):
        return "vp"
    if any(w in combined for w in ["head of", "director"]):
        return "head"
    if "manager" in combined:
        return "manager"
    if "lead" in combined:
        return "lead"
    if "senior" in combined or "sr." in combined:
        return "senior"
    return "senior"  # default assumption for a case study tool


def _detect_business_model(homepage_text: str, pricing_text: str) -> str:
    """Infer business model from website content."""
    combined = f"{homepage_text} {pricing_text}".lower()
    scores = {
        "B2B SaaS": 0,
        "DTC ecommerce": 0,
        "marketplace": 0,
    }
    for term in ["saas", "enterprise", "teams", "per seat", "per user", "annual plan",
                  "api", "integration", "workflow", "dashboard", "b2b", "demo",
                  "book a demo", "request demo", "free trial", "pricing plans"]:
        if term in combined:
            scores["B2B SaaS"] += 1

    for term in ["shop", "cart", "add to cart", "free shipping", "checkout",
                  "buy now", "collection", "product", "returns", "dtc"]:
        if term in combined:
            scores["DTC ecommerce"] += 1

    for term in ["marketplace", "buyers", "sellers", "listing", "two-sided",
                  "supply", "demand", "vendors", "merchants"]:
        if term in combined:
            scores["marketplace"] += 1

    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "unknown"
    return best


def _detect_growth_stage(homepage_text: str, headcount: str, notable_claims: list[str],
                          funding_stage: str = "unknown") -> str:
    """Infer growth stage from available signals."""
    # If we have real funding data from Exa, use it directly
    stage_map = {
        "IPO/Public": "enterprise",
        "Series F": "enterprise",
        "Series E": "enterprise",
        "Series D": "growth",
        "Series C": "growth",
        "Series B": "scaling",
        "Series A": "scaling",
        "Seed": "early",
        "Pre-seed": "early",
    }
    if funding_stage in stage_map:
        return stage_map[funding_stage]

    combined = f"{homepage_text} {' '.join(notable_claims)}".lower()

    emp_count = 0
    if headcount != "unknown":
        nums = re.findall(r"[\d,]+", headcount)
        if nums:
            emp_count = int(nums[-1].replace(",", ""))

    if emp_count > 1000 or "fortune" in combined:
        return "enterprise"
    if emp_count > 200 or any(w in combined for w in ["series c", "series d", "ipo"]):
        return "growth"
    if emp_count > 50 or any(w in combined for w in ["series b", "series a", "scaling"]):
        return "scaling"
    if emp_count > 0:
        return "early"

    if any(w in combined for w in ["10,000", "50,000", "100,000", "million"]):
        return "growth"
    if any(w in combined for w in ["1,000", "5,000", "thousands"]):
        return "scaling"

    return "unknown"


def _extract_key_skills(job_text: str) -> list[str]:
    """Pull out key skills and requirements from job description text."""
    skills = []
    lines = job_text.split("\n")
    in_requirements = False

    for line in lines:
        lower = line.lower().strip()
        if any(h in lower for h in ["requirement", "qualif", "what you", "you have",
                                     "you bring", "skills", "experience"]):
            in_requirements = True
            continue
        if in_requirements:
            stripped = line.strip().lstrip("•-–—*▪◦● ")
            if stripped and len(stripped) > 10:
                skills.append(stripped)
            if len(skills) >= 12:
                break
            if not stripped and skills:
                in_requirements = False

    return skills


def _infer_challenges(context: dict, scraped_data: dict) -> list[str]:
    """Use Claude Haiku to infer specific challenges instead of keyword rules."""
    news_raw = scraped_data.get("news", {}).get("raw", "")[:500]
    funding_raw = scraped_data.get("funding", {}).get("raw", "")[:300]

    prompt = f"""Company: {context['company_name']}
Business model: {context['business_model']}
Growth stage: {context['growth_stage']}
Role being hired: {context['job_title']}
Key skills required: {', '.join(context['key_skills_required'][:6])}
Marketing channels: {', '.join(context.get('marketing_channels', [])) or 'none detected'}
Competitors: {', '.join(context.get('competitors', [])) or 'none identified'}
Recent news: {news_raw or 'none'}
Funding info: {funding_raw or 'none'}

List 4 specific growth challenges this company is likely facing right now.
Each challenge must be specific to THIS company, not generic.
Format: one challenge per line, no bullets, no numbers, no headers."""

    try:
        client = anthropic.Anthropic()
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()
        challenges = [line.strip() for line in text.split("\n") if line.strip()]
        return challenges[:5]
    except Exception:
        # Fallback to basic rule-based inference
        return _infer_challenges_fallback(context)


def _infer_challenges_fallback(context: dict) -> list[str]:
    """Fallback rule-based challenges if Claude call fails."""
    challenges = []
    model = context.get("business_model", "unknown")
    stage = context.get("growth_stage", "unknown")
    channels = context.get("marketing_channels", [])
    competitors = context.get("competitors", [])

    if model == "B2B SaaS":
        challenges.append("Balancing self-serve growth with enterprise sales motion")
        if not channels:
            challenges.append("No visible paid acquisition — may be over-indexed on organic/outbound")
        if stage in ("early", "scaling"):
            challenges.append("Proving repeatable acquisition channels before next funding milestone")
    elif model == "DTC ecommerce":
        challenges.append("Rising CAC on paid social and search channels")
        paid_channels = [c for c in channels if "Ads" in c]
        if len(paid_channels) >= 2:
            challenges.append("Multi-channel paid dependency — diversification and efficiency pressure")
    elif model == "marketplace":
        challenges.append("Chicken-and-egg supply/demand balance")

    if stage == "scaling":
        challenges.append("Transitioning from founder-led growth to a scalable growth function")
    if stage == "growth":
        challenges.append("Maintaining growth rate while managing increasing complexity")

    if competitors:
        challenges.append(f"Competitive pressure from {', '.join(competitors[:3])}")

    if not challenges:
        challenges.append("Growth strategy and channel prioritization")

    return challenges


def build_context(job_data: dict, research_data: dict,
                  company_profile: dict | None = None,
                  requirements_map: dict | None = None) -> dict:
    """Build the structured context object from job data, research, and decomposed JD.

    Args:
        job_data: Dict with keys url, html, full_text, page_title, job_title, company_name, domain.
        research_data: Dict from research.research_all() with keys:
            funding, news, marketing, competitors, website, industry_intel.
        company_profile: Structured company profile from decomposer (optional).
        requirements_map: Structured requirements map from decomposer (optional).
    """
    job_text = job_data.get("full_text", "")
    job_title = job_data.get("job_title", "")
    company_name = job_data.get("company_name", "")

    # Extract sub-dicts
    funding = research_data.get("funding", {})
    news = research_data.get("news", {})
    marketing = research_data.get("marketing", {})
    comp = research_data.get("competitors", {})
    website = research_data.get("website", {})

    homepage = website.get("pages", {}).get("homepage", "")
    pricing = website.get("pages", {}).get("pricing", "")
    all_website_text = website.get("all_text", "")
    notable_claims = website.get("notable_claims", [])

    # Marketing signals
    channels = marketing.get("channels_mentioned", [])
    strategy_signals = marketing.get("strategy_signals", [])

    # Competitors
    competitors = comp.get("competitors", [])

    # Funding
    funding_stage = funding.get("stage", "unknown")
    total_raised = funding.get("raised", "")
    investors = funding.get("investors", [])
    revenue_hints = funding.get("revenue_hints", [])

    # News
    articles = news.get("articles", [])
    news_summary = "\n".join(
        f"- {a['title']}" for a in articles[:6] if a.get("title")
    )

    context = {
        "company_name": company_name,
        "job_title": job_title,
        "seniority": (company_profile or {}).get("seniority") or _detect_seniority(job_title, job_text),
        "job_description": job_text[:6000],
        "key_skills_required": _extract_key_skills(job_text),
        # Company info — enriched (prefer decomposer over heuristics)
        "company_description": all_website_text[:8000] if all_website_text else homepage[:3000],
        "business_model": (company_profile or {}).get("business_model") or _detect_business_model(homepage or all_website_text, pricing),
        "growth_stage": _detect_growth_stage(
            homepage or all_website_text, "unknown", notable_claims, funding_stage
        ),
        "notable_claims": notable_claims,
        # Decomposer fields (company profile)
        "industry": (company_profile or {}).get("industry", "unknown"),
        "product_type": (company_profile or {}).get("product_type", "unknown"),
        "company_stage": (company_profile or {}).get("company_stage", "unknown"),
        "headcount_estimate": (company_profile or {}).get("headcount_estimate", "unknown"),
        "market": (company_profile or {}).get("market", "unknown"),
        "reports_to": (company_profile or {}).get("reports_to", "unknown"),
        "team_size": (company_profile or {}).get("team_size", "unknown"),
        "role_type": (company_profile or {}).get("role_type", "unknown"),
        # Funding & financials
        "funding_stage": funding_stage,
        "total_raised": total_raised,
        "investors": investors,
        "revenue_hints": revenue_hints,
        # Marketing & channels
        "marketing_channels": channels,
        "strategy_signals": strategy_signals,
        "marketing_raw": marketing.get("raw", "")[:3000],
        # Competitors
        "competitors": competitors,
        "competitive_raw": comp.get("positioning", "")[:2000],
        "competitors_detail": comp.get("competitors_detail", "")[:3000],
        # News
        "recent_news": news_summary,
        "news_raw": news.get("raw", "")[:3000],
        # Industry-specific intel
        "industry_intel": research_data.get("industry_intel", {}).get("raw", "")[:3000],
        # Website deep data
        "product_details": website.get("pages", {}).get("about", "")[:2000],
        "careers_page": website.get("pages", {}).get("careers", "")[:1500],
        "pricing_details": pricing[:2000],
        # Requirements map from decomposer
        "requirements_map": requirements_map or {},
        # Challenges and coverage (filled below)
        "inferred_challenges": [],
        "coverage_gaps": [],
    }

    context["inferred_challenges"] = _infer_challenges(context, research_data)

    # Validate coverage if requirements_map is available
    if requirements_map:
        context = validate_coverage(context, requirements_map)

    return context


def validate_coverage(context: dict, requirements_map: dict) -> dict:
    """Ensure the context has enough signal to cover every tool, task, and KPI.

    For each uncovered item, add it to coverage_gaps so the generator
    can explicitly address it.
    """
    coverage_gaps = []
    context_str = str(context).lower()

    # Check tools
    for tool in requirements_map.get("tools_required", []):
        if tool.lower() not in context_str:
            coverage_gaps.append(f"tool:{tool}")

    # Check core tasks
    for task in requirements_map.get("core_tasks", []):
        if task.lower() not in context_str:
            coverage_gaps.append(f"task:{task}")

    # Emerging skills — always include explicitly since these are high-signal
    for skill in requirements_map.get("emerging_skills", []):
        coverage_gaps.append(f"emerging:{skill}")

    # Check primary KPIs
    for kpi in requirements_map.get("primary_kpis", []):
        if kpi.lower() not in context_str:
            coverage_gaps.append(f"kpi:{kpi}")

    context["coverage_gaps"] = coverage_gaps
    context["requirements_map"] = requirements_map
    return context
