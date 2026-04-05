import os
import asyncio
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
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
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
    "Accept-Encoding": "gzip, deflate",
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
    "/about", "/docs", "/tokenomics", "/team", "/whitepaper", "/roadmap",
]

MAX_PAGES = 6
REQUEST_TIMEOUT = 20
CRAWL_DELAY = 0.5
MAX_RETRIES = 3
# Threshold below which we treat the page as JS-rendered and fall back to Playwright
JS_RENDER_THRESHOLD = 100


def _decode_response(resp: requests.Response) -> str:
    """Decode a response body with proper encoding detection."""
    # Try chardet-detected encoding first
    detected = resp.apparent_encoding
    if detected:
        try:
            return resp.content.decode(detected, errors="replace")
        except (LookupError, UnicodeDecodeError):
            pass
    # Fall back to UTF-8 with replacement
    return resp.content.decode("utf-8", errors="replace")


def is_garbled(text: str, threshold: float = 0.10) -> bool:
    """Return True if the text looks like compressed/binary garbage.

    Checks both low control chars AND high bytes (>127) so brotli-compressed
    responses that requests can't decompress are caught, not just encoding issues.
    Uses a small sample to keep this fast.
    """
    if not text:
        return True
    sample = text[:2000]
    non_printable = sum(
        1 for ch in sample
        if ord(ch) > 127 or (ord(ch) < 32 and ch not in ("\n", "\r", "\t"))
    )
    return (non_printable / len(sample)) > threshold


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
                return _decode_response(resp)
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
                    return _decode_response(resp)
            except Exception:
                pass
            break
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            time.sleep(2 ** attempt)
        except Exception:
            break
    return None


async def fetch_with_playwright(url: str) -> str | None:
    """Render the page in a headless browser and return its visible text."""
    print(f"PLAYWRIGHT_AVAILABLE: {PLAYWRIGHT_AVAILABLE}")
    if not PLAYWRIGHT_AVAILABLE:
        return None
    print(f"Attempting Playwright fetch for: {url}")
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
                executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"),
            )
            page = await browser.new_page(
                user_agent=HEADERS["User-Agent"],
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            try:
                await page.goto(url, wait_until="networkidle", timeout=15000)
            except PlaywrightTimeout:
                # If networkidle times out, try domcontentloaded
                await page.goto(url, wait_until="domcontentloaded", timeout=10000)
            # Give extra time for lazy-loaded content
            await page.wait_for_timeout(500)
            text = await page.inner_text("body")
            await browser.close()
            if text:
                text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
            return text if text else None
    except PlaywrightTimeout:
        # Retry with a fresh browser using domcontentloaded
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
                    executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"),
                )
                page = await browser.new_page(user_agent=HEADERS["User-Agent"])
                await page.goto(url, wait_until="domcontentloaded", timeout=10000)
                await page.wait_for_timeout(500)
                text = await page.inner_text("body")
                await browser.close()
                if text:
                    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
                return text if text else None
        except Exception as e:
            print(f"Playwright error (domcontentloaded fallback): {e}")
            return None
    except Exception as e:
        print(f"Playwright error: {e}")
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
    # Strip null bytes and other non-printable control characters
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
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


IMPORTANT_SUBDOMAIN_PREFIXES = ["docs.", "whitepaper.", "app.", "wiki.", "learn.", "lite.", "paper."]
SOCIAL_DOMAINS = ["twitter.com", "x.com", "discord.gg", "discord.com", "t.me", "telegram.me"]


