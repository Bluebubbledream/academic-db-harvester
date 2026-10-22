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
  (5) build a local paper library, or (6) run a goal-driven multi-round
  retrieval that expands a research goal into adjacent-field terminology and
  citation snowballing across three sweeps, then reviews the whole corpus
  against the goal and reports the selection.
license: MIT
compatibility: Windows + Edge/Chromium 136+ (CDP endpoint), Python 3.9+, playwright, websocket-client
metadata:
  opencode/slash: "true"
  audience: researchers
  workflow: literature-harvesting
---

# Academic DB Harvester

## Flow at a glance

`--multi-round` turns a research **goal** into a reviewed corpus. The agent
running this skill makes three judgement calls; the script does everything else
and never decides anything on its own.

```
                        goal (any language)
                              │
             ┌────────────────┴────────────────┐
             │ R1  seed queries                │  goal → 2-3-gram search
             │     3 English queries          │  strings; a CJK goal needs
             └────────────────┬────────────────┘  --terms-file
                              │  corpus ①
             ┌────────────────┴────────────────┐
             │ R2  adjacent fields + the       │  co-word/PRF over author
             │     field's own vocabulary      │  keywords + OpenAlex subject
             └────────────────┬────────────────┘  taxonomy
                              │  ★ AGENT picks the terms
                              │  corpus ②
             ┌────────────────┴────────────────┐
             │ R3  citation snowball (optional)│  backward references +
             │     backward + forward          │  forward citations
             └────────────────┬────────────────┘
                              │  corpus ③
             ┌────────────────┴────────────────┐
             │ R4  corpus review               │  ★ AGENT labels every
             │     RELEVANT / MARGINAL /      │     paper from the content
             │     OFF_TOPIC / INVALID        │
             └────────────────┬────────────────┘
                              ▼
              selection + per-query precision table
```

### The three decision points

| # | Decision | How the agent makes it | File to edit |
|---|----------|-------------------------|--------------|
| 1 | goal → English search terms | writes the field's own vocabulary — a script cannot translate a Chinese goal into "orchestration" | `terms.txt` → `--terms-file` |
| 2 | which expansion queries to run | reads `term_review_round2.md` (candidate terms, plus the adjacent domains and *their* keywords) and flips `approved` | `reports/term_review_round2.json` |
| 3 | which papers belong in the result | reads `paper_review.md` in batches and labels every paper | `reports/paper_review.json` |

Everything else is mechanical, and the script reports what it did. Measured on
one run: the terms the agent chose gave 53–73% directly-relevant results while
the terms the script mined on its own gave 20–27% — the judgement is not
decoration, it is the difference between a usable corpus and a haystack.

### Command sequence

```bash
# 1) R1 - runs, then pauses with the round-2 candidate table
python scripts/harvester.py --source openalex --multi-round \
  --goal "<the research goal>" --terms-file terms.txt --review gate -o OUT

# 2) AGENT: read OUT/<run>/reports/term_review_round2.md, then edit
#    OUT/<run>/reports/term_review_round2.json (approved: false to skip a term)

# 3) R2 -> R4 - resumes, runs the approved queries, then pauses with the pack
python scripts/harvester.py --source openalex --multi-round \
  --goal "<the research goal>" --terms-file terms.txt \
  --approve OUT/<run>/reports/term_review_round2.json --resume OUT/<run> -o OUT2

# 4) AGENT: read OUT2/<run>/reports/paper_review.md, label every paper in
#    OUT2/<run>/reports/paper_review.json (verdict + reason)

# 5) ingest the verdicts - nothing is re-searched
python scripts/harvester.py --source openalex --multi-round \
  --goal "<the research goal>" --resume OUT2/<run> \
  --verdicts OUT2/<run>/reports/paper_review.json --rounds 4 -o OUT2/<run>
```

`--resume` loads the corpus from disk, so a paused run never pays for the
rounds it already completed. Round 3 is skipped unless `--rounds` includes it.

