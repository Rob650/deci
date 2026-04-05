import os
import re
import json
import asyncio
import httpx
import anthropic

VIRTUALS_API_BASE = "https://api2.virtuals.io/api/virtuals"
MODEL = "claude-sonnet-4-20250514"

VIRTUALS_SYSTEM_PROMPT = """You are Deci, an elite AI agent intelligence analyst.
You analyze AI agents from the Virtuals Protocol platform.
You produce structured, honest reports on these agents.
Be direct, specific, and highlight both strengths and risks without hype.

CRITICAL OUTPUT RULES:
1. Every field in your JSON response MUST be a plain string (not a nested object, not an array, not null).
2. Use bullet points (•) and newline characters within strings to structure multi-part content.
3. Always cite specific numbers from the provided data — never use vague estimates.
4. If a data point is unavailable, say "Not available" rather than omitting it."""

VIRTUALS_ANALYSIS_PROMPT = """Analyze this AI agent from the Virtuals Protocol platform.

## AGENT DATA
Name: {name}
Ticker: {ticker}
Description: {description}
Market Cap: {market_cap}
TVL: {tvl}
Volume (24h): {volume_24h}
Price: {price}
Contract Address: {contract_address}
Created: {created_at}

---

Produce a JSON report with EXACTLY these keys. ALL values must be plain strings except innovation_score.

{
  "what_it_does": "2-3 sentences: what this AI agent does, what problem it solves, and what unique capability it offers on the Virtuals Protocol.",
  "unique_value_prop": "The single most compelling reason this agent stands out. Quote the description if useful. Compare briefly to generic AI agents.",
  "market_position": "Assessment of market position:\\n• Market cap tier: [micro <$1M / small $1M-$10M / mid $10M-$100M / large >$100M]\\n• Volume indicator: [high / moderate / low / stagnant — based on 24h volume vs market cap ratio]\\n• TVL significance: [amount and what it implies, or Not available]\\n• Overall market standing: [leader / established / emerging / micro-cap]",
  "risk_assessment": "Structured risk assessment:\\n• Liquidity Risk: [LOW/MEDIUM/HIGH] — [reason based on volume and market cap]\\n• Market Risk: [LOW/MEDIUM/HIGH] — [reason based on price volatility indicators]\\n• Concept Risk: [LOW/MEDIUM/HIGH] — [is the agent concept viable and differentiated long-term?]\\n• Overall Risk: [LOW/MEDIUM/HIGH/VERY HIGH] — [1 sentence justification]",
  "innovation_score_rationale": "2-3 sentences explaining the innovation score. What is genuinely novel? What is derivative? Cite specifics from the description.",
  "innovation_score": <integer 1-10, where 10 = completely novel breakthrough, 1 = generic/copycat>
}

Scoring rubric for innovation_score:
- 9-10: Truly novel AI capability, unique use case, clear technical moat
- 7-8: Strong differentiation, meaningful niche, some novel elements
- 5-6: Decent concept, somewhat differentiated, but many similar agents exist
- 3-4: Generic AI agent with minimal differentiation
- 1-2: Copy of existing concept, no novel elements

Return ONLY valid JSON. No markdown fences, no preamble, no trailing text."""


def _fmt_num(val, prefix="$") -> str:
    if val is None:
        return "N/A"
    try:
        n = float(val)
    except (TypeError, ValueError):
        return "N/A"
    if n >= 1e9:
        return f"{prefix}{n / 1e9:.2f}B"
    if n >= 1e6:
        return f"{prefix}{n / 1e6:.2f}M"
    if n >= 1e3:
        return f"{prefix}{n / 1e3:.2f}K"
    return f"{prefix}{n:.6f}" if n < 0.01 else f"{prefix}{n:.4f}"


def _safe_float(val):
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _extract_image_url(attrs: dict) -> str:
    """Extract image URL from nested Strapi-style image attribute."""
    image_data = attrs.get("image", {})
    if not isinstance(image_data, dict):
        return ""
    inner = image_data.get("data", {})
    if not inner or not isinstance(inner, dict):
        return ""
    img_attrs = inner.get("attributes", {})
    return img_attrs.get("url", "")


async def fetch_all_virtuals_agents() -> list[dict]:
    """Paginate through the Virtuals API and return all agent records."""
    all_agents = []
    page = 1
    page_size = 100

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            params = {
                "filters[status]": "5",
                "sort[0]": "volume24h:desc",
                "sort[1]": "createdAt:desc",
                "populate[0]": "image",
                "populate[1]": "genesis",
                "populate[2]": "vibesInfo",
                "populate[3]": "creator",
                "pagination[page]": str(page),
                "pagination[pageSize]": str(page_size),
            }
            try:
                resp = await client.get(VIRTUALS_API_BASE, params=params)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"[virtuals] Error fetching page {page}: {e}", flush=True)
                break

            items = data.get("data", [])
            if not items:
                break

            all_agents.extend(items)

            pagination = data.get("meta", {}).get("pagination", {})
            total_pages = pagination.get("pageCount", 1)
            print(f"[virtuals] Fetched page {page}/{total_pages} ({len(items)} agents)", flush=True)
            if page >= total_pages:
                break
            page += 1

    return all_agents


