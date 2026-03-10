"""Deep company research using Exa (semantic search) and Firecrawl (website crawl)."""

import asyncio
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from rich.console import Console

console = Console()

# ---------------------------------------------------------------------------
# Domain-level cache (TTL 7 days)
# ---------------------------------------------------------------------------

CACHE_DIR = Path("/tmp/case_study_cache")
CACHE_TTL = 7 * 24 * 3600  # 7 days


def _cache_path(domain: str) -> Path:
    CACHE_DIR.mkdir(exist_ok=True)
    return CACHE_DIR / f"{domain.replace('.', '_').replace('/', '_')}.json"


def _load_cache(domain: str) -> dict | None:
    path = _cache_path(domain)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if time.time() - data["cached_at"] > CACHE_TTL:
            return None
        console.print(f"  [dim]Cache hit for {domain}[/dim]")
        return data["payload"]
    except Exception:
        return None


def _save_cache(domain: str, payload: dict):
    try:
        path = _cache_path(domain)
        path.write_text(json.dumps({"cached_at": time.time(), "payload": payload}))
    except Exception:
        pass  # cache failures are non-critical

# ---------------------------------------------------------------------------
# Exa helpers
# ---------------------------------------------------------------------------

def _get_exa_client():
    """Return an Exa client if API key is available, else None."""
    api_key = os.environ.get("EXA_API_KEY")
    if not api_key:
        return None
    from exa_py import Exa
    return Exa(api_key)


def _six_months_ago() -> str:
    return (datetime.utcnow() - timedelta(days=180)).strftime("%Y-%m-%dT%H:%M:%SZ")


async def research_funding(company_name: str) -> dict:
    """Search for funding rounds, valuation, and revenue signals."""
    exa = _get_exa_client()
    if not exa:
        return {"stage": "unknown", "raised": "", "investors": [], "revenue_hints": [], "raw": ""}

    console.print(f"  [dim]Exa: researching {company_name} funding...[/dim]")
    try:
        results = await asyncio.to_thread(
            exa.search_and_contents,
            f'"{company_name}" funding round valuation revenue series',
            num_results=5,
            text={"maxCharacters": 2000},
            category="news",
        )
        texts = [r.text for r in results.results if r.text]
        combined = "\n---\n".join(texts)
        return {
            "stage": _extract_funding_stage(combined),
            "raised": _extract_raised(combined),
            "investors": _extract_investors(combined),
            "revenue_hints": _extract_revenue_hints(combined),
            "raw": combined[:4000],
        }
    except Exception as e:
        console.print(f"  [yellow]Exa funding search failed: {e}[/yellow]")
        return {"stage": "unknown", "raised": "", "investors": [], "revenue_hints": [], "raw": ""}


async def research_news(company_name: str) -> dict:
    """Search for recent news, launches, partnerships, hires."""
    exa = _get_exa_client()
    if not exa:
        return {"articles": [], "raw": ""}

    console.print(f"  [dim]Exa: researching {company_name} news...[/dim]")
    try:
        results = await asyncio.to_thread(
            exa.search_and_contents,
            f'"{company_name}" launch OR partnership OR expansion OR hire OR acquisition',
            num_results=8,
            text={"maxCharacters": 1500},
            start_published_date=_six_months_ago(),
            category="news",
        )
        articles = []
        for r in results.results:
            articles.append({
                "title": r.title or "",
                "url": r.url or "",
                "text": (r.text or "")[:1500],
            })
        raw = "\n---\n".join(a["text"] for a in articles if a["text"])
        return {"articles": articles, "raw": raw[:6000]}
    except Exception as e:
        console.print(f"  [yellow]Exa news search failed: {e}[/yellow]")
        return {"articles": [], "raw": ""}