With `--llm` and `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `OPENAI_MODEL` set, the
same three decisions are made by an LLM inside a single run and the
file-shuttling above disappears; without a key the run degrades to the
agent-in-the-loop flow above.

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

## Multi-round Retrieval

One keyword is a guess. `--multi-round` turns a research *goal* into four
progressively wider sweeps, each mined from what the previous sweep found —
and then a fourth pass that checks whether the result is any good.

| Round | What it does | Where the terms come from | Network |
|-------|--------------|---------------------------|---------|
| **1** | Goal → seed search strings → harvest | `query_planner.seed_queries()`: n-gram decomposition of the goal, intent verbs stripped; CJK goals are passed through verbatim | no |
| **2** | Adjacent fields + the field's own professional vocabulary | `query_planner.mine_adjacent_terms()` (co-word / PRF over author keywords, trust weight 3.0) **and** `oa_graph.adjacent_terms()` (OpenAlex controlled subject tree: domain > field > subfield > topic) | taxonomy only |
| **3** | Backward + forward citation snowball | `query_planner.select_citation_seeds()` + `oa_graph.snowball()` (Wohlin 2014, EASE'14) | yes |
| **4** | Review the whole corpus against the goal | `paper_review.build_pack()` / `paper_review.ingest()` — the agent reads a compact batched pack and labels every paper | no |

Rounds 1-3 maximise recall. None of them checks precision: a co-word term can
be on-topic and still drag in publisher's notes, journal front matter and
proceedings prefaces, and a snowball will happily collect a survey's entire
bibliography. Round 4 is the only stage that can tell "this paper is about the
goal" from "this paper was merely retrieved".

### Run

```bash
# Recommended: an API source lets rounds 2 and 3 resolve ids back for free
python scripts/harvester.py --source openalex --multi-round \
  --goal "cyber range orchestration efficiency at ten-thousand-node scale" \
  --years 2020-2026 --count 50 --per-round 3 --download-pdf

# Chinese goal + a publisher source (round 1 still works; round 3 still snowballs)
python scripts/harvester.py --source ieee --multi-round \
  --keyword "cyber range" --goal "网络靶场大规模资源编排效率" \
  --years 2020-2026 --count 30
```

Round 1 always runs — it is the entry point. `--rounds` selects which rounds
follow it (`2,3,4` by default). Round 4 always pauses, because a script cannot
review itself — the agent running the skill does that.

**Cost guard.** Roughly `per_round × count` new candidates are collected *per
round*, so the defaults expect ~3 × 3 × 50 ≈ 450 before de-duplication, plus
`snowball_backward + snowball_forward` metadata-only records in round 3. Lower
`--per-round` or `--count` first if the corpus is exploding.

### Relevance measurement — and who decides

Rounds 2 and 3 widen from round 1, and widening is where retrieval quietly
loses the thread. An earlier version of this skill scored candidates against
a fixed threshold (PASS / REVIEW / BLOCKED) and dropped the BLOCKED ones. That
threshold was one guess about a corpus it had never seen, and it was wrong in
both directions at once: it discarded good papers whose vocabulary merely
differed in morphology (`containerized` vs `containerization`), and it threw
away references the reviewer would have wanted to see.

**So the harvester no longer decides anything.** Judging relevance belongs to
the agent running the skill, in two places:

| Decision | Who makes it | Mechanism |
|----------|--------------|-----------|
| which expansion queries to run | the agent | `reports/term_review_round2.json` — set `approved` to false to skip a term |
| which papers belong in the result | the agent | `reports/paper_review.json` — label every paper `RELEVANT` / `MARGINAL` / `OFF_TOPIC` / `INVALID` |

What the script still does is the mechanical part that *supports* those
judgements without pre-empting them: it measures each retrieved paper against
the round-1 corpus and writes the numbers down.

```
cov = fraction of the paper's content tokens that occur in the round-1
      vocabulary, where a token qualifies once it appears in
      max(1, 5% of the round-1 papers)
