import os
import json
import re
import anthropic

MODEL = "claude-sonnet-4-20250514"
MAX_CONTENT_CHARS = 80000

SYSTEM_PROMPT = """You are Deci, an elite crypto and technology project intelligence analyst.
You produce structured, honest, and deeply researched reports on blockchain, crypto, and tech projects.
You are direct, concise, and highlight both strengths and risks without hype.
Your analysis is based solely on the provided scraped website content — you do not speculate beyond what's there.
If information is missing or unclear, say so explicitly rather than guessing."""

ANALYSIS_PROMPT = """Analyze the following scraped content from a project's website and produce a structured intelligence report.

SCRAPED CONTENT:
{content}

---

Produce a JSON report with EXACTLY this structure. Be thorough but concise.

{{
  "name": "The project's actual name (extract from the website)",
  "sector": "Primary sector — one of: defi, dex, lending, gaming, gamefi, nft, layer1, layer2, bridge, oracle, dao, social, identity, privacy, payments, stablecoin, ai, data, storage, infrastructure, wallet, portfolio, other",
  "tags": ["2-6 tags that describe the project, e.g. defi, ethereum, lending, yield, governance"],
  "summary": "2-3 sentence executive summary — what it is, who it's for, and why it matters.",
  "scores": {{
    "team": <integer 1-10>,
    "tech": <integer 1-10>,
    "tokenomics": <integer 1-10>,
    "community": <integer 1-10>,
    "execution": <integer 1-10>,
    "overall": <integer 1-10>
  }},
  "categories": {{
    "project_focus": "What the project does, what problem it solves, and who it serves. 2-4 sentences.",
    "team": "Who's behind it — names, roles, backgrounds, relevant experience. If no team info found, say so explicitly.",
    "technology": "Technical architecture, stack, chain(s), novel mechanisms, open-source status, audits. Be specific.",
    "tokenomics": "Token name/ticker, supply, distribution, utility, vesting schedules, inflation/deflation mechanics. If no token, say so.",
    "roadmap": "Timeline and milestones — delivered vs. planned. Include specific dates/quarters if mentioned.",
    "market_opportunity": "Size and nature of the market they're targeting. TAM, growth trends, timing rationale.",
    "competitors": "Named competitors or inferred from the problem domain. Competitive positioning and differentiation.",
    "community_ecosystem": "Community size, channels, partnerships, integrations, developer ecosystem, grants programs.",
    "funding_investors": "Funding rounds, amounts, investors, grants, revenue model. If undisclosed, say so.",
    "unique_value_proposition": "What genuinely differentiates this project. Quote their own claims and critically assess them.",
    "regulatory_legal": "Regulatory exposure, jurisdiction, compliance measures, legal risks, KYC/AML stance.",
    "execution_risk": "Red flags, concerns, delivery likelihood. Consider team experience, funding, technical complexity, vague claims, missing info."
  }}
}}

Scoring guide (be honest, not generous):
- team: 1-3 anonymous/no info, 4-6 some background, 7-8 strong doxxed team, 9-10 exceptional track record
- tech: 1-3 vague/unproven, 4-6 reasonable approach, 7-8 solid technical depth, 9-10 genuinely novel/audited
- tokenomics: 1-3 poorly designed/missing, 4-6 standard/adequate, 7-8 well-designed, 9-10 exemplary
- community: 1-3 no evidence, 4-6 early/small, 7-8 active community, 9-10 large engaged ecosystem
- execution: 1-3 concept only, 4-6 early stage, 7-8 live product with traction, 9-10 proven delivery
- overall: weighted average reflecting your holistic assessment

Return ONLY valid JSON. No markdown, no code blocks, no preamble."""

COMPETITIVE_PROMPT = """You are Deci, a crypto and tech project intelligence analyst.

A user just analyzed a new project. Compare it against previously researched projects in the same space.

NEW PROJECT: {new_name}
Summary: {new_summary}
Scores: {new_scores}
Tags: {new_tags}

PREVIOUSLY RESEARCHED PROJECTS:
{similar_projects}

---

Write a concise Competitive Intelligence analysis (4-6 paragraphs) covering:
1. Where the new project stands relative to the field — stronger or weaker overall?
2. Category-by-category highlights: which project leads in team, tech, tokenomics, execution?
3. The new project's unique angle vs. existing players — genuine differentiation or me-too?
4. Key risks that emerge from comparing (e.g. if a competitor is further ahead)
5. One-sentence verdict: which project(s) look most promising and why?

Be direct, specific, and reference project names. Avoid generic praise."""

