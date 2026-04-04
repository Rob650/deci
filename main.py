import os
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, HttpUrl
import uvicorn

from scraper import scrape
from analyzer import analyze

app = FastAPI(title="Deci", description="Crypto/Tech Project Intelligence Reports")

templates = Jinja2Templates(directory="templates")

# Mount static files if directory exists and has files
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


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

    # Basic URL normalization
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

    return JSONResponse({
        "url": url,
        "pages_scraped": scraped["pages_scraped"],
        "report": report,
    })


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