```

Both sides of the comparison use the same fields (title + keywords + the first
800 characters of the abstract), and tokens are matched on a shared prefix of
at least five characters, so `containerized` matches `containerization` and
`orchestrating` matches `orchestration`. Without that fold, morphology alone
produces false "off-topic" readings on every good paper.

`cov` is **reported, never thresholded**. It goes to
`metadata/relevance_scores.csv` and onto each paper as `relevance`, so a
judgement can be audited against the number that informed it. It is kept out
of the human-readable review pack on purpose: a score next to a title anchors
the reader towards agreeing with it, and the point of round 4 is that the
reader judges from the content.

| `--review` | Behaviour |
|------------|-----------|
| `off` | No measurement at all |
| `annotate` *(default)* | Every retrieved paper is measured and **kept** |
| `gate` | Same, plus a pause before round 2 so the agent can pick the queries |

```bash
# 1) preview - runs round 1, mines round 2, writes the candidate table, pauses
python scripts/harvester.py --source openalex --multi-round \
  --goal "cyber range orchestration" --review gate -o OUT

# 2) the agent edits OUT/<run>/reports/term_review_round2.json, flips `approved`
# 3) execute - re-run against that list (round 1 repeats: one query)
python scripts/harvester.py --source openalex --multi-round \
  --goal "cyber range orchestration" \
  --approve OUT/<run>/reports/term_review_round2.json -o OUT2
```

The candidate list is a plain JSON file the agent edits;
`term_review_round2.md` is the human-readable twin, and it also carries the
adjacent subject domains with their keywords — the raw material for choosing
terms that the mined candidates do not contain.

**One measure stays on the query side**, because it costs no recall: every
round-2 query is issued as `<adjacent term> AND <round-1 core token>`, an
intersection with the topic rather than a bare term. An *ambiguous* term can
be genuinely on-topic and still retrieve a different global population —
`directed acyclic graphs` really does appear in a serverless-orchestration
cyber-range paper, yet searching it bare returns epidemiology and
circuit-synthesis papers. Anchoring narrows it instead of removing it.

The anchor has to be a *distinctive* token, so it is chosen from a band: a
seed token appearing in 10–50% of the round-1 corpus. A token in nearly every
paper (`language`, in a corpus of LLM papers) excludes nothing; one in nearly
none excludes too much. And below `--min-corpus-for-anchor` records (default
20) no anchor is used at all — on a small corpus every topic word is in most
of the papers, so any anchor is arbitrary and can exclude documents that
merely happen to use different wording. The expansion then runs on the
multi-word term alone, which is the only narrowing left that is safe.

### Goals in another language

OpenAlex and arXiv are English-language indexes, but they also index
Chinese-language literature, and a Chinese goal searched verbatim lands in the
wrong literature entirely. Measured on OpenAlex:

| Query | Hits | Top results |
|-------|------|-------------|
| `网络靶场` | 2 175 | Ixia 的网络靶场白皮书、中文《网络安全》课程改革…… plus 水文水资源、电气自动化、城乡配电网、高职网络诈骗 |
| `网络靶场 大规模资源编排` | 359 | **all five off-topic** — 水文水资源、电气自动化、配电网…… |
| `cyber range` | 409 497 | AIT Cyber Range、Survey of Cyber Ranges、KYPO Cyber Range——**all on-topic** |

So for a CJK goal against `openalex` / `arxiv`, the harvester **drops the CJK
seed query and says so** rather than filling the corpus with Chinese papers
about power grids. The English terms have to come from the agent running the
skill — it is the one party that can translate a goal into the field's own
vocabulary — and they go in a plain terms file:

```bash
cat > terms.txt <<'EOF'
cyber range orchestration
network range provisioning
EOF

python scripts/harvester.py --source openalex --multi-round \
  --goal "网络靶场大规模资源编排" --terms-file terms.txt
```

`--terms-file` takes one term per line (`#` comments allowed) or a JSON list.
Terms become the round-1 seeds verbatim, and the goal stays the *judging
target* for rounds 2 and 4 — which is where the Chinese is actually useful.

