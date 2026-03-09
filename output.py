"""Formats and saves the final case study document."""

import os
import re
from datetime import date
from pathlib import Path

from rich.console import Console

console = Console()

OUTPUTS_DIR = Path(__file__).parent / "outputs"


def _slugify(text: str) -> str:
    """Turn text into a filename-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text.strip("-")[:60]


def save_markdown(context: dict, case_study_text: str, custom_name: str | None = None) -> Path:
    """Save the case study as a markdown file."""
    OUTPUTS_DIR.mkdir(exist_ok=True)

    company = context.get("company_name", "unknown")
    job_title = context.get("job_title", "role")
    today = date.today().isoformat()

    if custom_name:
        filename = f"{_slugify(custom_name)}.md"
    else:
        filename = f"{_slugify(company)}-{_slugify(job_title)}-case-study.md"

    header = f"""---
Company: {company}
Role: {job_title}
Generated: {today}
---

"""

    content = header + case_study_text
    output_path = OUTPUTS_DIR / filename
    output_path.write_text(content, encoding="utf-8")
    console.print(f"\n[bold green]Saved:[/bold green] {output_path}")
    return output_path


def save_pdf(markdown_path: Path) -> Path | None:
    """Convert the markdown file to a styled PDF."""
    try:
        from weasyprint import HTML
    except ImportError:
        console.print("[yellow]weasyprint not installed — skipping PDF export.[/yellow]")
        console.print("  Install with: pip install weasyprint")
        return None

    md_text = markdown_path.read_text(encoding="utf-8")

    # Simple markdown to HTML conversion (basic subset)
    html_body = _md_to_html(md_text)

    styled_html = f"""<!DOCTYPE html>
<html>
<head>
<style>
    body {{
        font-family: 'Helvetica Neue', Arial, sans-serif;
        max-width: 700px;
        margin: 40px auto;
        padding: 0 20px;
        color: #1a1a1a;
        font-size: 14px;
        line-height: 1.6;
    }}
    h1 {{ font-size: 22px; margin-top: 32px; color: #111; }}
    h2 {{ font-size: 18px; margin-top: 28px; color: #222; border-bottom: 1px solid #eee; padding-bottom: 4px; }}
    h3 {{ font-size: 15px; margin-top: 20px; color: #333; }}
    ul {{ padding-left: 20px; }}
    li {{ margin-bottom: 6px; }}
    strong {{ color: #111; }}
    blockquote {{
        border-left: 3px solid #ccc;
        margin-left: 0;
        padding-left: 16px;
        color: #555;
    }}
    hr {{ border: none; border-top: 1px solid #ddd; margin: 24px 0; }}
    .metadata {{ color: #888; font-size: 12px; margin-bottom: 24px; }}
</style>
</head>
<body>
{html_body}
</body>
</html>"""

    pdf_path = markdown_path.with_suffix(".pdf")
    HTML(string=styled_html).write_pdf(str(pdf_path))
    console.print(f"[bold green]PDF saved:[/bold green] {pdf_path}")
    return pdf_path


def _md_to_html(md: str) -> str:
    """Minimal markdown to HTML for PDF rendering."""
    lines = md.split("\n")
    html_lines = []
    in_list = False
    in_frontmatter = False
    frontmatter_lines = []

    for line in lines:
        stripped = line.strip()

        # Handle YAML frontmatter
        if stripped == "---":
            if not in_frontmatter and not frontmatter_lines:
                in_frontmatter = True
                continue
            elif in_frontmatter:
                in_frontmatter = False
                html_lines.append('<div class="metadata">')
                for fm in frontmatter_lines:
                    html_lines.append(f"{fm}<br>")
                html_lines.append("</div><hr>")
                continue

        if in_frontmatter:
            frontmatter_lines.append(stripped)
            continue

        # Close list if we're leaving one
        if in_list and not stripped.startswith(("- ", "* ", "• ")):
            html_lines.append("</ul>")
            in_list = False

        if not stripped:
            html_lines.append("<br>")
        elif stripped.startswith("# "):
            html_lines.append(f"<h1>{_inline_format(stripped[2:])}</h1>")
        elif stripped.startswith("## "):
            html_lines.append(f"<h2>{_inline_format(stripped[3:])}</h2>")
        elif stripped.startswith("### "):
            html_lines.append(f"<h3>{_inline_format(stripped[4:])}</h3>")
        elif stripped.startswith(("- ", "* ", "• ")):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            content = stripped.lstrip("-*• ").strip()
            html_lines.append(f"<li>{_inline_format(content)}</li>")
        elif stripped.startswith("> "):
            html_lines.append(f"<blockquote>{_inline_format(stripped[2:])}</blockquote>")
        else:
            html_lines.append(f"<p>{_inline_format(stripped)}</p>")

    if in_list:
        html_lines.append("</ul>")

    return "\n".join(html_lines)


def _inline_format(text: str) -> str:
    """Handle bold and italic inline formatting."""
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    return text
