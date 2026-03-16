"""Loom-style video: slide deck + pitch voiceover + optional photo bubble."""

import io
import os
import json
import uuid
import asyncio
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timedelta

import httpx
import anthropic
from rich.console import Console

console = Console()

HAIKU_MODEL = "claude-haiku-4-5-20251001"

# ElevenLabs pre-made voices (fallback when voice_pref is "female"/"male")
VOICES = {
    "female": "21m00Tcm4TlvDq8ikWAM",  # Rachel
    "male": "TxGEqnHWrfWFTfGW9XjX",    # Josh
}

ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1"

# ---------------------------------------------------------------------------
# Caches for API list fetchers (1-hour TTL)
# ---------------------------------------------------------------------------

_voices_cache: dict = {"data": None, "fetched_at": 0}
_CACHE_TTL = 3600  # 1 hour


# ---------------------------------------------------------------------------
# ElevenLabs voice list (used by pitch + video voice selectors)
# ---------------------------------------------------------------------------

async def list_elevenlabs_voices() -> list[dict]:
    """Fetch available voices from ElevenLabs API. Cached for 1 hour."""
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        return []

    if _voices_cache["data"] is not None and (time.time() - _voices_cache["fetched_at"]) < _CACHE_TTL:
        return _voices_cache["data"]

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{ELEVENLABS_API_URL}/voices",
                headers={"xi-api-key": api_key},
            )
            if resp.status_code != 200:
                console.print(f"  [yellow]ElevenLabs voices fetch failed: {resp.status_code}[/yellow]")
                return _voices_cache["data"] or []

            voices_raw = resp.json().get("voices", [])
            voices = []
            for v in voices_raw:
                labels = v.get("labels", {})
                voices.append({
                    "voice_id": v["voice_id"],
                    "name": v.get("name", "Unknown"),
                    "category": v.get("category", ""),
                    "gender": labels.get("gender", ""),
                    "accent": labels.get("accent", ""),
                    "preview_url": v.get("preview_url", ""),
                })

            _voices_cache["data"] = voices
            _voices_cache["fetched_at"] = time.time()
            return voices

    except Exception as exc:
        console.print(f"  [yellow]ElevenLabs voices error: {exc}[/yellow]")
        return _voices_cache["data"] or []


# ---------------------------------------------------------------------------
# In-memory job store
# ---------------------------------------------------------------------------

video_jobs: dict[str, dict] = {}


def _cleanup_old_jobs():
    """Remove jobs older than 1 hour."""
    cutoff = datetime.utcnow() - timedelta(hours=1)
    expired = [
        jid for jid, job in video_jobs.items()
        if datetime.fromisoformat(job["created"]) < cutoff
    ]
    for jid in expired:
        del video_jobs[jid]


def create_video_job() -> str:
    """Create a new video job entry and return its ID."""
    _cleanup_old_jobs()
    job_id = str(uuid.uuid4())[:8]
    video_jobs[job_id] = {
        "status": "pending",
        "created": datetime.utcnow().isoformat(),
        "video_bytes": None,
        "error": None,
    }
    return job_id


def get_video_job(job_id: str) -> dict | None:
    """Get a video job by ID."""
    return video_jobs.get(job_id)


# ---------------------------------------------------------------------------
# Script generation (Haiku) — fallback when no pitch audio exists
# ---------------------------------------------------------------------------

SCRIPT_PROMPT = """Condense this diagnostic document into a 2-minute spoken script (~300 words).

DOCUMENT:
{markdown}

CANDIDATE NAME: {candidate_name}
COMPANY: {company_name}

RULES:
- First person, conversational, as if the candidate is presenting to the hiring manager
- Open with: "Hi, I'm {candidate_name}. I spent some time looking at {company_name}'s situation and put together a diagnosis."
- Cover: (1) the 1-2 biggest problems you identified, (2) your strongest solution with evidence, (3) one specific thing from your experience that proves you can do this
- Close with: "I'd love to walk through this in more detail. Happy to go deeper on any of it."
- NO jargon, NO bullet-point reading, NO "as you can see"
- Speak naturally — contractions, short sentences, occasional pauses marked with "..."
- Total: 280-320 words (strictly enforced)

Respond with ONLY the script text. No headers, no stage directions, no meta-commentary."""


