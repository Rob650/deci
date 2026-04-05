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
from analyzer import analyze, compare_against_library, compare_projects
from data_sources import collect_all_sources
from report_generator import generate_pdf_report
from database import (
    init_db,
    save_project,
    get_project,
    get_all_projects,
    get_similar_projects,
    search_projects,
    save_virtuals_agent,
    get_virtuals_agent,
    get_virtuals_agent_by_virtuals_id,
    get_all_virtuals_agents,
)
from virtuals_scanner import scan_all_agents, analyze_virtuals_agent

app = FastAPI(title="Deci", description="Crypto/Tech Project Intelligence Reports")

templates = Jinja2Templates(directory="templates")

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# In-memory report store — keyed by UUID, persists for the lifetime of the process
report_store: dict[str, dict] = {}

# In-memory job store — keyed by job_id
# Each job: {"status": "running"|"complete"|"error", "step": str, "result": dict|None, "detail": str|None, "created_at": datetime}
job_store: dict[str, dict] = {}

JOB_TTL_MINUTES = 30


def _cleanup_old_jobs():
    """Remove jobs older than JOB_TTL_MINUTES. Called lazily on new job creation."""
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(minutes=JOB_TTL_MINUTES)
    stale = [jid for jid, j in job_store.items() if j["created_at"] < cutoff]
    for jid in stale:
        job_store.pop(jid, None)


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


async def _do_analyze(url: str, job: dict | None = None) -> tuple:
    def set_step(step: str):
        if job is not None:
            job["step"] = step

    set_step("scraping")
    scraped = await scrape(url)
    if scraped.get("error") and scraped["pages_scraped"] == 0:
        raise HTTPException(status_code=422, detail=scraped["error"])

    set_step("collecting_sources")
    sources = await collect_all_sources(scraped)

    set_step("analyzing")
    try:
        report = await asyncio.to_thread(analyze, scraped, sources)
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")

    set_step("saving")
    return scraped, sources, report


