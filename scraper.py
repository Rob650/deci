import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time
import re
import io
import warnings

try:
    import PyPDF2
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

PRIORITY_KEYWORDS = [
    "about", "team", "roadmap", "docs", "documentation", "whitepaper",
    "white-paper", "tokenomics", "token", "faq", "vision", "mission",
    "technology", "tech", "protocol", "ecosystem", "investors", "advisors",
    "partners", "foundation", "careers", "blog", "news", "litepaper",
    "overview", "solution", "product", "features", "how-it-works",
    "use-cases", "security", "governance", "community",
]

FALLBACK_SUBPATHS = [
    "/about", "/team", "/docs", "/whitepaper", "/roadmap", "/faq",
    "/tokenomics", "/about-us", "/our-team", "/documentation",
    "/technology", "/ecosystem", "/blog", "/news", "/litepaper",
    "/protocol", "/product", "/features",
]

MAX_PAGES = 15
REQUEST_TIMEOUT = 20
CRAWL_DELAY = 0.5
MAX_RETRIES = 3
# Threshold below which we treat the page as JS-rendered and fall back to Playwright
JS_RENDER_THRESHOLD = 100


def is_same_domain(url: str, base: str) -> bool:
    try:
        return urlparse(url).netloc == urlparse(base).netloc
    except Exception:
        return False


def is_likely_content_page(url: str) -> bool:
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
    """Fetch a page with retry logic and SSL fallback."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
                verify=True,
            )
            if resp.status_code == 200:
                content_type = resp.headers.get("content-type", "")
                if "pdf" in content_type and PDF_SUPPORT:
                    return extract_pdf_text(resp.content)
                if "text" not in content_type and "html" not in content_type:
                    return None
                return resp.text
            if resp.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            return None
        except requests.exceptions.SSLError:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    resp = session.get(
                        url,
                        headers=HEADERS,
                        timeout=REQUEST_TIMEOUT,
                        allow_redirects=True,
                        verify=False,
                    )
                if resp.status_code == 200:
                    content_type = resp.headers.get("content-type", "")
                    if "pdf" in content_type and PDF_SUPPORT:
                        return extract_pdf_text(resp.content)
                    if "text" not in content_type and "html" not in content_type:
                        return None
                    return resp.text
            except Exception:
                pass
            break
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            time.sleep(2 ** attempt)
        except Exception:
            break
    return None


def fetch_with_playwright(url: str) -> str | None:
    """Render the page in a headless browser and return its visible text."""
    if not PLAYWRIGHT_AVAILABLE:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
            )
            page = browser.new_page(
                user_agent=HEADERS["User-Agent"],
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            page.goto(url, wait_until="networkidle", timeout=30000)
            # Give extra time for lazy-loaded content
            page.wait_for_timeout(2000)
            text = page.inner_text("body")
            browser.close()
            return text if text else None
    except PlaywrightTimeout:
        # If networkidle times out, try domcontentloaded
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
                )
                page = browser.new_page(user_agent=HEADERS["User-Agent"])
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(3000)
                text = page.inner_text("body")
                browser.close()
                return text if text else None
        except Exception:
            return None
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
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header",
                     "aside", "form", "iframe", "img", "svg", "button", "input"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def clean_playwright_text(raw_text: str) -> str:
    """Clean up text already extracted by Playwright (no HTML parsing needed)."""
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    return "\n".join(lines)


def get_page_title(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.find("title")
    return title.get_text(strip=True) if title else ""


def follow_meta_refresh(html: str, base_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.find("meta", attrs={"http-equiv": re.compile(r"refresh", re.I)})
    if meta and meta.get("content"):
        match = re.search(r"url=(.+)", meta["content"], re.I)
        if match:
            return urljoin(base_url, match.group(1).strip().strip("'\""))
    return None


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
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed_base = urlparse(url)
    base_origin = f"{parsed_base.scheme}://{parsed_base.netloc}"

    session = requests.Session()
    session.headers.update(HEADERS)

    visited = set()
    pages = []
    queue: list[tuple[str, int]] = [(url, 10)]
    # Track whether the site needs JS rendering
    needs_js_render = False

    while queue and len(pages) < MAX_PAGES:
        current_url, _ = queue.pop(0)
        if current_url in visited:
            continue
        visited.add(current_url)

        raw = fetch_page(current_url, session)

        # Handle meta refresh
        if raw:
            refresh_target = follow_meta_refresh(raw, current_url)
            if refresh_target and refresh_target not in visited:
                refreshed = fetch_page(refresh_target, session)
                if refreshed:
                    raw = refreshed
                    current_url = refresh_target
                    visited.add(current_url)

        # Detect JS-rendered pages on the first fetch
        if current_url == url:
            text_preview = clean_text(raw) if raw else ""
            if len(text_preview) < JS_RENDER_THRESHOLD:
                needs_js_render = True

        if needs_js_render and PLAYWRIGHT_AVAILABLE:
            # Use headless browser for this site
            pw_text = fetch_with_playwright(current_url)
            if pw_text:
                text = clean_playwright_text(pw_text)
                title = ""
                if raw:
                    title = get_page_title(raw)
                if len(text) > 50:
                    pages.append({"url": current_url, "title": title, "text": text[:8000]})

                # Discover subpages for JS sites via common paths (first page only)
                if current_url == url and len(pages) <= 1:
                    for subpath in FALLBACK_SUBPATHS:
                        candidate = base_origin + subpath
                        if candidate not in visited:
                            queue.append((candidate, 8))
                    queue.sort(key=lambda x: x[1], reverse=True)
                time.sleep(CRAWL_DELAY)
                continue

        if not raw:
            # Try fallback subpaths if main page is inaccessible
            if current_url == url:
                for subpath in FALLBACK_SUBPATHS:
                    candidate = base_origin + subpath
                    if candidate not in visited:
                        queue.append((candidate, 8))
            continue

        if current_url.lower().endswith(".pdf"):
            pages.append({"url": current_url, "title": "PDF Document", "text": raw})
            time.sleep(CRAWL_DELAY)
            continue

        title = get_page_title(raw)
        text = clean_text(raw)

        if len(text) > 50:
            pages.append({"url": current_url, "title": title, "text": text[:8000]})

            if len(text) < JS_RENDER_THRESHOLD and current_url == url:
                for subpath in FALLBACK_SUBPATHS:
                    candidate = base_origin + subpath
                    if candidate not in visited:
                        queue.insert(0, (candidate, 8))
        elif current_url == url:
            for subpath in FALLBACK_SUBPATHS:
                candidate = base_origin + subpath
                if candidate not in visited:
                    queue.insert(0, (candidate, 8))

        if len(pages) <= 5:
            links = discover_links(raw, url)
            for link_url, link_text, score in links:
                if link_url not in visited:
                    queue.append((link_url, score))
            queue.sort(key=lambda x: x[1], reverse=True)

        time.sleep(CRAWL_DELAY)

    if not pages:
        return {
            "base_url": url,
            "pages_scraped": 0,
            "content": [],
            "error": "Could not retrieve any content from the provided URL.",
        }

    return {
        "base_url": url,
        "pages_scraped": len(pages),
        "content": pages,
        "error": None,
    }
