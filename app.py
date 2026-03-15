"""Web frontend for the Job Case Study Generator."""

import json
import asyncio

from dotenv import load_dotenv
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.responses import StreamingResponse

from research import research_all
from analyzer import build_context
from decomposer import decompose_jd
from generator import generate_case_study, generate_case_study_streaming, score_case_quality
from applier import generate_application_streaming
from deck import generate_deck_pdf, generate_slide_deck_pdf
from video import (create_video_job, get_video_job, run_video_pipeline,
                   list_elevenlabs_voices)
from pitch import create_pitch_job, get_pitch_job, run_pitch_pipeline

load_dotenv(override=True)

app = FastAPI()
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/generate")
async def generate(request: Request):
    """Non-streaming endpoint (kept for CLI/API consumers)."""
    try:
        body = await request.json()
        company_name = body.get("company_name", "").strip()
        job_title = body.get("job_title", "").strip()
        jd_text = body.get("jd_text", "").strip()
        domain = body.get("domain", "").strip()

        if not company_name or not jd_text:
            return JSONResponse(
                {"error": "Company name and job description are required."},
                status_code=400,
            )

        if not job_title:
            job_title = "Growth Role"

        if not domain:
            domain = company_name.lower().replace(" ", "") + ".com"

        job_data = {
            "url": "",
            "html": "",
            "full_text": jd_text,
            "page_title": "",
            "job_title": job_title,
            "company_name": company_name,
            "domain": domain,
        }

        # Decompose JD into structured profile + requirements
        company_profile, requirements_map = await decompose_jd(jd_text, company_name)

        # Override job_title if decomposer extracted a better one
        if company_profile.get("role_title") and company_profile["role_title"] != "Growth Role":
            job_data["job_title"] = company_profile["role_title"]

        research_data = await research_all(company_name, domain, company_profile)
        context = build_context(job_data, research_data, company_profile, requirements_map)
        case_study, diagnosis = await generate_case_study(context)

        # Quality scoring (non-blocking best-effort)
        quality = await score_case_quality(case_study, company_name)

        return JSONResponse({
            "markdown": case_study,
            "quality": quality,
            "context": {
                "company_name": context["company_name"],
                "job_title": context["job_title"],
                "seniority": context["seniority"],
                "business_model": context["business_model"],
                "growth_stage": context["growth_stage"],
                "funding_stage": context.get("funding_stage", "unknown"),
                "industry": context.get("industry", "unknown"),
                "competitors": context.get("competitors", [])[:3],
                "marketing_channels": context.get("marketing_channels", [])[:5],
                "tools_required": (requirements_map or {}).get("tools_required", [])[:6],
                "coverage_gaps": len(context.get("coverage_gaps", [])),
            },
        })
    except Exception as exc:
        return JSONResponse(
            {"error": f"Generation failed: {str(exc)}"},
            status_code=500,
        )