async def _run_job(job_id: str, url: str):
    """Background task: run analysis and write result into job_store."""
    job = job_store[job_id]
    try:
        scraped, sources, report = await asyncio.wait_for(
            _do_analyze(url, job), timeout=120
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

        # Auto cross-comparison against all library projects
        job["step"] = "comparing"
        all_projects = await get_all_projects()
        other_projects = [p for p in all_projects if p["url"] != url]
        new_project_for_compare = {
            "name": project_name,
            "url": url,
            "sector": sector,
            "tags": tags,
            "scores": scores,
            "summary": summary,
            "analysis": report,
        }
        try:
            competitive_intel = await asyncio.to_thread(
                compare_against_library, new_project_for_compare, other_projects
            )
        except Exception as e:
            competitive_intel = {
                "competitors": f"Comparison unavailable: {str(e)}",
                "unique_qualities": "Not available.",
                "market_position": "Not available.",
                "overlap_areas": "Not available.",
            }
        report["competitive_intel"] = competitive_intel

        # Re-save project with competitive_intel included in analysis
        await save_project(
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

        job["status"] = "complete"
        job["result"] = {
            "url": url,
            "pages_scraped": scraped["pages_scraped"],
            "report_id": report_id,
            "db_id": db_id,
            "sources": source_summary,
            "report": report,
            "similar_projects": similar_projects,
            "competitive_intel": competitive_intel,
        }

    except asyncio.TimeoutError:
        job["status"] = "error"
        job["detail"] = "Analysis timed out — the site may be too slow or complex. Try a simpler URL."
    except HTTPException as e:
        job["status"] = "error"
        job["detail"] = e.detail
    except Exception as e:
        job["status"] = "error"
        job["detail"] = f"Analysis failed: {str(e)}"


@app.post("/api/analyze")
async def analyze_url(body: AnalyzeRequest):
    url = body.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required.")

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    _cleanup_old_jobs()

    job_id = str(uuid.uuid4())
    job_store[job_id] = {
        "status": "running",
        "step": "scraping",
        "result": None,
        "detail": None,
        "created_at": datetime.datetime.utcnow(),
    }

    asyncio.create_task(_run_job(job_id, url))

    return JSONResponse({"job_id": job_id, "status": "running"})


@app.get("/api/job/{job_id}")
async def get_job(job_id: str):
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or expired.")

    if job["status"] == "running":
        return JSONResponse({"status": "running", "step": job["step"]})
    elif job["status"] == "complete":
        return JSONResponse({"status": "complete", "result": job["result"]})
    else:
        return JSONResponse({"status": "error", "detail": job["detail"]})


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


# ── Virtuals ──────────────────────────────────────────────────────────────────

async def _run_virtuals_scan_job(job_id: str):
    job = job_store[job_id]
    try:
        result = await scan_all_agents(job)
        job["status"] = "complete"
        job["result"] = result
    except Exception as e:
        job["status"] = "error"
        job["detail"] = f"Scan failed: {str(e)}"


async def _run_virtuals_analyze_job(job_id: str, virtuals_id: str):
    job = job_store[job_id]
    try:
        agent = await get_virtuals_agent_by_virtuals_id(virtuals_id)
        if not agent:
            job["status"] = "error"
            job["detail"] = "Agent not found."
            return

        job["step"] = "analyzing"
        analysis = await asyncio.to_thread(analyze_virtuals_agent, agent)
        await save_virtuals_agent({**agent, "analysis": analysis})

        # Also save to the main projects table so it appears in the Research Library
        virtuals_url = f"https://app.virtuals.io/virtuals/{virtuals_id}"
        agent_name = agent.get("name") or virtuals_id
        ticker = agent.get("ticker") or ""
        innovation_score = analysis.get("innovation_score") if analysis else None
        scores = {"innovation": innovation_score} if innovation_score is not None else {}
        tags_parts = [ticker] if ticker else []
        analysis_tags = analysis.get("tags", "") if analysis else ""
        if analysis_tags:
            tags_parts.append(analysis_tags)
        tags_str = ", ".join(tags_parts)
        sector = analysis.get("sector", "AI Agent") if analysis else "AI Agent"
        summary = analysis.get("executive_summary", "") if analysis else ""
        await save_project(
            url=virtuals_url,
            name=agent_name,
            sector=sector,
            tags=tags_str,
            scores=scores,
            analysis=analysis or {},
            market_data={},
            summary=summary,
        )

        job["status"] = "complete"
        job["result"] = {"virtuals_id": virtuals_id, "analysis": analysis}
    except Exception as e:
        job["status"] = "error"
        job["detail"] = f"Analysis failed: {str(e)}"


@app.get("/api/virtuals/agents")
async def list_virtuals_agents():
    agents = await get_all_virtuals_agents()
    return JSONResponse([
        {
            "id": a["id"],
            "virtuals_id": a["virtuals_id"],
            "name": a["name"],
            "ticker": a["ticker"],
            "market_cap": a["market_cap"],
            "tvl": a["tvl"],
            "volume_24h": a["volume_24h"],
            "price": a["price"],
            "image_url": a["image_url"],
            "innovation_score": (a.get("analysis") or {}).get("innovation_score"),
            "last_scanned": a["last_scanned"],
        }
        for a in agents
    ])


@app.post("/api/virtuals/scan")
async def virtuals_scan():
    _cleanup_old_jobs()
    job_id = str(uuid.uuid4())
    job_store[job_id] = {
        "status": "running",
        "step": "fetching",
        "step_detail": "Starting scan...",
        "result": None,
        "detail": None,
        "created_at": datetime.datetime.utcnow(),
    }
    asyncio.create_task(_run_virtuals_scan_job(job_id))
    return JSONResponse({"job_id": job_id, "status": "running"})


@app.get("/api/virtuals/agent/{virtuals_id}")
async def get_virtuals_agent_detail(virtuals_id: str):
    agent = await get_virtuals_agent_by_virtuals_id(virtuals_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found.")
    return JSONResponse(agent)


@app.post("/api/virtuals/analyze/{virtuals_id}")
async def virtuals_analyze_agent(virtuals_id: str):
    agent = await get_virtuals_agent_by_virtuals_id(virtuals_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found.")

    _cleanup_old_jobs()
    job_id = str(uuid.uuid4())
    job_store[job_id] = {
        "status": "running",
        "step": "analyzing",
        "step_detail": f"Analyzing {agent.get('name', virtuals_id)}...",
        "result": None,
        "detail": None,
        "created_at": datetime.datetime.utcnow(),
    }
    asyncio.create_task(_run_virtuals_analyze_job(job_id, virtuals_id))
    return JSONResponse({"job_id": job_id, "status": "running"})


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
