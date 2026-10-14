---
name: academic-db-harvester
description: |
  Multi-source academic literature harvester using Edge browser CDP protocol.
  Connects to a logged-in Edge browser and harvests from IEEE Xplore,
  ScienceDirect (Elsevier) and Web of Science: searches by keyword/year,
  extracts metadata (title, authors, DOI, journal, year, abstract, keywords,
  citations), downloads PDF full text where the platform hosts it, and
  generates structured CSV/JSON/HTML reports. Fully local, no Zotero needed.
  Use when the user wants to: (1) search and collect papers from IEEE Xplore,
  ScienceDirect or Web of Science by keyword/year, (2) batch-download PDFs
  from full-text platforms, (3) extract structured metadata from search
  results or detail pages, (4) generate academic literature reports,
  or (5) build a local paper library.
license: MIT
compatibility: Windows + Edge/Chromium 136+ (CDP endpoint), Python 3.9+, playwright, websocket-client
metadata:
  opencode/slash: "true"
  audience: researchers
  workflow: literature-harvesting
---

# Academic DB Harvester

## Overview

Lightweight academic literature collector for multiple databases. Connects to
an already-open Edge browser via CDP (Chrome DevTools Protocol), reuses the
existing institutional login session, searches by keyword, extracts metadata,
downloads PDFs (full-text platforms only), and produces structured output.
Sources are pluggable adapters under `scripts/sites/`, so new databases can be
added without touching the harvester pipeline.

## Supported Sources

| `--source` | Platform | Hosts | PDF full text | Notes |
|------------|----------|-------|---------------|-------|
| `ieee` (default) | IEEE Xplore | ieeexplore.ieee.org | Yes | Session login required |
| `sciencedirect` | Elsevier ScienceDirect | www.sciencedirect.com | Yes | PDF endpoint is bot-protected; downloaded via browser-assisted fallback (see below) |
| `wos` | Web of Science (Clarivate) | webofscience.com / webofscience.clarivate.cn | No (citation index) | Redirects to the `.cn` mirror in mainland China; metadata/abstract/DOI only |
| `openalex` | OpenAlex | api.openalex.org | When open-access | **HTTP API — no browser, no login.** Indexes 250M+ works across all publishers; exposes citations and OA PDF links |
| `arxiv` | arXiv | export.arxiv.org | Yes (every record) | **HTTP API — no browser, no login.** Preprints with free full-text PDFs; no citation counts |

Two transports sit behind the same `--source` flag: browser-backed sources
(`ieee`, `sciencedirect`, `wos`) need Edge + CDP + your login; API sources
(`openalex`, `arxiv`) are plain HTTPS and start immediately. For broad coverage
run `openalex` first — it spans every publisher, including ones with no
dedicated adapter here — then narrow with a publisher source. For preprints with
guaranteed free full text, run `arxiv`.

## Quick Start

### Prerequisites

1. Launch Edge with CDP remote debugging using the bundled launcher:
   ```
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start_edge_cdp.ps1
   ```
   > **Why a launcher?** Chromium 136+ (including Edge 152) silently ignores
   > `--remote-debugging-port` when Edge uses the **default** user profile.
   > The launcher starts Edge with a **dedicated profile**
   > (`%USERPROFILE%\edge-cdp\profile`), which restores CDP, and on first run
   > copies cookies/login state from your main profile so the site logins are
   > preserved. Your normal Edge windows can stay open.
   > Windows users can also double-click `scripts/start_edge_cdp.bat`.
2. Open and sign in to the target site in the debug Edge window (first run only):
   - https://ieeexplore.ieee.org
   - https://www.sciencedirect.com
   - https://www.webofscience.com (redirects to the `.cn` mirror in mainland China)
3. Install Python dependencies:
   ```
   pip install playwright websocket-client
   playwright install chromium
   ```

### Run

```bash
# OpenAlex - no browser, no login, broad coverage (start here)
python scripts/harvester.py --source openalex --keyword "cyber range" --years 2020-2026 --count 100 --download-pdf

# arXiv - preprints, every record has a free PDF
python scripts/harvester.py --source arxiv --keyword "cyber range" --years 2022-2026 --count 30 --download-pdf

# IEEE Xplore (browser + login, publisher full text)
python scripts/harvester.py --source ieee --keyword "solid electrolyte interphase" --years 2020-2025 --count 200 --download-pdf

# ScienceDirect (Elsevier full text + PDF)
python scripts/harvester.py --source sciencedirect --keyword "cyber range" --years 2024-2026 --count 50 --download-pdf

# Web of Science (metadata/citations only)
python scripts/harvester.py --source wos --keyword "cyber range" --years 2024-2026 --count 100
```

For OpenAlex set `OPENALEX_MAILTO=you@example.com` to join the polite pool —
recommended, and required for reliable throughput at higher volumes.

