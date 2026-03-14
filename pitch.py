"""Audio pitch generator: slide-aligned narration for the PDF deck.

Pipeline: condense_for_slides() -> generate_pitch_script() -> generate_audio()
Total time: ~8-10 seconds (synchronous).
Cost: ~$0.02 per pitch (Haiku ~$0.001 + ElevenLabs ~$0.02).
"""

import os
import json
import base64

import anthropic
from rich.console import Console

from deck import condense_for_slides, fallback_slide_extraction
from video import generate_audio

console = Console()

HAIKU_MODEL = "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# Pitch script prompt (slide-aligned, 6 paragraphs)
# ---------------------------------------------------------------------------

PITCH_SCRIPT_PROMPT = """You are a confident job candidate delivering a 2-minute spoken pitch over a 6-slide deck.
The hiring manager is VIEWING the slides while HEARING your voice. Your job is to add context, interpretation, and conviction — NOT read what's already visible.

SLIDE DATA (what's on each slide):
{slide_data_json}

CANDIDATE NAME: {candidate_name}
COMPANY: {company_name}

Write exactly 6 paragraphs, one per slide. Separate paragraphs with a blank line.

PARAGRAPH 1 — COVER SLIDE (~20 words, 2 sentences):
The slide shows: candidate name, company name, role title, tagline.
You say: a confident intro and your one-sentence thesis about the company's situation.
Format: "Hi, I'm [name]. After studying [company]'s growth model, I believe the biggest unlock is [thesis from situation_summary]."

PARAGRAPH 2 — DIAGNOSIS SLIDE (~60 words, 3-4 sentences):
The slide shows: situation_summary headline + 3-4 stat cards with numbers.
You say: INTERPRET the numbers. Why they matter together. What tension they reveal when combined.
Do NOT recite the stat values. Say what they MEAN as a system.
Example: "That CPA looks fine in isolation, but paired with the payback period, it means you're funding growth you won't see returns on for over a year."

PARAGRAPH 3 — ACTION 1 SLIDE (~60 words, 3-4 sentences):
The slide shows: problem headline, 3 approach bullets, consequence box, key metric, evidence box.
You say: WHY this problem is urgent NOW, your reasoning for this specific approach over alternatives, and what your past experience taught you about solving this type of problem. Connect the action to revenue or retention consequence.
Do NOT list the approach bullets. Explain the LOGIC behind them.

PARAGRAPH 4 — ACTION 2 SLIDE (~60 words, 3-4 sentences):
Same structure as Action 1. Additionally, explain how this action COMPOUNDS with Action 1 — why doing both together creates more value than either alone.

PARAGRAPH 5 — INSIGHT SLIDE (~40 words, 2-3 sentences):
The slide shows: a contrarian claim with conventional vs. reality columns.
You say: the "aha" moment. Why most people get this wrong and what happens when you get it right. Land this with conviction — it should feel like a reveal, not a summary.

PARAGRAPH 6 — CLOSE SLIDE (~40 words, 2-3 sentences):
The slide shows: 3 first-30-day decisions + contact card.
You say: confidence about your first moves and a clear, specific ask. Reference the company's central problem.
End with: "I'd love to walk through [specific problem from the diagnosis] together."

RULES:
- First person, conversational, as if presenting live to the hiring manager
- Short sentences. Contractions. Occasional "..." for natural pauses
- NEVER say "as you can see" or "this slide shows" or reference slides directly
- NEVER repeat stat values verbatim from the data — interpret and contextualize them
- Use specific company names, tool names, and terms from the slide data
- The tone is confident and direct, not formal or academic
- Total word count: 280-300 words (strictly enforced)
- Respond with ONLY the spoken text. No headers, no stage directions, no markdown, no [brackets]."""


# ---------------------------------------------------------------------------
# Script generation
# ---------------------------------------------------------------------------

