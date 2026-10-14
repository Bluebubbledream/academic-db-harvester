# Release notes

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