async def research_marketing_presence(company_name: str, domain: str) -> dict:
    """Search for marketing channels, ad strategies, growth tactics."""
    exa = _get_exa_client()
    if not exa:
        return {"channels_mentioned": [], "strategy_signals": [], "raw": ""}

    console.print(f"  [dim]Exa: researching {company_name} marketing...[/dim]")
    try:
        results = await asyncio.to_thread(
            exa.search_and_contents,
            f'"{company_name}" OR "{domain}" marketing strategy ads channels growth SEO paid social',
            num_results=5,
            text={"maxCharacters": 2000},
        )
        texts = [r.text for r in results.results if r.text]
        combined = "\n---\n".join(texts)
        return {
            "channels_mentioned": _extract_channels(combined),
            "strategy_signals": _extract_strategy_signals(combined),
            "raw": combined[:4000],
        }
    except Exception as e:
        console.print(f"  [yellow]Exa marketing search failed: {e}[/yellow]")
        return {"channels_mentioned": [], "strategy_signals": [], "raw": ""}


async def research_competitors(company_name: str, domain: str) -> dict:
    """Search for competitors and market positioning."""
    exa = _get_exa_client()
    if not exa:
        return {"competitors": [], "positioning": "", "raw": "", "competitors_detail": ""}

    console.print(f"  [dim]Exa: researching {company_name} competitors...[/dim]")
    try:
        results = await asyncio.to_thread(
            exa.search_and_contents,
            f'"{company_name}" vs competitors alternative comparison',
            num_results=5,
            text={"maxCharacters": 2000},
        )
        texts = [r.text for r in results.results if r.text]
        combined = "\n---\n".join(texts)

        competitors = _extract_competitors(combined, company_name)

        # Secondary deep-dive on top 3 competitors
        detail = await _research_competitors_detail(exa, competitors[:3])

        return {
            "competitors": competitors,
            "positioning": combined[:2000],
            "raw": combined[:4000],
            "competitors_detail": detail,
        }
    except Exception as e:
        console.print(f"  [yellow]Exa competitor search failed: {e}[/yellow]")
        return {"competitors": [], "positioning": "", "raw": "", "competitors_detail": ""}


async def _research_competitors_detail(exa, competitors: list[str]) -> str:
    """Run secondary Exa searches for each competitor to get deeper intel."""
    if not competitors or not exa:
        return ""

    current_year = datetime.utcnow().year

    async def _research_one(name: str) -> str:
        try:
            results = await asyncio.to_thread(
                exa.search_and_contents,
                f'"{name}" marketing strategy growth {current_year}',
                num_results=3,
                text={"maxCharacters": 1500},
            )
            texts = [r.text for r in results.results if r.text]
            combined = "\n".join(texts)

            channel = _infer_primary_channel(combined)
            launches = _extract_recent_launches(combined, name)

            lines = [f"**{name}**"]
            if channel:
                lines.append(f"  Primary channel: {channel}")
            if launches:
                lines.append(f"  Recent moves: {launches}")
            lines.append(f"  Intel: {combined[:500]}")
            return "\n".join(lines)
        except Exception:
            return f"**{name}**: no additional data found"

    console.print(f"  [dim]Exa: deep-diving {len(competitors)} competitors...[/dim]")
    tasks = [_research_one(c) for c in competitors]
    results = await asyncio.gather(*tasks)
    return "\n\n".join(results)


def _infer_primary_channel(text: str) -> str:
    """Quick heuristic to identify primary acquisition channel from text."""
    t = text.lower()
    scores = {
        "Paid (Google/Meta)": 0,
        "Organic/SEO": 0,
        "Community/Word-of-mouth": 0,
        "Enterprise Sales": 0,
    }
    for term in ["google ads", "facebook ads", "meta ads", "paid", "ppc", "ad spend", "roas"]:
        if term in t:
            scores["Paid (Google/Meta)"] += 1
    for term in ["seo", "organic", "blog", "content marketing", "search ranking"]:
        if term in t:
            scores["Organic/SEO"] += 1
    for term in ["community", "word of mouth", "viral", "referral", "ambassador"]:
        if term in t:
            scores["Community/Word-of-mouth"] += 1
    for term in ["enterprise", "sales team", "outbound", "account executive", "demo"]:
        if term in t:
            scores["Enterprise Sales"] += 1

    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else ""


