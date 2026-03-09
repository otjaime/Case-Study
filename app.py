"""Web frontend for the Job Case Study Generator."""

import asyncio
import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from scraper import (
    scrape_company_website,
    scrape_meta_ads,
    scrape_similarweb,
    scrape_linkedin_company,
)
from analyzer import build_context
from generator import generate_case_study

load_dotenv()

app = FastAPI()
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/generate")
async def generate(request: Request):
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

        # Build job_data dict (same shape as scrape_job_posting returns)
        job_data = {
            "url": "",
            "html": "",
            "full_text": jd_text,
            "page_title": "",
            "job_title": job_title,
            "company_name": company_name,
            "domain": domain,
        }

        # Run company research scrapers in parallel
        company_data, ads_data, traffic, linkedin_data = await asyncio.gather(
            scrape_company_website(domain),
            scrape_meta_ads(company_name),
            scrape_similarweb(domain),
            scrape_linkedin_company(company_name),
        )

        context = build_context(job_data, company_data, ads_data, traffic, linkedin_data)
        case_study = await generate_case_study(context)

        return JSONResponse({
            "markdown": case_study,
            "context": {
                "company_name": context["company_name"],
                "job_title": context["job_title"],
                "seniority": context["seniority"],
                "business_model": context["business_model"],
                "growth_stage": context["growth_stage"],
                "paid_ads_activity": context["paid_ads_activity"],
            },
        })
    except Exception as exc:
        return JSONResponse(
            {"error": f"Generation failed: {str(exc)}"},
            status_code=500,
        )
