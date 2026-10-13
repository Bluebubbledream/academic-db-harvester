"""IEEE Xplore browser automation: search, pagination, metadata extraction."""

import sys
import time
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse

sys.path.insert(0, str(Path(__file__).parent))
from utils import setup_logger, sanitize_filename

logger = setup_logger("ieee_automation")

IEEE_SEARCH_URL = "https://ieeexplore.ieee.org/search/searchresult.jsp"
IEEE_DETAIL_BASE = "https://ieeexplore.ieee.org"


class IEEEXploreAutomation:
    """Controls an already-open IEEE Xplore tab via Playwright CDP connection."""

    # ── CSS / XPath selectors for IEEE Xplore ──
    SELECTORS = {
        # Search results page
        "result_item": "xpl-search-results-item",
        "result_list": ".ListResults",
        "result_link": "a[href*='/document/']",
        "result_title": ".result-item-title h2, .text-base-md-lh",
        "result_authors": ".authors-info, .author",
        "result_year": ".publisher-info-container span",
        "result_doi": "a[href*='doi.org']",
        "next_page": "button.next-btn, a.pagination-btn[aria-label='Next']",
        # Detail page
        "detail_title": "h1.document-title, .document-title h1",
        "detail_abstract": "div.abstract-text, .abstract-description xpl-abstract-view div",
        "detail_keywords": "xpl-keywords, .doc-keywords-list a",
        "detail_authors": "xpl-author-info, .authors-info a",
        "detail_doi": "a[href*='doi.org']",
        "citation_tab": "#citations",
        "bibtex_link": "a:has-text('BibTeX')",
        "pdf_link": "a[href*='stamp/stamp.jsp'], a[href$='.pdf']",
        "login_check": ".user-info, .login-user, [data-testid='user-menu']",
    }

    SORT_MAP = {
        "relevance": "relevance",
        "newest": "newest",
        "oldest": "oldest",
        "cited": "paper-citations",
    }

    def __init__(self, page, ws_url: str):
        self.page = page
        self.ws_url = ws_url

    # ── Search ──

    def build_search_url(
        self, keyword: str, years: str = "2020-2025", sort: str = "relevance"
    ) -> str:
        """Build an IEEE Xplore search URL."""
        year_start, _, year_end = years.partition("-")
        params = {
            "queryText": keyword,
            "highlight": "true",
            "returnFacets": "ALL",
            "returnType": "SEARCH",
            "matchPubs": "true",
            "ranges": f"{year_start}_{year_end}_Year",
            "sortType": self.SORT_MAP.get(sort, "relevance"),
            "rowsPerPage": "100",
        }
        return f"{IEEE_SEARCH_URL}?{urlencode(params)}"

    def navigate_to_search(self, keyword: str, years: str, sort: str = "relevance") -> None:
        """Navigate to search results page."""
        url = self.build_search_url(keyword, years, sort)
        logger.info("Navigating to search: %s", url[:120])
        self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        self._wait_for_results()

    def _wait_for_results(self) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        try:
            self.page.wait_for_selector(
                "xpl-search-results-item, a[href*='/document/'], .ListResults, [class*='result-item']",
                timeout=15000,
            )
        except Exception:
            pass
        # Robust poll: the IEEE SPA sometimes hydrates result cards late, and a
        # stale first pass yields 0 items even though the page reports results.
        # Poll until cards appear or the page explicitly reports an empty set.
        for _ in range(6):
            try:
                state = self.page.evaluate(
                    """() => {
                        const hasCards =
                            document.querySelectorAll('xpl-search-results-item').length > 0 ||
                            Array.from(document.querySelectorAll('a[href*="/document/"]'))
                                .some(a => /\\/document\\/\\d+/.test(a.href));
                        const text = (document.body && document.body.innerText) || '';
                        const empty = /No results found|unable to find results/i.test(text);
                        return { hasCards, empty };
                    }"""
                )
                if state.get("hasCards") or state.get("empty"):
                    break
            except Exception:
                pass
            time.sleep(3)
        time.sleep(1)

    # ── Results extraction ──

    def _get_page_text_sample(self, max_len: int = 500) -> str:
        """Get a sample of page text for debugging."""
        try:
            text = self.page.evaluate("() => document.body.innerText.slice(0, 500)")
            return text or "(empty)"
        except Exception:
            return "(error)"

    def extract_result_list(self) -> list[dict]:
        """Extract paper metadata from the current search results page."""
        js = r"""
        () => {
            const papers = [];

            const findItems = () => {
                const selectors = [
                    'xpl-search-results-item',
                    '.ListResults xpl-search-results-item',
                    '.ListResults > div',
                    '[class*="dashboard-item"]',
                    '[class*="result-item"]',
                    'li[class*="List-results-items"]',
                    'div[class*="List-results-items"]',
                    'article',
                    'div.card',
                    'div[class*="Card"]',
                    'div[class*="search-result"]'
                ];
                for (const selector of selectors) {
                    const items = Array.from(document.querySelectorAll(selector)).filter(el => el.querySelector('a[href*="/document/"]'));
                    if (items.length) return items;
                }

                const containers = new Map();
                document.querySelectorAll('a[href*="/document/"]').forEach(a => {
                    const id = (a.href.match(/\/document\/(\d+)/) || [])[1] || a.href;
                    let el = a.closest('xpl-search-results-item, li, article, div[class*="result"], div[class*="Result"], div[class*="card"], div[class*="Card"]');
                    if (!el) el = a.parentElement;
                    if (el && !containers.has(id)) containers.set(id, el);
                });
                return Array.from(containers.values());
            };

            const items = findItems();

            items.forEach((item) => {
                try {
                    const titleEl = item.querySelector('a[href*="/document/"]');
                    const title = titleEl ? titleEl.textContent.trim().replace(/\s+/g, ' ') : '';
                    if (!title || title.length < 5) return;
                    const url = titleEl ? titleEl.href : '';
                    const arnumber = url.match(/\/document\/(\d+)/) ? url.match(/\/document\/(\d+)/)[1] : '';

                    const authorEls = item.querySelectorAll('.author a, .authors-info span, [class*="author"] a, [class*="author"] span');
                    const authors = Array.from(authorEls).map(a => a.textContent.trim()).filter(s => s.length > 1);

                    const text = item.textContent || '';
                    const yearMatch = text.match(/\b(19\d{2}|20\d{2}|2030)\b/);
                    const year = yearMatch ? yearMatch[0] : '';

                    const doiEl = item.querySelector('a[href*="doi.org"]');
                    const doi = doiEl ? doiEl.textContent.trim().replace('https://doi.org/', '') : '';

                    const journalEl = item.querySelector('.publication-title, .publisher-info-container, .description, [class*="publication"], [class*="publisher"]');
                    const journal = journalEl ? journalEl.textContent.trim().replace(/\s+/g, ' ') : '';

                    const oaEl = item.querySelector('[class*="open-access"], [class*="OpenAccess"], .icon-open-access, [title*="Open Access"]');
                    const access_status = oaEl ? 'Open Access' : 'Subscription';

                    papers.push({ title, url, arnumber, authors, year, doi, journal, access_status });
                } catch(e) {}
            });

            return papers;
        }
        """
        try:
            papers = self.page.evaluate(js)
            logger.info("Extracted %d papers from current page", len(papers))
            if not papers:
                sample = self._get_page_text_sample(300)
                logger.warning("No results extracted. Page text sample: %s", sample)
            return papers
        except Exception as e:
            logger.error("Failed to extract results: %s", e)
            return []

    def has_next_page(self) -> bool:
        """Check if there is a next page of results."""
        js = """
        () => {
            const selectors = [
                'button.next-btn:not([disabled])',
                'a.pagination-btn[aria-label="Next"]:not(.disabled)',
                'button[aria-label="Next"]:not([disabled])',
                'a[aria-label="Next"]:not(.disabled)',
                '.pagination-next:not(.disabled) a',
                '.pagination-next:not(.disabled) button'
            ];
            return selectors.some(selector => document.querySelector(selector));
        }
        """
        try:
            return self.page.evaluate(js)
        except Exception:
            return False

    def go_next_page(self) -> bool:
        """Click next page. Returns False if no more pages."""
        js = """
        () => {
            const selectors = [
                'button.next-btn:not([disabled])',
                'a.pagination-btn[aria-label="Next"]:not(.disabled)',
                'button[aria-label="Next"]:not([disabled])',
                'a[aria-label="Next"]:not(.disabled)',
                '.pagination-next:not(.disabled) a',
                '.pagination-next:not(.disabled) button'
            ];
            for (const selector of selectors) {
                const next = document.querySelector(selector);
                if (next) { next.click(); return true; }
            }
            return false;
        }
        """
        try:
            before_url = self.page.url
            result = self.page.evaluate(js)
            if not result:
                return self.go_next_page_by_url()
            self._wait_for_results()
            if self.page.url == before_url:
                time.sleep(1)
            return True
        except Exception as e:
            logger.error("Failed to navigate to next page: %s", e)
            return self.go_next_page_by_url()

    def go_next_page_by_url(self) -> bool:
        parsed = urlparse(self.page.url)
        params = parse_qs(parsed.query)
        current_page = int(params.get("pageNumber", ["1"])[0] or "1")
        params["pageNumber"] = [str(current_page + 1)]
        query = urlencode(params, doseq=True)
        next_url = urlunparse(parsed._replace(query=query))
        try:
            self.page.goto(next_url, wait_until="domcontentloaded", timeout=30000)
            self._wait_for_results()
            return True
        except Exception as e:
            logger.error("Failed to navigate by URL to next page: %s", e)
            return False

    # ── Detail page ──

    def open_detail(self, paper_url: str) -> bool:
        """Navigate to a paper's detail page."""
        full_url = paper_url if paper_url.startswith("http") else IEEE_DETAIL_BASE + paper_url
        try:
            self.page.goto(full_url, wait_until="domcontentloaded", timeout=30000)
            # Wait for dynamic content to render
            time.sleep(4)
            # Try waiting for key elements
            try:
                self.page.wait_for_selector("h1, .document-title, [class*='title']", timeout=5000)
            except Exception:
                pass
            time.sleep(1)
            return True
        except Exception as e:
            logger.error("Failed to open detail page %s: %s", full_url[:80], e)
            return False

    def extract_detail(self) -> dict:
        """Extract full metadata from a detail page."""
        js = r"""
        () => {
            const result = {};
            const bodyText = document.body.innerText;
            const getMeta = function(name) {
                var el = document.querySelector('meta[name="' + name + '"]');
                return el ? el.content.trim() : '';
            };
            var titleEl = document.querySelector('h1.document-title span, h1, .document-title-text, [class*="document-title"] h1');
            result.title = titleEl ? titleEl.textContent.trim() : getMeta('citation_title');
            var absSels = ['div.abstract-text div', '.abstract-text', 'xpl-abstract-view div', '[class*="abstract"] div', '.document-abstract', 'div.abstract'];
            var absEl = null;
            for (var i = 0; i < absSels.length; i++) {
                try { absEl = document.querySelector(absSels[i]); if (absEl && absEl.textContent.trim().length > 20) break; } catch(e) {}
            }
            result.abstract = absEl ? absEl.textContent.trim() : '';
            var kwEls = document.querySelectorAll('xpl-keywords-item a, .doc-keywords-list a, ul.keywords a, [class*="keyword"] a');
            result.keywords = Array.from(kwEls).map(function(el) { return el.textContent.trim(); }).filter(function(s) { return s; });
            var authorMetas = document.querySelectorAll('meta[name="citation_author"]');
            if (authorMetas.length) {
                result.authors = Array.from(authorMetas).map(function(m) { return m.content.trim(); });
            } else {
                var authorEls = document.querySelectorAll('xpl-author-info a, .authors-info a, span.author a, [class*="author"] a[href*="author"]');
                result.authors = Array.from(authorEls).map(function(el) { return el.textContent.trim(); }).filter(function(s) { return s.length > 1; });
            }
            result.doi = getMeta('citation_doi');
            if (!result.doi) {
                var doiEl = document.querySelector('a[href*="doi.org"]');
                result.doi = doiEl ? doiEl.textContent.trim().replace('https://doi.org/', '') : '';
            }
            var yearMeta = document.querySelector('meta[name="citation_date"], meta[name="citation_publication_date"]');
            if (yearMeta) { var ym = yearMeta.content.match(/\d{4}/); result.year = ym ? ym[0] : ''; }
            if (!result.year) { var ym2 = bodyText.match(/(?:Date of Publication|Published|Publication Year)\s*:?\s*.*?(\d{4})/); result.year = ym2 ? ym2[1] : ''; }
            result.journal = getMeta('citation_journal_title');
            if (!result.journal) {
                var pubEl = document.querySelector('.publisher-info-container, .publication-title, [class*="publication"]');
                result.journal = pubEl ? pubEl.textContent.trim() : '';
            }
            var citeEl = document.querySelector('.document-ft-metrics-bar span, .citation-count, [class*="citation"] span, [class*="metrics"]');
            result.citation_count = citeEl ? citeEl.textContent.trim() : '0';
            var oaEl = document.querySelector('.open-access-badge, [class*="open-access"], [class*="OpenAccess"]');
            result.access_status = oaEl ? 'Open Access' : 'Subscription';
            var pdfLinks = [];
            document.querySelectorAll('a[href*="stamp/stamp.jsp"], a[href$=".pdf"]').forEach(function(a) { pdfLinks.push(a.href); });
            document.querySelectorAll('[class*="pdf-download"] a, [class*="download-pdf"] a, [class*="pdf-btn"] a').forEach(function(a) { if (a.href && pdfLinks.indexOf(a.href) < 0) pdfLinks.push(a.href); });
            document.querySelectorAll('a, button').forEach(function(el) {
                var text = (el.textContent || '').toLowerCase();
                var href = el.href || '';
                if ((text.indexOf('pdf') >= 0 || text.indexOf('download') >= 0) && href && href.indexOf('http') === 0) {
                    if (pdfLinks.indexOf(href) < 0) pdfLinks.push(href);
                }
            });
            var arnumMatch = window.location.href.match(/\/document\/(\d+)/);
            var arnumber = arnumMatch ? arnumMatch[1] : '';
            if (!pdfLinks.length && arnumber) { pdfLinks.push('https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=' + arnumber); }
            result.pdf_urls = pdfLinks.filter(function(v, i, a) { return a.indexOf(v) === i; });
            result.arnumber = arnumber;
            return result;
        }
        """
        try:
            return self.page.evaluate(js)
        except Exception as e:
            logger.error('Failed to extract detail: %s', e)
            return {}

    # Login check

    def check_login_status(self) -> bool:
        """Verify that the user is logged in to IEEE Xplore."""
        js = r"""
        () => {
            const indicators = [
                document.querySelector('.user-info'),
                document.querySelector('[data-testid="user-menu"]'),
                document.querySelector('.login-user-name'),
                document.querySelector('a[href*="profile"]'),
            ];
            return indicators.some(el => el !== null);
        }
        """
        try:
            logged_in = self.page.evaluate(js)
            if logged_in:
                logger.info("Login status: authenticated")
            else:
                logger.warning("Login status: NOT authenticated. Please sign in at ieeexplore.ieee.org")
            return bool(logged_in)
        except Exception:
            return False

    def get_cookies(self) -> list:
        """Get all browser cookies for reuse in direct HTTP requests."""
        try:
            return self.page.context.cookies()
        except Exception:
            return []

    # Impact Factor (EasyScholar)

    def extract_impact_factors(self) -> dict:
        """Extract EasyScholar IF labels from the page if extension is installed."""
        js = r"""
        () => {
            const result = {};
            document.querySelectorAll('[class*="easy-scholar"], [class*="easyscholar"]').forEach(el => {
                const title = el.closest('xpl-search-results-item, .result-item');
                const titleText = title ? title.querySelector('h2 a, h3 a')?.textContent?.trim()?.slice(0, 50) : 'unknown';
                result[titleText || 'unknown'] = el.textContent.trim();
            });
            return result;
        }
        """
        try:
            return self.page.evaluate(js)
        except Exception:
            return {}

    def extract_bibtex(self) -> str:
        """Extract BibTeX citation from detail page."""
        js = r"""
        () => {
            const bibBtn = document.querySelector('a:has-text("BibTeX"), button:has-text("BibTeX"), [data-tab="bibtex"]');
            if (bibBtn) bibBtn.click();
            const preEl = document.querySelector('pre, .bibtex-content, textarea.ris-text');
            if (preEl) return preEl.textContent.trim();
            const citeSection = document.querySelector('.citation-format');
            if (citeSection) return citeSection.textContent.trim();
            return '';
        }
        """
        try:
            bib = self.page.evaluate(js)
            if bib:
                import time
                time.sleep(1)
            return bib or ""
        except Exception:
            return ""

    def extract_ris(self) -> str:
        """Extract RIS citation from detail page."""
        js = r"""
        () => {
            const risBtn = document.querySelector('a:has-text("RIS"), button:has-text("RIS"), [data-tab="ris"]');
            if (risBtn) risBtn.click();
            const preEl = document.querySelector('pre, textarea.ris-text');
            return preEl ? preEl.textContent.trim() : '';
        }
        """
        try:
            return self.page.evaluate(js) or ""
        except Exception:
            return ""

