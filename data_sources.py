"""
data_sources.py — Multi-source async data collection for Deci.
All fetch functions handle failures gracefully and never raise.
"""
import asyncio
import re
import html as html_module
import httpx
from urllib.parse import quote_plus
from bs4 import BeautifulSoup

TIMEOUT = 12

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# ── Extraction helpers ────────────────────────────────────────────────────────

def extract_project_name(scraped_data: dict) -> str:
    """Extract a clean project name from scraped data."""
    for page in scraped_data.get("content", []):
        title = page.get("title", "").strip()
        if title:
            # Strip common suffixes like "| Company" or "- Tagline"
            name = re.sub(r'\s*[-|–—·]\s*.+$', '', title).strip()
            if name and 2 <= len(name) <= 50 and name.lower() not in {"home", "index", "untitled"}:
                return name
    # Fallback: extract from domain
    url = scraped_data.get("base_url", "")
    if url:
        m = re.search(r'https?://(?:www\.)?([a-zA-Z0-9-]+)', url)
        if m:
            return m.group(1).replace("-", " ").title()
    return "Unknown Project"


def extract_github_repo(text: str) -> str | None:
    """Find the primary GitHub repo (owner/repo) in scraped text."""
    skip_owners = {
        "github", "features", "pricing", "about", "explore", "trending",
        "collections", "events", "contact", "sponsors", "marketplace", "apps",
    }
    skip_repos = {
        "issues", "pull", "pulls", "discussions", "actions", "releases",
        "commits", "tree", "blob", "wiki", "graphs", "settings", "security",
        "stargazers", "watchers", "forks", "network", "compare",
    }
    matches = re.findall(r'github\.com/([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_.-]+)', text)
    for owner, repo in matches:
        if owner.lower() in skip_owners or repo.lower() in skip_repos:
            continue
        # Skip file paths (has extension other than .git)
        if "." in repo and not repo.endswith(".git"):
            continue
        repo = repo.replace(".git", "").rstrip(".")
        if repo:
            return f"{owner}/{repo}"
    return None


# ── GitHub ────────────────────────────────────────────────────────────────────

async def fetch_github_data(owner_repo: str) -> dict:
    try:
        gh_headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Deci-Intelligence/1.0",
        }
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{owner_repo}",
                headers=gh_headers,
            )
            if resp.status_code == 404:
                return {"available": False, "error": "Repository not found"}
            if resp.status_code == 403:
                return {"available": False, "error": "GitHub rate limit reached"}
            if resp.status_code != 200:
                return {"available": False, "error": f"HTTP {resp.status_code}"}

            data = resp.json()

            # Try to get contributor count via Link header trick
            contrib_count = None
            try:
                cr = await client.get(
                    f"https://api.github.com/repos/{owner_repo}/contributors?per_page=1&anon=1",
                    headers=gh_headers,
                )
                link = cr.headers.get("Link", "")
                m = re.search(r'page=(\d+)>; rel="last"', link)
                if m:
                    contrib_count = int(m.group(1))
                elif cr.status_code == 200:
                    contrib_count = len(cr.json())
            except Exception:
                pass

            return {
                "available": True,
                "repo": owner_repo,
                "stars": data.get("stargazers_count", 0),
                "forks": data.get("forks_count", 0),
                "open_issues": data.get("open_issues_count", 0),
                "watchers": data.get("watchers_count", 0),
                "contributors": contrib_count,
                "language": data.get("language", ""),
                "description": data.get("description", ""),
                "last_push": data.get("pushed_at", ""),
                "created_at": data.get("created_at", ""),
                "license": (data.get("license") or {}).get("name"),
                "topics": data.get("topics", []),
                "is_fork": data.get("fork", False),
                "archived": data.get("archived", False),
            }
    except Exception as e:
        return {"available": False, "error": str(e)}


# ── CoinGecko ─────────────────────────────────────────────────────────────────

async def fetch_coingecko_data(query: str) -> dict:
    try:
        cg_headers = {"Accept": "application/json"}
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            sr = await client.get(
                f"https://api.coingecko.com/api/v3/search?query={quote_plus(query)}",
                headers=cg_headers,
            )
            if sr.status_code == 429:
                return {"available": False, "error": "CoinGecko rate limit reached"}
            if sr.status_code != 200:
                return {"available": False, "error": f"Search HTTP {sr.status_code}"}

            coins = sr.json().get("coins", [])
            if not coins:
                return {"available": False, "error": "Token not found on CoinGecko"}

            coin_id = coins[0]["id"]

            dr = await client.get(
                f"https://api.coingecko.com/api/v3/coins/{coin_id}"
                "?localization=false&tickers=false&community_data=true&developer_data=false",
                headers=cg_headers,
            )
            if dr.status_code != 200:
                return {"available": False, "error": f"Detail HTTP {dr.status_code}"}

            d = dr.json()
            mkt = d.get("market_data", {})
            comm = d.get("community_data", {})

            return {
                "available": True,
                "name": d.get("name"),
                "symbol": (coins[0].get("symbol") or "").upper(),
                "coin_id": coin_id,
                "market_cap_rank": d.get("market_cap_rank"),
                "price_usd": mkt.get("current_price", {}).get("usd"),
                "market_cap_usd": mkt.get("market_cap", {}).get("usd"),
                "volume_24h_usd": mkt.get("total_volume", {}).get("usd"),
                "price_change_24h_pct": mkt.get("price_change_percentage_24h"),
                "price_change_7d_pct": mkt.get("price_change_percentage_7d"),
                "price_change_30d_pct": mkt.get("price_change_percentage_30d"),
                "ath_usd": mkt.get("ath", {}).get("usd"),
                "ath_change_pct": mkt.get("ath_change_percentage", {}).get("usd"),
                "total_supply": mkt.get("total_supply"),
                "circulating_supply": mkt.get("circulating_supply"),
                "max_supply": mkt.get("max_supply"),
                "fdv_usd": mkt.get("fully_diluted_valuation", {}).get("usd"),
                "twitter_followers": comm.get("twitter_followers"),
                "reddit_subscribers": comm.get("reddit_subscribers"),
                "categories": d.get("categories", []),
                "genesis_date": d.get("genesis_date"),
            }
    except Exception as e:
        return {"available": False, "error": str(e)}