For `ieee` / `sciencedirect` / `wos`, a CJK query is left in place: those
indexes search Chinese-language material too, and the query is usually worth
trying.

### Round 4: reviewing the corpus

Round 4 is where the agent running this skill reads what was actually
collected. The script never guesses a verdict; it writes a compact reading
pack, waits, and then ingests the verdicts.

```bash
# 1) run rounds 1-3, then pause with the review pack written
python scripts/harvester.py --source openalex --multi-round --goal "..." -o OUT
#    -> OUT/<run>/reports/paper_review.{json,md}

# 2) the agent reads the pack and fills `verdict` (+ `reason`) for every entry

# 3) hand the verdicts back - --resume loads the corpus from disk, so
#    rounds 1-3 are not re-searched
python scripts/harvester.py --source openalex --multi-round --goal "..." \
  --resume OUT/<run> --verdicts OUT/<run>/reports/paper_review.json --rounds 4
```

Verdicts: `RELEVANT` (answers the goal), `MARGINAL` (adjacent, keep as
background), `OFF_TOPIC` (retrieved but not about the goal), `INVALID` (not a
usable paper at all — publisher's note, editorial, front matter, a journal
title scraped as a record, retracted work). `RELEVANT` and `MARGINAL` are the
selection.

The pack is deliberately small — title, venue, keywords and the first ~300
characters of the abstract, in batches of `--review-batch` papers — because a
corpus of full abstracts does not fit in any sane review budget. Use
`--review-limit` to cap the pack when the corpus is very large.

Verdicts are matched back by **DOI, then title, then index**, so a re-run that
returns the same papers in a different order still lines up.

The most useful thing round 4 produces is not the selection but the
**per-query precision table** in `reports/round4_review_stats.md`: it tells you
which expansion query was worth running and which was not.

### Behaviour you should know about

- **Round 4 always pauses.** With `--multi-round` and no `--verdicts`, the run
  ends after writing the review pack. That is intentional: a script cannot
  review itself — the agent running the skill does. Pick up with
  `--resume <run dir> --verdicts <the pack the agent filled in> --rounds 4`;
  `--resume` loads the corpus from disk instead of re-searching, so rounds 1-3
  are not paid for twice.
- **Nothing is dropped.** The harvester measures relevance and writes the
  numbers to `metadata/relevance_scores.csv`; it never removes a paper on its
  own. What ends up in the result is decided in round 4.
- **A rate-limited source does not lose the run.** If the source starts
  answering HTTP 429 mid-run, the error is reported, everything collected so
  far is kept and saved, and the run continues with what it has. On OpenAlex
  this is the shared per-IP daily budget; set `OPENALEX_API_KEY` for a
  private one.
- **Round 3 ignores `--years`.** Backward references are older than the seed by
  construction; filtering them by the seed's window would discard exactly the
  classics the snowball exists to find. Set `--snowball-backward 0` to disable
  the backward pass, `--snowball-forward 0` to disable the forward one.
- **Round 2 degrades gracefully.** If OpenAlex is unreachable (offline, IP
  budget exhausted, proxy) the taxonomy pass is skipped with a warning and the
  co-word pass still runs. `--no-taxonomy` disables it outright.
- **Round 3 needs a DOI.** Seeds are selected from records that carry one, so a
  source without DOIs produces "snowball skipped".
- **Round 3 seeds from round 1 by default** (`--snowball-from round1`). A
  snowball amplifies whatever it is seeded from, so seeding from the whole
  corpus lets a round-2 term that drifted drag its own reference list in behind
  it — one off-topic expansion term can then own the entire round 3. Use
  `--snowball-from all` only when the expansion's lineage is wanted on purpose.
- **Everything lands in one corpus.** Records from all three rounds are
  de-duplicated on DOI / ARN / PII / uid / URL / title and stamped with
  `round`, `via` and `query`, so a paper that all three rounds reach is stored
  once and appears once in `candidates.json`.
- **Round 3 records have no detail page.** They arrive complete from OpenAlex,
  so their citations are generated by the BibTeX fallback rather than exported
  from a publisher page.

