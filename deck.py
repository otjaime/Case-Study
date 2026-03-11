"""Generates a presentation-style PDF deck from a diagnostic document."""

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
    """Split diagnostic markdown into structured sections.

    The diagnostic follows a predictable structure from the applier prompt:
    - Opening (text before first ##)
    - What I See (## heading)
    - What I'd Do — multiple sub-sections (## or ### per problem)
    - Non-obvious insight (## heading)
    - First 30 Days (## heading)
    - Close (## heading)
    - Email (### Email)
    """
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
    # Build (heading, body) pairs directly from the alternating structure
    merged = []
    i = 0
    while i < len(parts):
        part = parts[i]
        if re.match(r'^#{2,3}\s+', part):
            # This is a heading — next part (if exists) is its body
            clean_heading = re.sub(r'^#{2,3}\s+', '', part).strip()
            body = parts[i + 1].strip() if i + 1 < len(parts) else ""
            merged.append((clean_heading, body))
            i += 2
        else:
            # Text before any heading = opening (preamble)
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

        # Email section
        if "email" in lower:
            sections["email"] = body
            in_solutions = False
            continue

        # First 30 days
        if "30" in lower and ("day" in lower or "día" in lower):
            sections["first_30_days"] = body
            in_solutions = False
            continue

        # What I See / diagnosis
        if any(kw in lower for kw in ["what i see", "lo que veo", "diagnosis", "diagnóstico"]):
            sections["what_i_see"] = body
            in_solutions = False
            continue

        # Non-obvious insight
        if any(kw in lower for kw in ["non-obvious", "insight", "no obvio", "contrarian"]):
            sections["insight"] = body
            in_solutions = False
            continue

        # Close
        if any(kw in lower for kw in ["close", "cierre", "contact"]):
            sections["close"] = body
            in_solutions = False
            continue

        # What I'd Do — marks the start of solution sections
        if any(kw in lower for kw in ["what i'd do", "what i would do", "lo que haría",
                                       "my approach", "what i'd change"]):
            in_solutions = True
            # If there's body content, it might be the intro to the solutions
            continue

        # After "What I See" and before known sections — treat as solutions
        if sections["what_i_see"] or in_solutions:
            in_solutions = True
            sections["solutions"].append({"title": heading, "body": body})
            continue

    return sections


def _extract_key_metrics(text: str) -> list:
    """Pull bold text, percentages, and dollar amounts for stat cards."""
    metrics = []

    # Bold text patterns (likely key terms)
    bold_matches = re.findall(r'\*\*(.+?)\*\*', text)

    # Numbers with context: $X, X%, XM, Xk, X.Xx
    number_pattern = r'(?:\$[\d,.]+[MBKk]?|\d+(?:\.\d+)?%|\d+(?:\.\d+)?[xX]|\$[\d,.]+)'
    for bold in bold_matches:
        nums = re.findall(number_pattern, bold)
        if nums:
            metrics.append(bold)

    # If no bold metrics found, extract standalone metrics
    if not metrics:
        for match in re.finditer(number_pattern, text):
            # Get surrounding context (up to 40 chars before and after)
            start = max(0, match.start() - 40)
            end = min(len(text), match.end() + 40)
            context = text[start:end].strip()
            # Clean up to nearest word boundary
            context = re.sub(r'^\S*\s', '', context)
            context = re.sub(r'\s\S*$', '', context)
            if len(context) > 10:
                metrics.append(context)

    # Limit to 4 most interesting metrics
    return metrics[:4]


def _md_to_html(text: str) -> str:
    """Convert markdown text to HTML for deck rendering."""
    if not text:
        return ""

    lines = text.split("\n")
    html_lines = []
    in_list = False

    for line in lines:
        stripped = line.strip()

        if in_list and not stripped.startswith(("- ", "* ", "• ")):
            html_lines.append("</ul>")
            in_list = False

        if not stripped:
            if not in_list:
                html_lines.append("")
        elif stripped.startswith("### "):
            html_lines.append(f'<h4>{_inline(stripped[4:])}</h4>')
        elif stripped.startswith(("- ", "* ", "• ")):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            content = re.sub(r'^[-•]\s+|^\*\s+', '', stripped).strip()
            html_lines.append(f"<li>{_inline(content)}</li>")
        else:
            html_lines.append(f"<p>{_inline(stripped)}</p>")

    if in_list:
        html_lines.append("</ul>")

    return "\n".join(html_lines)


def _inline(text: str) -> str:
    """Handle bold, italic, and code inline formatting."""
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
    return text


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------

def generate_deck_pdf(markdown: str, profile: dict, company_name: str,
                      job_title: str, mapping_quality: dict) -> bytes:
    """Generate a presentation-style PDF deck from diagnostic markdown.

    Args:
        markdown: The full diagnostic document markdown.
        profile: Extracted candidate profile dict (nombre, contacto, empresas, etc.).
        company_name: Target company name.
        job_title: Target role title.
        mapping_quality: Dict with alto/medio/bajo/ninguno counts.

    Returns:
        PDF bytes ready to send as response.
    """
    from weasyprint import HTML

    # Parse the diagnostic into sections
    sections = _parse_diagnostic_sections(markdown)

    # Extract key metrics from the diagnosis section
    key_metrics = _extract_key_metrics(sections.get("what_i_see", ""))

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

    # Match level data for "Why Me" page
    match_data = {
        "alto": mapping_quality.get("alto", 0),
        "medio": mapping_quality.get("medio", 0),
        "bajo": mapping_quality.get("bajo", 0),
        "ninguno": mapping_quality.get("ninguno", 0),
    }
    total_matches = sum(match_data.values()) or 1  # avoid division by zero

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
        key_metrics=key_metrics,
        solutions=solutions_html,
        insight_html=insight_html,
        first_30_html=first_30_html,
        close_html=close_html,
        skills=skills,
        match_data=match_data,
        total_matches=total_matches,
    )

    # Render to PDF
    pdf_bytes = HTML(string=html_content).write_pdf()
    console.print("[bold green]PDF deck generated[/bold green]")
    return pdf_bytes
