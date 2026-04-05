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
When sources are missing or unavailable, note this explicitly and treat it as relevant to the risk assessment.

CRITICAL OUTPUT RULES:
1. Every field in your JSON response MUST be a plain string (not a nested object, not an array, not null).
2. Use bullet points (•) and newline characters within strings to structure multi-part content.
3. Always cite specific numbers, names, and dates from the provided data — never use vague estimates.
4. If a specific data point is unavailable, say "Not found in available data" rather than omitting it or guessing."""

ANALYSIS_PROMPT = """Analyze this project using all provided data sources and produce a comprehensive intelligence report.

## WEBSITE CONTENT (primary source)
{website_content}

## EXTERNAL DATA SOURCES
{external_data}

---

Produce a JSON report with EXACTLY these keys. ALL values must be plain strings — no nested objects.

{
  "executive_summary": "3-4 sentences. Include: (1) what this project is and what problem it solves, (2) current stage (mainnet/testnet/pre-launch), (3) one key metric (TVL, market cap, GitHub stars, or user count), and (4) the single most critical insight — positive or negative — that an investor must know. Be specific, no fluff.",

  "project_focus": "Technical deep-dive covering ALL of the following you can determine from the data:\\n• Layer: L1 / L2 / L3 / application / infrastructure\\n• Consensus mechanism (PoS, PoW, PoA, DPoS, etc.) if applicable\\n• VM / execution environment (EVM-compatible, custom VM, WASM, etc.)\\n• Smart contract language (Solidity, Rust, Move, Cairo, etc.)\\n• Key protocol design choices (rollup type, data availability, sequencer model, etc.)\\n• Specific use cases with named examples (e.g. 'enables Uniswap-style AMM for X asset class')\\n• Claimed performance metrics (TPS, finality time, gas costs) — quote directly from website\\n• What is genuinely novel vs. what is copied from existing solutions\\n\\nDECI TAKE: [2-3 sentences of original analysis — how innovative is this tech really? Is it genuinely novel or derivative? What's the biggest technical risk?]",

  "team": "For each team member you can identify:\\n• [Name] — [Title] — [Previous companies/projects with years] — [Education if stated] — [LinkedIn/Twitter if mentioned]\\nNote advisors and institutional backers with the same detail.\\nFlag any of these red flags if present: anonymous founders, LinkedIn profiles that don't check out, team members with failed/exit-scam history, advisors who are purely decorative, no verifiable track record.\\nIf team info is completely absent, state: 'Team is anonymous or undisclosed — HIGH risk flag.'\\n\\nDECI TAKE: [2-3 sentences — how does this team compare to competitors' teams? What's the biggest concern or strength?]",

  "tokenomics": "Use CoinGecko data if available. Cover ALL of the following:\\n• Token ticker and network\\n• Total supply: [exact number]\\n• Circulating supply: [exact number] ([X]% of total)\\n• Max supply: [exact number or 'unlimited']\\n• Current price: [price] | Market cap: [amount] | FDV: [amount] | 24h volume: [amount]\\n• ATH: [price] — currently [X]% below ATH\\n• Distribution breakdown: Team [X]% (vesting: [schedule]), Investors [X]% (vesting: [schedule]), Community/Ecosystem [X]%, Foundation [X]%, Public sale [X]%\\n• Token utility: governance / fee payment / staking / burning / collateral — describe each mechanism\\n• Inflation rate or emission schedule\\n• Assessment: are tokenomics investor-friendly or concerning? Give specific reasoning.\\n\\nDECI TAKE: [2-3 sentences — are these tokenomics investor-friendly? What's the biggest red flag or green flag?]",

  "roadmap": "List every milestone you can find from website/news, with status:\\n• [Q/Year] [Milestone name]: [DELIVERED ✓ / IN PROGRESS / UPCOMING / DELAYED ✗]\\nFor delivered items: note if it was on time or late.\\nFor upcoming: note the target date.\\nConclusion: assess the team's delivery track record — what % of promised milestones have been delivered on time? Are there patterns of delays?\\n\\nDECI TAKE: [2-3 sentences — is this team delivering? How does their execution compare to promises?]",

  "unique_value_proposition": "Quote their top 3-5 marketing claims verbatim (in quotes), then critically assess each:\\n• Claim: '[exact quote from website]'\\n  Reality: [your assessment — is this real differentiation or marketing language? Compare to alternatives.]\\nConclusion: what genuinely sets this project apart, if anything? Name the specific technical or business moat.\\n\\nDECI TAKE: [2-3 sentences — does this project have a real moat? If you had to bet on one thing about this project, what would it be?]",

  "competitive_landscape": "Name at least 4 direct competitors. For each:\\n• [Competitor Name] — Market cap: [amount if known] — TVL: [amount if known] — Key advantage over [this project]: [specific reason] — Key weakness vs [this project]: [specific reason]\\nOverall positioning: where does this project sit in the competitive matrix? Is it a market leader, fast-follower, or niche player? Who is currently winning this market and why?\\nIf no direct competitors exist (genuinely novel), explain why this market might be too small or too early.\\n\\nDECI TAKE: [2-3 sentences — who's actually winning this market and why? Where does this project really stand?]",

  "community_social": "IMPORTANT: Look for social media links in the WEBSITE CONTENT section — projects typically have Twitter/X, Discord, Telegram, and Reddit links in their footer, header, or community page. Extract and report these URLs even if CoinGecko doesn't have follower counts.\\n• Twitter/X: [URL from site if found] — Followers: [exact number from CoinGecko if available, else 'not found']\\n• Discord: [URL from site if found] — Members: [size if mentioned anywhere, or 'not found in data']\\n• Telegram: [URL from site if found] — Members: [size if mentioned, or 'not found in data']\\n• Reddit: [URL from site if found] — Subscribers: [exact number or 'not found']\\n• GitHub stars: [number] | Forks: [number] | Contributors: [number] | Last push: [date]\\n• GitHub commit activity: [daily/weekly/monthly — assess from last push and creation date]\\n• Community growth trend: [growing rapidly / steady / stagnant / declining] — based on what evidence?\\n• Engagement quality: [genuine technical discussion / mostly speculation / bot activity suspected]\\n\\nDECI TAKE: [2-3 sentences — is this community organic or manufactured? What does the engagement quality tell you?]",

  "on_chain_metrics": "From DeFiLlama and CoinGecko data:\\n• TVL: [exact amount] ([X]% change 1d, [X]% change 7d) — as of [date if available]\\n• Chains deployed on: [list]\\n• MCap/TVL ratio: [number — <1 is often undervalued, >3 is often overvalued]\\n• Protocol category (DeFiLlama): [category]\\n• 24h trading volume: [amount]\\n• Protocol revenue: [if available]\\n• Notable whale activity or concentration: [if mentioned]\\nIf no on-chain data is available: state this explicitly and assess what it implies — e.g. pre-launch, no DeFi component, or data gap.\\n\\nDECI TAKE: [2-3 sentences — what do the on-chain numbers really tell you about adoption and usage?]",

  "recent_events": "List 8-12 recent developments from the news and Twitter data. Each item:\\n• [Approx date or 'Recent'] — [Source] — [Specific event with named entities]\\nInclude: funding rounds (name the lead investor and amount), exchange listings (name the exchange), partnerships (name both parties), product launches (name the feature), governance votes (outcome and vote count), security incidents (nature and resolution).\\nIf news data is sparse: note this explicitly — limited news coverage can indicate low awareness or pre-launch stage.",

  "risk_assessment": "Provide a structured assessment. Each risk must cite SPECIFIC evidence from the data:\\n\\n• Team Risk: [LOW/MEDIUM/HIGH] — [evidence: named team members and their track records, or lack thereof]\\n• Technical Risk: [LOW/MEDIUM/HIGH] — [evidence: smart contract audit status (auditor name and date if available), centralization vectors (multisig, upgradeable contracts, single sequencer), oracle dependencies, open-source status]\\n• Tokenomics Risk: [LOW/MEDIUM/HIGH] — [evidence: upcoming token unlocks with dates, supply concentration, inflation rate vs. demand drivers]\\n• Market/Competition Risk: [LOW/MEDIUM/HIGH] — [evidence: named competitors and their traction vs. this project]\\n• Regulatory Risk: [LOW/MEDIUM/HIGH] — [evidence: token classification risk, geographic restrictions, compliance disclosures]\\n• Execution Risk: [LOW/MEDIUM/HIGH] — [evidence: delivery track record, current development stage, team size vs. scope]\\n\\nOverall Risk Rating: [LOW / MEDIUM / HIGH / VERY HIGH]\\nJustification: [2 sentences explaining the overall rating based on the most critical risks above]\\n\\nDECI TAKE: [2-3 sentences — if you were investing $10k today, what's the single biggest risk that could wipe it out?]",

  "sector": "ONE of: AI, DeFi, Gaming, Meme, L1/L2, NFT, Infrastructure, Social, RWA, Privacy, Oracle, DEX, Lending, Payments, Other",

  "tags": "comma-separated list of 3-8 relevant tags (e.g. 'ethereum, layer2, zk-rollup, privacy'). Lowercase, concise.",

  "competitive_intel": "Auto-generated after analysis",

  "scores": {
    "team": <integer 1-10>,
    "technology": <integer 1-10>,
    "tokenomics": <integer 1-10>,
    "community": <integer 1-10>,
    "execution": <integer 1-10>,
    "overall": <float rounded to 1 decimal, formula: team*0.25 + technology*0.25 + tokenomics*0.20 + community*0.15 + execution*0.15>,
    "team_rationale": "2-3 specific reasons for this score (cite names, facts, red flags)",
    "technology_rationale": "2-3 specific reasons for this score (cite tech stack, audits, innovation level)",
    "tokenomics_rationale": "2-3 specific reasons for this score (cite supply numbers, vesting, utility)",
    "community_rationale": "2-3 specific reasons for this score (cite follower counts, GitHub stats)",
    "execution_rationale": "2-3 specific reasons for this score (cite milestone delivery record, stage)"
  }
}

