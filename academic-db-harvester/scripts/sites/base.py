"""Base class for academic database site adapters.

Each browser-backed adapter controls one already-open browser tab (via
Playwright CDP) and implements a small uniform interface so the harvester can
stay source-agnostic. Sources reachable over a public HTTP API subclass
`ApiSiteAdapter` at the bottom of this file instead: it implements the same
interface, so the pipeline does not care which transport a source uses.

Adapter contract (implement in subclasses):
    name            : str  - registry key, e.g. "ieee"
    display_name    : str  - human-readable name
    hosts           : list[str] - URL prefixes used to find the right tab
    has_pdf         : bool - whether the site hosts full-text PDFs itself
    login_required  : bool - whether harvesting is blocked without login
    requires_browser: bool - False for pure-HTTP/API sources (no CDP, no login)

    build_search_url(keyword, years, sort) -> str
    navigate_to_search(keyword, years, sort)
    extract_result_list() -> list[dict]
    go_next_page() -> bool
    open_detail(url) -> bool
    extract_detail() -> dict
    check_login_status() -> bool
    extract_bibtex() -> str      (optional, may return "")
    extract_ris() -> str         (optional, may return "")
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from utils import setup_logger

logger = setup_logger("site_adapter")


class BaseSiteAdapter:
    name = "base"
    display_name = "Base Site"
    hosts: list[str] = []
    has_pdf = False
    login_required = True
    requires_browser = True        # False => pure HTTP/API source, no CDP tab needed

    def __init__(self, page, ws_url: str):
        self.page = page
        self.ws_url = ws_url

    # ── shared helpers ──────────────────────────────────────────────

    def _sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def _wait_for_any(self, selector: str, timeout: int = 15000) -> None:
        """Wait for any of the given selectors; never raises."""
        try:
            self.page.wait_for_selector(selector, timeout=timeout)
        except Exception:
            pass

    def _eval(self, js: str, arg=None, default=None):
        """Evaluate JS on the page; returns default on failure."""
        try:
            if arg is None:
                return self.page.evaluate(js)
            return self.page.evaluate(js, arg)
        except Exception as e:
            logger.error("[%s] evaluate failed: %s", self.name, e)
            return default

    def get_cookies(self) -> list:
        """Browser cookies for reuse in direct HTTP requests."""
        try:
            return self.page.context.cookies()
        except Exception:
            return []

    def extract_bibtex(self) -> str:
        return ""

    def extract_ris(self) -> str:
        return ""

    # ── interface (subclasses must implement) ───────────────────────

    def build_search_url(self, keyword: str, years: str = "2020-2025",
                         sort: str = "relevance") -> str:
        raise NotImplementedError

    def navigate_to_search(self, keyword: str, years: str,
                           sort: str = "relevance") -> None:
        raise NotImplementedError

    def extract_result_list(self) -> list[dict]:
        raise NotImplementedError

    def go_next_page(self) -> bool:
        raise NotImplementedError

    def open_detail(self, paper_url: str) -> bool:
        raise NotImplementedError

    def extract_detail(self) -> dict:
        raise NotImplementedError

    def check_login_status(self) -> bool:
        raise NotImplementedError


# ── API sources ────────────────────────────────────────────────────────


class ApiSiteAdapter(BaseSiteAdapter):
    """Adapter for sources reachable over plain HTTP (open APIs, public feeds).

    Subclasses implement `fetch_page(keyword, years, sort, page)`; the
    browser-facing methods below are satisfied by it, so the pipeline in
    `harvester.py` stays identical for browser-backed and API sources.

    Contract for subclasses:
        name, display_name, has_pdf, page_size
        fetch_page(keyword, years, sort, page) -> list[dict]
        (optional) enrich(paper) -> dict           extra detail for one record
        (optional) build_pdf_url(paper) -> str
    """

    requires_browser = False
    login_required = False
    page_size = 200
    host = ""                        # documented endpoint host, for error messages
    request_timeout = 45
    polite_ua = "academic-db-harvester/1.0 (OpenCode skill)"

    def __init__(self, page=None, ws_url: str | None = None):
        super().__init__(page, ws_url)
        self._keyword = ""
        self._years = ""
        self._sort = "relevance"
        self._page = 0
        self._records: list[dict] = []
        self._has_more = True

    # ── HTTP helper ─────────────────────────────────────────────────────

    def _http_get(self, url: str, headers: dict | None = None) -> bytes:
        """GET with a polite User-Agent, retries and backoff. Raises on failure."""
        import urllib.request
        import urllib.error

        h = {"User-Agent": self.polite_ua, "Accept": "application/json"}
        h.update(headers or {})
        last = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers=h)
                with urllib.request.urlopen(req, timeout=self.request_timeout) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                last = e
                if e.code in (429, 500, 502, 503, 504):
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
            except Exception as e:              # timeouts, DNS resets
                last = e
                time.sleep(1.5 * (attempt + 1))
        raise last if last else RuntimeError("request failed: %s" % url)

    def parse_years(self, years: str) -> tuple:
        """'2020-2025' / '2020' / '' -> (start_year, end_year), either may be None"""
        years = (years or "").strip()
        if not years:
            return None, None
        if "-" in years:
            a, b = years.split("-", 1)
        else:
            a = b = years
        try:
            return int(a.strip()[:4]), int(b.strip()[:4])
        except ValueError:
            return None, None

    # ── browser-facing interface, implemented over HTTP ──────────────────

    def fetch_page(self, keyword: str, years: str, sort: str, page: int) -> list[dict]:
        raise NotImplementedError

    def navigate_to_search(self, keyword: str, years: str, sort: str = "relevance") -> None:
        self._keyword, self._years, self._sort, self._page = keyword, years, sort, 1
        self._records = self.fetch_page(keyword, years, sort, 1)
        self._has_more = len(self._records) >= self.page_size

    def extract_result_list(self) -> list[dict]:
        return list(self._records)

    def go_next_page(self) -> bool:
        if not self._has_more:
            return False
        self._page += 1
        logger.info("[%s] fetching page %d", self.name, self._page)
        nxt = self.fetch_page(self._keyword, self._years, self._sort, self._page)
        if not nxt:
            self._has_more = False
            return False
        self._records = nxt
        return True

    def open_detail(self, paper_url: str) -> bool:
        """API sources return complete records; no detail page to open."""
        return True

    def extract_detail(self) -> dict:
        return {}

    def check_login_status(self) -> bool:
        return True

    def get_cookies(self) -> list:
        return []

    def build_pdf_url(self, paper: dict) -> str:
        urls = paper.get("pdf_urls") or []
        return urls[0] if urls else ""
