# Credits & Attribution

This file lists what `academic-db-harvester` builds on: the specifications it
implements, the publisher platforms it targets, the runtime dependencies it
installs, and the earlier work it grew out of. All links point to official
vendor or project-maintainer pages.

## 1. Specifications & official documentation

| Topic | Official source |
|---|---|
| Agent Skills format (frontmatter, discovery, naming rules) — OpenCode docs | https://opencode.ai/docs/skills/ |
| Chrome DevTools Protocol — Runtime/DOM/Network domains (Google-maintained specification site) | https://chromedevtools.github.io/devtools-protocol/ |
| Playwright for Python — API, selectors, `connect_over_cdp` | https://playwright.dev/python/docs/intro |
| MIT License — canonical text | https://opensource.org/license/mit |

## 2. Publisher platforms targeted (official sites)

| Platform | Official site | Role |
|---|---|---|
| IEEE Xplore (IEEE) | https://ieeexplore.ieee.org | Search, metadata, citations, PDF full text |
| ScienceDirect (Elsevier) | https://www.sciencedirect.com | Search, metadata, PDF full text |
| Web of Science (Clarivate) | https://www.webofscience.com | Search, metadata, citations (index database) |
| Web of Science China mirror (Clarivate) | https://webofscience.clarivate.cn | Same service, automatically used from mainland China |
| OpenAlex | https://openalex.org · docs https://docs.openalex.org | Cross-publisher bibliographic graph (public API) |
| arXiv | https://arxiv.org · API guide https://info.arxiv.org/help/api/user-manual.html | Open preprint server (public Atom API, free full text) |

This project contains **no** publisher code, markup or PDFs. It automates the
browser session you already have; access control remains with the publishers.

## 3. Runtime dependencies (installed via pip, not vendored)

| Package | License | Upstream |
|---|---|---|
| `playwright` (Python bindings, Chromium driver) | Apache-2.0 | https://playwright.dev/python/ |
| `websocket-client` | Apache-2.0 | https://github.com/liris/websocket-client |
| EasyScholar browser extension — **optional, only for `--min-if`** | see site | https://easyscholar.cc/ |

No third-party source code is copied into this repository.

## 4. Upstream work this skill grew out of

The project is a rewrite and generalization of an earlier, IEEE-only skill that
lived in the author's own local skill library: **`ieee-xplore-harvester`**,
which consisted of a single `SKILL.md` describing an IEEE Xplore-only tool.

### 4.1 Inherited from `ieee-xplore-harvester`

Measured line by line against that document, the reuse is concentrated in
**structure and inventories, not in prose**:

- 59 of its 110 content lines appear verbatim — but 33 of those are structural
  (section headings, code fences, table headers and separators) and 18 are file
  names from the output layout. Only **6 are identical sentences**.
- Of the 48 sentence-like lines in the predecessor: 16 verbatim, 20 reworded at
  ≥70 % similarity (mostly table rows extended with a few words), 9 lightly
  modified (50–70 %), 3 rewritten.
- In character terms 83 % of the predecessor's text is literally reused, but
  that figure is dominated by the 18-line output-layout block.

In substance, what was carried over:

- the document skeleton — Overview / Quick Start / Prerequisites / Run /
  Workflow / Output Structure / Scripts Reference / Key Parameters /
  Error Handling / References;
- the run example
  `python scripts/harvester.py --keyword "solid electrolyte interphase" --years 2020-2025 --count 200 --download-pdf`;
- the output layout (`metadata/`, `pdfs/`, `citations/`, `abstracts/`,
  `reports/`, `urls.csv`, `pending.csv`, `config.json`) and the three report
  artefacts (`summary.html`, `statistics.json`, `search_log.txt`);
- the extracted metadata fields (title, authors, DOI, abstract, keywords,
  citations), PDF validation (≥50 KB, valid header), priority tiers,
  de-duplication by article number / DOI / URL / title, and the EasyScholar
  impact-factor hook;
