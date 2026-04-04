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
from analyzer import analyze
from data_sources import collect_all_sources
from report_generator import generate_pdf_report

app = FastAPI(title="Deci", description="Crypto/Tech Project Intelligence Reports")

templates = Jinja2Templates(directory="templates")

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# In-memory report store — keyed by UUID, persists for the lifetime of the process
report_store: dict[str, dict] = {}


class AnalyzeRequest(BaseModel):
    url: str


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

    scraped = await scrape(url)
    if scraped.get("error") and scraped["pages_scraped"] == 0:
        raise HTTPException(status_code=422, detail=scraped["error"])

    # Fetch all external data sources in parallel (async)
    sources = await collect_all_sources(scraped)

    # Run sync Claude analysis in thread pool
    try:
        report = await asyncio.to_thread(analyze, scraped, sources)
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")

    # Store for PDF download
    report_id = str(uuid.uuid4())
    report_store[report_id] = {
        "url": url,
        "report": report,
        "sources": sources,
        "pages_scraped": scraped["pages_scraped"],
        "created_at": datetime.datetime.utcnow().isoformat(),
    }

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
        "sources": source_summary,
        "report": report,
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


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
