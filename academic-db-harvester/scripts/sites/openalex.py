"""OpenAlex adapter — open bibliographic graph of 250M+ works across all publishers.

No login, no browser: plain HTTPS against the public REST API. Because OpenAlex
indexes DOI and non-DOI literature from thousands of repositories, this source
covers far more ground than any single publisher site.

  - search : GET /works?search=...&filter=from_publication_date:...,to_publication_date:...
  - results: results[] with title, doi, publication_year, cited_by_count,
             authorships[].author.display_name, primary_location.source.display_name,
             best_oa_location.pdf_url, open_access.oa_status, abstract_inverted_index
  - detail : not needed (search response is already complete)

Docs: https://docs.openalex.org/api-entities/works/search-works
Set OPENALEX_MAILTO=you@example.com to join the polite pool (recommended, and
required for reliable throughput at higher volumes).
"""

import json
import os
import urllib.parse

from sites.base import ApiSiteAdapter


class OpenAlexSite(ApiSiteAdapter):
    name = "openalex"
    display_name = "OpenAlex (open bibliographic graph)"
    hosts = ["https://openalex.org"]
    has_pdf = True                 # only where an open-access PDF exists
    page_size = 200
    host = "api.openalex.org"

    SELECT = ("id,doi,title,display_name,publication_year,publication_date,"
              "cited_by_count,authorships,primary_location,best_oa_location,"
              "open_access,type,abstract_inverted_index,keywords,ids")

    SORTS = {
        "relevance": "relevance_score:desc",
        "newest": "publication_date:desc",
        "oldest": "publication_date:asc",
        "cited": "cited_by_count:desc",
    }

    # ── request ─────────────────────────────────────────────────────────

    def build_search_url(self, keyword: str, years: str = "", sort: str = "relevance",
                         page: int = 1) -> str:
        params = {
            "search": keyword,
            "per-page": str(self.page_size),
            "page": str(page),
            "sort": self.SORTS.get(sort, self.SORTS["relevance"]),
            "select": self.SELECT,
        }
        start, end = self.parse_years(years)
        if start:
            params["filter"] = "from_publication_date:%d-01-01" % start
            if end:
                params["filter"] += ",to_publication_date:%d-12-31" % end
        mailto = os.environ.get("OPENALEX_MAILTO", "").strip()
        if mailto:
            params["mailto"] = mailto
        return "https://api.openalex.org/works?" + urllib.parse.urlencode(params)

    def fetch_page(self, keyword: str, years: str, sort: str, page: int) -> list[dict]:
        url = self.build_search_url(keyword, years, sort, page)
        data = json.loads(self._http_get(url).decode("utf-8"))
        return [self._to_paper(w) for w in data.get("results", [])]

    # ── mapping ─────────────────────────────────────────────────────────

    @staticmethod
    def _abstract(inv: dict | None) -> str:
        """OpenAlex ships abstracts as {token: [positions]}; rebuild the text."""
        if not inv:
            return ""
        pairs = []
        for token, positions in inv.items():
            for p in positions:
                pairs.append((p, token))
        if not pairs:
            return ""
        pairs.sort(key=lambda x: x[0])
        return " ".join(t for _, t in pairs)[:4000]

    @staticmethod
    def _paper_url(w: dict) -> str:
        doi = (w.get("doi") or "").replace("https://doi.org/", "").strip()
        if doi:
            return "https://doi.org/" + doi
        return w.get("id") or ""

    def _to_paper(self, w: dict) -> dict:
        doi = (w.get("doi") or "").replace("https://doi.org/", "").strip()
        venue = ""
        pl = w.get("primary_location") or {}
        src = pl.get("source") or {}
        if src.get("display_name"):
            venue = src["display_name"]

        authors = []
        for a in (w.get("authorships") or []):
            name = ((a.get("author") or {}).get("display_name") or "").strip()
            if name:
                authors.append(name)

        pdf_urls = []
        oa = w.get("best_oa_location") or {}
        if oa.get("pdf_url"):
            pdf_urls.append(oa["pdf_url"])
        for loc in (w.get("locations") or []):
            u = loc.get("pdf_url")
            if u and u not in pdf_urls:
                pdf_urls.append(u)

        keywords = [k.get("display_name") for k in (w.get("keywords") or [])
                    if k.get("display_name")]

        return {
            "title": (w.get("title") or w.get("display_name") or "").strip(),
            "authors": authors,
            "year": w.get("publication_year") or "",
            "journal": venue,
            "doi": doi,
            "uid": (w.get("id") or "").rsplit("/", 1)[-1],
            "arnumber": "",
            "pii": "",
            "url": self._paper_url(w),
            "abstract": self._abstract(w.get("abstract_inverted_index")),
            "keywords": keywords,
            "citation_count": str(w.get("cited_by_count") or 0),
            "access_status": (w.get("open_access") or {}).get("oa_status") or "unknown",
            "pdf_urls": pdf_urls,
            "work_type": w.get("type") or "",
        }