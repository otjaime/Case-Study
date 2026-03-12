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
    """Split diagnostic markdown into structured sections.

    Handles two formats:
    - v1: ## What I'd Do → ### Problem 1, ### Problem 2
    - v2: ## 01 · Problem 1, ## 02 · Problem 2
    """
    sections = {
        "opening": "",
        "what_i_see": "",
        "solutions": [],
        "insight": "",
        "first_30_days": "",
        "close": "",
        "email": "",
        "experience_match_raw": "",
    }

    # Detect format: v2 uses numbered ## headings like "## 01 ·"
    is_v2 = bool(re.search(r'^##\s+\d+\s*[·.]', markdown, re.MULTILINE))

    # Split on ## headings only (### stays in body text for v2)
    if is_v2:
        parts = re.split(r'^(##\s+.+)$', markdown, flags=re.MULTILINE)
    else:
        parts = re.split(r'^(#{2,3}\s+.+)$', markdown, flags=re.MULTILINE)

    # Build (heading, body) pairs
    merged = []
    i = 0
    while i < len(parts):
        part = parts[i]
        if is_v2:
            is_heading = re.match(r'^##\s+', part)
        else:
            is_heading = re.match(r'^#{2,3}\s+', part)

        if is_heading:
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

        # Numbered solution: "01 · Title" or "01. Title"
        num_match = re.match(r'^(\d+)\s*[·.]\s*(.+)$', heading)
        if num_match:
            title = num_match.group(2).strip()
            sections["solutions"].append({"title": title, "body": body})
            continue

        if "email" in lower:
            sections["email"] = body
            in_solutions = False
            continue

        if "experience match" in lower:
            sections["experience_match_raw"] = body
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

        # "What I'd Do" marker (v1 format)
        if any(kw in lower for kw in ["what i'd do", "what i would do", "lo que haría",
                                       "my approach", "what i'd change"]):
            in_solutions = True
            # v1: body may contain ### sub-headings — split into solutions
            if body.strip():
                sub_parts = re.split(r'^(###\s+.+)$', body, flags=re.MULTILINE)
                si = 0
                while si < len(sub_parts):
                    sp = sub_parts[si]
                    if re.match(r'^###\s+', sp):
                        sub_heading = re.sub(r'^###\s+', '', sp).strip()
                        sub_body = sub_parts[si + 1].strip() if si + 1 < len(sub_parts) else ""
                        sections["solutions"].append({"title": sub_heading, "body": sub_body})
                        si += 2
                    else:
                        si += 1
            continue

        # Anything after what_i_see → treat as solution (v1 fallback)
        if sections["what_i_see"] or in_solutions:
            in_solutions = True
            sections["solutions"].append({"title": heading, "body": body})
            continue

    # Post-process: extract close from last section if close is empty
    if not sections["close"] and sections["first_30_days"]:
        body = sections["first_30_days"]
        # Look for trailing content after --- at the end
        parts = body.rsplit("---", 1)
        if len(parts) == 2 and parts[1].strip():
            trailing = parts[1].strip()
            if any(kw in trailing.lower() for kw in ["resonate", "welcome", "contact", "@", "deeper"]):
                sections["first_30_days"] = parts[0].strip()
                sections["close"] = trailing

    return sections


def _strip_header_from_opening(text: str) -> str:
    """Remove header elements already shown on cover page."""
    if not text:
        return ""
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('# '):
            continue
        if stripped.startswith('### '):
            continue
        if re.match(r'^\*\*[^*]+\*\*$', stripped):
            continue
        if '·' in stripped and '@' in stripped:
            continue
        if stripped in ('---', '***', '___'):
            continue
        cleaned.append(line)
    return '\n'.join(cleaned).strip()


def _parse_experience_match(raw_text: str) -> tuple:
    """Parse experience match section from markdown into counts and skills.

    Returns: (match_counts_dict, skills_list)
    """
    counts = {"alto": 0, "medio": 0, "bajo": 0, "ninguno": 0}

    strong = re.search(r'Strong Match[^0-9]*(\d+)', raw_text)
    method = re.search(r'Method Match[^0-9]*(\d+)', raw_text)
    adjacent = re.search(r'Adjacent[^0-9]*(\d+)', raw_text)
    reasoning = re.search(r'Reasoning[^0-9]*(\d+)', raw_text)

    if strong:
        counts["alto"] = int(strong.group(1))
    if method:
        counts["medio"] = int(method.group(1))
    if adjacent:
        counts["bajo"] = int(adjacent.group(1))
    if reasoning:
        counts["ninguno"] = int(reasoning.group(1))

    skills = []
    skills_match = re.search(r'\*\*Key Skills[:\s]*\*\*\s*(.+)', raw_text)
    if not skills_match:
        skills_match = re.search(r'Key Skills[:\s]*(.+)', raw_text)
    if skills_match:
        skills_text = skills_match.group(1).strip()
        skills = [s.strip().strip('*') for s in re.split(r'[·,]', skills_text) if s.strip()]

    return counts, skills


