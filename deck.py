"""Generates a styled PDF document from a diagnostic markdown."""

import re
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from rich.console import Console

console = Console()

TEMPLATES_DIR = Path(__file__).parent / "templates"


# ---------------------------------------------------------------------------
# Markdown section parser
# ---------------------------------------------------------------------------

def _parse_diagnostic_sections(markdown: str) -> dict:
    """Split diagnostic markdown into structured sections."""
    sections = {
        "opening": "",
        "what_i_see": "",
        "solutions": [],      # list of {"title": str, "body": str}
        "insight": "",
        "first_30_days": "",
        "close": "",
        "email": "",
    }

    # Split on ## headings (keep the heading text)
    parts = re.split(r'^(#{2,3}\s+.+)$', markdown, flags=re.MULTILINE)

    # parts alternates: [text_before, heading, text, heading, text, ...]
    merged = []
    i = 0
    while i < len(parts):
        part = parts[i]
        if re.match(r'^#{2,3}\s+', part):
            clean_heading = re.sub(r'^#{2,3}\s+', '', part).strip()
            body = parts[i + 1].strip() if i + 1 < len(parts) else ""
            merged.append((clean_heading, body))
            i += 2
        else:
            if part.strip() and not merged:
                merged.append(("preamble", part.strip()))
            i += 1

    # Classify each section
    in_solutions = False
    for heading, body in merged:
        lower = heading.lower()

        if heading == "preamble":
            sections["opening"] = body
            continue

        if "email" in lower:
            sections["email"] = body
            in_solutions = False
            continue

        if "30" in lower and ("day" in lower or "día" in lower):
            sections["first_30_days"] = body
            in_solutions = False
            continue

        if any(kw in lower for kw in ["what i see", "lo que veo", "diagnosis", "diagnóstico"]):
            sections["what_i_see"] = body
            in_solutions = False
            continue

        if any(kw in lower for kw in ["non-obvious", "insight", "no obvio", "contrarian"]):
            sections["insight"] = body
            in_solutions = False
            continue

        if any(kw in lower for kw in ["close", "cierre", "contact"]):
            sections["close"] = body
            in_solutions = False
            continue

        if any(kw in lower for kw in ["what i'd do", "what i would do", "lo que haría",
                                       "my approach", "what i'd change"]):
            in_solutions = True
            continue

        if sections["what_i_see"] or in_solutions:
            in_solutions = True
            sections["solutions"].append({"title": heading, "body": body})
            continue

    return sections


def _extract_key_metrics(text: str) -> list:
    """Pull bold text, percentages, and dollar amounts for stat cards."""
    metrics = []
    bold_matches = re.findall(r'\*\*(.+?)\*\*', text)
    number_pattern = r'(?:\$[\d,.]+[MBKk]?|\d+(?:\.\d+)?%|\d+(?:\.\d+)?[xX]|\$[\d,.]+)'
    for bold in bold_matches:
        nums = re.findall(number_pattern, bold)
        if nums:
            metrics.append(bold)
    if not metrics:
        for match in re.finditer(number_pattern, text):
            start = max(0, match.start() - 40)
            end = min(len(text), match.end() + 40)
            context = text[start:end].strip()
            context = re.sub(r'^\S*\s', '', context)
            context = re.sub(r'\s\S*$', '', context)
            if len(context) > 10:
                metrics.append(context)
    return metrics[:4]


# ---------------------------------------------------------------------------
# Markdown → HTML converter (table-aware)
# ---------------------------------------------------------------------------

