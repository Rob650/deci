import os
import uuid
import asyncio
import datetime
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import uvicorn

from scraper import scrape
from analyzer import analyze, compare_projects
from data_sources import collect_all_sources
from report_generator import generate_pdf_report
from database import (
    init_db,
    save_project,
    get_project,
    get_all_projects,
    get_similar_projects,
    search_projects,
)

app = FastAPI(title="Deci", description="Crypto/Tech Project Intelligence Reports")

templates = Jinja2Templates(directory="templates")

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# In-memory report store — keyed by UUID, persists for the lifetime of the process
report_store: dict[str, dict] = {}


@app.on_event("startup")
async def startup():
    try:
        await init_db()
    except Exception as e:
        # Log but don't crash — app runs without DB persistence
        print(f"WARNING: database init failed: {e}", flush=True)


# ── Models ────────────────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    url: str


class CompareRequest(BaseModel):
    project_ids: list[int]


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


async def _do_analyze(url: str) -> dict:
    scraped = await scrape(url)
    if scraped.get("error") and scraped["pages_scraped"] == 0:
        raise HTTPException(status_code=422, detail=scraped["error"])

    sources = await collect_all_sources(scraped)

    try:
        report = await asyncio.to_thread(analyze, scraped, sources)
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")

    return scraped, sources, report


@app.post("/api/analyze")
async def analyze_url(body: AnalyzeRequest):
    url = body.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required.")

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        scraped, sources, report = await asyncio.wait_for(_do_analyze(url), timeout=90)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="Analysis timed out — the site may be too slow or complex. Try a simpler URL.",
        )

    # Store for PDF download
    report_id = str(uuid.uuid4())
    report_store[report_id] = {
        "url": url,
        "report": report,
        "sources": sources,
        "pages_scraped": scraped["pages_scraped"],
        "created_at": datetime.datetime.utcnow().isoformat(),
    }

    # Persist to DB
    meta = sources.get("_meta", {})
    project_name = meta.get("project_name") or url
    sector = report.get("sector", "Other")
    tags = report.get("tags", "")
    scores = report.get("scores", {})
    summary = report.get("executive_summary", "")
    market_data = sources.get("coingecko", {})

    db_id = await save_project(
        url=url,
        name=project_name,
        sector=sector,
        tags=tags,
        scores=scores,
        analysis=report,
        market_data=market_data,
        summary=summary,
    )

    # Fetch similar projects for competitive context
    similar_raw = await get_similar_projects(sector, tags, exclude_url=url)
    similar_projects = [
        {
            "id": p["id"],
            "url": p["url"],
            "name": p["name"],
            "sector": p["sector"],
            "tags": p["tags"],
            "scores": p["scores"],
            "summary": p["summary"],
            "updated_at": p["updated_at"],
        }
        for p in similar_raw
    ]

    # Build condensed source summary for the frontend
    source_summary: dict = {}
    for key in ["github", "coingecko", "defillama", "news", "twitter"]:
        s = sources.get(key, {})
        entry: dict = {"available": s.get("available", False)}
        if key in ("news", "twitter") and s.get("available"):
            entry["count"] = len(s.get("results", []))
        source_summary[key] = entry

    return JSONResponse({
        "url": url,
        "pages_scraped": scraped["pages_scraped"],
        "report_id": report_id,
        "db_id": db_id,
        "sources": source_summary,
        "report": report,
        "similar_projects": similar_projects,
    })


@app.get("/api/report/{report_id}")
async def download_report(report_id: str):
    data = report_store.get(report_id)
    if not data:
        raise HTTPException(status_code=404, detail="Report not found or expired.")

    try:
        pdf_bytes = await asyncio.to_thread(
            generate_pdf_report,
            data["url"],
            data["report"],
            data["sources"],
            data["pages_scraped"],
            data["created_at"],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {str(e)}")

    project_name = data["sources"].get("_meta", {}).get("project_name", "report")
    safe_name = (
        "".join(c for c in project_name if c.isalnum() or c in "- ")
        .strip()
        .replace(" ", "-")
        .lower()
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="deci-{safe_name}.pdf"'},
    )


@app.get("/api/projects")
async def list_projects():
    projects = await get_all_projects()
    # Return lightweight list (no full analysis blob)
    return JSONResponse([
        {
            "id": p["id"],
            "url": p["url"],
            "name": p["name"],
            "sector": p["sector"],
            "tags": p["tags"],
            "scores": p["scores"],
            "summary": p["summary"],
            "analyzed_at": p["analyzed_at"],
            "updated_at": p["updated_at"],
        }
        for p in projects
    ])


@app.get("/api/project/{project_id}")
async def get_project_detail(project_id: int):
    project = await get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")
    return JSONResponse(project)


@app.post("/api/compare")
async def compare(body: CompareRequest):
    if len(body.project_ids) < 2:
        raise HTTPException(status_code=400, detail="At least 2 project IDs required.")
    if len(body.project_ids) > 5:
        raise HTTPException(status_code=400, detail="Maximum 5 projects can be compared.")

    projects = []
    for pid in body.project_ids:
        p = await get_project(pid)
        if p:
            projects.append(p)

    if len(projects) < 2:
        raise HTTPException(status_code=404, detail="Could not find enough projects.")

    try:
        comparison = await asyncio.to_thread(compare_projects, projects)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Comparison failed: {str(e)}")

    return JSONResponse({
        "comparison": comparison,
        "projects": [
            {"id": p["id"], "name": p["name"], "sector": p["sector"], "scores": p["scores"]}
            for p in projects
        ],
    })


@app.get("/api/search")
async def search(q: str = ""):
    if not q.strip():
        return JSONResponse([])
    results = await search_projects(q.strip())
    return JSONResponse([
        {
            "id": p["id"],
            "url": p["url"],
            "name": p["name"],
            "sector": p["sector"],
            "tags": p["tags"],
            "scores": p["scores"],
            "summary": p["summary"],
            "updated_at": p["updated_at"],
        }
        for p in results
    ])


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