async def generate_script(markdown: str, candidate_name: str,
                          company_name: str) -> str:
    """Generate a 2-minute spoken script from the diagnostic document."""
    client = anthropic.AsyncAnthropic()
    message = await client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=1000,
        messages=[{"role": "user", "content": SCRIPT_PROMPT.format(
            markdown=markdown[:8000],
            candidate_name=candidate_name or "the candidate",
            company_name=company_name or "the company",
        )}],
    )
    return message.content[0].text.strip()


# ---------------------------------------------------------------------------
# Audio generation (ElevenLabs TTS)
# ---------------------------------------------------------------------------

async def generate_audio(script: str, voice_pref: str = "female") -> bytes:
    """Generate audio from script using ElevenLabs TTS API."""
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise ValueError("ELEVENLABS_API_KEY not set")

    # If voice_pref looks like a voice_id (long string), use directly.
    # Otherwise fall back to named presets for backward compat with pitch.py.
    voice_id = voice_pref if len(voice_pref) > 10 else VOICES.get(voice_pref, VOICES["female"])

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{ELEVENLABS_API_URL}/text-to-speech/{voice_id}",
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
            },
            json={
                "text": script,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75,
                },
            },
        )
        if response.status_code != 200:
            raise ValueError(f"ElevenLabs API error: {response.status_code} — {response.text[:200]}")
        return response.content


# ---------------------------------------------------------------------------
# Loom-style video generation (local: PyMuPDF + Pillow + ffmpeg)
# ---------------------------------------------------------------------------

