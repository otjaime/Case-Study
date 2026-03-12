"""Formats and saves the final case study document."""
import os
import re
from datetime import date
from pathlib import Path
from rich.console import Console

console = Console()
OUTPUTS_DIR = Path(__file__).parent / "outputs"


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text.strip("-")[:60]


def save_markdown(context: dict, case_study_text: str, custom_name: str | None = None) -> Path:
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


# ---------------------------------------------------------------------------
# CSS — single column, clean typography, proper page breaks
# ---------------------------------------------------------------------------
PDF_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

* { box-sizing: border-box; margin: 0; padding: 0; }

@page {
    size: A4;
    margin: 22mm 20mm 20mm 20mm;
}

body {
    font-family: 'Inter', 'Helvetica Neue', Arial, sans-serif;
    font-size: 10.5pt;
    line-height: 1.65;
    color: #1a1a1a;
    max-width: 100%;
}

/* ---- HEADINGS ---- */
h1 {
    font-size: 20pt;
    font-weight: 700;
    color: #0f1923;
    margin-top: 36pt;
    margin-bottom: 8pt;
    page-break-after: avoid;
}

h2 {
    font-size: 14pt;
    font-weight: 600;
    color: #0f1923;
    margin-top: 24pt;
    margin-bottom: 6pt;
    border-bottom: 1.5px solid #e2e8f0;
    padding-bottom: 4pt;
    page-break-after: avoid;
}

h3 {
    font-size: 11.5pt;
    font-weight: 600;
    color: #1e293b;
    margin-top: 16pt;
    margin-bottom: 4pt;
    page-break-after: avoid;
}

/* ---- BODY TEXT ---- */
p {
    margin-bottom: 9pt;
    text-align: left;
    orphans: 3;
    widows: 3;
}

/* ---- LISTS ---- */
ul, ol {
    margin-left: 18pt;
    margin-bottom: 9pt;
}

li {
    margin-bottom: 4pt;
    text-align: left;
}