def scan_single_agent(agent_data: dict) -> dict:
    """Format raw Virtuals API response into a clean normalized dict."""
    attrs = agent_data.get("attributes", {})
    return {
        "virtuals_id": str(agent_data.get("id", "")),
        "name": attrs.get("name", ""),
        "ticker": attrs.get("symbol", attrs.get("ticker", "")),
        "description": attrs.get("description", ""),
        "market_cap": _safe_float(attrs.get("mcap")),
        "tvl": _safe_float(attrs.get("tvl")),
        "volume_24h": _safe_float(attrs.get("volume24h")),
        "price": _safe_float(attrs.get("tokenPrice", attrs.get("price"))),
        "contract_address": attrs.get("tokenAddress", ""),
        "image_url": _extract_image_url(attrs),
        "created_at": attrs.get("createdAt", ""),
    }


def analyze_virtuals_agent(agent_data: dict) -> dict:
    """Call Claude to analyze a single Virtuals agent. Returns analysis dict."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable is not set.")

    client = anthropic.Anthropic(api_key=api_key)

    prompt = VIRTUALS_ANALYSIS_PROMPT
    prompt = prompt.replace("{name}", agent_data.get("name", "Unknown"))
    prompt = prompt.replace("{ticker}", agent_data.get("ticker", "N/A"))
    prompt = prompt.replace("{description}", (agent_data.get("description", "") or "No description available")[:2000])
    prompt = prompt.replace("{market_cap}", _fmt_num(agent_data.get("market_cap")))
    prompt = prompt.replace("{tvl}", _fmt_num(agent_data.get("tvl")))
    prompt = prompt.replace("{volume_24h}", _fmt_num(agent_data.get("volume_24h")))
    prompt = prompt.replace("{price}", _fmt_num(agent_data.get("price")))
    prompt = prompt.replace("{contract_address}", agent_data.get("contract_address", "N/A") or "N/A")
    prompt = prompt.replace("{created_at}", agent_data.get("created_at", "N/A") or "N/A")

    message = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=VIRTUALS_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        analysis = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            try:
                analysis = json.loads(match.group(0))
            except json.JSONDecodeError:
                return {"what_it_does": "Analysis parsing failed.", "innovation_score": 5}
        else:
            return {"what_it_does": "Analysis parsing failed.", "innovation_score": 5}

    # Normalize innovation_score
    try:
        analysis["innovation_score"] = max(1, min(10, int(analysis.get("innovation_score", 5))))
    except (TypeError, ValueError):
        analysis["innovation_score"] = 5

    return analysis


async def scan_all_agents(job: dict | None = None) -> dict:
    """Orchestrate: fetch all → save to DB → analyze top 50 by volume."""
    from database import save_virtuals_agent, get_virtuals_agent_by_virtuals_id

    def set_step(step: str, detail: str = ""):
        if job is not None:
            job["step"] = step
            job["step_detail"] = detail
        print(f"[virtuals] {step}: {detail}", flush=True)

    # 1. Fetch all agents
    set_step("fetching", "Fetching all agents from Virtuals API...")
    raw_agents = await fetch_all_virtuals_agents()
    set_step("fetching", f"Fetched {len(raw_agents)} agents total")

    # 2. Parse and save all to DB
    set_step("saving", f"Saving {len(raw_agents)} agents to database...")
    saved = []
    for raw in raw_agents:
        agent = scan_single_agent(raw)
        try:
            await save_virtuals_agent(agent)
            saved.append(agent)
        except Exception as e:
            print(f"[virtuals] Error saving agent {agent.get('name')}: {e}", flush=True)

    # 3. Analyze top 50 by volume (skip already-analyzed agents)
    top_agents = sorted(
        [a for a in saved if a.get("volume_24h") is not None],
        key=lambda x: x.get("volume_24h") or 0,
        reverse=True,
    )[:50]

    set_step("analyzing", f"Analyzing top {len(top_agents)} agents by volume...")

    sem = asyncio.Semaphore(5)
    analyzed_count = 0

    async def analyze_one(agent: dict):
        nonlocal analyzed_count
        async with sem:
            try:
                existing = await get_virtuals_agent_by_virtuals_id(agent["virtuals_id"])
                if existing and existing.get("analysis"):
                    return  # already has analysis — skip
                analysis = await asyncio.to_thread(analyze_virtuals_agent, agent)
                await save_virtuals_agent({**agent, "analysis": analysis})
                analyzed_count += 1
                set_step("analyzing", f"Analyzed {analyzed_count}/{len(top_agents)} agents")
            except Exception as e:
                print(f"[virtuals] Error analyzing {agent.get('name')}: {e}", flush=True)

    await asyncio.gather(*[analyze_one(a) for a in top_agents])

    return {
        "total_fetched": len(raw_agents),
        "total_saved": len(saved),
        "total_analyzed": analyzed_count,
    }
