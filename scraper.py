import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time
import re
import io

try:
    import PyPDF2
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DeciResearchBot/1.0; +https://deci.app)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

PRIORITY_KEYWORDS = [
    "about", "team", "roadmap", "docs", "documentation", "whitepaper",
    "white-paper", "tokenomics", "token", "faq", "vision", "mission",
    "technology", "tech", "protocol", "ecosystem", "investors", "advisors",
    "partners", "foundation", "careers", "blog", "news", "litepaper",
    "overview", "solution", "product", "features", "how-it-works",
    "use-cases", "security", "governance", "community",
]

MAX_PAGES = 15
REQUEST_TIMEOUT = 15
CRAWL_DELAY = 0.5


def is_same_domain(url: str, base: str) -> bool:
    try:
        return urlparse(url).netloc == urlparse(base).netloc
    except Exception:
        return False


def is_likely_content_page(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    # Skip common non-content paths
    skip_patterns = [
        r"\.(png|jpg|jpeg|gif|svg|webp|ico|css|js|woff|woff2|ttf|eot|xml|json)$",
        r"/(cdn-cgi|wp-admin|wp-login|wp-json|api/|graphql|_next/|static/|assets/)",
        r"\?.*page=\d+",
        r"#",
    ]
    for pattern in skip_patterns:
        if re.search(pattern, url, re.IGNORECASE):
            return False
    return True


def score_link(href: str, text: str) -> int:
    score = 0
    combined = (href + " " + text).lower()
    for kw in PRIORITY_KEYWORDS:
        if kw in combined:
            score += 2
    return score


def fetch_page(url: str, session: requests.Session) -> str | None:
    try:
        resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if resp.status_code != 200:
            return None
        content_type = resp.headers.get("content-type", "")
        if "pdf" in content_type and PDF_SUPPORT:
            return extract_pdf_text(resp.content)
        if "text" not in content_type and "html" not in content_type:
            return None
        return resp.text
    except Exception:
        return None


def extract_pdf_text(content: bytes) -> str | None:
    if not PDF_SUPPORT:
        return None
    try:
        reader = PyPDF2.PdfReader(io.BytesIO(content))
        parts = []
        for page in reader.pages[:20]:
            text = page.extract_text()
            if text:
                parts.append(text)
        return "\n".join(parts) if parts else None
    except Exception:
        return None


def clean_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    # Remove boilerplate tags
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header",
                     "aside", "form", "iframe", "img", "svg", "button", "input"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    # Collapse whitespace
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def get_page_title(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.find("title")
    return title.get_text(strip=True) if title else ""


def discover_links(html: str, base_url: str) -> list[tuple[str, str, int]]:
    """Returns list of (absolute_url, link_text, score) sorted by score desc."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(strip=True)
        if not href or href.startswith("mailto:") or href.startswith("tel:"):
            continue
        absolute = urljoin(base_url, href)
        # Strip fragments
        absolute = absolute.split("#")[0]
        if not absolute or absolute in seen:
            continue
        if not is_same_domain(absolute, base_url):
            continue
        if not is_likely_content_page(absolute):
            continue
        seen.add(absolute)
        score = score_link(href, text)
        results.append((absolute, text, score))
    results.sort(key=lambda x: x[2], reverse=True)
    return results


def scrape(url: str) -> dict:
    """
    Main entry point. Returns:
    {
        "base_url": str,
        "pages_scraped": int,
        "content": [{"url": str, "title": str, "text": str}],
        "error": str | None
    }
    """
    # Normalize URL
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    session = requests.Session()
    session.headers.update(HEADERS)

    visited = set()
    pages = []
    queue: list[tuple[str, int]] = [(url, 10)]  # (url, score)

    while queue and len(pages) < MAX_PAGES:
        current_url, _ = queue.pop(0)
        if current_url in visited:
            continue
        visited.add(current_url)

        raw = fetch_page(current_url, session)
        if not raw:
            continue

        # If it's already plain text (PDF extracted), use directly
        if current_url.lower().endswith(".pdf"):
            pages.append({"url": current_url, "title": "PDF Document", "text": raw})
            time.sleep(CRAWL_DELAY)
            continue

        title = get_page_title(raw)
        text = clean_text(raw)

        if len(text) > 200:
            pages.append({"url": current_url, "title": title, "text": text[:8000]})

        # Discover more links from this page (only for the first few pages)
        if len(pages) <= 5:
            links = discover_links(raw, url)
            for link_url, link_text, score in links:
                if link_url not in visited:
                    queue.append((link_url, score))
            # Re-sort queue by score
            queue.sort(key=lambda x: x[1], reverse=True)

        time.sleep(CRAWL_DELAY)

    if not pages:
        return {"base_url": url, "pages_scraped": 0, "content": [], "error": "Could not retrieve any content from the provided URL."}

    return {
        "base_url": url,
        "pages_scraped": len(pages),
        "content": pages,
        "error": None,
    }
