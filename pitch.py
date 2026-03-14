"""Audio pitch generator: slide-aligned narration for the PDF deck.

Pipeline: condense_for_slides() -> generate_pitch_script() -> generate_audio()
Runs as async background task with polling (same pattern as video.py).
Cost: ~$0.02 per pitch (Haiku ~$0.001 + ElevenLabs ~$0.02).
"""

import os
import json
import uuid
import base64
from datetime import datetime, timedelta

import anthropic
from rich.console import Console

from deck import condense_for_slides, fallback_slide_extraction
from video import generate_audio

console = Console()

HAIKU_MODEL = "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# In-memory job store (same pattern as video.py)
# ---------------------------------------------------------------------------

pitch_jobs: dict[str, dict] = {}


def _cleanup_old_jobs():
    """Remove jobs older than 1 hour."""
    cutoff = datetime.utcnow() - timedelta(hours=1)
    expired = [
        jid for jid, job in pitch_jobs.items()
        if datetime.fromisoformat(job["created"]) < cutoff
    ]
    for jid in expired:
        del pitch_jobs[jid]


def create_pitch_job() -> str:
    """Create a new pitch job entry and return its ID."""
    _cleanup_old_jobs()
    job_id = str(uuid.uuid4())[:8]
    pitch_jobs[job_id] = {
        "status": "pending",
        "created": datetime.utcnow().isoformat(),
        "script": None,
        "audio_b64": None,
        "audio_available": False,
        "word_count": 0,
        "error": None,
    }
    return job_id


def get_pitch_job(job_id: str) -> dict | None:
    """Get a pitch job by ID."""
    return pitch_jobs.get(job_id)


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
    """Generate a per-slide narration script from condensed slide data."""
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
# Async pipeline (background task)
# ---------------------------------------------------------------------------

async def run_pitch_pipeline(job_id: str, markdown: str, jd_text: str,
                             candidate_name: str, company_name: str,
                             voice_pref: str = "female"):
    """Run the full pitch pipeline as a background task.

    Updates job status as it progresses:
        pending -> condensing -> scripting -> audio -> ready (or error)
    """
    job = pitch_jobs.get(job_id)
    if not job:
        return

    try:
        # Step 1: Condense diagnostic into slide data
        job["status"] = "condensing"
        console.print("[bold]Pitch: condensing diagnostic for slides...[/bold]")
        slide_data = await condense_for_slides(markdown, jd_text=jd_text)

        if slide_data is None:
            console.print("  [yellow]Haiku condensation failed — using regex fallback[/yellow]")
            slide_data = fallback_slide_extraction(markdown)

        if not slide_data or not slide_data.get("actions"):
            raise ValueError("Could not extract slide data from diagnostic")

        # Step 2: Generate script from slide data
        job["status"] = "scripting"
        console.print("[bold]Pitch: generating script...[/bold]")
        script = await generate_pitch_script(slide_data, candidate_name, company_name)

        if not script or len(script.strip()) < 50:
            raise ValueError("Script generation returned empty or unusable text")

        job["script"] = script
        job["word_count"] = len(script.split())

        # Step 3: Generate audio (if ElevenLabs key is available)
        elevenlabs_key = os.environ.get("ELEVENLABS_API_KEY")
        if not elevenlabs_key:
            console.print("  [yellow]ELEVENLABS_API_KEY not set — script only[/yellow]")
            job["status"] = "ready"
            job["error"] = "Audio unavailable: ElevenLabs API key not configured."
            return

        job["status"] = "audio"
        console.print("[bold]Pitch: generating audio...[/bold]")
        audio_bytes = await generate_audio(script, voice_pref)
        job["audio_b64"] = base64.b64encode(audio_bytes).decode("ascii")
        job["audio_available"] = True
        console.print(f"  [green]Audio generated: {len(audio_bytes):,} bytes[/green]")

        job["status"] = "ready"

    except Exception as exc:
        console.print(f"  [red]Pitch pipeline error: {exc}[/red]")
        job["status"] = "error"
        job["error"] = str(exc)
