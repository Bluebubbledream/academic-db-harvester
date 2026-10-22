# Release notes

## v1.1.0 — 2026-10-22

Goal-driven multi-round retrieval, and a lot of things that did not work.

### Added
- **Multi-round retrieval** (`--multi-round`): a research *goal* becomes four
  sweeps instead of one keyword search.
  - **R1** — the goal is decomposed into seed queries (`query_planner.py`);
    a CJK goal is rejected against the English-language indexes and the
    field's own English terms are supplied through `--terms-file`.
  - **R2** — adjacent fields and their professional vocabulary, from two
    independent sources: co-word / pseudo-relevance-feedback mining over
    author keywords, and the OpenAlex controlled subject tree
    (domain > field > subfield > topic).
  - **R3** — backward and forward citation snowballing through OpenAlex
    (Wohlin 2014, EASE'14).
  - **R4** — the whole corpus is reviewed against the goal and labelled
    `RELEVANT` / `MARGINAL` / `OFF_TOPIC` / `INVALID`.
- **The reviewer is the agent running the skill.** The script never decides
  anything: it mines candidates, writes the tables, and waits. Three decision
  points belong to the agent — the English seed terms, which round-2 queries
  to run, and which papers belong in the result.
- **Optional LLM path** (`--llm`): the same three decisions made by an
  OpenAI-compatible endpoint inside a single run, with the file workflow as
  the fallback when no key is configured.
- **`--resume`** — continue a paused run from the corpus on disk. The rounds
  already completed are not re-run, so handing the round-4 verdicts back
  costs no searches.
- **Round-2 query anchoring** — expansion queries are issued as
  `<term> AND <round-1 core token>`, with the core token chosen from a
  10–50 % band so that it actually narrows, and no anchor at all on a corpus
  below 20 records.
- Per-query precision table (`reports/round4_review_stats.md`) with the
  directly-relevant rate next to the kept rate, which is what shows which
  expansion query was worth running.

### Changed
- The relevance threshold was **removed**. A fixed coverage threshold used to
  decide what was dropped; it was wrong in both directions at once. Relevance
  is now measured (`metadata/relevance_scores.csv`) and never thresholded — the
  reviewer decides. A gate that cannot be overridden is not a gate, it is a
  bug with opinions.
- Round 2 de-duplicates the candidate terms, drops a term that already covers
  more than 35 % of the corpus, drops a term that is 60 % the same as a seed
  query, and searches a term bare when the anchor would be redundant.
- The harvest pipeline was refactored into a `Collector`, so a single-round
  run is the degenerate case of one query and every round de-duplicates against
  the others.

### Fixed
- **IEEE Xplore extraction** (re-verified live 2026-10-22): author keywords
  live on the separate route `/document/<arnumber>/keywords` and were never
  captured; the citation count returned the whole metrics toolbar
  (`"View Document 68FullText ViewsAlerts…"`) instead of the number; the
  abstract carried an `Abstract:` prefix; keyword entries were duplicated with
  a trailing comma.
- **arXiv and Web of Science query syntax**: a query that already carried
  field prefixes was wrapped a second time (`all:"all:"x" AND all:"y""`,
  `TS=(""a" AND "b"")`), silently returning nothing. WoS also stacked a
  second, contradictory `PY=` filter onto queries that already had one.
- **The browser tab lookup** searched only the first browser context and
  failed as soon as another Edge window was open; it now scans every context,
  skips pages that failed to load, and opens a tab itself when none matches
  instead of giving up.
- **A rate-limited source no longer loses the run**: HTTP 429 is reported, the
  papers collected so far are kept and saved, and the run continues. OpenAlex
  rate-limits by public IP address, so a shared budget is a real possibility.
- **BOM handling**: a terms file written by PowerShell's `Out-File -Encoding
  UTF8` put a byte-order mark on the first term.
- **De-duplication across rounds** now matches on any of DOI / ARN / PII /
  uid / URL / title, and the keyword vocabulary is folded to one case before
  mining, because IEEE's index terms arrive with inconsistent casing and
  split a term's document frequency in half.

### Known limitations
- Round 3 is **not free**: on the run above it produced 86 records of which
  7 % were directly relevant, and it amplified the drift of round 2. It is
  therefore skipped in the recommended flow.
- A CJK goal cannot be searched directly against OpenAlex or arXiv — the
  English terms have to be supplied.
- The round-4 review is linear in the corpus size; a few hundred papers is
  comfortable, a few thousand needs the pack to be read in batches over
  several turns.
- Web of Science is an index database: metadata only, no hosted PDFs.
- Publisher front-ends change; extraction may need selector updates.

## v1.0.0 — 2026-10-13

First public release.

### Added
- Multi-source harvesting: **IEEE Xplore**, **ScienceDirect (Elsevier)**,
  **Web of Science (Clarivate)**, **OpenAlex**, **arXiv**, selected with `--source`.
- Two transports behind one flag: browser-backed sources ride Edge + CDP,
  API sources are plain HTTPS with no browser and no login.
- **OpenAlex adapter** (verified live 2026-10-14): cross-publisher coverage of
  250M+ works, with DOI, venue, citation counts, keywords, rebuilt abstracts and
  direct open-access PDF links. Honours `OPENALEX_MAILTO` for the polite pool.
- **arXiv adapter** (verified live 2026-10-14): preprints via the public Atom API
  with free full text on every record (4/4 PDFs downloaded in the test run).
  Enforces arXiv's ≥3 s request spacing; `ARXIV_MIN_INTERVAL` overrides it.
- Adapter architecture (`scripts/sites/`): the pipeline is source- *and*
  transport-agnostic, a new database is one file plus one registry entry
  (`ApiSiteAdapter` for HTTP sources, `BaseSiteAdapter` for browser sources).
- Edge CDP launcher that works around Chromium ≥136 ignoring
  `--remote-debugging-port` on the default profile (dedicated profile + one-time
  login-state copy).
- Metadata extraction: title, authors, DOI, journal, year, abstract, keywords,
  citation counts, access status.
- Citation export: BibTeX + RIS, with a minimal BibTeX fallback for sources that
  do not expose one.
- PDF download for full-text sources, with retry, `%PDF-` validation, a
  browser-assisted fallback for the ScienceDirect JS challenge, and a local
  library check to skip existing files.
- Multi-dimensional de-duplication (DOI / ARN / PII / WOS uid / URL / title),
  priority tiers, `pending.csv` for failures.
- Reports: interactive `summary.html`, `statistics.json`, `search_log.txt`.
- Optional impact-factor filter via the EasyScholar extension (`--min-if`).

### Notes
- Selectors were verified live on 2026-09-30; see `references/cdp_reference.md`.
- The skill is exposed as a slash command (`/academic-db-harvester`) through
  `metadata.opencode/slash`.
- Attribution and upstream lineage: `CREDITS.md`. Licensed under MIT.

### Known limitations
- Web of Science is an index database: metadata only, no hosted PDFs.
- ACM DL and DBLP are not wired up yet (403 without a browser session / bot
  challenge) — see `CREDITS.md` §5 for the evidence; OpenAlex plus arXiv cover a
  large share of what those would return.
- Search-engine-specific sorting flags do not apply to every source; with
  `--sort cited` on an aggregate source, off-topic but highly cited papers can
  surface.
- Publisher front-ends change; extraction may need selector updates.