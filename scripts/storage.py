"""Output directory creation and file organization."""

import json
import csv
from pathlib import Path
from datetime import datetime
from typing import Any

from utils import sanitize_filename, save_json, save_csv


FIELD_ORDER = [
    "index", "title", "authors", "year", "journal", "doi", "arnumber",
    "uid", "pii", "impact_factor", "access_status", "priority", "url",
    "detail_url", "abstract", "keywords", "citation_count",
]


class OutputManager:
    """Manages the hierarchical output directory for harvested papers."""

    def __init__(self, root_dir: Path, keyword: str, years: str, source: str = "ieee"):
        self.root = root_dir
        self.keyword = keyword
        self.years = years
        self.source = source
        self._create_dirs()
        self._save_config()

    def _create_dirs(self) -> None:
        """Create all output subdirectories."""
        dirs = [
            self.metadata_dir,
            self.pdfs_dir,
            self.citations_dir,
            self.abstracts_dir,
            self.reports_dir,
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

    def _save_config(self) -> None:
        """Save run configuration."""
        save_json(self.config_path, {
            "keyword": self.keyword,
            "years": self.years,
            "source": self.source,
            "created_at": datetime.now().isoformat(),
        })

    # ---- Path properties ----

    @property
    def metadata_dir(self) -> Path:
        return self.root / "metadata"

    @property
    def pdfs_dir(self) -> Path:
        return self.root / "pdfs"

    @property
    def citations_dir(self) -> Path:
        return self.root / "citations"

    @property
    def abstracts_dir(self) -> Path:
        return self.root / "abstracts"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def config_path(self) -> Path:
        return self.root / "config.json"

    @property
    def urls_csv(self) -> Path:
        return self.root / "urls.csv"

    @property
    def pending_csv(self) -> Path:
        return self.root / "pending.csv"

    # ---- Candidate I/O ----

    def save_candidates(self, candidates: list[dict]) -> None:
        """Save all candidates to JSON and CSV."""
        save_json(self.metadata_dir / "candidates.json", candidates)
        save_csv(self.metadata_dir / "candidates.csv", candidates, FIELD_ORDER)

    def save_priority_split(self, candidates: list[dict]) -> None:
        """Split candidates by priority tier and save."""
        tiers: dict[str, list[dict]] = {"high": [], "medium": [], "low": []}
        for c in candidates:
            tier = c.get("priority", "low")
            tiers.setdefault(tier, []).append(c)
        for tier, items in tiers.items():
            if items:
                save_json(self.metadata_dir / f"{tier}_priority.json", items)
                save_csv(self.metadata_dir / f"{tier}_priority.csv", items, FIELD_ORDER)

    def save_references_bib(self, bib_entries: list[str]) -> None:
        """Write all BibTeX entries to a single file."""
        path = self.metadata_dir / "references.bib"
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(bib_entries))

    # ---- Per-paper I/O ----

    def save_paper_metadata(self, index: int, paper: dict) -> None:
        """Save single paper metadata as JSON."""
        prefix = f"{index:03d}"
        fn = f"{prefix}_{sanitize_filename(paper.get('title', 'untitled'), 60)}.json"
        save_json(self.metadata_dir / fn, paper)

    def save_citation(self, index: int, paper: dict, bib: str, ris: str) -> None:
        """Save BibTeX and RIS citation files for a paper."""
        prefix = f"{index:03d}"
        base = sanitize_filename(paper.get("title", "untitled"), 50)
        if bib:
            (self.citations_dir / f"{prefix}_{base}_citation.bib").write_text(bib, encoding="utf-8")
        if ris:
            (self.citations_dir / f"{prefix}_{base}_citation.ris").write_text(ris, encoding="utf-8")

    def save_abstract(self, index: int, paper: dict) -> None:
        """Save abstract as a text file."""
        abstract = paper.get("abstract", "")
        if not abstract:
            return
        prefix = f"{index:03d}"
        base = sanitize_filename(paper.get("title", "untitled"), 50)
        (self.abstracts_dir / f"{prefix}_{base}_abstract.txt").write_text(
            f"{paper.get('title', '')}\n\n{abstract}", encoding="utf-8"
        )

    # ---- Bulk exports ----

    def save_all_abstracts(self, papers: list[dict]) -> None:
        """Concatenate all abstracts into one file."""
        lines: list[str] = []
        for i, p in enumerate(papers, 1):
            abstract = p.get("abstract", "")
            if abstract:
                lines.append(f"=== [{i}] {p.get('title', '')} ===\n{abstract}\n")
        (self.abstracts_dir / "all_abstracts.txt").write_text("\n".join(lines), encoding="utf-8")

    def save_all_references_bib(self, bib_entries: list[str]) -> None:
        """Write merged BibTeX."""
        (self.citations_dir / "all_references.bib").write_text("\n".join(bib_entries), encoding="utf-8")

    def save_urls_csv(self, papers: list[dict]) -> None:
        """Save URL index."""
        rows = [{"index": p.get("index", i + 1), "title": p.get("title", ""),
                  "url": p.get("url", ""), "doi": p.get("doi", "")}
                 for i, p in enumerate(papers)]
        save_csv(self.urls_csv, rows, ["index", "title", "url", "doi"])

    def save_pending_csv(self, pending: list[dict]) -> None:
        """Save pending/failed items."""
        fieldnames = ["index", "title", "reason", "url"]
        save_csv(self.pending_csv, pending, fieldnames)
