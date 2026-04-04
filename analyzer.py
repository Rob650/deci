import os
import json
import re
import anthropic

MODEL = "claude-sonnet-4-20250514"
MAX_CONTENT_CHARS = 80000  # total chars sent to Claude

SYSTEM_PROMPT = """You are Deci, an elite crypto and technology project intelligence analyst.
You produce structured, honest, and deeply researched reports on blockchain, crypto, and tech projects.
You are direct, concise, and highlight both strengths and risks without hype.
Your analysis is based solely on the provided scraped website content — you do not speculate beyond what's there.
If information is missing or unclear, say so explicitly rather than guessing."""

ANALYSIS_PROMPT = """Analyze the following scraped content from a project's website and produce a structured intelligence report.

SCRAPED CONTENT:
{content}

---

Produce a JSON report with EXACTLY these six keys. Be thorough but concise for each section:

{{
  "project_focus": "What the project does, what problem it solves, and who it serves. 2-4 sentences.",
  "team": "Who's behind the project — names, roles, backgrounds, relevant experience, LinkedIn/social presence if mentioned. If no team info found, say so explicitly.",
  "roadmap": "Timeline and milestones — what has already been delivered vs. what is planned. Include specific dates/quarters if mentioned. If no roadmap found, say so.",
  "unique_value_proposition": "What genuinely differentiates this project from alternatives. Be specific — avoid vague claims like 'innovative' or 'revolutionary'. Quote their own claims and assess them.",
  "competitors": "Who else operates in this space. Name actual competitors if mentioned, or infer from the problem domain. Assess competitive positioning.",
  "execution_risk": "Red flags, concerns, and honest assessment of delivery likelihood. Consider: team experience, funding clarity, technical complexity, market timing, regulatory exposure, vague claims, missing info, and any other risk factors. Be direct."
}}

Return ONLY valid JSON. No markdown, no code blocks, no preamble."""


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
    """
    Takes scraper output, calls Claude, returns structured report dict.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable is not set.")

    client = anthropic.Anthropic(api_key=api_key)

    content_block = build_content_block(scraped_data)

    if not content_block.strip():
        return {
            "project_focus": "No content could be extracted from the provided URL.",
            "team": "N/A",
            "roadmap": "N/A",
            "unique_value_proposition": "N/A",
            "competitors": "N/A",
            "execution_risk": "Unable to analyze — no content was scraped.",
        }

    prompt = ANALYSIS_PROMPT.format(content=content_block)

    message = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()

    # Strip markdown code fences if Claude wrapped it anyway
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        report = json.loads(raw)
    except json.JSONDecodeError:
        # Attempt to extract JSON object from response
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            report = json.loads(match.group(0))
        else:
            report = {
                "project_focus": "Analysis complete but response parsing failed.",
                "team": "See raw output.",
                "roadmap": "See raw output.",
                "unique_value_proposition": "See raw output.",
                "competitors": "See raw output.",
                "execution_risk": raw[:2000],
            }

    # Ensure all keys present
    keys = ["project_focus", "team", "roadmap", "unique_value_proposition", "competitors", "execution_risk"]
    for k in keys:
        if k not in report:
            report[k] = "Not available."

    return report
