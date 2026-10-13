# Academic Database CDP Automation Reference

Covers the CDP plumbing plus per-site DOM patterns for the bundled adapters
(IEEE Xplore, ScienceDirect, Web of Science).

## CDP Endpoints

### List all open tabs

```
GET http://127.0.0.1:9222/json/list
```

Returns JSON array of tab objects:

```json
[
  {
    "id": "TAB_ID",
    "type": "page",
    "url": "https://ieeexplore.ieee.org/search/searchresult.jsp?queryText=...",
    "title": "IEEE Xplore Search Results",
    "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/TAB_ID"
  }
]
```

### Get browser version

```
GET http://127.0.0.1:9222/json/version
```

Returns browser info including `Browser`, `Protocol-Version`, `User-Agent`.

## Starting Edge with Remote Debugging

> **Important (Chromium 136+, Edge 152 tested):** Edge silently ignores
> `--remote-debugging-port` when it is started with the **default** user-data-dir.
> You must point Edge at a **dedicated profile directory** for CDP to come up.

**Recommended - bundled launcher:**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_edge_cdp.ps1
# optional: -Port 9333 -SkipCookieCopy -ProfileDir D:\edge-cdp\profile
```

The launcher:
- creates/uses `%USERPROFILE%\edge-cdp\profile` as `--user-data-dir`
- on first run copies `Local State`, `Default\Network\Cookies`, `Preferences`,
  `Login Data`, `Web Data` from the main Edge profile (keeps IEEE login)
- opens https://ieeexplore.ieee.org and waits until `/json/version` responds
- can be double-clicked via `scripts\start_edge_cdp.bat`

**Manual equivalent:**

```powershell
msedge.exe --remote-debugging-port=9222 --user-data-dir="$env:USERPROFILE\edge-cdp\profile" `
           --no-first-run --no-default-browser-check https://ieeexplore.ieee.org
```

Note: with a dedicated profile, your normal Edge windows stay open - no need to
close them. If Edge with the dedicated profile is already running WITHOUT the
debug flag, close all of its windows first, then relaunch.

## IEEE Xplore URL Patterns

### Search URL

```
https://ieeexplore.ieee.org/search/searchresult.jsp?queryText={keyword}&ranges={start}_{end}_Year&sortType=relevance
```

### Detail page

```
https://ieeexplore.ieee.org/document/{arnumber}
```

### PDF download (authenticated)

```
https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber={arnumber}
```

### Export citations

```
https://ieeexplore.ieee.org/document/{arnumber}/citations
```

## Key DOM Selectors (IEEE Xplore 2024+)

### Search Results Page

| Element | Selector |
|---------|----------|
| Result item | `xpl-search-results-item` |
| Title link | `h2 a, h3 a, [href*="/document/"]` |
| Authors | `.author a, .authors-info span` |
| Year | `text matching /Year:\s*\d{4}/` |
| Journal | `.publication-title, .publisher-info-container` |
| Next page | `button.next-btn, a[aria-label="Next"]` |
| DOI link | `a[href*="doi.org"]` |

### Detail Page

| Element | Selector |
|---------|----------|
| Title | `h1.document-title span, .document-title-text` |
| Abstract | `div.abstract-text div, xpl-abstract-view` |
| Authors | `xpl-author-info a, .authors-info a.author-link` |
| DOI | `a[href*="doi.org"]` |
| Keywords | `xpl-keywords-item a, .doc-keywords-list a` |
| PDF link | `a[href*="stamp/stamp.jsp"], a[href$=".pdf"]` |
| BibTeX tab | `a:has-text("BibTeX")` |
| RIS tab | `a:has-text("RIS")` |
| Citation count | `.document-ft-metrics-bar span` |

## Login Detection

Check for any of these elements to verify authentication:

- `.user-info`
- `[data-testid="user-menu"]`
- `.login-user-name`
- `a[href*="profile"]`

If none found, the user is not logged in.

