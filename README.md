# academic-db-harvester

An [OpenCode](https://opencode.ai) / Agent Skills skill that harvests academic
literature from **IEEE Xplore**, **ScienceDirect (Elsevier)** and **Web of
Science** by driving an already-logged-in Edge browser over the Chrome DevTools
Protocol. No API keys, no Zotero, everything stays on your machine.

> 中文：本仓库是一个 Agent Skill，通过 CDP 复用你已登录的 Edge 浏览器，在三大数据库检索、
> 抽取题录、按需下载 PDF 并生成 CSV/JSON/HTML 报告。全部本地运行。

---

## Why this exists

Bulk metadata retrieval usually means API keys, rate limits and per-site
client libraries. This skill takes a different route: it attaches to the browser
you are already signed in to, so it inherits your institutional access, and it
keeps every site-specific quirk inside a small adapter instead of spreading it
through the pipeline.

- **Reuses your session** – no credentials stored, no token management.
- **Adapters, not conditionals** – new databases are added, not bolted on.
- **Honest reporting** – paywalled or blocked papers land in `pending.csv`
  instead of silently disappearing.

## Requirements

| Item | Detail |
|---|---|
| OS | Windows (bundled launcher is PowerShell) |
| Browser | Edge / Chromium 136+ (CDP on the default profile is silently ignored by Chromium ≥136) |
| Python | 3.9+ |
| Deps | `playwright`, `websocket-client` (`pip install -r academic-db-harvester/scripts/requirements.txt`) — **only needed for the browser-backed sources** |
| Playwright browser | `playwright install chromium` (browser-backed sources only) |
| Access | Your own IEEE / Elsevier / Clarivate login and entitlements |

## Install

Clone, then copy the skill folder into any of the locations OpenCode scans:

Download or clone this repository, then copy the `academic-db-harvester/`
folder into any of the locations OpenCode scans — pick one:

```powershell
# from the cloned repository root
Copy-Item -Recurse .\academic-db-harvester "$HOME\.config\opencode\skills\"  # user-global
Copy-Item -Recurse .\academic-db-harvester .opencode\skills\                # per project
Copy-Item -Recurse .\academic-db-harvester .claude\skills\                 # Claude-compatible loaders
Copy-Item -Recurse .\academic-db-harvester .agents\skills\                 # agent-compatible loaders
```

The folder name must stay `academic-db-harvester` — the skill name in
`SKILL.md` has to match the directory that contains it.

Discoverable skill locations (OpenCode docs): `.opencode/skills/<name>/`,
`~/.config/opencode/skills/<name>/`, `.claude/skills/<name>/`,
`.agents/skills/<name>/` and their user-global equivalents.

| Platform | Role | Transport | PDF |
|---|---|---|---|
| IEEE Xplore | Search, metadata, citations, full text | Edge + CDP + login | yes |
| ScienceDirect (Elsevier) | Search, metadata, full text | Edge + CDP + login | yes |
| Web of Science (Clarivate) | Search, metadata, citations | Edge + CDP + login | no (index) |
| **OpenAlex** | Cross-publisher graph: DOI, venue, citations, keywords, OA links | **HTTPS API — no browser** | when open-access |
| **arXiv** | Preprints: title, authors, abstract, subjects, published venue | **HTTPS API — no browser** | yes, every record |

OpenAlex covers 250M+ works across all publishers, including ones with no
dedicated adapter here — start there for broad coverage, then narrow with a
publisher source. arXiv is the cheapest source when you need full text rather
than citations.

With the skill installed you can just ask, e.g. *"harvest 50 papers on
`solid electrolyte interphase` from IEEE Xplore, 2020-2025, download the PDFs"*.
Or run the scripts directly:

```bash
# 1. start Edge with a CDP port (dedicated profile; first run copies your logins)
powershell -NoProfile -ExecutionPolicy Bypass -File academic-db-harvester/scripts/start_edge_cdp.ps1

# 2. open and sign in to the target site inside that Edge window (first run only)

# 3. harvest — OpenAlex and arXiv need no browser at all
python academic-db-harvester/scripts/harvester.py --source openalex \
       --keyword "cyber range" --years 2020-2026 --count 100 --download-pdf

python academic-db-harvester/scripts/harvester.py --source arxiv \
       --keyword "cyber range" --years 2022-2026 --count 30 --download-pdf

# 4. or a publisher source (browser + your login)
python academic-db-harvester/scripts/harvester.py --source ieee \
       --keyword "solid electrolyte interphase" --years 2020-2025 --count 200 --download-pdf
python academic-db-harvester/scripts/harvester.py --source sciencedirect \
       --keyword "cyber range" --years 2024-2026 --count 50 --download-pdf
python academic-db-harvester/scripts/harvester.py --source wos \
       --keyword "cyber range" --years 2024-2026 --count 100
```

| Flag | Type | Default | Meaning |
|---|---|---|---|
| `--source` | str | `ieee` | `ieee` / `sciencedirect` / `wos` / `openalex` / `arxiv` |
| `--keyword` | str | required | Search terms (quote multi-word phrases) |
| `--years` | str | `2020-2025` | Year range |
| `--count` | int | `50` | Max papers |
| `--sort` | str | `relevance` | `relevance` / `newest` / `oldest` / `cited` |
| `--download-pdf` | flag | off | Download full text (full-text sources only) |
| `--min-if` | float | `0` | Impact-factor filter (IEEE; needs the EasyScholar extension) |
| `--output` | path | `~/Desktop/论文/…` | Output root |
| `--cdp-port` | int | `9222` | Edge remote-debugging port |

## Output

```
{timestamp}_{keyword}[_{source}]/
  metadata/     candidates.json|csv, priority tiers, references.bib
  pdfs/         001_{title}_{arnumber|pii|uid}.pdf, download_log.json
  citations/    per-paper .bib / .ris, all_references.bib
  abstracts/    per-paper .txt, all_abstracts.txt
  reports/      summary.html, statistics.json, search_log.txt
  urls.csv      paper links
  pending.csv   failures / paywalled items
  config.json   run parameters
```

## Extensibility

The pipeline (`harvester.py`) is source-agnostic and transport-agnostic. Pick one
of two base classes; a new database is one file plus one registry entry.

**API-backed (recommended first)** — subclass `ApiSiteAdapter`
(`scripts/sites/base.py`) and implement a single method:

```python
fetch_page(keyword, years, sort, page) -> list[dict]
```

plus `name`, `display_name`, `has_pdf`, `page_size`. The base class supplies
paging, detail handling, cookie-free PDF picking, `_http_get()` with
retry/backoff and `parse_years()`. `scripts/sites/openalex.py` is ~120 lines and
works as a complete reference.

**Browser-backed** — subclass `BaseSiteAdapter`, implement `build_search_url`,
`navigate_to_search`, `extract_result_list`, `go_next_page`, `open_detail`,
`extract_detail`, `check_login_status` (`extract_bibtex` / `extract_ris`
optional), and declare `name`, `display_name`, `hosts` (URL prefixes used to find
the right tab), `has_pdf`, `login_required`. Reusable helpers `_wait_for_any`,
`_eval`, `get_cookies` and `_sleep` come from the base class.

Finally register it in `scripts/sites/__init__.py` → `SITE_REGISTRY`; the source is
then available as `--source <name>` with no changes to download, storage or
reporting code.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Cannot reach the CDP endpoint | Run `scripts/start_edge_cdp.ps1` (or double-click the `.bat`). Chromium ≥136 ignores `--remote-debugging-port` on the default profile. |
| "not logged in" | Sign in to the site inside the debug Edge window (first run copies cookies from your main profile). |
| PDF download failed | Paywall / no entitlement, or the ScienceDirect JS challenge was not solved → recorded in `pending.csv`. |
| CAPTCHA | Solve it manually in the Edge window; the run continues afterwards. |
| API source returns HTTP 429 | OpenAlex: set `OPENALEX_MAILTO=you@example.com`. arXiv: keep the ≥3 s request spacing the adapter enforces, or raise `ARXIV_MIN_INTERVAL`. Retries with backoff are built in. |
| Duplicates | De-duplicated by DOI / ARN / PII / WOS uid / URL / title. |

## Responsible use

This tool automates *your own* browsing session for personal research. Keep it
at that scale: respect each publisher's terms of use, do not redistribute
harvested PDFs, and do not run bulk jobs against institutional endpoints at a
rate that degrades service for others. Access control is enforced by the
publishers, not by this project — nothing here bypasses a paywall; it only
reuses the session you already have.

## Credits & attribution

See [`CREDITS.md`](./CREDITS.md) for the standards, platform APIs and upstream
work this builds on, with links to the official documentation.

## License

MIT — see [`LICENSE`](./LICENSE).