def _md_to_html(text: str) -> str:
    """Convert markdown text to HTML for PDF rendering.

    Handles: headings, bold/italic, lists (ul + ol), tables, blockquotes,
    horizontal rules, and links.
    """
    if not text:
        return ""

    lines = text.split("\n")
    html_lines = []
    in_list = False
    in_ordered_list = False
    in_table = False
    table_rows = []

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Detect line types
        is_list_item = stripped.startswith(("- ", "* ", "• "))
        is_ordered_item = bool(re.match(r"^\d+\.\s", stripped))
        is_table_row = stripped.startswith("|") and stripped.endswith("|")

        # Close open structures if line doesn't match
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

        # Empty line
        if not stripped:
            html_lines.append("")
            i += 1
            continue

        # Horizontal rule
        if stripped in ("---", "***", "___"):
            html_lines.append("<hr>")
            i += 1
            continue

        # Headings
        if stripped.startswith("#### "):
            html_lines.append(f"<h4>{_inline(stripped[5:])}</h4>")
        elif stripped.startswith("### "):
            html_lines.append(f"<h3>{_inline(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            html_lines.append(f"<h2>{_inline(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            html_lines.append(f"<h1>{_inline(stripped[2:])}</h1>")

        # Blockquote
        elif stripped.startswith("> "):
            content = stripped[2:]
            html_lines.append(f"<blockquote><p>{_inline(content)}</p></blockquote>")

        # Table
        elif is_table_row:
            in_table = True
            table_rows.append(stripped)

        # Unordered list
        elif is_list_item:
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            content = re.sub(r'^[-•]\s+|^\*\s+', '', stripped).strip()
            html_lines.append(f"<li>{_inline(content)}</li>")

        # Ordered list
        elif is_ordered_item:
            if not in_ordered_list:
                html_lines.append("<ol>")
                in_ordered_list = True
            content = re.sub(r"^\d+\.\s*", "", stripped)
            html_lines.append(f"<li>{_inline(content)}</li>")

        # Paragraph
        else:
            html_lines.append(f"<p>{_inline(stripped)}</p>")

        i += 1

    # Close open structures at EOF
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
        cells = [c.strip() for c in row.strip("|").split("|")]
        # Skip separator rows like |---|---|
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
    """Handle bold, italic, code, and link inline formatting."""
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
    text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2">\1</a>', text)
    return text


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------

def generate_deck_pdf(markdown: str, profile: dict, company_name: str,
                      job_title: str, mapping_quality: dict) -> bytes:
    """Generate a styled A4 PDF from diagnostic markdown.

    Args:
        markdown: The full diagnostic document markdown.
        profile: Extracted candidate profile dict (nombre, contacto, etc.).
        company_name: Target company name.
        job_title: Target role title.
        mapping_quality: Dict with alto/medio/bajo/ninguno counts.

    Returns:
        PDF bytes ready to send as response.
    """
    from weasyprint import HTML

    # Parse the diagnostic into sections
    sections = _parse_diagnostic_sections(markdown)

    # Convert section bodies to HTML
    opening_html = _md_to_html(sections.get("opening", ""))
    what_i_see_html = _md_to_html(sections.get("what_i_see", ""))
    insight_html = _md_to_html(sections.get("insight", ""))
    first_30_html = _md_to_html(sections.get("first_30_days", ""))
    close_html = _md_to_html(sections.get("close", ""))

    solutions_html = []
    for sol in sections.get("solutions", []):
        solutions_html.append({
            "title": sol["title"],
            "body": _md_to_html(sol["body"]),
        })

    # Candidate info from profile
    candidate_name = profile.get("nombre", "")
    contact = profile.get("contacto", "")
    current_role = profile.get("rol_actual", "")
    skills = profile.get("skills_funcionales", [])[:8]

    # Match level data
    match_data = {
        "alto": mapping_quality.get("alto", 0),
        "medio": mapping_quality.get("medio", 0),
        "bajo": mapping_quality.get("bajo", 0),
        "ninguno": mapping_quality.get("ninguno", 0),
    }

    # Load and render the Jinja2 template
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("deck.html")

    html_content = template.render(
        candidate_name=candidate_name or "Candidate",
        company_name=company_name or "Company",
        job_title=job_title or "Role",
        contact=contact,
        current_role=current_role,
        date_str=date.today().strftime("%B %Y"),
        opening_html=opening_html,
        what_i_see_html=what_i_see_html,
        solutions=solutions_html,
        insight_html=insight_html,
        first_30_html=first_30_html,
        close_html=close_html,
        skills=skills,
        match_data=match_data,
    )

    # Render to PDF
    pdf_bytes = HTML(string=html_content).write_pdf()
    console.print("[bold green]PDF document generated[/bold green]")
    return pdf_bytes
