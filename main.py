import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import uvicorn

from scraper import scrape
from analyzer import analyze, generate_competitive_analysis, generate_comparison
from database import (
    init_db,
    save_project,
    get_project,
    get_project_by_id,
    get_all_projects,
    get_similar_projects,
    search_projects,
    save_comparison,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="Deci",
    description="Crypto/Tech Project Intelligence Reports",
    lifespan=lifespan,
)

templates = Jinja2Templates(directory="templates")

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


class AnalyzeRequest(BaseModel):
    url: str


class CompareRequest(BaseModel):
    project_ids: list[int]


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/analyze")
async def analyze_url(body: AnalyzeRequest):
    url = body.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required.")

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Scrape
    scraped = scrape(url)
    if scraped.get("error") and scraped["pages_scraped"] == 0:
        raise HTTPException(status_code=422, detail=scraped["error"])

    # Analyze
    try:
        report = analyze(scraped)
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")

    # Persist to database
    tags = report.get("tags", [])
    sector = report.get("sector", "other")

    project_id = await save_project(
        url=url,
        name=report.get("name", "Unknown Project"),
        scores=report.get("scores", {}),
        categories=report.get("categories", {}),
        market_data={},
        sources={"pages_scraped": scraped["pages_scraped"]},
        tags=tags,
        sector=sector,
        summary=report.get("summary", ""),
    )

    # Find similar projects for competitive intelligence
    similar = await get_similar_projects(tags, sector, exclude_url=url)
    similar_summaries = []

    if similar:
        competitive_text = generate_competitive_analysis(report, similar)
        if competitive_text:
            report["competitive_intelligence"] = competitive_text

        similar_summaries = [
            {
                "id": p["id"],
                "name": p.get("name", "Unknown"),
                "sector": p.get("sector", ""),
                "overall_score": (p.get("scores") or {}).get("overall", 0),
                "summary": p.get("summary", "")[:200],
                "analyzed_at": p.get("analyzed_at", ""),
            }
            for p in similar
        ]

    return JSONResponse({
        "url": url,
        "project_id": project_id,
        "pages_scraped": scraped["pages_scraped"],
        "report": report,
        "similar_projects": similar_summaries,
    })


@app.get("/api/projects")
async def list_projects(q: str = None):
    if q:
        projects = await search_projects(q)
    else:
        projects = await get_all_projects()

    return JSONResponse([
        {
            "id": p["id"],
            "url": p["url"],
            "name": p.get("name", "Unknown"),
            "sector": p.get("sector", ""),
            "tags_list": p.get("tags_list", []),
            "summary": p.get("summary", ""),
            "scores": p.get("scores") or {},
            "analyzed_at": p.get("analyzed_at", ""),
        }
        for p in projects
    ])


@app.get("/api/project/{project_id}")
async def get_project_detail(project_id: int):
    project = await get_project_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")
    return JSONResponse(project)


@app.post("/api/compare")
async def compare_projects(body: CompareRequest):
    if len(body.project_ids) < 2:
        raise HTTPException(status_code=400, detail="At least 2 project IDs required.")
    if len(body.project_ids) > 5:
        raise HTTPException(status_code=400, detail="Maximum 5 projects can be compared.")

    projects = []
    for pid in body.project_ids:
        p = await get_project_by_id(pid)
        if not p:
            raise HTTPException(status_code=404, detail=f"Project {pid} not found.")
        projects.append(p)

    comparison_text = generate_comparison(projects)

    comparison_data = {
        "projects": [
            {
                "id": p["id"],
                "name": p.get("name", "Unknown"),
                "url": p.get("url", ""),
                "sector": p.get("sector", ""),
                "scores": p.get("scores") or {},
                "summary": p.get("summary", ""),
                "tags_list": p.get("tags_list", []),
            }
            for p in projects
        ],
        "analysis": comparison_text,
    }

    await save_comparison(body.project_ids, comparison_data)

    return JSONResponse(comparison_data)


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