def _extract_recent_launches(text: str, company_name: str) -> str:
    """Extract recent product launches or positioning changes."""
    patterns = [
        rf"{re.escape(company_name)}\s+(?:launched|announced|released|introduced|unveiled)\s+(.{{30,120}}?)(?:\.|$)",
        r"(?:new feature|new product|rebrand|pivot|expansion)\s+(.{20,100}?)(?:\.|$)",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(0).strip()[:150]
    return ""


# ---------------------------------------------------------------------------
# Firecrawl helpers
# ---------------------------------------------------------------------------

def _get_firecrawl_client():
    """Return a Firecrawl client if API key is available, else None."""
    api_key = os.environ.get("FIRECRAWL_API_KEY")
    if not api_key:
        return None
    from firecrawl import Firecrawl
    return Firecrawl(api_key=api_key)


# Target paths to scrape on the company website
_TARGET_PATHS = [
    "/",
    "/about",
    "/pricing",
    "/customers",
    "/case-studies",
    "/integrations",
    "/careers",
    "/blog",
]


async def crawl_website(domain: str) -> dict:
    """Deep crawl of the company website using Firecrawl."""
    fc = _get_firecrawl_client()
    if not fc:
        # Fallback: use basic scraper
        from scraper import scrape_company_website
        basic = await scrape_company_website(domain)
        return {
            "pages": {"homepage": basic.get("homepage_text", "")},
            "all_text": basic.get("homepage_text", ""),
            "notable_claims": basic.get("notable_claims", []),
        }

    console.print(f"  [dim]Firecrawl: mapping {domain}...[/dim]")
    base_url = f"https://{domain}"

    try:
        # First, map the site to discover URLs
        site_map = await asyncio.to_thread(fc.map, url=base_url, limit=50)

        # site_map may be a list of URLs or an object with .links
        if hasattr(site_map, "links"):
            discovered_urls = site_map.links or []
        elif isinstance(site_map, list):
            discovered_urls = site_map
        else:
            discovered_urls = []

        # Match discovered URLs against target paths
        urls_to_scrape = set()
        for target in _TARGET_PATHS:
            full = base_url + target
            # Check both exact and with trailing slash
            for u in discovered_urls:
                if isinstance(u, str) and (u.rstrip("/") == full.rstrip("/") or target == "/"):
                    urls_to_scrape.add(u)
                    break
            else:
                # If not found in map, try anyway
                urls_to_scrape.add(full)

        # Limit to reasonable number
        urls_to_scrape = list(urls_to_scrape)[:10]

        console.print(f"  [dim]Firecrawl: scraping {len(urls_to_scrape)} pages...[/dim]")

        # Scrape each page
        pages = {}
        all_texts = []

        async def _scrape_one(url: str):
            try:
                result = await asyncio.to_thread(fc.scrape, url, formats=["markdown"])
                md = ""
                if hasattr(result, "markdown"):
                    md = result.markdown or ""
                elif isinstance(result, dict):
                    md = result.get("markdown", "")
                return url, md[:5000]
            except Exception:
                return url, ""

        tasks = [_scrape_one(u) for u in urls_to_scrape]
        results = await asyncio.gather(*tasks)

        for url, md in results:
            # Determine page name from path
            path = url.replace(base_url, "").strip("/") or "homepage"
            pages[path] = md
            if md:
                all_texts.append(md)

        all_text = "\n\n---\n\n".join(all_texts)

        return {
            "pages": pages,
            "all_text": all_text[:15000],
            "notable_claims": _extract_claims_from_text(all_text),
        }

    except Exception as e:
        console.print(f"  [yellow]Firecrawl crawl failed: {e}[/yellow]")
        # Fallback
        from scraper import scrape_company_website
        basic = await scrape_company_website(domain)
        return {
            "pages": {"homepage": basic.get("homepage_text", "")},
            "all_text": basic.get("homepage_text", ""),
            "notable_claims": basic.get("notable_claims", []),
        }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def research_all(company_name: str, domain: str, company_profile: dict | None = None) -> dict:
    """Run all research functions in parallel and consolidate results.

    Uses a domain-level file cache (TTL 7 days) to avoid redundant API calls.
    If company_profile is provided, research queries are guided by industry/stage.
    """
    cached = _load_cache(domain)
    if cached:
        return cached

    console.print("\n[bold]Researching company (Exa + Firecrawl)...[/bold]\n")

    industry = (company_profile or {}).get("industry", "")
    stage = (company_profile or {}).get("company_stage", "")

    # Build industry-specific news query
    news_extra = ""
    if industry:
        news_extra = f" {industry}"

    funding, news, marketing, competitors, website = await asyncio.gather(
        research_funding(company_name),
        research_news(company_name + news_extra),
        research_marketing_presence(company_name, domain),
        research_competitors(company_name, domain),
        crawl_website(domain),
    )

    # Additional industry/stage-specific research
    industry_data = await _research_industry_specific(company_name, industry, stage)

    result = {
        "funding": funding,
        "news": news,
        "marketing": marketing,
        "competitors": competitors,
        "website": website,
        "industry_intel": industry_data,
    }

    _save_cache(domain, result)
    return result


async def _research_industry_specific(company_name: str, industry: str, stage: str) -> dict:
    """Run additional searches tailored to the company's industry and stage."""
    exa = _get_exa_client()
    if not exa or not industry or industry == "unknown":
        return {"raw": ""}

    queries = []
    ind = industry.lower()

    # Industry-specific queries
    if "fintech" in ind or "finance" in ind:
        queries.append(f'"{company_name}" regulatory compliance fintech')
    if "retail" in ind or "ecommerce" in ind or "cpg" in ind:
        queries.append(f'"{company_name}" omnichannel digital transformation retail')
    if "energy" in ind or "oil" in ind or "fuel" in ind:
        queries.append(f'"{company_name}" digital ecosystem app loyalty')
    if "edtech" in ind or "education" in ind:
        queries.append(f'"{company_name}" student engagement platform growth')
    if "saas" in ind or "software" in ind:
        queries.append(f'"{company_name}" product-led growth enterprise expansion')

    # Stage-specific queries
    if stage == "corporate" or stage == "enterprise":
        queries.append(f'"{company_name}" annual report strategy digital transformation')
    elif stage == "scaleup":
        queries.append(f'"{company_name}" growth metrics scaling challenges')
    elif stage == "startup":
        queries.append(f'"{company_name}" product market fit early traction')

    if not queries:
        return {"raw": ""}

    console.print(f"  [dim]Exa: industry-specific research ({industry})...[/dim]")

    all_texts = []
    for query in queries[:2]:  # Max 2 extra queries to control cost
        try:
            results = await asyncio.to_thread(
                exa.search_and_contents,
                query,
                num_results=3,
                text={"maxCharacters": 1500},
            )
            texts = [r.text for r in results.results if r.text]
            all_texts.extend(texts)
        except Exception:
            continue

    return {"raw": "\n---\n".join(all_texts)[:4000]}


# ---------------------------------------------------------------------------
# Extraction helpers — simple regex/keyword extraction from raw text
# ---------------------------------------------------------------------------

def _extract_funding_stage(text: str) -> str:
    t = text.lower()
    # Order from most recent to earliest; use contextual patterns to avoid false matches
    patterns = [
        (r"(?:went public|ipo filing|public offering|listed on|nasdaq|nyse)", "IPO/Public"),
        (r"series\s+f\b", "Series F"),
        (r"series\s+e\b", "Series E"),
        (r"series\s+d\b", "Series D"),
        (r"series\s+c\b", "Series C"),
        (r"series\s+b\b", "Series B"),
        (r"series\s+a\b", "Series A"),
        (r"(?:seed\s+(?:round|funding|stage|investment)|pre-seed)", "Seed"),
    ]
    for pattern, label in patterns:
        if re.search(pattern, t):
            return label
    return "unknown"


def _extract_raised(text: str) -> str:
    patterns = [
        r"\$[\d,.]+\s*(?:billion|B)\b",
        r"\$[\d,.]+\s*(?:million|M)\b",
        r"raised\s+\$[\d,.]+[BM]?",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(0).strip()
    return ""


def _extract_investors(text: str) -> list[str]:
    investor_keywords = [
        "sequoia", "a16z", "andreessen", "accel", "benchmark", "greylock",
        "lightspeed", "index ventures", "tiger global", "softbank", "ycombinator",
        "y combinator", "kleiner perkins", "ggv", "general catalyst", "insight partners",
        "coatue", "ribbit", "founders fund", "bessemer", "ivp",
    ]
    found = []
    t = text.lower()
    for inv in investor_keywords:
        if inv in t:
            found.append(inv.title())
    return found[:5]


def _extract_revenue_hints(text: str) -> list[str]:
    patterns = [
        r"\$[\d,.]+[BMK]?\s*(?:ARR|MRR|revenue|run rate)",
        r"(?:ARR|MRR|revenue)\s*(?:of|reached|exceeded|surpassed)\s*\$[\d,.]+[BMK]?",
        r"[\d,.]+\s*(?:million|billion)\s*(?:in\s+)?(?:ARR|MRR|revenue)",
        r"(?:profitable|profitability|break.?even|cash.?flow positive)",
    ]
    hints = []
    for p in patterns:
        for m in re.finditer(p, text, re.IGNORECASE):
            hints.append(m.group(0).strip())
    return hints[:5]


def _extract_channels(text: str) -> list[str]:
    channel_map = {
        "google ads": "Google Ads",
        "facebook ads": "Facebook Ads",
        "meta ads": "Meta Ads",
        "instagram": "Instagram",
        "linkedin ads": "LinkedIn Ads",
        "tiktok": "TikTok",
        "youtube": "YouTube",
        "seo": "SEO",
        "content marketing": "Content Marketing",
        "email marketing": "Email Marketing",
        "affiliate": "Affiliate",
        "podcast": "Podcast",
        "webinar": "Webinars",
        "referral": "Referral Program",
        "influencer": "Influencer Marketing",
        "product.led": "Product-Led Growth",
        "freemium": "Freemium",
        "outbound": "Outbound Sales",
    }
    found = []
    t = text.lower()
    for keyword, label in channel_map.items():
        if re.search(keyword, t):
            found.append(label)
    return found


def _extract_strategy_signals(text: str) -> list[str]:
    signals = []
    t = text.lower()
    signal_map = {
        "product-led growth": "Product-led growth approach",
        "sales-led": "Sales-led growth model",
        "community": "Community-driven growth",
        "virality": "Viral growth loops",
        "word of mouth": "Word-of-mouth driven",
        "enterprise sales": "Enterprise sales motion",
        "self-serve": "Self-serve funnel",
        "plg": "Product-led growth",
        "bottoms.up": "Bottom-up adoption",
        "land.and.expand": "Land-and-expand strategy",
    }
    for keyword, label in signal_map.items():
        if re.search(keyword, t):
            signals.append(label)
    return signals


def _extract_competitors(text: str, company_name: str) -> list[str]:
    patterns = [
        rf"{re.escape(company_name)}\s+(?:vs\.?|versus|compared to|or)\s+(\w[\w\s]*?)(?:\.|,|\n|$)",
        r"competitors?\s+(?:include|like|such as)\s+([\w\s,]+?)(?:\.|$)",
        r"alternatives?\s+(?:to|include|like)\s+([\w\s,]+?)(?:\.|$)",
    ]
    competitors = set()
    for p in patterns:
        for m in re.finditer(p, text, re.IGNORECASE):
            names = m.group(1).split(",")
            for n in names:
                n = n.strip().strip(".")
                if n and len(n) > 2 and n.lower() != company_name.lower():
                    competitors.add(n)
    return list(competitors)[:8]


def _extract_claims_from_text(text: str) -> list[str]:
    patterns = [
        r"[\d,.]+[+]?\s*(?:customers?|companies|users?|teams?|organizations?)",
        r"(?:Fortune|Inc\.?|Forbes)\s*\d+",
        r"\$[\d,.]+[BMK]?\+?\s*(?:in\s+)?(?:revenue|ARR|MRR|saved|processed|managed)",
        r"(?:trusted by|used by|loved by|chosen by)\s+[\d,.]+\+?\s*\w+",
        r"\d+[+]?\s*(?:countries|integrations|languages)",
        r"\d+[x%]\s+(?:faster|cheaper|more|growth|improvement|increase|reduction)",
    ]
    claims = set()
    for p in patterns:
        for m in re.finditer(p, text, re.IGNORECASE):
            claim = m.group(0).strip()
            if len(claim) > 8:
                claims.add(claim)
    return list(claims)[:15]
