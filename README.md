# academic-db-harvester

An [OpenCode](https://opencode.ai) / Agent Skills skill that harvests academic
literature from **IEEE Xplore**, **ScienceDirect (Elsevier)** and **Web of
Science** by driving an already-logged-in Edge browser over the Chrome DevTools
Protocol, plus **OpenAlex** and **arXiv** over plain HTTPS. No API keys, no
Zotero, everything stays on your machine.

It also runs a **goal-driven multi-round retrieval** loop: give it a research
goal rather than a keyword, and it expands the goal into seed queries, mines
the adjacent field's own terminology, follows the citation graph, and then
produces a corpus that has been reviewed paper by paper.

> 中文：本仓库是一个 Agent Skill，通过 CDP 复用你已登录的 Edge 浏览器，在三大数据库检索、
> 抽取题录、按需下载 PDF 并生成 CSV/JSON/HTML 报告。全部本地运行。
> 另支持「多轮检索」：给出研究目标而非单个关键词，自动扩展为多轮检索并对全量文献逐篇复核。

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

**This repository is the single source of truth.** Clone it, run the install
script, and edit the skill *here* — never in the installed copy.

```bash
git clone git@github.com:Bluebubbledream/academic-db-harvester.git
cd academic-db-harvester
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

`install.ps1` mirrors `academic-db-harvester/` into a skills directory. The
default is `$HOME\.agents\skills`; point it elsewhere with `-Target`:

```powershell
# user-global
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 `
  -Target "$HOME\.config\opencode\skills"

# per project
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 `
  -Target "D:\project\.opencode\skills"
```

The mirror is one-way and total: files removed from the source are removed
from the installed copy, so the two never drift. `__pycache__` is never
copied, and the script fails loudly if the installed copy has no `SKILL.md`
or if the skill name stops matching the folder name.

Discoverable skill locations (OpenCode docs): `.opencode/skills/<name>/`,
`~/.config/opencode/skills/<name>/`, `.claude/skills/<name>/`,
`.agents/skills/<name>/` and their user-global equivalents. The folder name
must stay `academic-db-harvester` — the skill name in `SKILL.md` has to match
the directory that contains it.

## Developing

```
<this repo>                     the only place to edit
   └── academic-db-harvester/   the skill
        ├── SKILL.md            the contract: what the agent may do, and how
        └── scripts/            the implementation
```

1. Edit the skill **in this repository**.
2. `powershell -ExecutionPolicy Bypass -File .\install.ps1` to push it into
   the agent's skills directory.
3. Run the skill and check the output.
4. Commit and push.

The installed copy under `~/.agents/skills/` is generated output — treat it
like `__pycache__` and never hand-edit it. The next `install.ps1` run
discards any manual change made there.

Two things worth knowing before you change the code:

- **The script never decides anything.** Relevance, which expansion queries to
  run and which papers belong in the result are the reviewer's calls; the
  harvester mines candidates, writes the tables and reports what it did. A
  fixed threshold used to make those calls and it was wrong in both
  directions — see `RELEASE_NOTES.md` v1.1.0.
- **The browser adapters are the fragile part.** Publisher front-ends change;
  when extraction degrades, re-verify the selectors listed in
  `academic-db-harvester/references/cdp_reference.md` and record the date you
  checked in `CREDITS.md` §5.

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
| `--count` | int | `50` | Max papers per query |
| `--sort` | str | `relevance` | `relevance` / `newest` / `oldest` / `cited` |
| `--download-pdf` | flag | off | Download full text (full-text sources only) |
| `--min-if` | float | `0` | Impact-factor filter (IEEE; needs the EasyScholar extension) |
| `--output` | path | `~/Desktop/论文/…` | Output root |
| `--cdp-port` | int | `9222` | Edge remote-debugging port |
| `--multi-round` | flag | off | Run the goal-driven multi-round loop (below) |
| `--goal` | str | — | Research goal in free text; round 1 decomposes it into seed queries |
| `--terms-file` | path | — | Round-1 seed terms, one per line; the bridge when the goal is in a language the index does not understand |
| `--rounds` | str | `2,3,4` | Which rounds follow round 1 (round 1 always runs) |
| `--per-round` | int | `3` | Max queries per round |
| `--review` | str | `annotate` | `off` / `annotate` (measure, keep everything) / `gate` (also pause for the review) |
| `--approve` | path | — | Reviewer-edited query table to execute |
| `--verdicts` | path | — | Round-4 verdict JSON |
| `--resume` | path | — | Continue a paused run from the corpus on disk |
| `--llm` | flag | off | Let an LLM make the three decisions in one run (needs `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `OPENAI_MODEL`) |

