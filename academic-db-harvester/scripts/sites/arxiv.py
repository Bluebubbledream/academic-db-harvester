"""arXiv adapter — open preprint server, reached over its public Atom API.

No login, no browser. Every arXiv paper has a free full-text PDF, which makes
this the highest-value API source when you want PDFs rather than citations.

  - search : GET /api/query?search_query=...&start=...&max_results=...
  - results: Atom <entry> with id, title, summary, author[].name, published,
             arxiv:doi, arxiv:journal_ref, arxiv:comment, category[].term,
             link[title="pdf"]
  - detail : not needed (the search response is already complete)

arXiv asks automated clients to stay at >=3 seconds between requests and to send
a descriptive User-Agent; this adapter enforces both (`ARXIV_MIN_INTERVAL` to
override the interval). Bulk paging through large result sets is discouraged by
the API terms, so keep --count moderate or narrow the query.

Docs: https://info.arxiv.org/help/api/user-manual.html
"""

import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET

from sites.base import ApiSiteAdapter

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
OPENSEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"

_last_request = [0.0]

# A keyword that already carries field prefixes or boolean syntax - which is
# what the multi-round planner produces when it anchors an expansion query -
# must not be wrapped a second time. 'all:"all:"x" AND all:"y""' is malformed
# and silently returns nothing, which is worse than a visible error.
FIELDED = re.compile(r"\b(all|ti|abs|au|co|jr|cat|rn|id|ms|ac|cc):", re.I)


class ArxivSite(ApiSiteAdapter):
    name = "arxiv"
    display_name = "arXiv (open preprints + free PDFs)"
    hosts = ["https://arxiv.org"]
    has_pdf = True
    page_size = 100
    host = "export.arxiv.org"
    polite_ua = "academic-db-harvester/1.0 (OpenCode skill; arXiv API client)"

    ENDPOINT = "https://export.arxiv.org/api/query"

    SORTS = {
        "relevance": "",                                        # API default
        "newest": "&sortBy=submittedDate&sortOrder=descending",
        "oldest": "&sortBy=submittedDate&sortOrder=ascending",
        "cited": "&sortBy=submittedDate&sortOrder=descending",   # no citation ranking on arXiv
    }

    # ── request ─────────────────────────────────────────────────────────

    def _throttled_get(self, url: str) -> bytes:
        """GET with a polite User-Agent and arXiv's >=3s request spacing."""
        interval = float(os.environ.get("ARXIV_MIN_INTERVAL", "3.0"))
        wait = _last_request[0] + interval - time.time()
        if wait > 0:
            time.sleep(wait)
        data = self._http_get(url, headers={"Accept": "application/atom+xml"})
        _last_request[0] = time.time()
        return data

    def build_search_url(self, keyword: str, years: str = "", sort: str = "relevance",
                         page: int = 1) -> str:
        start, end = self.parse_years(years)
        keyword = (keyword or "").strip()
        if FIELDED.search(keyword) or " AND " in keyword or " OR " in keyword:
            query = keyword                      # already a formed query
        else:
            query = 'all:"%s"' % keyword.replace('"', " ")
        if start:
            query += " AND submittedDate:[%d0101 TO %d1231]" % (start, end or start)
        url = ("%s?search_query=%s&start=%d&max_results=%d"
               % (self.ENDPOINT, urllib.parse.quote(query),
                  (page - 1) * self.page_size, self.page_size))
        return url + self.SORTS.get(sort, "")

    def fetch_page(self, keyword: str, years: str, sort: str, page: int) -> list[dict]:
        url = self.build_search_url(keyword, years, sort, page)
        root = ET.fromstring(self._throttled_get(url))
        try:
            total_n = int(root.findtext(OPENSEARCH + "totalResults", "0"))
        except ValueError:
            total_n = 0
        self._total_results = total_n
        self._has_more = page * self.page_size < total_n
        return [self._to_paper(e) for e in root.findall(ATOM + "entry")]

    # ── mapping ─────────────────────────────────────────────────────────

    @staticmethod
    def _clean(text: str) -> str:
        return " ".join((text or "").split())

    def _to_paper(self, entry) -> dict:
        abs_url = self._clean(entry.findtext(ATOM + "id", ""))
        # version suffix (v1/v2) is kept in the PDF link but stripped from the id
        arxiv_id = abs_url.rsplit("/abs/", 1)[-1] if "/abs/" in abs_url else abs_url

        pdf_url = ""
        for link in entry.findall(ATOM + "link"):
            if link.get("title") == "pdf":
                pdf_url = self._clean(link.get("href", ""))
                break
        if not pdf_url and arxiv_id:
            pdf_url = "https://arxiv.org/pdf/" + arxiv_id

        authors = []
        for a in entry.findall(ATOM + "author"):
            name = self._clean(a.findtext(ATOM + "name", ""))
            if name:
                authors.append(name)

        keywords = [self._clean(c.get("term", "")) for c in entry.findall(ATOM + "category")]

        published = self._clean(entry.findtext(ATOM + "published", ""))
        year = published[:4] if len(published) >= 4 else ""

        journal_ref = self._clean(entry.findtext(ARXIV + "journal_ref", ""))
        comment = self._clean(entry.findtext(ARXIV + "comment", ""))

        return {
            "title": self._clean(entry.findtext(ATOM + "title", "")),
            "authors": authors,
            "year": year,
            "journal": journal_ref,          # published-venue note when present
            "doi": self._clean(entry.findtext(ARXIV + "doi", "")),
            "uid": arxiv_id.split("v")[0] if arxiv_id else "",
            "arnumber": "",
            "pii": "",
            "url": abs_url,
            "abstract": self._clean(entry.findtext(ATOM + "summary", "")),
            "keywords": keywords,
            "citation_count": "0",            # arXiv exposes no citation counts
            "access_status": "open",          # preprints are free to read
            "pdf_urls": [pdf_url] if pdf_url else [],
            "comment": comment,
            "version": arxiv_id,
        }