@app.post("/generate-stream")
async def generate_stream(request: Request):
    """Streaming endpoint using Server-Sent Events."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid request body."}, status_code=400)

    company_name = body.get("company_name", "").strip()
    job_title = body.get("job_title", "").strip()
    jd_text = body.get("jd_text", "").strip()
    domain = body.get("domain", "").strip()

    if not company_name or not jd_text:
        return JSONResponse(
            {"error": "Company name and job description are required."},
            status_code=400,
        )

    if not job_title:
        job_title = "Growth Role"

    if not domain:
        domain = company_name.lower().replace(" ", "") + ".com"

    job_data = {
        "url": "",
        "html": "",
        "full_text": jd_text,
        "page_title": "",
        "job_title": job_title,
        "company_name": company_name,
        "domain": domain,
    }

    async def event_stream():
        try:
            # Phase 0: Decompose JD
            yield f"data: {json.dumps({'stage': 'decomposing'})}\n\n"
            company_profile, requirements_map = await decompose_jd(jd_text, company_name)

            # Override job_title if decomposer extracted a better one
            if company_profile.get("role_title") and company_profile["role_title"] != "Growth Role":
                job_data["job_title"] = company_profile["role_title"]

            # Phase 1: Research (guided by company profile)
            yield f"data: {json.dumps({'stage': 'researching'})}\n\n"
            research_data = await research_all(company_name, domain, company_profile)

            # Phase 2: Analysis + coverage validation
            yield f"data: {json.dumps({'stage': 'analyzing'})}\n\n"
            context = build_context(job_data, research_data, company_profile, requirements_map)

            # Send context metadata
            ctx_meta = {
                "company_name": context["company_name"],
                "job_title": context["job_title"],
                "seniority": context["seniority"],
                "business_model": context["business_model"],
                "growth_stage": context["growth_stage"],
                "funding_stage": context.get("funding_stage", "unknown"),
                "industry": context.get("industry", "unknown"),
                "competitors": context.get("competitors", [])[:3],
                "marketing_channels": context.get("marketing_channels", [])[:5],
                "tools_required": requirements_map.get("tools_required", [])[:6],
                "coverage_gaps": len(context.get("coverage_gaps", [])),
            }
            yield f"data: {json.dumps({'context': ctx_meta})}\n\n"

            # Phase 3+4: Diagnosis + streaming case generation
            full_text = ""
            async for event in generate_case_study_streaming(context):
                yield f"data: {json.dumps(event)}\n\n"
                if "chunk" in event:
                    full_text += event["chunk"]

            # Phase 5: Quality scoring (fast Haiku call)
            try:
                quality = await score_case_quality(full_text, company_name)
                yield f"data: {json.dumps({'quality': quality})}\n\n"
            except Exception:
                pass

        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/apply-stream")
async def apply_stream(request: Request):
    """Streaming endpoint to generate a personalized application document."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid request body."}, status_code=400)

    case_study = body.get("case_study", "").strip()
    jd_text = body.get("jd_text", "").strip()
    company_name = body.get("company_name", "").strip()
    job_title = body.get("job_title", "").strip()
    cv_text = body.get("cv_text", "").strip()

    if not case_study or not cv_text:
        return JSONResponse(
            {"error": "Case study and CV are required."},
            status_code=400,
        )

    async def event_stream():
        try:
            async for event in generate_application_streaming(
                case_study, jd_text, company_name, job_title, cv_text
            ):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/export-deck")
async def export_deck(request: Request):
    """Generate a presentation-style PDF deck from the diagnostic document."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid request body."}, status_code=400)

    markdown = body.get("markdown", "").strip()
    profile = body.get("profile") or {}
    company_name = body.get("company_name", "").strip()
    job_title = body.get("job_title", "").strip()
    mapping_quality = body.get("mapping_quality") or {}
    deck_format = body.get("format", "slides")
    jd_text = body.get("jd_text", "").strip()

    if not markdown:
        return JSONResponse(
            {"error": "Diagnostic markdown is required."},
            status_code=400,
        )

    try:
        if deck_format == "slides":
            pdf_bytes = await generate_slide_deck_pdf(
                markdown=markdown,
                profile=profile,
                company_name=company_name,
                job_title=job_title,
                mapping_quality=mapping_quality,
                jd_text=jd_text,
            )
            slug = (company_name or "company").lower().replace(" ", "-")[:30]
            filename = f"slides-{slug}.pdf"
        else:
            pdf_bytes = generate_deck_pdf(
                markdown=markdown,
                profile=profile,
                company_name=company_name,
                job_title=job_title,
                mapping_quality=mapping_quality,
            )
            slug = (company_name or "company").lower().replace(" ", "-")[:30]
            filename = f"diagnostic-{slug}.pdf"

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as exc:
        return JSONResponse(
            {"error": f"PDF generation failed: {str(exc)}"},
            status_code=500,
        )


@app.post("/generate-pitch")
async def generate_pitch_endpoint(request: Request):
    """Start an async pitch generation pipeline (condense → script → audio)."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid request body."}, status_code=400)

    markdown = body.get("markdown", "").strip()
    candidate_name = body.get("candidate_name", "").strip()
    company_name = body.get("company_name", "").strip()
    jd_text = body.get("jd_text", "").strip()
    voice_pref = body.get("voice_pref", "female").strip()

    if not markdown:
        return JSONResponse(
            {"error": "Diagnostic markdown is required."},
            status_code=400,
        )

    job_id = create_pitch_job()

    asyncio.create_task(
        run_pitch_pipeline(job_id, markdown, jd_text,
                           candidate_name, company_name, voice_pref)
    )

    return JSONResponse({"job_id": job_id})


