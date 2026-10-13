"""Web of Science (Clarivate) adapter - metadata/citation index, no full-text PDFs.

Selectors verified live on 2026-09-30 against webofscience.clarivate.cn
(institutional access), advanced-search + results + full-record DOM:
  - search : #advancedSearchInputArea (full query syntax, e.g. TS=("...") AND PY=(2024-2026))
  - results: app-record, a[data-ta=summary-record-title-link], SumAuthTa-* author links,
             [data-ta=summary-record-pubdate], .summary-source-title-link,
             [data-ta=stat-number-citation-related-count], button[data-ta=next-page-button]
  - detail : [data-ta=FullRTa-fullRecordtitle-0], [data-ta=FullRTa-DOI],
             [data-ta=FullRTa-pubdate], [data-ta=FullRTa-abstract-basic],
             FRkeywordsTa-authorKeywordLink-*

Note: Web of Science redirects to the Chinese mirror (webofscience.clarivate.cn)
when accessed from mainland China; both hosts are recognized.
"""

import re

from sites.base import BaseSiteAdapter


class WOSSite(BaseSiteAdapter):
    name = "wos"
    display_name = "Web of Science"
    hosts = [
        "https://www.webofscience.com",
        "https://webofscience.clarivate.cn",
    ]
    has_pdf = False          # index database - no hosted full text
    login_required = False   # metadata accessible under institutional access; do not hard-block

    ADV_SEARCH_PATH = "/wos/woscc/advanced-search"

    def _origin(self) -> str:
        m = re.match(r"(https://[^/]+)", self.page.url or "")
        return m.group(1) if m else "https://www.webofscience.com"

    # ── query building ──────────────────────────────────────────────

    @staticmethod
    def _build_query(keyword: str, years: str) -> str:
        query = f'TS=("{keyword}")'
        year_start, _, year_end = (years or "").partition("-")
        year_start = year_start.strip()
        if year_start.isdigit():
            query += f" AND PY=({year_start}-{(year_end or year_start).strip()})"
        return query

    def build_search_url(self, keyword: str, years: str = "2020-2025",
                         sort: str = "relevance") -> str:
        # WoS executes searches in-session; the URL is the advanced search page.
        return self._origin() + self.ADV_SEARCH_PATH

    def navigate_to_search(self, keyword: str, years: str,
                           sort: str = "relevance") -> None:
        self.page.goto(self._origin() + self.ADV_SEARCH_PATH,
                       wait_until="domcontentloaded", timeout=90000)
        self._wait_for_any("#advancedSearchInputArea", 40000)
        self._sleep(2)

        query = self._build_query(keyword, years)
        filled = self._eval(r"""
        (q) => {
            const el = document.querySelector('#advancedSearchInputArea');
            if (!el) return false;
            el.focus();
            el.value = q;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
        }
        """, arg=query, default=False)
        if not filled:
            return
        self._sleep(1)

        self._eval(r"""
        () => {
            const sels = ['button[data-ta="search-button"]'];
            for (const s of sels) {
                const el = document.querySelector(s);
                if (el && el.offsetParent !== null) { el.click(); return true; }
            }
            const btns = Array.from(document.querySelectorAll('button'));
            const b = btns.find(x => (x.textContent || '').trim() === 'Search' && x.offsetParent !== null);
            if (b) { b.click(); return true; }
            return false;
        }
        """, default=False)

        self._sleep(6)
        self._wait_for_any("app-record", 40000)
        self._sleep(3)

    # ── results ─────────────────────────────────────────────────────

    def extract_result_list(self) -> list[dict]:
        # WoS renders the results list lazily (virtual scrolling), so we scroll
        # through the list step by step and accumulate records by uid.
        js = r"""
        async () => {
            const sleep = ms => new Promise(r => setTimeout(r, ms));
            const found = new Map();

            const collect = () => {
                document.querySelectorAll('app-record').forEach(rec => {
                    const a = rec.querySelector('a[data-ta="summary-record-title-link"]');
                    if (!a) return;
                    const href = a.href;
                    const uidMatch = href.match(/full-record\/([^\/?#]+)/);
                    const uid = uidMatch ? decodeURIComponent(uidMatch[1]) : href;
                    if (found.has(uid)) return;
                    const title = a.textContent.trim().replace(/\s+/g, ' ');
                    const authors = Array.from(
                        rec.querySelectorAll('a[data-ta*="DisplayName-author-en"] span')
                    ).map(s => s.textContent.trim()).filter(s => s);
                    const pd = rec.querySelector('[data-ta="summary-record-pubdate"]');
                    let year = pd ? pd.textContent.replace(/\D/g, '') : '';
                    if (!year) {
                        const m = rec.textContent.match(/\b(19|20)\d{2}\b/);
                        year = m ? m[0] : '';
                    }
                    let journal = '';
                    const st = rec.querySelector('a.summary-source-title-link, .summary-source-title-link');
                    if (st) journal = st.textContent.trim().replace(/\s+/g, ' ');
                    if (!journal) {
                        const cf = rec.querySelector('span[name="conf_title"]');
                        if (cf) journal = cf.textContent.trim();
                    }
                    const citeEl = rec.querySelector('[data-ta="stat-number-citation-related-count"]');
                    const citation_count = citeEl ? citeEl.textContent.trim() : '';
                    found.set(uid, {
                        title, url: href, uid, authors, journal, year, doi: '',
                        citation_count, access_status: 'Subscription', pdf_urls: [],
                    });
                });
            };

            const scrollers = [];
            document.querySelectorAll('*').forEach(el => {
                if (el.scrollHeight > el.clientHeight + 200 && el.clientHeight > 200) scrollers.push(el);
            });
            const targets = scrollers.length ? scrollers : [document.scrollingElement || document.documentElement];

            collect();
            for (const el of targets) {
                const max = el.scrollHeight;
                for (let y = 0; y <= max; y += 500) {
                    el.scrollTop = y;
                    await sleep(180);
                    collect();
                    if (found.size >= 100) break;
                }
                el.scrollTop = 0;
                await sleep(150);
                collect();
            }
            return Array.from(found.values());
        }
        """
        return self._eval(js, default=[]) or []

    def go_next_page(self) -> bool:
        first_title = self._eval(
            "() => { const a = document.querySelector('a[data-ta=\"summary-record-title-link\"]'); return a ? a.textContent.trim() : ''; }",
            default="",
        )
        clicked = self._eval(r"""
        () => {
            const btns = Array.from(document.querySelectorAll('button[data-ta="next-page-button"]'));
            const b = btns.find(x => x.offsetParent !== null && !x.disabled);
            if (b) { b.click(); return true; }
            return false;
        }
        """, default=False)
        if not clicked:
            return False
        self._sleep(4)
        self._wait_for_any("app-record", 30000)
        self._sleep(2)
        new_title = self._eval(
            "() => { const a = document.querySelector('a[data-ta=\"summary-record-title-link\"]'); return a ? a.textContent.trim() : ''; }",
            default="",
        )
        return new_title != first_title

    # ── detail ──────────────────────────────────────────────────────

    def open_detail(self, paper_url: str) -> bool:
        try:
            self.page.goto(paper_url, wait_until="domcontentloaded", timeout=60000)
            self._wait_for_any("[data-ta='FullRTa-fullRecordtitle-0']", 30000)
            self._sleep(2)
            return True
        except Exception:
            return False

    def extract_detail(self) -> dict:
        js = r"""
        () => {
            const q = s => { const el = document.querySelector(s); return el ? el.textContent.trim() : ''; };
            const r = {};
            r.title = q('[data-ta="FullRTa-fullRecordtitle-0"]');
            const uidMatch = location.href.match(/full-record\/([^\/?#]+)/);
            r.uid = uidMatch ? decodeURIComponent(uidMatch[1]) : '';
            r.doi = q('[data-ta="FullRTa-DOI"]').replace(/^DOI\s*/i, '');
            r.journal = q('[data-ta="FullRTa-sourceLabel"]');
            const y = q('[data-ta="FullRTa-pubdate"]');
            const ym = y.match(/\d{4}/);
            r.year = ym ? ym[0] : '';
            const authors = [];
            document.querySelectorAll('a[data-ta*="DisplayName-author-en"]').forEach(el => {
                const t = el.textContent.trim();
                if (t && authors.indexOf(t) < 0) authors.push(t);
            });
            r.authors = authors;
            r.abstract = q('[data-ta="FullRTa-abstract-basic"]');
            const kws = [];
            document.querySelectorAll('[data-ta^="FRkeywordsTa-authorKeywordLink-"]').forEach(el => {
                const t = el.textContent.trim();
                if (t && kws.indexOf(t) < 0) kws.push(t);
            });
            r.keywords = kws;
            r.pdf_urls = [];
            r.citation_count = '';
            return r;
        }
        """
        return self._eval(js, default={}) or {}

    # ── login ───────────────────────────────────────────────────────

    def check_login_status(self) -> bool:
        # Web of Science shows the institution name when entitlement is active,
        # but the UI differs per tenant - never block metadata harvesting.
        self._eval(r"""
        () => {
            const body = document.body.innerText || '';
            const m = body.match(/Sign in to access\s*\n([^\n]+)/);
            return m ? m[1].trim() : '';
        }
        """, default="")
        return True