## Multi-round retrieval

One keyword is a guess. `--multi-round` turns a research **goal** into four
progressively wider sweeps, and then a pass that checks whether the result is
any good.

```
                    goal (any language)
                          │
       ┌──────────────────┴──────────────────┐
       │ R1  seed queries                    │  goal → 2-3-gram search strings
       │ R2  adjacent fields + the field's   │  co-word/PRF over author keywords
       │     own professional vocabulary      │  + OpenAlex subject taxonomy
       │ R3  citation snowball (optional)    │  backward references + forward citations
       │ R4  corpus review                    │  every paper labelled by the reviewer
       └──────────────────┬──────────────────┘
                          ▼
              selection + per-query precision table
```

**The script never decides anything.** The agent running the skill makes three
judgement calls; everything else is mechanical and reported.

| # | Decision | How |
|---|----------|-----|
| 1 | goal → English search terms | the agent writes the field's own vocabulary — a script cannot translate a Chinese goal into "orchestration" |
| 2 | which expansion queries to run | the agent reads `term_review_round2.md` (candidate terms plus the adjacent domains and *their* keywords) and flips `approved` |
| 3 | which papers belong in the result | the agent reads `paper_review.md` in batches and labels every paper |

```bash
# 1) R1, then pause with the round-2 candidate table
python academic-db-harvester/scripts/harvester.py --source openalex --multi-round \
  --goal "cyber range orchestration" --terms-file terms.txt --review gate -o OUT

# 2) the agent edits OUT/<run>/reports/term_review_round2.json

# 3) R2 → R4, then pause with the reading pack
python academic-db-harvester/scripts/harvester.py --source openalex --multi-round \
  --goal "cyber range orchestration" --terms-file terms.txt \
  --approve OUT/<run>/reports/term_review_round2.json --resume OUT/<run> -o OUT2

# 4) the agent labels every paper in OUT2/<run>/reports/paper_review.json

# 5) ingest the verdicts - nothing is re-searched
python academic-db-harvester/scripts/harvester.py --source openalex --multi-round \
  --goal "cyber range orchestration" --resume OUT2/<run> \
  --verdicts OUT2/<run>/reports/paper_review.json --rounds 4 -o OUT2/<run>
```

`--resume` loads the corpus from disk, so a paused run never pays twice for
the rounds it already completed. With `--llm` and an OpenAI-compatible
endpoint configured, the same three decisions are made by an LLM inside a
single run and the file-shuttling above disappears.

Round 3 is **not** in the recommended flow: on the run that produced the
numbers in `RELEASE_NOTES.md` it returned 86 records of which 7 % were
directly relevant, and it amplified the drift of round 2.

## Output

```
{timestamp}_{keyword}[_{source}]/
  metadata/     candidates.json|csv, priority tiers, references.bib
                provenance.csv        (multi-round) which round, which query
                relevance_scores.csv  (multi-round) coverage per paper
                ai_review.csv         (multi-round) the reviewer's verdict
                selected_papers.json|csv  (multi-round) the selection
  pdfs/         001_{title}_{arnumber|pii|uid}.pdf, download_log.json
  citations/    per-paper .bib / .ris, all_references.bib
  abstracts/    per-paper .txt, all_abstracts.txt
  reports/      summary.html, statistics.json, search_log.txt
                query_plan.json|md            (multi-round) the reproducible plan
                term_review_round2.json|md    (multi-round) round-2 candidates
                paper_review.json|md          (multi-round) the reading pack
                round4_review_stats.json|md   (multi-round) precision per query
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