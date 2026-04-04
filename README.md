# DECI — Project Intelligence

A web-based research tool that analyzes crypto and tech project websites and generates structured intelligence reports.

## What It Does

Provide a URL → Deci scrapes the site (multiple pages, docs, whitepapers) → Claude AI analyzes the content → Returns a structured report covering:

1. **Project Focus** — What the project does and what problem it solves
2. **Team** — Who's behind it and their backgrounds
3. **Roadmap** — Timeline, milestones, delivered vs. planned
4. **Unique Value Proposition** — What genuinely differentiates them
5. **Competitors** — Who else is in the space
6. **Execution Risk** — Red flags, concerns, delivery likelihood

## Deploy to Railway

1. Push this repo to GitHub
2. Create a new Railway project and connect the GitHub repo
3. Add environment variable: `ANTHROPIC_API_KEY=your_key_here`
4. Railway will auto-detect and deploy

## Run Locally

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set your API key
export ANTHROPIC_API_KEY=your_key_here

# Run
uvicorn main:app --reload --port 8000
```

Then open http://localhost:8000

## Stack

- **Backend:** Python + FastAPI
- **Scraping:** requests + BeautifulSoup (no API keys needed)
- **AI:** Anthropic Claude (claude-sonnet-4-20250514)
- **Frontend:** Vanilla HTML/CSS/JS, dark terminal theme
- **Deploy:** Railway (Procfile + nixpacks.toml included)
