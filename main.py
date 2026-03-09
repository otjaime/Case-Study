"""CLI entry point for the Job Case Study Generator."""

import argparse
import asyncio
import sys

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

from scraper import scrape_job_posting
from research import research_all
from analyzer import build_context
from generator import generate_case_study
from output import save_markdown, save_pdf

console = Console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a personalized case study from a job posting URL."
    )
    parser.add_argument(
        "--url",
        required=True,
        help="URL of the job posting",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Custom output filename (without extension)",
    )
    parser.add_argument(
        "--pdf",
        action="store_true",
        help="Also export as PDF",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    console.print(Panel("[bold]Job Case Study Generator[/bold]", style="blue"))

    # 1. Scrape job posting
    job_data = await scrape_job_posting(args.url)

    company_name = job_data["company_name"]
    domain = job_data["domain"]

    if not company_name:
        console.print("[yellow]Could not detect company name from the posting.[/yellow]")
        company_name = input("Enter the company name: ").strip()
        job_data["company_name"] = company_name

    if not domain:
        console.print("[yellow]Could not detect company domain from the posting.[/yellow]")
        domain = input("Enter the company domain (e.g. stripe.com): ").strip()
        job_data["domain"] = domain

    if not job_data["job_title"]:
        console.print("[yellow]Could not detect job title from the posting.[/yellow]")
        job_data["job_title"] = input("Enter the job title: ").strip()

    # 2. Research company (Exa + Firecrawl + fallback scrapers)
    research_data = await research_all(company_name, domain)

    # 3. Build structured context
    console.print("\n[bold]Analyzing data...[/bold]\n")
    context = build_context(job_data, research_data)

    # Report what was collected
    console.print("[bold]Data collection summary:[/bold]")
    console.print(f"  Company:        {context['company_name']}")
    console.print(f"  Role:           {context['job_title']}")
    console.print(f"  Seniority:      {context['seniority']}")
    console.print(f"  Business model: {context['business_model']}")
    console.print(f"  Growth stage:   {context['growth_stage']}")
    console.print(f"  Funding:        {context.get('funding_stage', 'unknown')} ({context.get('total_raised', 'n/a')})")
    console.print(f"  Channels:       {', '.join(context.get('marketing_channels', [])) or 'none detected'}")
    console.print(f"  Competitors:    {', '.join(context.get('competitors', [])) or 'none found'}")
    console.print(f"  Skills found:   {len(context['key_skills_required'])}")
    console.print(f"  Claims found:   {len(context['notable_claims'])}")

    # 4. Generate case study
    case_study = await generate_case_study(context)

    # 5. Save output
    md_path = save_markdown(context, case_study, custom_name=args.name)

    if args.pdf:
        save_pdf(md_path)

    console.print(Panel("[bold green]Done![/bold green]", style="green"))


def main() -> None:
    load_dotenv(override=True)
    args = parse_args()

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(1)
    except SystemExit as e:
        console.print(f"\n[red]{e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