- the CDP detection description (`http://127.0.0.1:9222/json/list`, login state
  checked by DOM inspection) and the dependency install commands.

The explanatory prose was largely rewritten; this repository is an incremental
rewrite of its predecessor, not a clean-room implementation of it.

### 4.2 Changed in this rewrite

- **Architecture**: one hard-wired IEEE path → an adapter contract
  (`scripts/sites/base.py`) plus `SITE_REGISTRY`, so `--source` picks the
  adapter and the pipeline stays source-agnostic.
- **New sources**: ScienceDirect (including a browser-assisted fallback for the
  `pdfft` JS challenge) and Web of Science (metadata/citations, both regional
  domains supported).
- **De-duplication** extended to DOI / ARN / PII / WOS uid / URL / title.
- **Flags**: added `--source`, `--sort`, `--min-if`, `--cdp-port`.
- **Launcher**: a bundled `start_edge_cdp.ps1` replaces the predecessor's
  "close every Edge window first" instruction, because Chromium ≥136 silently
  ignores `--remote-debugging-port` on the default profile. The launcher uses a
  dedicated profile, copies login state once, and passes
  `--remote-allow-origins=*` (required for the CDP WebSocket upgrade the code
  performs via `chromium.connect_over_cdp`).

### 4.3 `edge-cdp` — related skill, not a code ancestor

A separate skill in the same local library documenting how to start Edge with
CDP on Windows. Measured textual similarity with this repository is 2.9 %
(character level), so it is **not** an ancestor of the documentation. It is the
source of two operational requirements that are implemented here: the dedicated
`--user-data-dir`, and the mandatory `--remote-allow-origins=*`.

### 4.4 Provenance of the Python implementation

The predecessor kept no `scripts/` directory, so the modules in this repository
(`harvester.py`, `cdp_detector.py`, `ieee_automation.py`, `pdf_downloader.py`,
`reporter.py`, `storage.py`, `utils.py`, `sites/`) were implemented for this
project against the behaviour its predecessor documented, and their interface is
defined by `scripts/sites/base.py`.

*If you later swap any module for a third-party implementation, credit that
author and license in this section before publishing — the MIT license in
`LICENSE` covers only what was written for this project.*

## 5. Maintenance notes

- Site selectors were verified live on **2026-09-30** against IEEE Xplore,
  ScienceDirect (institutional session) and `webofscience.clarivate.cn`.
  Publisher front-ends change; when extraction degrades, re-verify the selectors
  listed in `references/cdp_reference.md`.
- The OpenAlex adapter was verified live on **2026-10-14** against
  `api.openalex.org` (`/works?search=…`, year filter, four sort modes, inverted
  abstract reconstruction, OA PDF links, PDF download of a 1.1 MB file verified by
  `%PDF-` header).
- The arXiv adapter was verified live on **2026-10-14** against
  `export.arxiv.org/api/query`: four records retrieved, **4/4 PDFs downloaded**
  (1.0–6.7 MB, all `%PDF-`-validated). An earlier note in this file reported arXiv
  as unreachable with HTTP 429; that was a rate-limit triggered by back-to-back
  probing, not a block. arXiv asks automated clients to stay ≥3 s apart, which
  `ApiSiteAdapter` subclasses now enforce (`ARXIV_MIN_INTERVAL`).
- Sources evaluated during the same session and **deliberately not included**,
  with the reason recorded so they are not re-trialled blindly:
  `dblp.org` → bot-challenge interstitial; `dl.acm.org` → HTTP 403 without a
  browser session (would need a browser-backed adapter); Crossref discovery →
  returns component DOIs (`type: component`, e.g. "Figure 1: …") unless heavily
  filtered, so it is better suited to DOI enrichment than to discovery.
- Each adapter records its verification date in its module docstring, so a
  failure can be traced to a dated check instead of guesswork.