COMPARE_PROMPT = """You are Deci, a crypto and tech project intelligence analyst.

Compare these {count} projects side-by-side based on their stored intelligence data.

{projects_block}

---

Write a thorough comparative analysis (6-8 paragraphs) covering:
1. Executive overview — what each project does and their fundamental differences
2. Team comparison — relative strength, track records, transparency
3. Technology comparison — which has the most robust/novel approach
4. Tokenomics comparison — which has better token design (if applicable)
5. Execution comparison — who has shipped more, demonstrated more traction
6. Risk comparison — relative risk profiles
7. Market positioning — who targets what, overlap and differentiation
8. Final recommendation — rank them and explain why

Be direct, specific, and name each project throughout. Avoid vague statements."""


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


def analyze(scraped_data: dict) -> dict:
    """Takes scraper output, calls Claude, returns structured report dict."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable is not set.")

    client = anthropic.Anthropic(api_key=api_key)
    content_block = build_content_block(scraped_data)

    if not content_block.strip():
        return _empty_report()

    prompt = ANALYSIS_PROMPT.format(content=content_block)

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
                return _empty_report(raw_fallback=raw)
        else:
            return _empty_report(raw_fallback=raw)

    return _normalise_report(report)


def generate_competitive_analysis(new_report: dict, similar_projects: list) -> str:
    """Generate competitive intelligence comparing new project vs stored ones."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return ""

    client = anthropic.Anthropic(api_key=api_key)

    similar_blocks = []
    for p in similar_projects:
        scores = p.get("scores") or {}
        block = (
            f"- {p.get('name', p.get('url', 'Unknown'))}: "
            f"{p.get('summary', 'No summary.')} | Scores: {json.dumps(scores)}"
        )
        similar_blocks.append(block)

    prompt = COMPETITIVE_PROMPT.format(
        new_name=new_report.get("name", "Unknown"),
        new_summary=new_report.get("summary", ""),
        new_scores=json.dumps(new_report.get("scores", {})),
        new_tags=", ".join(new_report.get("tags", [])),
        similar_projects="\n".join(similar_blocks),
    )

    try:
        message = client.messages.create(
            model=MODEL,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
    except Exception:
        return ""


def generate_comparison(projects: list) -> str:
    """Generate a multi-project comparison analysis."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return ""

    client = anthropic.Anthropic(api_key=api_key)

    blocks = []
    for p in projects:
        scores = p.get("scores") or {}
        cats = p.get("categories") or {}
        block = (
            f"PROJECT: {p.get('name', p.get('url', 'Unknown'))}\n"
            f"URL: {p.get('url', '')}\n"
            f"Sector: {p.get('sector', 'unknown')}\n"
            f"Summary: {p.get('summary', 'No summary.')}\n"
            f"Scores: {json.dumps(scores)}\n"
            f"Team: {cats.get('team', 'N/A')[:400]}\n"
            f"Technology: {cats.get('technology', 'N/A')[:400]}\n"
            f"Execution Risk: {cats.get('execution_risk', 'N/A')[:400]}\n"
        )
        blocks.append(block)

    prompt = COMPARE_PROMPT.format(
        count=len(projects),
        projects_block="\n---\n".join(blocks),
    )

    try:
        message = client.messages.create(
            model=MODEL,
            max_tokens=2500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
    except Exception:
        return "Comparison generation failed."


def _normalise_report(report: dict) -> dict:
    """Ensure all expected keys are present with sensible defaults."""
    report.setdefault("name", "Unknown Project")
    report.setdefault("sector", "other")
    report.setdefault("tags", [])
    report.setdefault("summary", "")

    scores = report.setdefault("scores", {})
    for key in ["team", "tech", "tokenomics", "community", "execution", "overall"]:
        val = scores.get(key, 5)
        try:
            scores[key] = max(1, min(10, int(val)))
        except (TypeError, ValueError):
            scores[key] = 5

    cats = report.setdefault("categories", {})
    for key in [
        "project_focus", "team", "technology", "tokenomics", "roadmap",
        "market_opportunity", "competitors", "community_ecosystem",
        "funding_investors", "unique_value_proposition", "regulatory_legal",
        "execution_risk",
    ]:
        cats.setdefault(key, "Not available.")

    return report


def _empty_report(raw_fallback: str = None) -> dict:
    cats = {k: "Not available." for k in [
        "project_focus", "team", "technology", "tokenomics", "roadmap",
        "market_opportunity", "competitors", "community_ecosystem",
        "funding_investors", "unique_value_proposition", "regulatory_legal",
        "execution_risk",
    ]}
    if raw_fallback:
        cats["project_focus"] = raw_fallback[:2000]

    return {
        "name": "Unknown Project",
        "sector": "other",
        "tags": [],
        "summary": "No content could be extracted from the provided URL.",
        "scores": {k: 1 for k in ["team", "tech", "tokenomics", "community", "execution", "overall"]},
        "categories": cats,
    }