async def generate_pitch_script(slide_data: dict, candidate_name: str,
                                company_name: str) -> str:
    """Generate a per-slide narration script from condensed slide data.

    Args:
        slide_data: Dict from condense_for_slides() with situation_summary,
                    stat_cards, actions, insight, first_30_days, close_line.
        candidate_name: Candidate's name for the intro.
        company_name: Target company name.

    Returns:
        Script text (~280-300 words, 6 paragraphs separated by blank lines).
    """
    client = anthropic.AsyncAnthropic()

    slide_data_json = json.dumps(slide_data, indent=2, ensure_ascii=False)

    message = await client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=1200,
        messages=[{"role": "user", "content": PITCH_SCRIPT_PROMPT.format(
            slide_data_json=slide_data_json,
            candidate_name=candidate_name or "the candidate",
            company_name=company_name or "the company",
        )}],
    )

    script = message.content[0].text.strip()

    word_count = len(script.split())
    if word_count < 200:
        console.print(f"  [yellow]Pitch script short: {word_count} words (expected ~290)[/yellow]")
    elif word_count > 400:
        console.print(f"  [yellow]Pitch script long: {word_count} words (expected ~290)[/yellow]")
    else:
        console.print(f"  [green]Pitch script generated: {word_count} words[/green]")

    return script


# ---------------------------------------------------------------------------
# Full pipeline: condense -> script -> audio
# ---------------------------------------------------------------------------

async def generate_pitch(markdown: str, jd_text: str, candidate_name: str,
                         company_name: str,
                         voice_pref: str = "female") -> dict:
    """Full pitch pipeline: condense slides -> generate script -> TTS audio.

    Args:
        markdown: Full diagnostic document markdown (from apply step).
        jd_text: Original job description text (for AI/tooling detection).
        candidate_name: Candidate name for intro.
        company_name: Target company.
        voice_pref: "female" or "male" for ElevenLabs voice.

    Returns:
        Dict with keys:
            - script (str): The narration text.
            - audio_b64 (str | None): Base64-encoded MP3, or None if unavailable.
            - audio_available (bool): Whether audio was generated.
            - word_count (int): Script word count.
            - error (str | None): Error message if something went partially wrong.
    """
    # Step 1: Condense diagnostic into slide data
    console.print("[bold]Pitch: condensing diagnostic for slides...[/bold]")
    slide_data = await condense_for_slides(markdown, jd_text=jd_text)

    if slide_data is None:
        console.print("  [yellow]Haiku condensation failed — using regex fallback[/yellow]")
        slide_data = fallback_slide_extraction(markdown)

    if not slide_data or not slide_data.get("actions"):
        raise ValueError("Could not extract slide data from diagnostic — markdown may be empty or malformed")

    # Step 2: Generate script from slide data
    console.print("[bold]Pitch: generating script...[/bold]")
    script = await generate_pitch_script(slide_data, candidate_name, company_name)

    if not script or len(script.strip()) < 50:
        raise ValueError("Script generation returned empty or unusable text")

    word_count = len(script.split())

    # Step 3: Generate audio (if ElevenLabs key is available)
    audio_b64 = None
    audio_available = False
    audio_error = None

    elevenlabs_key = os.environ.get("ELEVENLABS_API_KEY")
    if not elevenlabs_key:
        console.print("  [yellow]ELEVENLABS_API_KEY not set — returning script only[/yellow]")
        audio_error = "Audio unavailable: ElevenLabs API key not configured. Script is available below."
    else:
        try:
            console.print("[bold]Pitch: generating audio...[/bold]")
            audio_bytes = await generate_audio(script, voice_pref)
            audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
            audio_available = True
            console.print(f"  [green]Audio generated: {len(audio_bytes):,} bytes[/green]")
        except Exception as exc:
            console.print(f"  [red]Audio generation failed: {exc}[/red]")
            audio_error = f"Audio generation failed: {str(exc)}. Script is available below."

    return {
        "script": script,
        "audio_b64": audio_b64,
        "audio_available": audio_available,
        "word_count": word_count,
        "error": audio_error,
    }
