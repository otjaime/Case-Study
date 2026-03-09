"""Scraping logic for job postings, company websites, and public data sources."""

import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from rich.console import Console

console = Console()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

TIMEOUT = 20.0


async def _fetch_html(url: str) -> str | None:
    """Fetch HTML with httpx, falling back to Playwright for JS-rendered pages."""
    try:
        async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=TIMEOUT) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
    except httpx.HTTPError:
        pass

    # Fallback: Playwright for JS-heavy pages
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="networkidle", timeout=30000)
            html = await page.content()
            await browser.close()
            return html
    except Exception as exc:
        console.print(f"[yellow]Warning:[/yellow] Could not fetch {url}: {exc}")
        return None


def _extract_text(html: str) -> str:
    """Strip tags and return readable text."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def _infer_domain(html: str, url: str) -> str | None:
    """Try to infer the company domain from the job posting page."""
    soup = BeautifulSoup(html, "html.parser")

    # Look for links that point to the company site
    for a in soup.find_all("a", href=True):
        href = a["href"]
        parsed = urlparse(href)
        if parsed.netloc and parsed.netloc not in urlparse(url).netloc:
            # Skip common non-company domains
            skip = ["linkedin.com", "twitter.com", "facebook.com", "github.com",
                     "glassdoor.com", "indeed.com", "lever.co", "greenhouse.io",
                     "workday.com", "google.com", "youtube.com", "instagram.com"]
            if not any(s in parsed.netloc for s in skip):
                return parsed.netloc

    # Fallback: try to pull from og:url or similar meta tags
    for meta in soup.find_all("meta", attrs={"property": re.compile(r"og:url|og:site_name")}):
        content = meta.get("content", "")
        if content:
            parsed = urlparse(content if content.startswith("http") else f"https://{content}")
            if parsed.netloc:
                return parsed.netloc

    return None


# ── Job Posting ──────────────────────────────────────────────────────────────


async def scrape_job_posting(url: str) -> dict:
    """Scrape a job posting URL and return structured data."""
    console.print(f"[bold blue]Scraping job posting:[/bold blue] {url}")
    html = await _fetch_html(url)
    if not html:
        raise SystemExit(f"Error: Could not fetch job posting at {url}. Check the URL and try again.")

    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("title")
    page_title = title_tag.get_text(strip=True) if title_tag else ""
    full_text = _extract_text(html)
    domain = _infer_domain(html, url)

    # Try to pull structured elements
    job_title = ""
    company_name = ""

    # Common patterns: "Job Title at Company" or "Company - Job Title"
    if " at " in page_title:
        parts = page_title.split(" at ", 1)
        job_title = parts[0].strip()
        company_name = parts[1].split("|")[0].split("-")[0].strip()
    elif " - " in page_title:
        parts = page_title.split(" - ", 1)
        job_title = parts[0].strip()
        company_name = parts[1].split("|")[0].split("-")[0].strip()

    # Look for og: meta tags as backup
    if not company_name:
        og_site = soup.find("meta", attrs={"property": "og:site_name"})
        if og_site:
            company_name = og_site.get("content", "")

    console.print(f"  [green]Job title:[/green] {job_title or '(will infer from text)'}")
    console.print(f"  [green]Company:[/green]  {company_name or '(will infer from text)'}")
    console.print(f"  [green]Domain:[/green]   {domain or '(not found)'}")

    return {
        "url": url,
        "html": html,
        "full_text": full_text,
        "page_title": page_title,
        "job_title": job_title,
        "company_name": company_name,
        "domain": domain,
    }


# ── Company Website ──────────────────────────────────────────────────────────


async def scrape_company_website(domain: str) -> dict:
    """Scrape the company's homepage and key pages."""
    console.print(f"[bold blue]Scraping company website:[/bold blue] {domain}")
    result = {
        "homepage_text": "",
        "pricing_text": "",
        "blog_exists": False,
        "notable_claims": [],
    }

    base_url = f"https://{domain}"

    # Homepage
    html = await _fetch_html(base_url)
    if html:
        result["homepage_text"] = _extract_text(html)[:5000]
        soup = BeautifulSoup(html, "html.parser")

        # Extract notable claims (numbers, stats)
        text = result["homepage_text"]
        claims = re.findall(
            r"(?:[\d,]+\+?\s+(?:customers|companies|users|teams|people|businesses|downloads))"
            r"|(?:(?:used|trusted|loved)\s+by\s+[\w\s,]+)"
            r"|(?:Fortune\s+\d+)"
            r"|(?:\$[\d.]+[BMK]?\+?\s+\w+)",
            text, re.IGNORECASE
        )
        result["notable_claims"] = list(set(claims))[:10]
        console.print(f"  [green]Homepage:[/green] scraped ({len(result['homepage_text'])} chars)")
    else:
        console.print("  [yellow]Homepage:[/yellow] could not fetch")

    # Pricing
    pricing_html = await _fetch_html(f"{base_url}/pricing")
    if pricing_html:
        result["pricing_text"] = _extract_text(pricing_html)[:3000]
        console.print("  [green]Pricing page:[/green] found")
    else:
        console.print("  [dim]Pricing page:[/dim] not found")

    # Blog check
    blog_html = await _fetch_html(f"{base_url}/blog")
    if blog_html:
        result["blog_exists"] = True
        console.print("  [green]Blog:[/green] found")
    else:
        console.print("  [dim]Blog:[/dim] not found")

    return result