## Site Adapter Patterns (scripts/sites/)

### ScienceDirect

- Search URL: `https://www.sciencedirect.com/search?qs={kw}&date={y0}-{y1}&show=100&sortBy=relevance|date`
- Results: `li.ResultItem` (has `data-doi`), title `a.result-list-title-link`
  (href `/science/article/pii/{PII}`), authors `ol.Authors span.author`,
  journal/date `.srctitle-date-fields`, PDF `a.download-link[href*=pdfft]`,
  pagination `li.pagination-link.next-link` (`&offset=100` steps)
- Detail: `meta[name=citation_title|citation_doi|citation_journal_title|citation_publication_date]`,
  authors `.author-group .given-name + .surname`, abstract = innermost
  `.abstract.author`, keywords `.keywords-section .keyword`, PDF links contain
  the current PII (`/pdfft?md5=...`)
- PDF download: `pdfft` runs a `crasolve` JS challenge -> browser-assisted
  fallback (navigate, wait for `pdf.sciencedirectassets.com`, then same-origin
  `fetch(location.href)`); plain HTTP clients get 403

### Web of Science (webofscience.com / webofscience.clarivate.cn)

- Advanced search: textarea `#advancedSearchInputArea`, query syntax
  `TS=("kw") AND PY=(y0-y1)`, click the button whose text is `Search`
- Results: `app-record`; title `a[data-ta="summary-record-title-link"]`
  (href `/wos/woscc/full-record/{uid}`), authors
  `a[data-ta*="DisplayName-author-en"]` (group index varies:
  `SumAuthTa-{N}-DisplayName-author-en-{i}`), pubdate
  `[data-ta="summary-record-pubdate"]`, source title
  `a.summary-source-title-link`, citations
  `[data-ta="stat-number-citation-related-count"]`, next page
  `button[data-ta="next-page-button"]`
- Results list is lazy/virtual rendered -> scroll stepwise and accumulate by uid
- Full record: `[data-ta="FullRTa-fullRecordtitle-0"]`, DOI
  `[data-ta="FullRTa-DOI"]`, pubdate `[data-ta="FullRTa-pubdate"]`, abstract
  `[data-ta="FullRTa-abstract-basic"]`, keywords
  `[data-ta^="FRkeywordsTa-authorKeywordLink-"]`
- No hosted PDFs (citation index)

## PDF Download Strategy

1. **Preferred**: Navigate to `https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber={arnumber}` and let browser download
2. **Alternative**: Extract cookies from CDP session, use `requests`/`urllib` with Cookie header
3. **Validation**: Check file size >= 50KB and header starts with `%PDF-`

### Cookie Extraction (Playwright)

```python
cookies = page.context.cookies()
# Returns list of {name, value, domain, path, ...}
```

## Common Issues

| Issue | Solution |
|-------|----------|
| "Cannot connect to CDP" | Run `scripts/start_edge_cdp.ps1` - Edge 136+ ignores `--remote-debugging-port` on the default profile; a dedicated `--user-data-dir` is required |
| "No IEEE tabs found" | Open https://ieeexplore.ieee.org in the debug Edge |
| "Not authenticated" | Sign in to IEEE Xplore in the debug Edge |
| "Response was HTML" | Likely paywall or expired session; re-login |
| "Invalid PDF header" | Download link may be broken; try alternative URL |
| CAPTCHA | Pause automation, alert user to solve manually |

## Troubleshooting Flow

```
1. powershell -ExecutionPolicy Bypass -File scripts\start_edge_cdp.ps1
   (starts Edge with CDP on the dedicated profile, opens ieeexplore)
   ↓
2. python scripts/cdp_detector.py
   ↓ (if no IEEE tabs)
3. Open https://ieeexplore.ieee.org and sign in
   ↓
4. python scripts/cdp_detector.py (verify)
   ↓
5. python scripts/harvester.py --keyword "..." --count 10
```