/* ---- STRONG / EM ---- */
strong { font-weight: 600; color: #0f1923; }
em { font-style: italic; }
code {
    font-family: 'Courier New', monospace;
    font-size: 9pt;
    background: #f1f5f9;
    padding: 1pt 3pt;
    border-radius: 2pt;
}

/* ---- BLOCKQUOTE (insight callout) ---- */
blockquote {
    border-left: 3pt solid #3b82f6;
    margin: 14pt 0;
    padding: 10pt 14pt;
    background: #f8fafc;
    color: #334155;
    border-radius: 0 4pt 4pt 0;
    page-break-inside: avoid;
}

blockquote p { margin-bottom: 0; }

/* ---- HR ---- */
hr {
    border: none;
    border-top: 1px solid #e2e8f0;
    margin: 18pt 0;
}

/* ---- TABLES ---- */
table {
    width: 100%;
    border-collapse: collapse;
    margin: 14pt 0;
    font-size: 9.5pt;
    page-break-inside: avoid;
}

thead tr {
    background: #0f1923;
    color: #ffffff;
}

thead th {
    padding: 7pt 8pt;
    text-align: left;
    font-weight: 600;
    font-size: 9pt;
    letter-spacing: 0.02em;
}

tbody tr:nth-child(even) { background: #f8fafc; }
tbody tr:nth-child(odd)  { background: #ffffff; }

tbody td {
    padding: 6pt 8pt;
    border-bottom: 1px solid #e2e8f0;
    vertical-align: top;
    text-align: left;
}

/* ---- METADATA (frontmatter) ---- */
.metadata {
    color: #94a3b8;
    font-size: 9pt;
    margin-bottom: 20pt;
    padding-bottom: 12pt;
    border-bottom: 1px solid #e2e8f0;
}

/* ---- PAGE BREAK CONTROL ---- */
h1, h2, h3 { page-break-after: avoid; }
table, blockquote, ul, ol { page-break-inside: avoid; }
p { page-break-inside: avoid; }
"""


def save_pdf(markdown_path: Path) -> Path | None:
    try:
        from weasyprint import HTML, CSS
    except ImportError:
        console.print("[yellow]weasyprint not installed — skipping PDF export.[/yellow]")
        return None

    md_text = markdown_path.read_text(encoding="utf-8")
    html_body = _md_to_html(md_text)

    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>{PDF_CSS}</style>
</head>
<body>
{html_body}
</body>
</html>"""

    pdf_path = markdown_path.with_suffix(".pdf")
    HTML(string=full_html).write_pdf(str(pdf_path))
    console.print(f"[bold green]PDF saved:[/bold green] {pdf_path}")
    return pdf_path


# ---------------------------------------------------------------------------
# Markdown → HTML parser
# ---------------------------------------------------------------------------

def _md_to_html(md: str) -> str:
    lines = md.split("\n")
    html_lines = []
    in_list = False
    in_ordered_list = False
    in_table = False
    in_frontmatter = False
    frontmatter_lines = []
    table_rows = []
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # --- FRONTMATTER ---
        if stripped == "---" and i == 0:
            in_frontmatter = True
            i += 1
            continue
        if in_frontmatter:
            if stripped == "---":
                in_frontmatter = False
                html_lines.append('<div class="metadata">')
                for fm in frontmatter_lines:
                    html_lines.append(f"<span>{fm}</span><br>")
                html_lines.append("</div>")
            else:
                frontmatter_lines.append(stripped)
            i += 1
            continue

        # --- CLOSE OPEN STRUCTURES if line doesn't match ---
        is_list_item = stripped.startswith(("- ", "* ", "• "))
        is_ordered_item = bool(re.match(r"^\d+\.\s", stripped))
        is_table_row = stripped.startswith("|") and stripped.endswith("|")

        if in_list and not is_list_item:
            html_lines.append("</ul>")
            in_list = False
        if in_ordered_list and not is_ordered_item:
            html_lines.append("</ol>")
            in_ordered_list = False
        if in_table and not is_table_row:
            html_lines.extend(_render_table(table_rows))
            table_rows = []
            in_table = False

        # --- EMPTY LINE ---
        if not stripped:
            html_lines.append("")
            i += 1
            continue

        # --- HR ---
        if stripped in ("---", "***", "___") and not in_frontmatter:
            html_lines.append("<hr>")
            i += 1
            continue

        # --- HEADINGS ---
        if stripped.startswith("#### "):
            html_lines.append(f"<h4>{_inline(stripped[5:])}</h4>")
        elif stripped.startswith("### "):
            html_lines.append(f"<h3>{_inline(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            html_lines.append(f"<h2>{_inline(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            html_lines.append(f"<h1>{_inline(stripped[2:])}</h1>")

        # --- BLOCKQUOTE ---
        elif stripped.startswith("> "):
            content = stripped[2:]
            html_lines.append(f"<blockquote><p>{_inline(content)}</p></blockquote>")

        # --- TABLE ---
        elif is_table_row:
            in_table = True
            table_rows.append(stripped)

        # --- UNORDERED LIST ---
        elif is_list_item:
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            content = re.sub(r'^[-•]\s+|^\*\s+', '', stripped).strip()
            html_lines.append(f"<li>{_inline(content)}</li>")

        # --- ORDERED LIST ---
        elif is_ordered_item:
            if not in_ordered_list:
                html_lines.append("<ol>")
                in_ordered_list = True
            content = re.sub(r"^\d+\.\s*", "", stripped)
            html_lines.append(f"<li>{_inline(content)}</li>")

        # --- PARAGRAPH ---
        else:
            html_lines.append(f"<p>{_inline(stripped)}</p>")

        i += 1

    # Close any open structures at EOF
    if in_list:
        html_lines.append("</ul>")
    if in_ordered_list:
        html_lines.append("</ol>")
    if in_table:
        html_lines.extend(_render_table(table_rows))

    return "\n".join(html_lines)


def _render_table(rows: list[str]) -> list[str]:
    """Convert raw markdown table rows into HTML table."""
    if not rows:
        return []

    html = ["<table>"]
    header_done = False

    for row in rows:
        # Skip separator rows like |---|---|
        cells = [c.strip() for c in row.strip("|").split("|")]
        if all(re.match(r"^[-:]+$", c.strip()) for c in cells if c.strip()):
            continue

        if not header_done:
            html.append("<thead><tr>")
            for cell in cells:
                html.append(f"<th>{_inline(cell)}</th>")
            html.append("</tr></thead><tbody>")
            header_done = True
        else:
            html.append("<tr>")
            for cell in cells:
                html.append(f"<td>{_inline(cell)}</td>")
            html.append("</tr>")

    html.append("</tbody></table>")
    return html


def _inline(text: str) -> str:
    """Handle bold, italic, code inline formatting."""
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", text)
    text = re.sub(r"\*\*(.+?)\*\*",     r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*",         r"<em>\1</em>", text)
    text = re.sub(r"`(.+?)`",           r"<code>\1</code>", text)
    # Links
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', text)
    return text