Scoring rubric (be honest — do not inflate):
- 9-10: Exceptional. Top-tier team/tech/metrics, clear differentiation, strong evidence.
- 7-8: Strong. Above average with minor concerns backed by evidence.
- 5-6: Average. Present but unremarkable, mixed signals, or data gaps.
- 3-4: Below average. Notable weaknesses or red flags with specific evidence.
- 1-2: Poor. Major problems, anonymous team, no traction, or critical data missing.

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

    prompt = ANALYSIS_PROMPT.replace("{website_content}", content_block).replace("{external_data}", external_data[:MAX_SOURCES_CHARS])

    message = client.messages.create(
        model=MODEL,
        max_tokens=8000,
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

    # Ensure all required keys exist and are strings (not nested objects)
    required = [
        "executive_summary", "project_focus", "team", "tokenomics", "roadmap",
        "unique_value_proposition", "competitive_landscape", "community_social",
        "on_chain_metrics", "recent_events", "risk_assessment",
    ]
    for k in required:
        val = report.get(k)
        if val is None or val == "":
            report[k] = "Not available."
        elif not isinstance(val, str):
            # Claude returned a nested object — flatten it to a string
            if isinstance(val, dict):
                report[k] = "\n".join(
                    f"• {str(field).replace('_', ' ').title()}: {str(v)}"
                    for field, v in val.items()
                )
            elif isinstance(val, list):
                report[k] = "\n".join(f"• {str(item)}" for item in val)
            else:
                report[k] = str(val)

    # Normalize sector
    valid_sectors = {
        "AI", "DeFi", "Gaming", "Meme", "L1/L2", "NFT", "Infrastructure",
        "Social", "RWA", "Privacy", "Oracle", "DEX", "Lending", "Payments", "Other",
    }
    sector = report.get("sector", "Other")
    if sector not in valid_sectors:
        report["sector"] = "Other"
    if "tags" not in report or not isinstance(report.get("tags"), str):
        report["tags"] = ""

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
        "sector": "Other", "tags": "",
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
        "sector": "Other", "tags": "",
        "scores": {"team": 5, "technology": 5, "tokenomics": 5, "community": 5, "execution": 5, "overall": 5.0},
    }