## Output Structure

Output layout, rooted at `~/Desktop/论文/` (or `--output`):

```
{timestamp}_{keyword}[_{source}]/
  metadata/
    candidates.json / candidates.csv        <- de-duplicated corpus, all rounds
    high_priority.json / medium_priority.json / low_priority.json
    provenance.csv                          <- index / round / via / query per record
    round1_candidates.json / .csv           <- multi-round only, per-round snapshot
    round2_candidates.json / .csv
    round3_candidates.json / .csv
    ai_review.csv                           <- round 4, one row per paper with its verdict
    selected_papers.json / .csv             <- round 4, RELEVANT + MARGINAL
    relevance_scores.csv                        <- cov per retrieved paper (a measurement, not a verdict)
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
    query_plan.json / query_plan.md         <- multi-round only, the reproducible plan
    term_review_round2.json / .md           <- pre-query advisory table (round 2)
    paper_review.json / paper_review.md     <- round 4 reading pack
    round4_review_stats.json / .md          <- round 4 verdicts + precision table
  urls.csv
  pending.csv
  config.json
```

`provenance.csv` is the audit trail for a multi-round run: `round` says which
sweep produced the record, `via` says how (`seed_query`, `adjacency`,
`backward`, `forward`), and `query` is the exact search string that surfaced it.

## Scripts Reference

| Script | Purpose |
|--------|---------|
| `scripts/start_edge_cdp.ps1` | Launch Edge with CDP on a dedicated profile (Edge 136+ compatible); verifies the endpoint |
| `scripts/start_edge_cdp.bat` | Double-click wrapper for the PowerShell launcher |
| `scripts/harvester.py` | Main entry point; source-agnostic pipeline orchestration, single-round or multi-round |
| `scripts/query_planner.py` | Multi-round *planning*: seed-query decomposition, co-word/PRF adjacency mining, citation-seed selection, plan rendering |
| `scripts/oa_graph.py` | OpenAlex *graph* operations: subject-taxonomy adjacency (round 2) and backward/forward citation snowballing (round 3) |
| `scripts/term_gate.py` | Topic gate: scores retrieved papers against the round-1 corpus, drops the off-topic ones, emits the query advisory table, computes the query anchor |
| `scripts/paper_review.py` | Round 4: builds the batched reading pack, ingests the verdicts, emits the selection and the per-query precision table |
| `scripts/sites/` | Site adapters (`base.py` interface, `ieee.py`, `sciencedirect.py`, `wos.py`, `openalex.py`, `arxiv.py`) |
| `scripts/cdp_detector.py` | Edge CDP browser detection and tab discovery (source-aware) |
| `scripts/ieee_automation.py` | IEEE Xplore search, pagination, metadata extraction (used by `sites/ieee.py`) |
| `scripts/pdf_downloader.py` | PDF download with retry, validation, logging, and browser-assisted fallback |
| `scripts/storage.py` | Output directory creation, file organization, CSV/JSON I/O |
| `scripts/reporter.py` | HTML report and statistics generation |
| `scripts/utils.py` | Shared helpers: logging, filename sanitization, retry decorator, BibTeX fallback |

