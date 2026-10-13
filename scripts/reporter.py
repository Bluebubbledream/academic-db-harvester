"""HTML report generation and statistics."""

import sys
import json
from pathlib import Path
from datetime import datetime
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent))
from utils import save_json, setup_logger

logger = setup_logger("reporter")


def generate_statistics(papers: list[dict], download_summary: dict | None = None) -> dict:
    """Generate statistics from collected papers."""
    total = len(papers)
    if total == 0:
        return {"total": 0}

    years = [p.get("year", "") for p in papers if p.get("year")]
    journals = [p.get("journal", "Unknown")[:60] for p in papers]
    priorities = [p.get("priority", "low") for p in papers]
    access = [p.get("access_status", "Unknown") for p in papers]

    stats = {
        "total_candidates": total,
        "year_distribution": dict(Counter(years).most_common()),
        "journal_distribution": dict(Counter(journals).most_common(10)),
        "priority_distribution": dict(Counter(priorities)),
        "access_distribution": dict(Counter(access)),
    }
    if download_summary:
        stats["pdf_download"] = download_summary
    return stats


def generate_html_report(
    papers: list[dict],
    stats: dict,
    keyword: str,
    years: str,
    output_path: Path,
) -> None:
    """Generate an interactive HTML summary report."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rows_html = ""
    for p in papers:
        idx = p.get("index", "")
        title = p.get("title", "")[:100]
        authors = ", ".join(p.get("authors", [])[:3])
        year = p.get("year", "")
        journal = p.get("journal", "")[:60]
        doi = p.get("doi", "")
        priority = p.get("priority", "low")
        access = p.get("access_status", "")
        priority_color = {"high": "#dc3545", "medium": "#fd7e14", "low": "#6c757d"}.get(priority, "#6c757d")
        rows_html += f"""
        <tr>
            <td>{idx}</td>
            <td class="title-col">{title}</td>
            <td>{authors}</td>
            <td>{year}</td>
            <td class="journal-col">{journal}</td>
            <td style="color:{priority_color};font-weight:600">{priority.upper()}</td>
            <td>{access}</td>
            <td><a href="https://doi.org/{doi}" target="_blank">DOI</a></td>
        </tr>"""

    total = stats.get("total_candidates", 0)
    pdf_stats = stats.get("pdf_download", {})
    pdf_success = pdf_stats.get("success", 0)
    pdf_skipped = pdf_stats.get("skipped", 0)
    pdf_failed = pdf_stats.get("failed", 0)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Academic DB Harvester Report</title>
<style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f7fa; color: #333; }}
    .container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
    h1 {{ color: #00629b; margin-bottom: 8px; }}
    .meta {{ color: #666; font-size: 14px; margin-bottom: 24px; }}
    .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 24px; }}
    .stat-card {{ background: #fff; border-radius: 8px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); text-align: center; }}
    .stat-card .num {{ font-size: 32px; font-weight: 700; color: #00629b; }}
    .stat-card .label {{ font-size: 13px; color: #666; margin-top: 4px; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
    th {{ background: #00629b; color: #fff; padding: 12px 10px; text-align: left; font-size: 13px; position: sticky; top: 0; }}
    td {{ padding: 10px; border-bottom: 1px solid #eee; font-size: 13px; }}
    tr:hover {{ background: #f0f7ff; }}
    .title-col {{ max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .journal-col {{ max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .filter-bar {{ margin-bottom: 12px; }}
    .filter-bar input {{ padding: 8px 12px; border: 1px solid #ddd; border-radius: 4px; width: 300px; font-size: 14px; }}
</style>
</head>
<body>
<div class="container">
    <h1>Academic DB Harvester Report</h1>
    <div class="meta">
        Keyword: <strong>{keyword}</strong> &middot;
        Year range: <strong>{years}</strong> &middot;
        Generated: <strong>{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</strong>
    </div>

    <div class="stats">
        <div class="stat-card"><div class="num">{total}</div><div class="label">Total Candidates</div></div>
        <div class="stat-card"><div class="num">{stats.get("priority_distribution", {}).get("high", 0)}</div><div class="label">High Priority</div></div>
        <div class="stat-card"><div class="num">{pdf_success}</div><div class="label">PDFs Downloaded</div></div>
        <div class="stat-card"><div class="num">{pdf_skipped}</div><div class="label">PDFs Skipped</div></div>
        <div class="stat-card"><div class="num">{pdf_failed}</div><div class="label">PDFs Failed</div></div>
    </div>

    <div class="filter-bar">
        <input type="text" id="searchInput" placeholder="Filter papers by title, author, journal..." onkeyup="filterTable()">
    </div>

    <div style="overflow-x:auto;">
    <table id="paperTable">
        <thead>
            <tr>
                <th>#</th>
                <th>Title</th>
                <th>Authors</th>
                <th>Year</th>
                <th>Journal</th>
                <th>Priority</th>
                <th>Access</th>
                <th>DOI</th>
            </tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>
    </div>
</div>

<script>
function filterTable() {{
    const input = document.getElementById('searchInput');
    const filter = input.value.toLowerCase();
    const rows = document.querySelectorAll('#paperTable tbody tr');
    rows.forEach(row => {{
        const text = row.textContent.toLowerCase();
        row.style.display = text.includes(filter) ? '' : 'none';
    }});
}}
</script>
</body>
</html>"""

    output_path.write_text(html, encoding="utf-8")
    logger.info("HTML report saved to %s", output_path)


def generate_search_log(
    keyword: str, years: str, count: int, stats: dict,
    download_summary: dict | None, output_path: Path,
) -> None:
    """Generate a plain-text search log."""
    lines = [
        f"Academic DB Harvester - Search Log",
        f"{'=' * 50}",
        f"Keyword:      {keyword}",
        f"Year Range:   {years}",
        f"Target Count: {count}",
        f"Completed at: {datetime.now().isoformat()}",
        f"",
        f"Results:",
        f"  Total candidates: {stats.get('total_candidates', 0)}",
        f"  High priority:    {stats.get('priority_distribution', {}).get('high', 0)}",
        f"  Medium priority:  {stats.get('priority_distribution', {}).get('medium', 0)}",
        f"  Low priority:     {stats.get('priority_distribution', {}).get('low', 0)}",
    ]
    if download_summary:
        lines += [
            f"",
            f"PDF Downloads:",
            f"  Total:   {download_summary.get('total', 0)}",
            f"  Success: {download_summary.get('success', 0)}",
            f"  Skipped: {download_summary.get('skipped', 0)}",
            f"  Failed:  {download_summary.get('failed', 0)}",
            f"  Size:    {download_summary.get('total_size_kb', 0):.1f} KB",
        ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Search log saved to %s", output_path)