def _get_audio_duration(audio_path: str) -> float:
    """Get audio duration in seconds using ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", audio_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise ValueError(f"ffprobe failed: {result.stderr[:200]}")
    info = json.loads(result.stdout)
    return float(info["format"]["duration"])


def _make_circular_photo(photo_bytes: bytes, size: int = 120) -> "Image":
    """Crop a photo into a circle with a white border, return as RGBA PIL Image."""
    from PIL import Image, ImageDraw

    img = Image.open(io.BytesIO(photo_bytes)).convert("RGBA")
    # Crop to square
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
    img = img.resize((size, size), Image.LANCZOS)

    # Create circular mask
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, size, size), fill=255)

    # Apply mask
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(img, (0, 0), mask)

    # White border ring
    border = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    border_draw = ImageDraw.Draw(border)
    border_draw.ellipse((0, 0, size - 1, size - 1), outline=(255, 255, 255, 220), width=3)
    result = Image.alpha_composite(result, border)

    return result


def generate_loom_video(
    pdf_bytes: bytes,
    audio_bytes: bytes,
    photo_bytes: bytes | None = None,
) -> bytes:
    """Generate a Loom-style video: slides + voiceover + optional photo bubble.

    Returns MP4 bytes. Runs synchronously (called via asyncio.to_thread).
    """
    import fitz  # PyMuPDF
    from PIL import Image

    tmpdir = tempfile.mkdtemp(prefix="loom_video_")

    try:
        # 1. PDF → slide images
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        num_slides = len(doc)
        console.print(f"  [dim]PDF has {num_slides} slides[/dim]")

        # Prepare circular photo overlay if provided
        photo_overlay = None
        if photo_bytes:
            try:
                photo_overlay = _make_circular_photo(photo_bytes, size=120)
            except Exception as exc:
                console.print(f"  [yellow]Photo overlay failed: {exc}[/yellow]")

        for i, page in enumerate(doc):
            # Render at 150 DPI → ~1280×720 for our 254mm×143mm pages
            pix = page.get_pixmap(dpi=150)
            slide_img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGBA")

            # Overlay photo bubble in bottom-left
            if photo_overlay:
                margin = 24
                x = margin
                y = slide_img.height - photo_overlay.height - margin
                slide_img.paste(photo_overlay, (x, y), photo_overlay)

            # Save as RGB PNG (ffmpeg doesn't like RGBA)
            slide_rgb = slide_img.convert("RGB")
            slide_rgb.save(os.path.join(tmpdir, f"slide_{i:02d}.png"))

        doc.close()

        # 2. Write audio to temp file
        audio_path = os.path.join(tmpdir, "audio.mp3")
        with open(audio_path, "wb") as f:
            f.write(audio_bytes)

        # 3. Get audio duration and calculate per-slide timing
        total_duration = _get_audio_duration(audio_path)
        slide_duration = total_duration / num_slides
        console.print(f"  [dim]Audio: {total_duration:.1f}s, {slide_duration:.1f}s per slide[/dim]")

        # 4. Write ffmpeg concat file
        concat_path = os.path.join(tmpdir, "concat.txt")
        with open(concat_path, "w") as f:
            for i in range(num_slides):
                slide_path = os.path.join(tmpdir, f"slide_{i:02d}.png")
                f.write(f"file '{slide_path}'\n")
                f.write(f"duration {slide_duration:.3f}\n")
            # Repeat last slide (ffmpeg concat needs it for last duration)
            last_slide = os.path.join(tmpdir, f"slide_{num_slides - 1:02d}.png")
            f.write(f"file '{last_slide}'\n")

        # 5. Run ffmpeg
        output_path = os.path.join(tmpdir, "output.mp4")
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_path,
            "-i", audio_path,
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-r", "24", "-preset", "fast",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            "-movflags", "+faststart",
            output_path,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise ValueError(f"ffmpeg failed: with return code {result.returncode} {result.stderr[-500:]}")

        # 6. Read output
        with open(output_path, "rb") as f:
            mp4_bytes = f.read()

        console.print(f"  [dim]Video generated: {len(mp4_bytes) / 1024 / 1024:.1f} MB[/dim]")
        return mp4_bytes

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Full pipeline (runs as background task)
# ---------------------------------------------------------------------------

async def run_video_pipeline(
    job_id: str, markdown: str, profile: dict,
    company_name: str, job_title: str, jd_text: str,
    mapping_quality: dict = None,
    voice_pref: str = "female",
    existing_audio: bytes | None = None,
    photo_bytes: bytes | None = None,
):
    """Run the Loom-style video pipeline: slides PDF → video composition.

    If existing_audio is provided (e.g. from pitch), skips ElevenLabs TTS.
    """
    job = video_jobs.get(job_id)
    if not job:
        return

    try:
        # Step 1: Generate slide deck PDF
        job["status"] = "slides"
        from deck import generate_slide_deck_pdf
        pdf_bytes = await generate_slide_deck_pdf(
            markdown=markdown,
            profile=profile,
            company_name=company_name,
            job_title=job_title,
            mapping_quality=mapping_quality or {},
            jd_text=jd_text,
        )
        console.print(f"  [dim]Slide PDF generated ({len(pdf_bytes)} bytes)[/dim]")

        # Step 2: Audio — reuse pitch audio if available, otherwise generate
        if existing_audio:
            audio_bytes = existing_audio
            console.print(f"  [dim]Reusing pitch audio ({len(audio_bytes)} bytes)[/dim]")
        else:
            job["status"] = "audio"
            candidate_name = profile.get("nombre", "")
            script = await generate_script(markdown, candidate_name, company_name)
            audio_bytes = await generate_audio(script, voice_pref)
            console.print(f"  [dim]Audio generated ({len(audio_bytes)} bytes)[/dim]")

        # Step 3: Compose video (CPU-bound, run in thread)
        job["status"] = "video"
        mp4_bytes = await asyncio.to_thread(
            generate_loom_video, pdf_bytes, audio_bytes, photo_bytes
        )

        job["video_bytes"] = mp4_bytes
        job["status"] = "ready"
        console.print(f"  [bold green]Loom video ready ({len(mp4_bytes) / 1024 / 1024:.1f} MB)[/bold green]")

    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        console.print(f"  [red]Video pipeline error: {e}[/red]")
