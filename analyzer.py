import os
import json
import re
import anthropic

MODEL = "claude-sonnet-4-20250514"
MAX_CONTENT_CHARS = 60000   # website content budget
MAX_SOURCES_CHARS = 20000   # external sources budget

SYSTEM_PROMPT = """You are Deci, an elite crypto and technology project intelligence analyst.
You produce structured, honest, and deeply researched reports on blockchain, crypto, and tech projects.
You synthesize data from multiple sources: the project website, GitHub repo stats, CoinGecko token data, DeFiLlama TVL data, and recent news/social mentions.
You are direct, specific, and highlight both strengths and risks without hype.
When sources are missing or unavailable, note this and treat it as potentially relevant to the risk assessment."""

ANALYSIS_PROMPT = """Analyze this project using all provided data sources and produce a comprehensive intelligence report.

## WEBSITE CONTENT (primary source)
{website_content}

## EXTERNAL DATA SOURCES
{external_data}

---

Produce a JSON report with EXACTLY these keys. Be thorough, specific, and honest.

{{
  "executive_summary": "2-3 sentences: what is this project, what stage is it at, and the single most important insight an investor or analyst should know.",

  "project_focus": "What it does, what problem it solves, the technology stack, any genuine technical innovation. 3-5 sentences. Quote specific claims from the website where relevant.",

  "team": "Who is behind the project — names, roles, backgrounds, relevant experience. Note any advisors or investors mentioned. If team info is thin or anonymous, flag this explicitly.",

  "tokenomics": "Token supply (total, circulating, max), distribution breakdown, vesting schedules, token utility, inflation rate. Use CoinGecko data if available. Assess whether tokenomics are investor-friendly or concerning.",

  "roadmap": "Past delivery track record (what was promised vs. delivered) and upcoming milestones with specific dates/quarters if mentioned. Assess whether the team has a history of on-time delivery.",

  "unique_value_proposition": "What genuinely differentiates this from alternatives. Quote their specific claims, then critically assess each one. Avoid generic praise like 'innovative' or 'revolutionary'.",

  "competitive_landscape": "Name the actual direct competitors. Compare this project's positioning. Who is winning in this space and why? Be specific about advantages and disadvantages.",

  "community_social": "Quantify the community: Twitter/X followers (from CoinGecko if available), GitHub stars/forks/contributors/commit frequency, Discord/Telegram size if mentioned. Assess engagement quality.",

  "on_chain_metrics": "TVL from DeFiLlama (if available), trading volume, active addresses, protocol revenue, chain deployments. If no on-chain data is available, state this clearly and explain implications.",

  "recent_events": "CRITICAL — List the most recent significant developments in bullet format. Pull from the news search results and Twitter mentions provided. Include: product launches, partnerships, funding rounds, exchange listings, community milestones, or notable announcements. Format as bullet list with approximate date/source where available. If limited data, say so explicitly.",

  "risk_assessment": "Structured risk breakdown:\\n• Team Risk: [assessment]\\n• Technical Risk: [assessment]\\n• Tokenomics Risk: [assessment]\\n• Market/Competition Risk: [assessment]\\n• Regulatory Risk: [assessment]\\n• Execution Risk: [assessment]\\n\\nOverall Risk Rating: LOW / MEDIUM / HIGH / VERY HIGH",

  "scores": {{
    "team": <integer 1-10>,
    "technology": <integer 1-10>,
    "tokenomics": <integer 1-10>,
    "community": <integer 1-10>,
    "execution": <integer 1-10>,
    "overall": <float rounded to 1 decimal, formula: team*0.25 + technology*0.25 + tokenomics*0.20 + community*0.15 + execution*0.15>
  }}
}}

Scoring rubric (be honest — do not inflate):
- 9-10: Exceptional. Top-tier team/tech, strong metrics, clear differentiation.
- 7-8: Strong. Above average with minor concerns.
- 5-6: Average. Present but unremarkable, or mixed signals.
- 3-4: Below average. Notable weaknesses or red flags.
- 1-2: Poor. Major problems, missing critical information, or high risk.

Return ONLY valid JSON. No markdown fences, no preamble, no trailing text."""


def _fmt_num(n, prefix="$") -> str:
    if n is None:
        return "N/A"
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "N/A"
    if n >= 1e9:
        return f"{prefix}{n / 1e9:.2f}B"
    if n >= 1e6:
        return f"{prefix}{n / 1e6:.2f}M"
    if n >= 1e3:
        return f"{prefix}{n / 1e3:.2f}K"
    return f"{prefix}{n:.4f}" if n < 1 else f"{prefix}{n:.2f}"


