"""ScienceDirect (Elsevier) adapter.

Selectors verified live on 2026-09-30 against the institutional ScienceDirect
interface (search+detail DOM):
  - results: li.ResultItem, a.result-list-title-link, ol.Authors span.author,
             .srctitle-date-fields, a.download-link, li.pagination-link.next-link
  - detail : meta[name=citation_*], .author-group, .abstract.author,
             .keywords-section .keyword, a[href*=pdfft]
"""

from urllib.parse import urlencode

from sites.base import BaseSiteAdapter


class ScienceDirectSite(BaseSiteAdapter):
    name = "sciencedirect"
    display_name = "ScienceDirect"
    hosts = ["https://www.sciencedirect.com"]
    has_pdf = True
    login_required = False  # metadata is visible without login; PDFs need entitlement

    SEARCH_URL = "https://www.sciencedirect.com/search"
    SORT_MAP = {
        "relevance": "relevance",
        "newest": "date",
        "oldest": "date",
        "cited": "relevance",
    }

    # ── search ──────────────────────────────────────────────────────

    def build_search_url(self, keyword: str, years: str = "2020-2025",
                         sort: str = "relevance") -> str:
        year_start, _, year_end = (years or "").partition("-")
        params = {
            "qs": keyword,
            "show": "100",
            "sortBy": self.SORT_MAP.get(sort, "relevance"),
        }
        if year_start.strip():
            params["date"] = f"{year_start.strip()}-{(year_end or year_start).strip()}"
        return f"{self.SEARCH_URL}?{urlencode(params)}"

    def navigate_to_search(self, keyword: str, years: str,
                           sort: str = "relevance") -> None:
        url = self.build_search_url(keyword, years, sort)
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        self._wait_for_any("li.ResultItem, [class*='ResultItem']", 25000)
        self._sleep(2)

    def extract_result_list(self) -> list[dict]:
        js = r"""
        () => {
            const out = [];
            document.querySelectorAll('li.ResultItem').forEach(li => {
                const a = li.querySelector('a.result-list-title-link, h2 a[href*="/science/article/pii/"]');
                if (!a) return;
                const href = a.href;
                const pii = (href.match(/\/pii\/([A-Z0-9]+)/) || [])[1] || '';
                const title = a.textContent.trim().replace(/\s+/g, ' ');
                const authors = Array.from(li.querySelectorAll('ol.Authors span.author'))
                    .map(s => s.textContent.trim()).filter(s => s);
                let journal = '', pubdate = '';
                const sj = li.querySelector('.srctitle-date-fields');
                if (sj) {
                    const ja = sj.querySelector('a');
                    if (ja) journal = ja.textContent.trim();
                    sj.querySelectorAll('span').forEach(sp => {
                        const t = sp.textContent.trim();
                        if (t && !sp.querySelector('a') && depends(t)) pubdate = t;
                    });
                    function depends(t) { return /\d{4}/.test(t) || /^[A-Z][a-z]+ \d{4}$/.test(t); }
                }
                const doi = li.getAttribute('data-doi') || '';
                let year = '';
                const ym = (pubdate || li.textContent).match(/\b(19|20)\d{2}\b/);
                if (ym) year = ym[0];
                const accessEl = li.querySelector('.access-label');
                const access = accessEl ? accessEl.textContent.trim() : '';
                const pdfA = li.querySelector('a.download-link[href*="pdfft"]');
                out.push({
                    title, url: href, pii, doi, authors, journal, year,
                    access_status: /open/i.test(access) ? 'Open Access' : 'Subscription',
                    pdf_urls: pdfA ? [pdfA.href] : [],
                });
            });
            return out;
        }
        """
        papers = self._eval(js, default=[]) or []
        return papers

    def go_next_page(self) -> bool:
        js = r"""
        () => {
            const a = document.querySelector('li.pagination-link.next-link a');
            if (a) { a.click(); return true; }
            return false;
        }
        """
        clicked = self._eval(js, default=False)
        if clicked:
            self._sleep(3)
            self._wait_for_any("li.ResultItem", 15000)
            return True
        # URL fallback: bump offset by 100
        try:
            import re
            url = self.page.url
            if "offset=" in url:
                new_url = re.sub(r"offset=(\d+)", lambda m: f"offset={int(m.group(1)) + 100}", url)
            else:
                sep = "&" if "?" in url else "?"
                new_url = f"{url}{sep}offset=100"
            self.page.goto(new_url, wait_until="domcontentloaded", timeout=60000)
            self._wait_for_any("li.ResultItem", 20000)
            self._sleep(2)
            return True
        except Exception:
            return False

    # ── detail ──────────────────────────────────────────────────────

    def open_detail(self, paper_url: str) -> bool:
        try:
            self.page.goto(paper_url, wait_until="domcontentloaded", timeout=60000)
            self._wait_for_any("meta[name='citation_title'], .abstract.author", 20000)
            self._sleep(2)
            return True
        except Exception:
            return False

    def extract_detail(self) -> dict:
        js = r"""
        () => {
            const r = {};
            const meta = n => {
                const el = document.querySelector('meta[name="' + n + '"]');
                return el ? el.content.trim() : '';
            };
            r.title = meta('citation_title');
            r.doi = meta('citation_doi');
            r.journal = meta('citation_journal_title');
            const pd = meta('citation_publication_date');
            const ym = pd.match(/\d{4}/);
            r.year = ym ? ym[0] : '';
            const authors = [];
            document.querySelectorAll('.author-group button[data-xocs-content-type="author"], .author-group .react-xocs-alternative-link').forEach(el => {
                const g = el.querySelector('.given-name');
                const s = el.querySelector('.surname');
                let name = '';
                if (g || s) name = ((g ? g.textContent : '') + ' ' + (s ? s.textContent : '')).trim();
                else name = (el.textContent || '').trim();
                if (name && authors.indexOf(name) < 0) authors.push(name);
            });
            r.authors = authors;
            let abs = '';
            document.querySelectorAll('.abstract.author').forEach(el => {
                if (!el.querySelector('.abstract.author')) {
                    const t = el.textContent.trim();
                    if (t.length > abs.length) abs = t;
                }
            });
            if (!abs) {
                const el = document.querySelector('#abstracts');
                if (el) abs = el.textContent.trim();
            }
            r.abstract = abs.replace(/^Abstract\s*/i, '');
            const kws = [];
            document.querySelectorAll('.keywords-section .keyword').forEach(el => {
                const t = (el.textContent || '').trim();
                if (t && kws.indexOf(t) < 0) kws.push(t);
            });
            r.keywords = kws;
            const pii = (location.pathname.match(/\/pii\/([A-Z0-9]+)/) || [])[1] || '';
            r.pii = pii;
            const pdfs = [];
            document.querySelectorAll('a[href*="pdfft"], a[href*="/pdf?"]').forEach(a => {
                if (pii && a.href.indexOf(pii) >= 0 && pdfs.indexOf(a.href) < 0) pdfs.push(a.href);
            });
            if (pii && !pdfs.length) {
                pdfs.push(location.origin + '/science/article/pii/' + pii + '/pdfft?isDTMRedir=true&download=true');
            }
            r.pdf_urls = pdfs;
            r.access_status = document.querySelector('.OpenAccessArchive, .open-access-label') ? 'Open Access' : 'Subscription';
            r.citation_count = '';
            return r;
        }
        """
        return self._eval(js, default={}) or {}

    # ── login ───────────────────────────────────────────────────────

    def check_login_status(self) -> bool:
        js = r"""
        () => {
            const inst = document.querySelector('[class*="institution"]');
            if (inst) {
                const t = inst.textContent.trim();
                if (t.length > 3) return t;
            }
            const body = document.body.innerText;
            if (body.indexOf('Access through your organization') >= 0) return '';
            return 'unknown';
        }
        """
        result = self._eval(js, default="")
        if result and result != "unknown":
            return True
        return True  # metadata always available; PDF step reports entitlement problems
