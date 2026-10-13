"""Shared utilities for the Academic DB Harvester."""

import re
import time
import json
import logging
import functools
from pathlib import Path
from datetime import datetime
from typing import Callable, Any


def setup_logger(name: str, log_file: Path | None = None) -> logging.Logger:
    """Create a logger that writes to console and optionally a file."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger


def sanitize_filename(name: str, max_len: int = 80) -> str:
    """Sanitize a string for use as a filename."""
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", "_", name)
    name = name.strip("_.")
    if len(name) > max_len:
        name = name[:max_len].rsplit("_", 1)[0]
    return name


def generate_output_dir(base: str | None, keyword: str) -> Path:
    """Generate a timestamped output directory."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_kw = sanitize_filename(keyword, max_len=40)
    root = Path(base) if base else Path.home() / "Desktop" / "论文"
    return root / f"{ts}_{safe_kw}"


def retry(
    max_attempts: int = 3,
    base_delay: float = 2.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
):
    """Decorator: retry with exponential backoff."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            delay = base_delay
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt < max_attempts:
                        time.sleep(delay)
                        delay *= backoff
            raise last_exc  # type: ignore[misc]
        return wrapper
    return decorator


def validate_pdf(filepath: Path, min_size_kb: int = 50) -> bool:
    """Check if a file is a valid PDF by header and size."""
    if not filepath.exists():
        return False
    if filepath.stat().st_size < min_size_kb * 1024:
        return False
    with open(filepath, "rb") as f:
        header = f.read(5)
    return header == b"%PDF-"


def load_json(path: Path) -> dict | list:
    """Load a JSON file, return empty dict on failure."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def build_bibtex(paper: dict) -> str:
    """Build a minimal BibTeX entry from harvested metadata (fallback only)."""
    title = str(paper.get("title", "")).strip()
    authors = paper.get("authors") or []
    if isinstance(authors, list):
        author = " and ".join(str(a) for a in authors if a)
    else:
        author = str(authors)
    journal = str(paper.get("journal", "")).strip()
    year = str(paper.get("year", "")).strip()
    doi = str(paper.get("doi", "")).strip()
    url = str(paper.get("url", "")).strip()
    key_seed = doi or sanitize_filename(title, 30) or "reference"
    key = re.sub(r"[^A-Za-z0-9_:.-]", "_", key_seed)
    fields = [f"  title = {{{title}}}"]
    if author:
        fields.append(f"  author = {{{author}}}")
    if journal:
        fields.append(f"  journal = {{{journal}}}")
    if year:
        fields.append(f"  year = {{{year}}}")
    if doi:
        fields.append(f"  doi = {{{doi}}}")
    if url:
        fields.append(f"  url = {{{url}}}")
    return "@article{%s,\n%s\n}" % (key, ",\n".join(fields))


def save_json(path: Path, data: dict | list) -> None:
    """Save data as JSON with indentation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def save_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    """Save a list of dicts as CSV."""
    import csv
    if not rows:
        return
    if fieldnames is None:
        fieldnames = list(rows[0].keys())
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
