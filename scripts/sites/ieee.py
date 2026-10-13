"""IEEE Xplore adapter - wraps the existing IEEEXploreAutomation implementation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from ieee_automation import IEEEXploreAutomation
from sites.base import BaseSiteAdapter


class IEEESite(BaseSiteAdapter):
    """IEEE Xplore adapter (delegates to IEEEXploreAutomation)."""

    name = "ieee"
    display_name = "IEEE Xplore"
    hosts = ["https://ieeexplore.ieee.org"]
    has_pdf = True
    login_required = True

    def __init__(self, page, ws_url: str):
        super().__init__(page, ws_url)
        self.impl = IEEEXploreAutomation(page, ws_url)

    # ── explicit delegation ─────────────────────────────────────────

    def build_search_url(self, keyword, years="2020-2025", sort="relevance"):
        return self.impl.build_search_url(keyword, years, sort)

    def navigate_to_search(self, keyword, years, sort="relevance"):
        return self.impl.navigate_to_search(keyword, years, sort)

    def extract_result_list(self):
        return self.impl.extract_result_list()

    def go_next_page(self):
        return self.impl.go_next_page()

    def open_detail(self, paper_url):
        return self.impl.open_detail(paper_url)

    def extract_detail(self):
        return self.impl.extract_detail()

    def check_login_status(self):
        return self.impl.check_login_status()

    def extract_bibtex(self):
        return self.impl.extract_bibtex()

    def extract_ris(self):
        return self.impl.extract_ris()

    def extract_impact_factors(self):
        return self.impl.extract_impact_factors()
