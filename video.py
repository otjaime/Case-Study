"""AI video explainer: script generation, TTS, and lip-sync video."""

import os
import uuid
import asyncio
from datetime import datetime, timedelta

import httpx
import anthropic
from rich.console import Console

console = Console()

HAIKU_MODEL = "claude-haiku-4-5-20251001"

# ElevenLabs pre-made voices
VOICES = {
    "female": "21m00Tcm4TlvDq8ikWAM",  # Rachel
    "male": "TxGEqnHWrfWFTfGW9XjX",    # Josh
}

ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1"
HEYGEN_API_URL = "https://api.heygen.com"

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
        "script": None,
        "video_url": None,
        "error": None,
    }
    return job_id


def get_video_job(job_id: str) -> dict | None:
    """Get a video job by ID."""
    return video_jobs.get(job_id)


# ---------------------------------------------------------------------------
# Step 1: Script generation (Haiku)
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
# Step 2: Audio generation (ElevenLabs TTS)
# ---------------------------------------------------------------------------

async def generate_audio(script: str, voice_pref: str = "female") -> bytes:
    """Generate audio from script using ElevenLabs TTS API."""
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise ValueError("ELEVENLABS_API_KEY not set")

    voice_id = VOICES.get(voice_pref, VOICES["female"])

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
# Step 3: Lip-sync video generation (HeyGen)
# ---------------------------------------------------------------------------

async def generate_video_heygen(audio_bytes: bytes, photo_bytes: bytes) -> str:
    """Submit a lip-sync video generation job to HeyGen. Returns video_id."""
    import base64

    api_key = os.environ.get("HEYGEN_API_KEY")
    if not api_key:
        raise ValueError("HEYGEN_API_KEY not set")

    audio_b64 = base64.b64encode(audio_bytes).decode()
    photo_b64 = base64.b64encode(photo_bytes).decode()

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{HEYGEN_API_URL}/v2/video/generate",
            headers={
                "x-api-key": api_key,
                "Content-Type": "application/json",
            },
            json={
                "video_inputs": [{
                    "character": {
                        "type": "photo",
                        "photo_url": f"data:image/jpeg;base64,{photo_b64}",
                    },
                    "voice": {
                        "type": "audio",
                        "audio_url": f"data:audio/mpeg;base64,{audio_b64}",
                    },
                }],
                "dimension": {"width": 1280, "height": 720},
            },
        )
        if response.status_code != 200:
            raise ValueError(f"HeyGen API error: {response.status_code} — {response.text[:200]}")

        data = response.json()
        video_id = data.get("data", {}).get("video_id")
        if not video_id:
            raise ValueError(f"HeyGen did not return video_id: {data}")
        return video_id


async def check_video_status_heygen(video_id: str) -> dict:
    """Poll HeyGen for video generation status."""
    api_key = os.environ.get("HEYGEN_API_KEY")
    if not api_key:
        raise ValueError("HEYGEN_API_KEY not set")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{HEYGEN_API_URL}/v1/video_status.get",
            headers={"x-api-key": api_key},
            params={"video_id": video_id},
        )
        if response.status_code != 200:
            return {"status": "error", "error": f"Status check failed: {response.status_code}"}

        data = response.json().get("data", {})
        status = data.get("status", "unknown")

        if status == "completed":
            return {"status": "completed", "video_url": data.get("video_url", "")}
        elif status == "failed":
            return {"status": "failed", "error": data.get("error", "Unknown error")}
        else:
            return {"status": "processing"}


# ---------------------------------------------------------------------------
# Full pipeline (runs as background task)
# ---------------------------------------------------------------------------

async def run_video_pipeline(job_id: str, markdown: str, profile: dict,
                             company_name: str, photo_bytes: bytes,
                             voice_pref: str = "female"):
    """Run the full video pipeline: script → audio → video."""
    job = video_jobs.get(job_id)
    if not job:
        return

    try:
        # Step 1: Script
        job["status"] = "script"
        candidate_name = profile.get("nombre", "")
        script = await generate_script(markdown, candidate_name, company_name)
        job["script"] = script
        console.print(f"  [dim]Video script generated ({len(script.split())} words)[/dim]")

        # Step 2: Audio
        job["status"] = "audio"
        audio_bytes = await generate_audio(script, voice_pref)
        console.print(f"  [dim]Audio generated ({len(audio_bytes)} bytes)[/dim]")

        # Step 3: Video
        job["status"] = "video"
        video_id = await generate_video_heygen(audio_bytes, photo_bytes)
        console.print(f"  [dim]HeyGen job submitted: {video_id}[/dim]")

        # Poll for completion (max 5 minutes)
        for _ in range(60):
            await asyncio.sleep(5)
            result = await check_video_status_heygen(video_id)

            if result["status"] == "completed":
                job["video_url"] = result["video_url"]
                job["status"] = "ready"
                console.print(f"  [bold green]Video ready: {result['video_url'][:60]}...[/bold green]")
                return

            if result["status"] == "failed":
                raise ValueError(f"HeyGen generation failed: {result.get('error', 'unknown')}")

        raise ValueError("Video generation timed out (5 minutes)")

    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        console.print(f"  [red]Video pipeline error: {e}[/red]")
