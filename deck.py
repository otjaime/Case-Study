"""Generates styled PDF documents from diagnostic markdown.

Two output formats:
  - A4 document: generate_deck_pdf() — text-heavy, standalone reading
  - 16:9 slides:  generate_slide_deck_pdf() — minimal text, visual support for video
"""

import re
import json
import asyncio
from datetime import date
from pathlib import Path

import anthropic
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


# ---------------------------------------------------------------------------
# Slide deck: Haiku condensation + 16:9 PDF
# ---------------------------------------------------------------------------

HAIKU_MODEL = "claude-haiku-4-5-20251001"

CONDENSE_FOR_SLIDES_PROMPT = """Condense this diagnostic document into slide-ready data.
You are extracting KEY DATA POINTS and SHORT HEADLINES — not summarizing paragraphs.

DIAGNOSTIC DOCUMENT:
{markdown}

Respond ONLY with valid JSON matching this schema:
{{
  "situation_summary": "One sentence (max 15 words) capturing the company's inflection point",
  "stat_cards": [
    {{
      "value": "the number, percentage, or dollar amount (e.g. '$96', '70%', '5x')",
      "label": "what the number measures (max 4 words)",
      "context": "why it matters (max 8 words)"
    }}
  ],
  "solutions": [
    {{
      "headline": "what you are fixing — active voice, max 8 words",
      "bullets": ["action point max 12 words", "action point max 12 words", "action point max 12 words"],
      "key_metric": {{
        "value": "the single most important number for this problem",
        "label": "what it measures (max 5 words)"
      }},
      "match_level": "alto | medio | bajo | ninguno",
      "evidence_line": "one sentence: what the candidate did + result (max 20 words)"
    }}
  ],
  "insight": {{
    "claim": "the contrarian observation — one sentence, max 25 words",
    "implication": "what to do about it — one sentence, max 15 words"
  }},
  "first_30_days": [
    {{
      "action": "specific decision or action — max 8 words",
      "reason": "why this first — max 12 words"
    }}
  ]
}}

EXTRACTION RULES:
- stat_cards: extract 3-4 cards. Pull REAL numbers from the document. Prefer: revenue/growth,
  key ratio (CPA, retention, LTV), channel concentration, and a risk metric. Do NOT invent numbers.
- solutions: one per problem section (typically 3). The headline must be the PROBLEM being
  solved, not the deliverable. Each bullet is a specific action, not a description.
- match_level: if the section references the candidate's past experience with concrete results,
  it's "alto". If it references methodology transfer, "medio". If it says "I haven't done exactly
  this", "bajo". If it uses benchmarks/reasoning only, "ninguno".
- evidence_line: extract the candidate's SPECIFIC experience cited in each solution section.
  Must reference a company name and a result. If none cited, use empty string "".
- first_30_days: extract exactly 3 items. Use the "First 30 Days" section if it exists.
- insight: extract from the "Non-obvious" or "Insight" section.
- ALL text must be SHORT. This is for presentation slides, not a document."""


def _try_parse_json(text: str, container: str = "object"):
    """Extract and parse JSON from model output, handling truncated responses."""
    if container == "object":
        start_char, end_char = "{", "}"
    else:
        start_char, end_char = "[", "]"

    start = text.find(start_char)
    if start < 0:
        return None

    end = text.rfind(end_char) + 1
    if end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    # Attempt truncation repair
    fragment = text[start:]
    stripped = fragment.rstrip()
    if stripped and stripped[-1] not in (end_char, start_char, ",", '"',
                                         "e", "l", "0", "1", "2", "3",
                                         "4", "5", "6", "7", "8", "9"):
        last_comma = stripped.rfind(",")
        if last_comma > 0:
            stripped = stripped[:last_comma]

    open_braces = stripped.count("{") - stripped.count("}")
    open_brackets = stripped.count("[") - stripped.count("]")
    repaired = stripped + "}" * max(open_braces, 0) + "]" * max(open_brackets, 0)

    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        return None


async def _condense_for_slides(markdown: str) -> dict | None:
    """Condense diagnostic markdown into slide-ready data using Haiku."""
    client = anthropic.AsyncAnthropic()

    for attempt in range(2):
        try:
            message = await client.messages.create(
                model=HAIKU_MODEL,
                max_tokens=2000,
                messages=[{"role": "user", "content": CONDENSE_FOR_SLIDES_PROMPT.format(
                    markdown=markdown[:8000]
                )}],
            )
            text = message.content[0].text.strip()

            if message.stop_reason == "max_tokens":
                console.print("  [yellow]Slide condensation truncated — attempting repair[/yellow]")

            result = _try_parse_json(text, container="object")
            if result and result.get("solutions"):
                console.print(f"  [green]Slide data condensed: {len(result['solutions'])} solutions[/green]")
                return result

            console.print(f"  [yellow]Slide JSON parse failed (attempt {attempt + 1})[/yellow]")
        except Exception as exc:
            console.print(f"  [yellow]Slide condensation error (attempt {attempt + 1}): {exc}[/yellow]")

        if attempt == 0:
            await asyncio.sleep(1)

    return None