# ── Compare ──────────────────────────────────────────────────────────────────

COMPARE_PROMPT = """You are Deci, a crypto/tech project intelligence analyst. Compare the following projects for an investor.

{projects_data}

---

Write a competitive intelligence comparison covering:
1. **Side-by-Side Overview** — brief summary of each project's positioning
2. **Category Winners** — for each of Team, Technology, Tokenomics, Community, Execution: state which project wins and why (1-2 sentences each)
3. **Key Differentiators** — what truly separates these projects from each other
4. **Investment Verdict** — which project(s) are most compelling and why; who should avoid each

Be specific, direct, and data-driven. Reference scores and metrics. Format with clear headers."""


LIBRARY_COMPARE_PROMPT = """You are Deci, a crypto/tech project intelligence analyst. A new project has just been analyzed. Compare it against all existing projects in the research library to identify its competitive position.

## NEW PROJECT
NEW_PROJECT_DATA

## EXISTING LIBRARY (EXISTING_PROJECT_COUNT projects)
EXISTING_PROJECTS_DATA

---

Produce a structured competitive intelligence report with EXACTLY these four sections:

**UNIQUE STRENGTHS**
What this project does that none of the library projects do. Be specific — name the differentiating feature, mechanism, or market position. If nothing is truly unique, say so honestly.

**DIRECT COMPETITORS**
Which library projects are most similar and why. For each: name the project, explain the overlap (same sector, similar tech, same target market), and note the key similarity score.

**COMPETITIVE ADVANTAGES**
Where this project clearly wins vs. the library. Cite specific scores, metrics, or capabilities where it outperforms.

**COMPETITIVE WEAKNESSES**
Where library competitors are stronger. Cite specific scores, metrics, or capabilities where they outperform this project.

Be direct and data-driven. Reference scores. If the library has no relevant comparisons, state that clearly."""