def _format_sources(sources: dict) -> str:
    """Format external source data as readable text for Claude."""
    parts = []
    meta = sources.get("_meta", {})
    parts.append(f"Project Name Detected: {meta.get('project_name', 'Unknown')}")
    if meta.get("github_repo"):
        parts.append(f"GitHub Repo Detected: {meta['github_repo']}")

    # GitHub
    gh = sources.get("github", {})
    if gh.get("available"):
        parts.append(f"""
### GitHub ({gh['repo']})
- Stars: {gh.get('stars', 0):,}
- Forks: {gh.get('forks', 0):,}
- Contributors: {gh.get('contributors') or 'unknown'}
- Open Issues: {gh.get('open_issues', 0):,}
- Primary Language: {gh.get('language') or 'unknown'}
- Last Push: {gh.get('last_push') or 'unknown'}
- Created: {gh.get('created_at') or 'unknown'}
- License: {gh.get('license') or 'none'}
- Topics: {', '.join(gh.get('topics', [])) or 'none'}
- Is Fork: {gh.get('is_fork', False)}
- Archived: {gh.get('archived', False)}""")
    else:
        parts.append(f"\n### GitHub\nNot available: {gh.get('error', 'unknown error')}")

    # CoinGecko
    cg = sources.get("coingecko", {})
    if cg.get("available"):
        pct = lambda v: f"{v:.1f}%" if v is not None else "N/A"
        supply = lambda v: f"{v:,.0f}" if v else "N/A"
        parts.append(f"""
### CoinGecko Token Data ({cg.get('symbol', '')} — rank #{cg.get('market_cap_rank') or 'unranked'})
- Price: {_fmt_num(cg.get('price_usd'))}
- Market Cap: {_fmt_num(cg.get('market_cap_usd'))}
- 24h Volume: {_fmt_num(cg.get('volume_24h_usd'))}
- Fully Diluted Valuation: {_fmt_num(cg.get('fdv_usd'))}
- Price Change 24h: {pct(cg.get('price_change_24h_pct'))}
- Price Change 7d: {pct(cg.get('price_change_7d_pct'))}
- Price Change 30d: {pct(cg.get('price_change_30d_pct'))}
- ATH: {_fmt_num(cg.get('ath_usd'))} ({pct(cg.get('ath_change_pct'))} from ATH)
- Circulating Supply: {supply(cg.get('circulating_supply'))}
- Total Supply: {supply(cg.get('total_supply'))}
- Max Supply: {supply(cg.get('max_supply')) if cg.get('max_supply') else 'Unlimited/Unknown'}
- Twitter Followers (CoinGecko): {f"{cg.get('twitter_followers'):,}" if cg.get('twitter_followers') else 'N/A'}
- Reddit Subscribers: {f"{cg.get('reddit_subscribers'):,}" if cg.get('reddit_subscribers') else 'N/A'}
- Genesis Date: {cg.get('genesis_date') or 'N/A'}
- Categories: {', '.join(cg.get('categories', [])) or 'N/A'}""")
    else:
        parts.append(f"\n### CoinGecko\nNot available: {cg.get('error', 'unknown error')}")

    # DeFiLlama
    dl = sources.get("defillama", {})
    if dl.get("available"):
        tvl = dl.get("tvl_usd")
        tvl_str = _fmt_num(tvl) if tvl else "N/A"
        pct = lambda v: f"{v:.1f}%" if v is not None else "N/A"
        parts.append(f"""
### DeFiLlama TVL Data
- Protocol: {dl.get('name', 'N/A')}
- TVL: {tvl_str}
- TVL Change 1d: {pct(dl.get('tvl_change_1d_pct'))}
- TVL Change 7d: {pct(dl.get('tvl_change_7d_pct'))}
- Category: {dl.get('category') or 'N/A'}
- Chains: {', '.join(dl.get('chains', [])) or 'N/A'}
- MCap/TVL Ratio: {dl.get('mcap_tvl_ratio') or 'N/A'}""")
    else:
        parts.append(f"\n### DeFiLlama\nNot available: {dl.get('error', 'unknown error')}")

    # News
    news = sources.get("news", {})
    if news.get("available") and news.get("results"):
        parts.append("\n### Recent News & Announcements (past 3 months, web search)")
        for i, item in enumerate(news["results"][:12], 1):
            parts.append(f"{i}. {item['title']}")
            if item.get("snippet"):
                parts.append(f"   Snippet: {item['snippet']}")
            if item.get("source"):
                parts.append(f"   Source: {item['source']}")
    else:
        parts.append(f"\n### Recent News\nNot available: {news.get('error', 'no results found')}")

    # Twitter
    tw = sources.get("twitter", {})
    if tw.get("available") and tw.get("results"):
        parts.append("\n### Twitter/X Mentions (past month, web search)")
        for i, item in enumerate(tw["results"][:8], 1):
            parts.append(f"{i}. {item['title']}")
            if item.get("snippet"):
                parts.append(f"   {item['snippet']}")
    else:
        parts.append(f"\n### Twitter/X Mentions\nNot available: {tw.get('error', 'no results found')}")

    return "\n".join(parts)