def _truncate_words(text: str, max_words: int) -> str:
    """Truncate text at a word boundary."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return ' '.join(words[:max_words])


def _fallback_slide_extraction(markdown: str) -> dict:
    """Build slide data from regex parsing when Haiku is unavailable."""
    sections = _parse_diagnostic_sections(markdown)
    source_text = sections.get("what_i_see", "") or sections.get("opening", "")
    metrics = _extract_key_metrics(source_text)

    number_pat = re.compile(r'(\$[\d,.]+[MBKk]?|\d+(?:\.\d+)?%|\d+(?:\.\d+)?[xX])')

    stat_cards = []
    for m in metrics[:4]:
        num = number_pat.search(m)
        if num:
            # Build a clean label from context around the number
            clean = re.sub(r'\*\*|__|[*_]', '', m).strip()
            val = num.group(1)
            # Try to extract a label from text after the number
            after_idx = clean.find(val) + len(val)
            after = clean[after_idx:].strip().lstrip('+-,;: ')
            before_idx = clean.find(val)
            before = clean[:before_idx].strip().rstrip('+-,;: ')
            # Prefer text after the number (e.g. "$96 CPA"), fallback to before
            if after and len(after) > 2:
                label = ' '.join(after.split()[:4])
            elif before and len(before) > 2:
                label = ' '.join(before.split()[-4:])
            else:
                label = "Key Metric"
            # Clean up label: strip trailing punctuation and filler words
            label = label.strip('.,;:()—–- ').strip()
            label = re.sub(r'\s+(—|–|-|in|into|a|the|at|of|and|or)$', '', label, flags=re.IGNORECASE)
            stat_cards.append({"value": val, "label": label or "Key Metric", "context": ""})

    solutions = []
    for sol in sections.get("solutions", [])[:3]:
        bullets = []
        for line in sol["body"].split("\n"):
            s = line.strip()
            # Match list items
            if s.startswith(("- ", "* ", "• ")) or re.match(r"^\d+\.\s", s):
                bullet_text = re.sub(r'^[-•*]\s+|^\d+\.\s+', '', s).strip()
                bullet_text = re.sub(r'\*\*|__|[*_]', '', bullet_text)  # strip markdown
                if len(bullet_text) > 5:
                    bullets.append(bullet_text[:80])
            # Match ### sub-headings as bullets
            elif s.startswith("### "):
                heading = s[4:].strip()
                heading = re.sub(r'\*\*|__|[*_]', '', heading)
                if len(heading) > 3:
                    bullets.append(heading[:80])
            # Match bold-prefixed lines like **Action:** description
            elif re.match(r'^\*\*[^*]+\*\*', s) and not s.startswith("**The ") and not s.startswith("**Why"):
                bold_text = re.sub(r'\*\*|__|[*_]', '', s).strip()
                if len(bold_text) > 5:
                    bullets.append(bold_text[:80])
            if len(bullets) >= 3:
                break
        if not bullets:
            bullets = [sol["title"]]

        # Try to extract a key metric from the solution body
        key_metric = {"value": "", "label": ""}
        body_nums = number_pat.findall(sol["body"])
        if body_nums:
            key_metric = {"value": body_nums[0], "label": "Key metric"}

        solutions.append({
            "headline": sol["title"][:60],
            "bullets": bullets,
            "key_metric": key_metric,
            "match_level": "",
            "evidence_line": "",
        })

    # Extract situation summary from what_i_see first paragraph
    situation_summary = ""
    if source_text:
        for line in source_text.split("\n"):
            s = line.strip()
            # Skip headings, blockquotes, empty lines, HRs
            if not s or s.startswith(("#", ">", "---", "***", "___", "|")):
                continue
            # Skip bold-only lines
            if re.match(r'^\*\*[^*]+\*\*$', s):
                continue
            # Take first real sentence
            clean = re.sub(r'\*\*|__|[*_]', '', s).strip()
            # Truncate to ~15 words
            words = clean.split()
            if len(words) > 15:
                situation_summary = ' '.join(words[:15]) + '...'
            else:
                situation_summary = clean
            break

    # Extract insight — strip blockquote markers
    insight_text = sections.get("insight", "")
    claim = ""
    implication = ""
    if insight_text:
        # Strip all > markers and join into clean text
        clean_lines = []
        for line in insight_text.strip().split("\n"):
            cleaned = re.sub(r'^>\s*', '', line).strip()
            if cleaned:
                clean_lines.append(cleaned)
        if clean_lines:
            # First line (strip bold) is the claim
            first = re.sub(r'\*\*|__|[*_]', '', clean_lines[0]).strip()
            claim = _truncate_words(first, 25)
            # Look for a substantive sentence in the rest as implication
            for cl in clean_lines[1:]:
                clean_cl = re.sub(r'\*\*|__|[*_]', '', cl).strip()
                if clean_cl and len(clean_cl) > 20:
                    implication = _truncate_words(clean_cl, 18)
                    break

    # Extract first 30 days
    first_30 = []
    f30_text = sections.get("first_30_days", "")
    for line in f30_text.split("\n"):
        s = line.strip()
        # Strip leading list markers
        s = re.sub(r'^[-•*]\s+', '', s)
        bold_match = re.match(r'\*\*(.+?)\*\*[:\s]*(.+)', s)
        if bold_match:
            bold_part = bold_match.group(1).strip()
            rest = bold_match.group(2).strip()
            # Strip "Decision N" prefix
            action = re.sub(r'^Decision\s+\d+[:\s]*', '', bold_part, flags=re.IGNORECASE).strip()
            reason = rest
            # If action is empty (bold was just "Decision N"), extract from rest
            if not action:
                # Split on " — because " or " — " to separate action from reason
                if ' — because ' in rest:
                    parts = rest.split(' — because ', 1)
                    action = parts[0].strip()
                    reason = parts[1].strip()
                elif ' — ' in rest:
                    parts = rest.split(' — ', 1)
                    action = parts[0].strip()
                    reason = parts[1].strip()
                else:
                    # Take first 8 words as action
                    words = rest.split()
                    action = ' '.join(words[:8])
                    reason = ' '.join(words[8:])
            elif ' — ' in reason:
                reason = reason.split(' — ')[0].strip()
            first_30.append({
                "action": _truncate_words(action, 10),
                "reason": _truncate_words(reason, 14),
            })
        if len(first_30) >= 3:
            break

    return {
        "situation_summary": situation_summary,
        "stat_cards": stat_cards,
        "solutions": solutions,
        "insight": {"claim": claim, "implication": implication},
        "first_30_days": first_30,
    }


async def generate_slide_deck_pdf(markdown: str, profile: dict, company_name: str,
                                   job_title: str, mapping_quality: dict) -> bytes:
    """Generate a landscape 16:9 slide deck PDF from diagnostic markdown.

    Uses Claude Haiku to condense the diagnostic into slide-ready data,
    then renders via WeasyPrint.
    """
    from weasyprint import HTML

    # Step 1: Condense via Haiku
    console.print("[bold]Condensing diagnostic for slides...[/bold]")
    slide_data = await _condense_for_slides(markdown)

    if slide_data is None:
        console.print("  [yellow]Haiku failed — using regex fallback[/yellow]")
        slide_data = _fallback_slide_extraction(markdown)

    # Step 2: Extract profile and match data (same as generate_deck_pdf)
    sections = _parse_diagnostic_sections(markdown)
    header_profile = _parse_header_profile(sections.get("opening", ""))

    candidate_name = profile.get("nombre", "") or header_profile.get("nombre", "")
    contact = profile.get("contacto", "") or header_profile.get("contacto", "")
    current_role = profile.get("rol_actual", "") or header_profile.get("rol_actual", "")
    skills = profile.get("skills_funcionales", [])[:8]

    if not company_name:
        company_name = header_profile.get("company", "Company")
    if not job_title:
        job_title = header_profile.get("rol_target", "Role")

    match_data = {
        "alto": mapping_quality.get("alto", 0),
        "medio": mapping_quality.get("medio", 0),
        "bajo": mapping_quality.get("bajo", 0),
        "ninguno": mapping_quality.get("ninguno", 0),
    }
    if sum(match_data.values()) == 0 and sections.get("experience_match_raw"):
        parsed_counts, parsed_skills = _parse_experience_match(sections["experience_match_raw"])
        match_data = parsed_counts
        if not skills:
            skills = parsed_skills[:8]

    # Step 3: Render template
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("slides.html")

    html_content = template.render(
        candidate_name=candidate_name or "Candidate",
        company_name=company_name or "Company",
        job_title=job_title or "Role",
        contact=contact,
        current_role=current_role,
        date_str=date.today().strftime("%B %Y"),
        slide_data=slide_data,
        skills=skills,
        match_data=match_data,
    )

    pdf_bytes = HTML(string=html_content).write_pdf()
    console.print("[bold green]Slide deck PDF generated[/bold green]")
    return pdf_bytes