def _extract_important_links(html: str, base_url: str) -> list[tuple[str, int]]:
    """Find links to subdomains, GitHub repos, social media, and key info pages.

    Returns list of (absolute_url, priority_score) for URLs outside the main domain
    that are likely to contain valuable project information.
    """
    soup = BeautifulSoup(html, "html.parser")
    base_parsed = urlparse(base_url)
    base_netloc = base_parsed.netloc
    # Root domain without subdomains (e.g. "project.io" from "www.project.io")
    parts = base_netloc.split(".")
    root_domain = ".".join(parts[-2:]) if len(parts) >= 2 else base_netloc

    results = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("mailto:") or href.startswith("tel:"):
            continue
        absolute = urljoin(base_url, href)
        absolute = absolute.split("#")[0]
        if not absolute or absolute in seen:
            continue
        if not is_likely_content_page(absolute):
            continue

        parsed = urlparse(absolute)
        netloc = parsed.netloc

        # Skip same domain — already handled by discover_links
        if netloc == base_netloc:
            continue

        score = 0

        # Subdomains of the same root domain (docs.project.io, app.project.io, etc.)
        if root_domain in netloc and netloc != base_netloc:
            for prefix in IMPORTANT_SUBDOMAIN_PREFIXES:
                if netloc.startswith(prefix) or netloc.startswith("www." + prefix.rstrip(".")):
                    score = 9
                    break
            if score == 0:
                # Any other subdomain of the project's root domain
                score = 7

        # GitHub repos (org/repo level, not just github.com root)
        elif "github.com" in netloc:
            path_parts = [p for p in parsed.path.split("/") if p]
            if len(path_parts) >= 2:
                score = 8

        # Social media / community
        else:
            for social in SOCIAL_DOMAINS:
                if social in netloc:
                    score = 4
                    break

        if score > 0:
            seen.add(absolute)
            results.append((absolute, score))

    return results


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


async def _scrape_inner(url: str, pages: list) -> dict:
    """
    Inner scrape logic. Appends scraped pages to the shared `pages` list so
    the caller can access partial results even if a timeout cancels this coroutine.
    """
    parsed_base = urlparse(url)
    base_origin = f"{parsed_base.scheme}://{parsed_base.netloc}"

    session = requests.Session()
    session.headers.update(HEADERS)

    visited = set()
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

        # Detect JS-rendered pages or encoding failures on the first fetch
        if current_url == url:
            # Check raw bytes first — brotli garbage is visible before clean_text strips it
            if raw and is_garbled(raw):
                print(f"Garbled/compressed response detected for: {current_url} — going straight to Playwright")
                needs_js_render = True
                raw = None  # discard garbage so Playwright result isn't mixed with it
            else:
                text_preview = clean_text(raw) if raw else ""
                print(f"Initial fetch text length: {len(text_preview)}")
                if len(text_preview) < JS_RENDER_THRESHOLD or is_garbled(text_preview):
                    needs_js_render = True
                    if is_garbled(text_preview):
                        print("Content appears garbled — falling back to Playwright")

        if needs_js_render and PLAYWRIGHT_AVAILABLE:
            # Use headless browser for this site
            print(f"Using Playwright fallback for: {current_url}")
            pw_text = await fetch_with_playwright(current_url)
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
                await asyncio.sleep(CRAWL_DELAY)
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
            await asyncio.sleep(CRAWL_DELAY)
            continue

        title = get_page_title(raw)
        text = clean_text(raw)

        # If static fetch returned garbage, try Playwright before giving up
        if is_garbled(text) and PLAYWRIGHT_AVAILABLE:
            print(f"Garbled content detected for {current_url} — retrying with Playwright")
            pw_text = await fetch_with_playwright(current_url)
            if pw_text:
                text = clean_playwright_text(pw_text)

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

            # On the main page, also discover important external links:
            # subdomains (docs.*, app.*), GitHub repos, social media
            if current_url == url:
                for ext_url, ext_score in _extract_important_links(raw, url):
                    if ext_url not in visited:
                        queue.append((ext_url, ext_score))

            queue.sort(key=lambda x: x[1], reverse=True)

        await asyncio.sleep(CRAWL_DELAY)

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


async def scrape(url: str) -> dict:
    """
    Main entry point with a 45-second overall timeout.
    Returns whatever pages were collected if the timeout is hit.
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    pages: list = []
    try:
        return await asyncio.wait_for(_scrape_inner(url, pages), timeout=45)
    except asyncio.TimeoutError:
        print(f"Scrape timed out after 45s for {url} — returning {len(pages)} partial page(s)")
        if pages:
            return {
                "base_url": url,
                "pages_scraped": len(pages),
                "content": pages,
                "error": None,
            }
        return {
            "base_url": url,
            "pages_scraped": 0,
            "content": [],
            "error": "Scrape timed out before any content could be retrieved.",
        }