## Key Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `--source` | str | `ieee` | Data source: `ieee`, `sciencedirect`, `wos`, `openalex`, `arxiv` |
| `--keyword` | str | (required) | Search keyword(s); may be omitted when `--multi-round --goal` is given |
| `--years` | str | `2020-2025` | Year range, e.g. `2024-2026` (ignored by the round-3 snowball) |
| `--count` | int | `50` | Max papers to collect **per query** (IEEE/SD fetch up to 100/page; WoS scrolls the whole results list) |
| `--sort` | str | `relevance` | Sort order: `relevance`, `newest`, `oldest`, `cited` (IEEE/SD; WoS uses its own default) |
| `--download-pdf` | flag | `False` | Enable PDF download (full-text sources only) |
| `--min-if` | float | `0` | Minimum impact factor filter (requires EasyScholar, IEEE) |
| `--output` | str | auto | Output directory (auto-generates if not set) |
| `--cdp-port` | int | `9222` | Edge remote debugging port |
| `--multi-round` | flag | `False` | Run the multi-round loop (see [Multi-round Retrieval](#multi-round-retrieval)) |
| `--goal` | str | `""` | Research goal in free text; round 1 decomposes it into seed queries |
| `--terms-file` | str | — | File of round-1 seed terms (one per line, or a JSON list); the bridge when the goal is in a language the index does not understand |
| `--resume` | str | — | Load the corpus from a previous run directory and continue; rounds already present are not re-run |
| `--rounds` | str | `2,3,4` | Which rounds to run after round 1 (round 1 always runs) |
| `--per-round` | int | `3` | Max queries executed per round |
| `--min-support` | int | `2` | Round-2 minimum term support |
| `--max-term-coverage` | float | `0.35` | Round 2: drop a mined term that already covers more than this share of the corpus — mined terms are ranked by frequency, so the top ones are the topic's own vocabulary and cannot expand it |
| `--max-seed-overlap` | float | `0.6` | Round 2: drop a mined term whose Jaccard overlap with a seed query reaches this — it would retrieve the same corpus again |
| `--min-corpus-for-anchor` | int | `20` | Round 2: below this corpus size no anchor token is used, because no token is distinctive enough to narrow by |
| `--no-taxonomy` | flag | `False` | Skip the OpenAlex subject-taxonomy pass in round 2 |
| `--snowball-seeds` | int | `5` | Round-3 seed papers |
| `--snowball-from` | str | `round1` | Seed round 3 from `round1` papers only, or from `all` |
| `--snowball-backward` | int | `60` | Max references pulled in round 3 (`0` disables) |
| `--snowball-forward` | int | `40` | Max citing works pulled in round 3 (`0` disables) |
| `--review` | str | `annotate` | Relevance measurement: `off`, `annotate` (measure and keep everything), `gate` (also pause so the agent picks the round-2 queries) |
| `--approve` | str | — | Reviewer-edited query table to execute instead of re-mining the terms |
| `--verdicts` | str | — | Round-4 verdict JSON (the filled-in `paper_review.json`) |
| `--review-limit` | int | `0` | Round 4: cap the review pack at N papers (`0` = all) |
| `--review-batch` | int | `30` | Round 4: papers per batch in the reading pack |
| `--llm` | flag | `False` | Let an LLM make the three decisions inside one run instead of the agent shuttling files; needs `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `OPENAI_MODEL` |
| `--llm-model` | str | env | Model name for `--llm` (falls back to `OPENAI_MODEL`) |
| `--llm-base-url` | str | env | OpenAI-compatible endpoint for `--llm` (falls back to `OPENAI_BASE_URL`) |
| `--llm-api-key` | str | env | Key for `--llm` (falls back to `OPENAI_API_KEY`); never written to a report or a log |
| `--llm-terms` | int | `4` | Round 1: how many seed terms to plan |
| `--llm-batch` | int | `12` | Round 4: papers per LLM labelling call |
| `--llm-max-calls` | int | `24` | Round 4: cap on LLM labelling calls before the run falls back to the file workflow |

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
  **No account, no key, no login.** It is an anonymous public API: requests are
  rate-limited by *public IP address* when unauthenticated (OpenAlex shares one
  free daily budget of roughly $0.10 across your whole egress IP), so hitting
  HTTP 429 means the budget ran out — not that anything is wrong. Two optional
  environment variables change that: `OPENALEX_MAILTO` joins the polite pool,
  and `OPENALEX_API_KEY` grants a private budget. Neither is required and
  neither is set by default. Rounds 2 and 3 of the multi-round loop read the
  same API (`primary_topic` / `topics` for the subject tree, `referenced_works`
  and `filter=cites:` for the snowball), and every error leaving
  `oa_graph.py` is passed through `redact()` first, so a key can never reach a
  log line or a report file.
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
