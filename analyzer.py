"""Processes scraped data into a structured context object for Claude."""

import re


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
    # B2B SaaS signals
    for term in ["saas", "enterprise", "teams", "per seat", "per user", "annual plan",
                  "api", "integration", "workflow", "dashboard", "b2b", "demo",
                  "book a demo", "request demo", "free trial", "pricing plans"]:
        if term in combined:
            scores["B2B SaaS"] += 1

    # DTC ecommerce signals
    for term in ["shop", "cart", "add to cart", "free shipping", "checkout",
                  "buy now", "collection", "product", "returns", "dtc"]:
        if term in combined:
            scores["DTC ecommerce"] += 1

    # Marketplace signals
    for term in ["marketplace", "buyers", "sellers", "listing", "two-sided",
                  "supply", "demand", "vendors", "merchants"]:
        if term in combined:
            scores["marketplace"] += 1

    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "unknown"
    return best


def _detect_growth_stage(homepage_text: str, headcount: str, notable_claims: list[str]) -> str:
    """Infer growth stage from available signals."""
    combined = f"{homepage_text} {' '.join(notable_claims)}".lower()

    # Parse headcount
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

    # Fallback heuristics from text
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
            # End section on empty line after collecting some skills
            if not stripped and skills:
                in_requirements = False

    return skills


def _infer_challenges(context: dict) -> list[str]:
    """Best-guess growth challenges before sending to Claude."""
    challenges = []
    model = context.get("business_model", "unknown")
    stage = context.get("growth_stage", "unknown")
    ads = context.get("paid_ads_activity", "none")

    if model == "B2B SaaS":
        challenges.append("Balancing self-serve growth with enterprise sales motion")
        if ads == "none":
            challenges.append("No visible paid acquisition — may be over-indexed on organic/outbound")
        if stage in ("early", "scaling"):
            challenges.append("Proving repeatable acquisition channels before next funding milestone")
    elif model == "DTC ecommerce":
        challenges.append("Rising CAC on paid social and search channels")
        if ads == "heavy":
            challenges.append("Scale pressure on ad spend ROI — likely hitting diminishing returns")
    elif model == "marketplace":
        challenges.append("Chicken-and-egg supply/demand balance")

    if stage == "scaling":
        challenges.append("Transitioning from founder-led growth to a scalable growth function")
    if stage == "growth":
        challenges.append("Maintaining growth rate while managing increasing complexity")

    if ads == "heavy":
        challenges.append("Heavy ad spend suggests dependency on paid channels — diversification needed")

    if not challenges:
        challenges.append("Growth strategy and channel prioritization")

    return challenges


def build_context(
    job_data: dict,
    company_data: dict,
    ads_data: dict,
    traffic_estimate: str,
    linkedin_data: dict,
) -> dict:
    """Build the structured context object from all scraped data."""

    job_text = job_data.get("full_text", "")
    job_title = job_data.get("job_title", "")
    company_name = job_data.get("company_name", "")
    homepage = company_data.get("homepage_text", "")
    pricing = company_data.get("pricing_text", "")
    headcount = linkedin_data.get("headcount", "unknown")
    notable_claims = company_data.get("notable_claims", [])

    context = {
        "company_name": company_name,
        "job_title": job_title,
        "seniority": _detect_seniority(job_title, job_text),
        "job_description": job_text[:6000],
        "key_skills_required": _extract_key_skills(job_text),
        "company_description": homepage[:3000],
        "business_model": _detect_business_model(homepage, pricing),
        "growth_stage": _detect_growth_stage(homepage, headcount, notable_claims),
        "paid_ads_activity": ads_data.get("volume", "none"),
        "ad_themes": ads_data.get("themes", []),
        "estimated_traffic": traffic_estimate,
        "notable_claims": notable_claims,
        "inferred_challenges": [],
    }

    context["inferred_challenges"] = _infer_challenges(context)

    return context