def build_content_block(scraped_data: dict) -> str:
    parts = []
    total = 0
    for page in scraped_data["content"]:
        header = f"\n\n=== PAGE: {page['title']} ===\nURL: {page['url']}\n\n"
        body = page["text"]
        chunk = header + body
        if total + len(chunk) > MAX_CONTENT_CHARS:
            remaining = MAX_CONTENT_CHARS - total
            if remaining > 500:
                parts.append(chunk[:remaining])
            break
        parts.append(chunk)
        total += len(chunk)
    return "".join(parts)


def analyze(scraped_data: dict, sources: dict | None = None) -> dict:
    """
    Takes scraper output + external sources, calls Claude, returns structured report dict.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable is not set.")

    client = anthropic.Anthropic(api_key=api_key)

    content_block = build_content_block(scraped_data)
    external_data = _format_sources(sources or {})

    if not content_block.strip():
        return _empty_report()

    prompt = ANALYSIS_PROMPT.format(
        website_content=content_block,
        external_data=external_data[:MAX_SOURCES_CHARS],
    )

    message = client.messages.create(
        model=MODEL,
        max_tokens=6000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        report = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            try:
                report = json.loads(match.group(0))
            except json.JSONDecodeError:
                return _error_report(raw)
        else:
            return _error_report(raw)

    # Ensure all required keys exist
    required = [
        "executive_summary", "project_focus", "team", "tokenomics", "roadmap",
        "unique_value_proposition", "competitive_landscape", "community_social",
        "on_chain_metrics", "recent_events", "risk_assessment",
    ]
    for k in required:
        if k not in report:
            report[k] = "Not available."

    # Validate and normalize scores
    if "scores" not in report or not isinstance(report.get("scores"), dict):
        report["scores"] = {"team": 5, "technology": 5, "tokenomics": 5, "community": 5, "execution": 5, "overall": 5.0}
    else:
        s = report["scores"]
        for sk in ["team", "technology", "tokenomics", "community", "execution"]:
            try:
                s[sk] = max(1, min(10, int(s.get(sk, 5))))
            except (TypeError, ValueError):
                s[sk] = 5
        try:
            s["overall"] = round(float(s.get("overall", 5.0)), 1)
        except (TypeError, ValueError):
            s["overall"] = round(
                s["team"] * 0.25 + s["technology"] * 0.25 +
                s["tokenomics"] * 0.20 + s["community"] * 0.15 +
                s["execution"] * 0.15,
                1,
            )

    return report


def _empty_report() -> dict:
    return {
        "executive_summary": "No content could be extracted from the provided URL.",
        "project_focus": "N/A", "team": "N/A", "tokenomics": "N/A",
        "roadmap": "N/A", "unique_value_proposition": "N/A",
        "competitive_landscape": "N/A", "community_social": "N/A",
        "on_chain_metrics": "N/A", "recent_events": "N/A",
        "risk_assessment": "Unable to analyze — no content was scraped.",
        "scores": {"team": 1, "technology": 1, "tokenomics": 1, "community": 1, "execution": 1, "overall": 1.0},
    }


def _error_report(raw: str) -> dict:
    return {
        "executive_summary": "Analysis complete but response parsing failed.",
        "project_focus": "N/A", "team": "N/A", "tokenomics": "N/A",
        "roadmap": "N/A", "unique_value_proposition": "N/A",
        "competitive_landscape": "N/A", "community_social": "N/A",
        "on_chain_metrics": "N/A", "recent_events": raw[:2000],
        "risk_assessment": "N/A",
        "scores": {"team": 5, "technology": 5, "tokenomics": 5, "community": 5, "execution": 5, "overall": 5.0},
    }