@app.get("/pitch-status/{job_id}")
async def pitch_status(job_id: str):
    """Poll for pitch generation status."""
    job = get_pitch_job(job_id)
    if not job:
        return JSONResponse({"error": "Job not found."}, status_code=404)

    return JSONResponse({
        "status": job["status"],
        "script": job.get("script"),
        "audio_b64": job.get("audio_b64"),
        "audio_available": job.get("audio_available", False),
        "word_count": job.get("word_count", 0),
        "error": job.get("error"),
    })


@app.get("/elevenlabs-voices")
async def elevenlabs_voices():
    """Fetch available ElevenLabs voices."""
    voices = await list_elevenlabs_voices()
    return JSONResponse(voices)


@app.post("/generate-video")
async def generate_video(request: Request):
    """Start Loom-style video generation (slides + voiceover + optional photo)."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid request body."}, status_code=400)

    markdown = body.get("markdown", "").strip()
    profile = body.get("profile") or {}
    company_name = body.get("company_name", "").strip()
    job_title = body.get("job_title", "").strip()
    jd_text = body.get("jd_text", "").strip()
    mapping_quality = body.get("mapping_quality") or {}
    voice_pref = body.get("voice_pref", "female").strip()
    pitch_audio_b64 = body.get("pitch_audio_b64", "").strip()
    photo_b64 = body.get("photo_b64", "").strip()

    if not markdown:
        return JSONResponse({"error": "Diagnostic markdown is required."}, status_code=400)

    import base64

    # Decode pitch audio if provided (reuse instead of calling ElevenLabs again)
    existing_audio = None
    if pitch_audio_b64:
        try:
            existing_audio = base64.b64decode(pitch_audio_b64)
        except Exception:
            pass

    # Decode photo if provided (optional, for corner bubble)
    photo_bytes = None
    if photo_b64:
        try:
            photo_bytes = base64.b64decode(photo_b64)
        except Exception:
            pass

    job_id = create_video_job()

    asyncio.create_task(
        run_video_pipeline(job_id, markdown, profile, company_name,
                           job_title, jd_text, mapping_quality,
                           voice_pref, existing_audio, photo_bytes)
    )

    return JSONResponse({"job_id": job_id})


@app.get("/video-status/{job_id}")
async def video_status(job_id: str):
    """Poll for video generation status."""
    job = get_video_job(job_id)
    if not job:
        return JSONResponse({"error": "Job not found."}, status_code=404)

    video_url = f"/video-file/{job_id}" if job["status"] == "ready" else None

    return JSONResponse({
        "status": job["status"],
        "video_url": video_url,
        "error": job.get("error"),
    })


@app.get("/video-file/{job_id}")
async def video_file(job_id: str):
    """Serve the generated MP4 video."""
    job = get_video_job(job_id)
    if not job or not job.get("video_bytes"):
        return JSONResponse({"error": "Video not found."}, status_code=404)
    return Response(
        content=job["video_bytes"],
        media_type="video/mp4",
        headers={"Content-Disposition": f'inline; filename="video-{job_id}.mp4"'},
    )