def compare_against_library(new_project: dict, existing_projects: list[dict]) -> str:
    """Compare a newly analyzed project against all existing projects in the library."""
    if not existing_projects:
        return "No other projects in the library yet — re-run after adding more projects to see competitive intelligence."

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set.")

    client = anthropic.Anthropic(api_key=api_key)

    scores = new_project.get("scores", {})
    analysis = new_project.get("analysis", {}) or new_project
    new_block = (
        f"Name: {new_project.get('name', 'Unknown')} | URL: {new_project.get('url', 'N/A')}\n"
        f"Sector: {new_project.get('sector', 'Unknown')} | Tags: {new_project.get('tags', 'N/A')}\n"
        f"Scores — Team: {scores.get('team','N/A')}, Technology: {scores.get('technology','N/A')}, "
        f"Tokenomics: {scores.get('tokenomics','N/A')}, Community: {scores.get('community','N/A')}, "
        f"Execution: {scores.get('execution','N/A')}, Overall: {scores.get('overall','N/A')}\n"
        f"Summary: {new_project.get('summary') or new_project.get('executive_summary', 'N/A')}\n"
        f"Technology: {str(analysis.get('project_focus', 'N/A'))[:400]}\n"
        f"Unique Value: {str(analysis.get('unique_value_proposition', 'N/A'))[:300]}\n"
        f"Competitive Landscape: {str(analysis.get('competitive_landscape', 'N/A'))[:300]}"
    )

    lib_blocks = []
    for p in existing_projects[:20]:  # cap at 20 to stay within token limits
        ps = p.get("scores", {})
        pa = p.get("analysis", {}) or {}
        lib_blocks.append(
            f"• {p.get('name', 'Unknown')} ({p.get('url', '')})\n"
            f"  Sector: {p.get('sector', 'Unknown')} | Tags: {p.get('tags', 'N/A')}\n"
            f"  Scores: Team {ps.get('team','?')}, Tech {ps.get('technology','?')}, Overall {ps.get('overall','?')}\n"
            f"  Summary: {str(p.get('summary', 'N/A'))[:200]}\n"
            f"  Technology: {str(pa.get('project_focus', 'N/A'))[:200]}"
        )

    prompt = (
        LIBRARY_COMPARE_PROMPT
        .replace("NEW_PROJECT_DATA", new_block)
        .replace("EXISTING_PROJECT_COUNT", str(len(existing_projects)))
        .replace("EXISTING_PROJECTS_DATA", "\n\n".join(lib_blocks))
    )

    message = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    return message.content[0].text.strip()


def compare_projects(projects: list[dict]) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set.")

    client = anthropic.Anthropic(api_key=api_key)

    blocks = []
    for p in projects:
        scores = p.get("scores", {})
        analysis = p.get("analysis", {})
        block = f"""### {p.get('name', p.get('url', 'Unknown'))} — {p.get('url', '')}
Sector: {p.get('sector', 'Unknown')} | Tags: {p.get('tags', 'N/A')}
Scores — Team: {scores.get('team', 'N/A')}, Technology: {scores.get('technology', 'N/A')}, Tokenomics: {scores.get('tokenomics', 'N/A')}, Community: {scores.get('community', 'N/A')}, Execution: {scores.get('execution', 'N/A')}, Overall: {scores.get('overall', 'N/A')}
Summary: {p.get('summary', 'N/A')}

Team: {str(analysis.get('team', 'N/A'))[:400]}
Technology: {str(analysis.get('project_focus', 'N/A'))[:400]}
Tokenomics: {str(analysis.get('tokenomics', 'N/A'))[:300]}
Competitive Landscape: {str(analysis.get('competitive_landscape', 'N/A'))[:300]}
Risk: {str(analysis.get('risk_assessment', 'N/A'))[:300]}"""
        blocks.append(block)

    prompt = COMPARE_PROMPT.format(projects_data="\n\n---\n\n".join(blocks))

    message = client.messages.create(
        model=MODEL,
        max_tokens=3000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    return message.content[0].text.strip()
