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

Measured against that document, **59 of its 110 content lines are carried over
verbatim** and roughly a dozen more are reworded (75–99 % similar). Concretely:

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
- Each adapter records its verification date in its module docstring, so a
  failure can be traced to a dated check instead of guesswork.