By default output goes to `~/Desktop/论文/{timestamp}_{keyword}[_{source}]/`
(override the root with `--output`).

## When to use which source

- **ieee** – full text + citations available; requires an institutional session.
- **sciencedirect** – Elsevier full text; PDF endpoint is JS-protected, handled automatically.
- **wos** – broadest coverage for metadata/citations only (index database, no hosted PDFs).

## Workflow

### 1. Launch and Detect the Debug Browser

Run the launcher (see above). Verify CDP and find site tabs with:
```
python scripts/cdp_detector.py --source sciencedirect
```
The script queries `http://127.0.0.1:9222/json/list` and filters tabs by the
source's host list.

### 2. Execute Search and Extract Candidates

Run `scripts/harvester.py --source <name>` with search parameters. The adapter
for the chosen source:
- builds the search (URL-based for IEEE/ScienceDirect; query-syntax driven on
  the Web of Science advanced-search page: `TS=("kw") AND PY=(2024-2026)`)
- paginates and de-duplicates candidates (ARN / DOI / PII / WOS uid / URL / title)
- saves candidates to `metadata/candidates.json` and `metadata/candidates.csv`
- classifies papers into high/medium/low priority tiers

### 3. Extract Full Metadata

For each candidate the harvester opens the detail page and extracts:
- Abstract, keywords, DOI, journal, year, authors, citation counts
- Citation formats (BibTeX/RIS on IEEE; a minimal BibTeX entry is generated
  for other sources when the site does not expose one)
- Access status (Open Access / Subscription)

### 4. Download PDFs

When `--download-pdf` is set and the source hosts full text (`ieee`,
`sciencedirect`):
- Checks the existing local library folder for already-downloaded PDFs before downloading
- IEEE: plain HTTP download reusing browser session cookies
- ScienceDirect: the `pdfft` endpoint is protected by a JS challenge
  ("crasolve"); the harvester falls back to a **browser-assisted download**
  (drives the real browser through the challenge, then fetches the signed
  asset URL from within the page)
- Web of Science: skipped with a warning (citation index, no hosted PDFs)
- Validates files (minimum 50KB, valid `%PDF-` header), retries, logs to
  `pdfs/download_log.json`

### 5. Generate Reports

After collection completes, the script generates:
- `reports/summary.html` - interactive HTML report with tables, charts, and stats
- `reports/statistics.json` - machine-readable statistics
- `reports/search_log.txt` - human-readable execution log

## Output Structure

Output layout, rooted at `~/Desktop/论文/` (or `--output`):

```
{timestamp}_{keyword}[_{source}]/
  metadata/
    candidates.json / candidates.csv
    high_priority.json / medium_priority.json / low_priority.json
    references.bib
  pdfs/
    001_{title}_{arnumber|pii|uid}.pdf
    download_log.json
  citations/
    001_citation.bib / 001_citation.ris
    all_references.bib
  abstracts/
    001_abstract.txt
    all_abstracts.txt
  reports/
    summary.html
    statistics.json
    search_log.txt
  urls.csv
  pending.csv
  config.json
```

## Scripts Reference

| Script | Purpose |
|--------|---------|
| `scripts/start_edge_cdp.ps1` | Launch Edge with CDP on a dedicated profile (Edge 136+ compatible); verifies the endpoint |
| `scripts/start_edge_cdp.bat` | Double-click wrapper for the PowerShell launcher |
| `scripts/harvester.py` | Main entry point; source-agnostic pipeline orchestration |
| `scripts/sites/` | Site adapters (`base.py` interface, `ieee.py`, `sciencedirect.py`, `wos.py`) |
| `scripts/cdp_detector.py` | Edge CDP browser detection and tab discovery (source-aware) |
| `scripts/ieee_automation.py` | IEEE Xplore search, pagination, metadata extraction (used by `sites/ieee.py`) |
| `scripts/pdf_downloader.py` | PDF download with retry, validation, logging, and browser-assisted fallback |
| `scripts/storage.py` | Output directory creation, file organization, CSV/JSON I/O |
| `scripts/reporter.py` | HTML report and statistics generation |
| `scripts/utils.py` | Shared helpers: logging, filename sanitization, retry decorator, BibTeX fallback |

## Key Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `--source` | str | `ieee` | Data source: `ieee`, `sciencedirect`, `wos` |
| `--keyword` | str | (required) | Search keyword(s) |
| `--years` | str | `2020-2025` | Year range, e.g. `2024-2026` |
| `--count` | int | `50` | Max papers to collect (IEEE/SD fetch up to 100/page; WoS scrolls the whole results list) |
| `--sort` | str | `relevance` | Sort order: `relevance`, `newest`, `oldest`, `cited` (IEEE/SD; WoS uses its own default) |
| `--download-pdf` | flag | `False` | Enable PDF download (full-text sources only) |
| `--min-if` | float | `0` | Minimum impact factor filter (requires EasyScholar, IEEE) |
| `--output` | str | auto | Output directory (auto-generates if not set) |
| `--cdp-port` | int | `9222` | Edge remote debugging port |