def _parse_header_profile(preamble: str) -> dict:
    """Extract candidate profile from markdown header/preamble.

    Handles formats like:
        # Diagnostic Brief: Company
        ### VP, Growth Marketing
        **Name**
        info · info · email · date
    """
    profile = {}

    # Extract name from bold text
    name_match = re.search(r'\*\*([^*]+)\*\*', preamble)
    if name_match:
        profile["nombre"] = name_match.group(1).strip()

    # Extract email
    email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', preamble)
    if email_match:
        profile["contacto"] = email_match.group(0)

    # Extract title from ### heading or first line after #
    title_match = re.search(r'^###\s+(.+)$', preamble, re.MULTILINE)
    if title_match:
        profile["rol_target"] = title_match.group(1).strip()

    # Extract company from # heading
    company_match = re.search(r'^#\s+.*?:\s*(.+)$', preamble, re.MULTILINE)
    if company_match:
        profile["company"] = company_match.group(1).strip()

    # Extract role/descriptor line (line after name, contains · separators)
    meta_match = re.search(r'\*\*[^*]+\*\*\s*\n(.+)', preamble)
    if meta_match:
        meta_line = meta_match.group(1).strip()
        parts = [p.strip() for p in meta_line.split('·')]
        if parts:
            profile["rol_actual"] = parts[0]

    return profile


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

    Handles: headings, bold/italic, lists (ul + ol), tables, blockquotes
    (including multi-line), horizontal rules, and links.
    """
    if not text:
        return ""

    lines = text.split("\n")
    html_lines = []
    in_list = False
    in_ordered_list = False
    in_table = False
    in_blockquote = False
    blockquote_lines = []
    table_rows = []

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Detect line types
        is_list_item = stripped.startswith(("- ", "* ", "• "))
        is_ordered_item = bool(re.match(r"^\d+\.\s", stripped))
        is_table_row = stripped.startswith("|") and stripped.endswith("|")
        is_blockquote = stripped.startswith("> ") or stripped == ">"

        # Close blockquote if leaving
        if in_blockquote and not is_blockquote:
            bq_text = "\n".join(blockquote_lines)
            html_lines.append(f"<blockquote><p>{_inline(bq_text)}</p></blockquote>")
            blockquote_lines = []
            in_blockquote = False

        # Close other open structures if line doesn't match
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

        # Blockquote (multi-line)
        if is_blockquote:
            in_blockquote = True
            content = stripped[2:] if stripped.startswith("> ") else ""
            # Strip nested > for continuation lines
            content = re.sub(r'^>\s*', '', content)
            blockquote_lines.append(content)
            i += 1
            continue

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

        # Italic line starting with * (like *For creative velocity...*)
        elif stripped.startswith("*") and stripped.endswith("*") and not stripped.startswith("**"):
            html_lines.append(f"<p><em>{_inline(stripped[1:-1])}</em></p>")

        # Paragraph
        else:
            html_lines.append(f"<p>{_inline(stripped)}</p>")

        i += 1

    # Close open structures at EOF
    if in_blockquote:
        bq_text = "\n".join(blockquote_lines)
        html_lines.append(f"<blockquote><p>{_inline(bq_text)}</p></blockquote>")
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
        # Skip empty-header tables (like experience match with | | |)
        if not header_done and all(not c.strip() for c in cells):
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

    # If profile is minimal, try to extract from the markdown header
    header_profile = _parse_header_profile(sections.get("opening", ""))
    candidate_name = profile.get("nombre", "") or header_profile.get("nombre", "")
    contact = profile.get("contacto", "") or header_profile.get("contacto", "")
    current_role = profile.get("rol_actual", "") or header_profile.get("rol_actual", "")
    skills = profile.get("skills_funcionales", [])[:8]

    if not company_name:
        company_name = header_profile.get("company", "Company")
    if not job_title:
        job_title = header_profile.get("rol_target", "Role")

    # Match data: prefer structured data, fall back to parsing from markdown
    match_data = {
        "alto": mapping_quality.get("alto", 0),
        "medio": mapping_quality.get("medio", 0),
        "bajo": mapping_quality.get("bajo", 0),
        "ninguno": mapping_quality.get("ninguno", 0),
    }
    total_structured = sum(match_data.values())

    if total_structured == 0 and sections.get("experience_match_raw"):
        parsed_counts, parsed_skills = _parse_experience_match(sections["experience_match_raw"])
        match_data = parsed_counts
        if not skills:
            skills = parsed_skills[:8]

    # Convert section bodies to HTML (strip header info already on cover)
    opening_html = _md_to_html(_strip_header_from_opening(sections.get("opening", "")))
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
