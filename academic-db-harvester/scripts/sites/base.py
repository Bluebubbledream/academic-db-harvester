"""Base class for academic database site adapters.

Each adapter controls one already-open browser tab (via Playwright CDP) and
implements a small uniform interface so the harvester can stay source-agnostic.

Adapter contract (implement in subclasses):
    name            : str  - registry key, e.g. "ieee"
    display_name    : str  - human-readable name
    hosts           : list[str] - URL prefixes used to find the right tab
    has_pdf         : bool - whether the site hosts full-text PDFs itself
    login_required  : bool - whether harvesting is blocked without login

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
