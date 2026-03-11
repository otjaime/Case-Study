"""Web frontend for the Job Case Study Generator."""

import json

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import StreamingResponse

from research import research_all
from analyzer import build_context
from decomposer import decompose_jd
from generator import generate_case_study, generate_case_study_streaming, score_case_quality
from applier import generate_application_streaming

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