# ── DeFiLlama ─────────────────────────────────────────────────────────────────

async def fetch_defillama_data(query: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(
                "https://api.llama.fi/protocols",
                headers={"Accept": "application/json"},
            )
            if resp.status_code != 200:
                return {"available": False, "error": f"HTTP {resp.status_code}"}

            query_lower = query.lower().strip()
            best = None
            best_score = 0

            for p in resp.json():
                name = (p.get("name") or "").lower()
                slug = (p.get("slug") or "").lower()
                symbol = (p.get("symbol") or "").lower()

                score = 0
                if query_lower == name or query_lower == slug:
                    score = 3
                elif query_lower in name or name in query_lower:
                    score = 2
                elif query_lower in slug or slug in query_lower:
                    score = 1
                elif query_lower == symbol:
                    score = 2

                if score > best_score:
                    best_score = score
                    best = p

            if not best or best_score == 0:
                return {"available": False, "error": "Protocol not found on DeFiLlama"}

            tvl = best.get("tvl")
            mcap = best.get("mcap")

            return {
                "available": True,
                "name": best.get("name"),
                "tvl_usd": tvl,
                "tvl_change_1d_pct": best.get("change_1d"),
                "tvl_change_7d_pct": best.get("change_7d"),
                "category": best.get("category"),
                "chains": best.get("chains", []),
                "mcap_tvl_ratio": round(mcap / tvl, 3) if tvl and mcap and tvl > 0 else None,
                "audits": best.get("audits"),
            }
    except Exception as e:
        return {"available": False, "error": str(e)}


# ── News & social search ──────────────────────────────────────────────────────

def _parse_ddg_results(html: str) -> list[dict]:
    """Parse DuckDuckGo HTML results page."""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for div in soup.find_all("div", class_="result"):
        title_el = div.find("a", class_="result__a")
        snippet_el = div.find("a", class_="result__snippet")
        url_el = div.find("a", class_="result__url")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
        source = url_el.get_text(strip=True) if url_el else ""
        if title and len(title) > 8:
            items.append({"title": title, "snippet": snippet, "source": source})
    return items


async def search_news(project_name: str) -> dict:
    """Search DuckDuckGo for recent news about the project (past 3 months)."""
    try:
        query = f'"{project_name}" crypto OR blockchain announcement OR launch OR partnership OR funding'
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}&df=3m"
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers=HEADERS)
            if resp.status_code != 200:
                return {"available": False, "error": f"HTTP {resp.status_code}"}
            items = _parse_ddg_results(resp.text)[:12]
            if not items:
                # Retry with simpler query
                query2 = f"{project_name} news 2025"
                url2 = f"https://html.duckduckgo.com/html/?q={quote_plus(query2)}"
                resp2 = await client.get(url2, headers=HEADERS)
                if resp2.status_code == 200:
                    items = _parse_ddg_results(resp2.text)[:12]
            return {
                "available": bool(items),
                "results": items,
                "error": None if items else "No news results found",
            }
    except Exception as e:
        return {"available": False, "error": str(e)}


async def search_twitter_mentions(project_name: str) -> dict:
    """Search DuckDuckGo for recent Twitter/X mentions."""
    try:
        query = f'site:twitter.com OR site:x.com "{project_name}"'
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}&df=m"
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers=HEADERS)
            if resp.status_code != 200:
                return {"available": False, "error": f"HTTP {resp.status_code}"}
            items = _parse_ddg_results(resp.text)[:10]
            return {
                "available": bool(items),
                "results": items,
                "error": None if items else "No Twitter results found",
            }
    except Exception as e:
        return {"available": False, "error": str(e)}


# ── Orchestrator ──────────────────────────────────────────────────────────────

async def collect_all_sources(scraped_data: dict) -> dict:
    """
    Collect all external data sources in parallel.
    Returns dict with keys: github, coingecko, defillama, news, twitter, _meta
    """
    all_text = " ".join(p.get("text", "") for p in scraped_data.get("content", []))
    project_name = extract_project_name(scraped_data)
    github_repo = extract_github_repo(all_text)

    coros = [
        fetch_coingecko_data(project_name),
        fetch_defillama_data(project_name),
        search_news(project_name),
        search_twitter_mentions(project_name),
    ]
    labels = ["coingecko", "defillama", "news", "twitter"]

    if github_repo:
        coros.append(fetch_github_data(github_repo))
        labels.append("github")

    results = await asyncio.gather(*coros, return_exceptions=True)

    sources: dict = {}
    for label, result in zip(labels, results):
        if isinstance(result, Exception):
            sources[label] = {"available": False, "error": str(result)}
        else:
            sources[label] = result

    if "github" not in sources:
        sources["github"] = {"available": False, "error": "No GitHub repo URL found in scraped content"}

    sources["_meta"] = {
        "project_name": project_name,
        "github_repo": github_repo,
    }

    return sources
