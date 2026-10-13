# Release notes

## v1.0.0 — 2026-10-13

First public release.

### Added
- Multi-source harvesting: **IEEE Xplore**, **ScienceDirect (Elsevier)**,
  **Web of Science (Clarivate)**, selected with `--source`.
- Adapter architecture (`scripts/sites/`): the pipeline is source-agnostic, a new
  database is one file plus one registry entry.
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
- Search-engine-specific sorting flags do not apply to every source.
- Publisher front-ends change; extraction may need selector updates.