## Per-source Notes

- **ScienceDirect**: results render 100 per page (`show=100`); the search page
  exposes `li.ResultItem` with a `data-doi` attribute; detail pages expose
  `meta[citation_*]` plus DOM-based authors/abstract/keywords. PDF downloads
  use the browser-assisted fallback because the `pdfft` endpoint runs a JS
  bot challenge that blocks plain HTTP clients.
- **Web of Science**: searches run through the advanced-search textarea
  (`#advancedSearchInputArea`); the results list is lazy/virtual rendered, so
  the adapter scrolls and accumulates records by uid. Records land on the
  `.cn` mirror when accessed from mainland China - both domains are supported.
  No PDFs (index database); use the DOI to fetch full text elsewhere.
- **IEEE Xplore**: the default source; unchanged behaviour of the search,
  pagination and metadata extraction steps.
- **OpenAlex**: API-backed but spans every publisher, so it returns records that
  no publisher-site adapter here can see. Each record carries DOI, venue,
  citation count, keywords and — where the work is open access — a direct PDF
  URL. With `--sort cited` ranking is by citation count, so off-topic but heavily
  cited papers can surface; keep the default `relevance` for discovery. Year
  filtering is server-side (`from_publication_date` / `to_publication_date`),
  and abstracts arrive as an inverted index that the adapter rebuilds into text.
- **arXiv**: every record is open access with a direct PDF, which makes this the
  cheapest source when full text matters more than citations. Fields come from
  the Atom API: title, authors, abstract, subjects, and the optional
  `arxiv:doi` / `arxiv:journal_ref` (the published venue, when the preprint has
  since appeared elsewhere). arXiv exposes no citation counts, so they are
  recorded as `0` and priority scoring leans on recency plus open access.
  **Throttling matters:** arXiv asks automated clients to stay at ≥3 s between
  requests; the adapter enforces this and exposes `ARXIV_MIN_INTERVAL` (seconds)
  if a different cadence is needed. Bulk paging through large result sets is
  discouraged by the API terms — narrow the query or keep `--count` moderate.

## Error Handling

- **No Edge debug port**: Run `scripts/start_edge_cdp.ps1` (Edge 136+ ignores the
  debug flag on the default profile; the launcher uses a dedicated profile). If
  Edge with that profile is already open without the flag, close all its windows first.
- **Not logged in**: Sign in to the target site in the debug Edge window (hard
  check only for sources that require it, e.g. IEEE).
- **Paywall / no entitlement**: Paper marked `pending` in `pending.csv`.
- **ScienceDirect 403/challenge on PDF**: handled automatically through the
  browser-assisted fallback; if it still fails, the paper stays in `pending.csv`.
- **CAPTCHA**: Pause and alert user to solve manually.
- **API source rate-limited** (HTTP 429): set the source's polite-pool variable —
  `OPENALEX_MAILTO=you@example.com` — and retry; `_http_get()` already backs off
  and retries three times. On arXiv a 429 usually means requests were fired faster
  than the ≥3 s cadence; the adapter already spaces them, so if you see it, raise
  `ARXIV_MIN_INTERVAL`.
- **Network timeout**: Auto-retry up to 3 times with exponential backoff.
- **PDF validation failure**: Re-download once, then mark as failed.

## Extending

Adding a database means adding one adapter under `scripts/sites/` and one entry in
`SITE_REGISTRY`; the pipeline itself never changes. Pick the transport first:

**Browser-backed** (publisher site, needs login): subclass `BaseSiteAdapter` and
implement `build_search_url`, `navigate_to_search`, `extract_result_list`,
`go_next_page`, `open_detail`, `extract_detail`, `check_login_status`
(`extract_bibtex` / `extract_ris` are optional). Declare `name`, `display_name`,
`hosts` (used to find the right tab), `has_pdf` and `login_required`. The base
class already provides `_wait_for_any`, `_eval`, `get_cookies` and `_sleep`.

**API-backed** (public HTTP, no login): subclass `ApiSiteAdapter` and implement a
single method — `fetch_page(keyword, years, sort, page) -> list[dict]` — plus the
class attributes `name`, `display_name`, `has_pdf` and `page_size`. The base class
turns that into the full interface (paging, detail, cookies, PDF picking) and
supplies `_http_get()` with retry/backoff plus `parse_years()`. Reference
implementation: `sites/openalex.py` (~120 lines for a complete source).

Either way, register it in `SITE_REGISTRY` and it becomes available as
`--source <name>`.

## References

Load `references/cdp_reference.md` when debugging CDP connection issues or
understanding site page DOM structure and selectors (IEEE Xplore,
ScienceDirect, Web of Science).

## Credits

See `../CREDITS.md` at the repository root for the standards, platform APIs and
upstream work this skill builds on.