# ── Meta Ads Library ─────────────────────────────────────────────────────────


async def scrape_meta_ads(company_name: str) -> dict:
    """Check Meta Ads Library for the company's ad activity."""
    console.print(f"[bold blue]Checking Meta Ads Library:[/bold blue] {company_name}")
    result = {
        "has_ads": False,
        "volume": "none",
        "formats": [],
        "themes": [],
    }

    url = (
        "https://www.facebook.com/ads/library/"
        f"?active_status=all&ad_type=all&country=ALL"
        f"&q={company_name}&search_type=keyword_unordered"
    )

    html = await _fetch_html(url)
    if not html:
        console.print("  [yellow]Could not access Meta Ads Library[/yellow]")
        return result

    text = _extract_text(html)
    lower = text.lower()

    # Detect if ads are present
    ad_indicators = ["sponsored", "active", "inactive", "ad ", "ads ", "started running"]
    if any(ind in lower for ind in ad_indicators):
        result["has_ads"] = True

        # Rough volume estimate
        ad_count_matches = re.findall(r"(\d+)\s+results?", lower)
        if ad_count_matches:
            count = int(ad_count_matches[0])
            if count > 50:
                result["volume"] = "heavy"
            elif count > 15:
                result["volume"] = "moderate"
            else:
                result["volume"] = "light"
        else:
            result["volume"] = "light"

        # Format detection
        for fmt in ["video", "image", "carousel"]:
            if fmt in lower:
                result["formats"].append(fmt)

        console.print(f"  [green]Ads found:[/green] volume={result['volume']}, formats={result['formats']}")
    else:
        console.print("  [dim]No ads found[/dim]")

    return result


# ── Public Sources (best effort) ─────────────────────────────────────────────


async def scrape_similarweb(domain: str) -> str:
    """Try to get traffic estimates from SimilarWeb public page."""
    console.print(f"[bold blue]Checking SimilarWeb:[/bold blue] {domain}")
    url = f"https://www.similarweb.com/website/{domain}/"
    html = await _fetch_html(url)
    if not html:
        console.print("  [yellow]Could not access SimilarWeb[/yellow]")
        return "unavailable"

    text = _extract_text(html)
    # Look for visit numbers
    visits = re.findall(
        r"([\d.]+[KMB]?\s*(?:visits?|monthly visits?|total visits?))",
        text, re.IGNORECASE
    )
    if visits:
        estimate = visits[0].strip()
        console.print(f"  [green]Traffic estimate:[/green] {estimate}")
        return estimate

    console.print("  [dim]No traffic data found[/dim]")
    return "unavailable"


async def scrape_linkedin_company(company_name: str) -> dict:
    """Try to pull basic info from LinkedIn company page."""
    console.print(f"[bold blue]Checking LinkedIn:[/bold blue] {company_name}")
    result = {"headcount": "unknown", "growth_signals": []}

    slug = company_name.lower().replace(" ", "-").replace(",", "").replace(".", "")
    url = f"https://www.linkedin.com/company/{slug}/"
    html = await _fetch_html(url)
    if not html:
        console.print("  [yellow]Could not access LinkedIn page[/yellow]")
        return result

    text = _extract_text(html)

    # Headcount
    emp_match = re.findall(r"([\d,]+(?:-[\d,]+)?)\s*employees?", text, re.IGNORECASE)
    if emp_match:
        result["headcount"] = emp_match[0]
        console.print(f"  [green]Headcount:[/green] {result['headcount']}")
    else:
        console.print("  [dim]Headcount not found[/dim]")

    return result
