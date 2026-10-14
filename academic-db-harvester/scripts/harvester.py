#!/usr/bin/env python3
"""
Multi-source Academic Harvester - Main Entry Point

Connects to an already-open Edge browser via CDP, searches an academic
database (IEEE Xplore / ScienceDirect / Web of Science), extracts paper
metadata, optionally downloads PDFs, and generates structured output.

Usage:
    python harvester.py --source sciencedirect --keyword "cyber range" --years 2024-2026 --count 30 --download-pdf
    python harvester.py --source wos --keyword "cyber range" --years 2024-2026 --count 30
    python harvester.py --keyword "lithium battery" --years 2022-2025 --count 30 --download-pdf
"""

import sys
import time
import argparse
from pathlib import Path
from datetime import datetime

# Ensure scripts/ is on path
sys.path.insert(0, str(Path(__file__).parent))

from cdp_detector import detect_browser, get_browser_ws_url
from sites import get_adapter, list_sources
from pdf_downloader import PDFDownloader
from storage import OutputManager
from reporter import generate_statistics, generate_html_report, generate_search_log
from utils import setup_logger, generate_output_dir, sanitize_filename, save_json, build_bibtex

logger = setup_logger("harvester")


def classify_priority(paper: dict, idx: int) -> str:
    """Classify a paper into high/medium/low priority based on available signals."""
    access = paper.get("access_status", "").lower()
    year_str = paper.get("year", "")
    citation_count = paper.get("citation_count", "0")

    score = 0

    # Open Access papers are preferred. Publishers label this differently:
    # IEEE/Elsevier say "Open Access", OpenAlex says gold/green/hybrid/diamond.
    if "open" in access or any(t in access for t in ("gold", "green", "hybrid", "diamond")):
        score += 3

    # Newer papers preferred
    try:
        year = int(year_str)
        current_year = datetime.now().year
        if year >= current_year - 1:
            score += 2
        elif year >= current_year - 3:
            score += 1
    except (ValueError, TypeError):
        pass

    # Highly cited papers
    try:
        cites = int(citation_count)
        if cites >= 50:
            score += 3
        elif cites >= 10:
            score += 1
    except (ValueError, TypeError):
        pass

    if score >= 5:
        return "high"
    elif score >= 3:
        return "medium"
    return "low"


def main():
    parser = argparse.ArgumentParser(
        description="Multi-source Academic Harvester - literature automation via Edge CDP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python harvester.py --source ieee --keyword "solid electrolyte" --count 20
  python harvester.py --source sciencedirect --keyword "cyber range" --years 2024-2026 --count 30 --download-pdf
  python harvester.py --source wos --keyword "cyber range" --years 2024-2026 --count 100

Sources:
  ieee           IEEE Xplore (browser + login, full text + PDF)
  sciencedirect  Elsevier ScienceDirect (browser + login, full text + PDF)
  wos            Web of Science (browser + login, metadata/citations only)
  openalex       OpenAlex open graph (HTTP API, no browser; OA PDFs where available)

Prerequisites:
  Browser-backed sources (ieee / sciencedirect / wos):
  1. Start Edge with CDP via the bundled launcher:
       powershell -ExecutionPolicy Bypass -File scripts/start_edge_cdp.ps1
  2. Open and sign in to the target site in that Edge window (first run only)
  3. pip install playwright && playwright install chromium

  API sources (openalex): nothing to install or sign in to.
        """,
    )
    parser.add_argument("--source", type=str, default="ieee", choices=list_sources(),
                        help="Data source (default: ieee)")
    parser.add_argument("--keyword", "-k", type=str, required=True,
                        help="Search keyword(s) (required)")
    parser.add_argument("--years", "-y", type=str, default="2020-2025",
                        help="Year range, e.g. 2020-2025 (default: 2020-2025)")
    parser.add_argument("--count", "-c", type=int, default=50,
                        help="Maximum number of papers to collect (default: 50)")
    parser.add_argument("--sort", "-s", type=str, default="relevance",
                        choices=["relevance", "newest", "oldest", "cited"],
                        help="Sort order (default: relevance)")
    parser.add_argument("--download-pdf", "-d", action="store_true", default=False,
                        help="Download PDF full-text for each paper")
    parser.add_argument("--min-if", type=float, default=0.0,
                        help="Minimum impact factor filter (requires EasyScholar extension)")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Output directory (auto-generated if not set)")
    parser.add_argument("--cdp-port", "-p", type=int, default=9222,
                        help="Edge remote debugging port (default: 9222)")
    args = parser.parse_args()

    # ── Step 1: Resolve site adapter ──
    adapter_cls = get_adapter(args.source)
    adapter_name = getattr(adapter_cls, "display_name", args.source)
    login_required = getattr(adapter_cls, "login_required", True)
    needs_browser = getattr(adapter_cls, "requires_browser", True)
    logger.info("=" * 60)
    logger.info("Multi-source Academic Harvester")
    logger.info("Source: %s (%s)", adapter_name, args.source)
    logger.info("Transport: %s", "Edge CDP (browser)" if needs_browser else "HTTPS API")
    logger.info("=" * 60)

    browser_ws_url = None
    if needs_browser:
        if detect_browser(args.cdp_port) is None:
            logger.error(
                "Cannot connect to Edge. Start the debug browser with:\n"
                "  powershell -ExecutionPolicy Bypass -File scripts/start_edge_cdp.ps1\n"
                "  (or manually: msedge.exe --remote-debugging-port=%d "
                "--remote-allow-origins=* "
                "--user-data-dir=\"%%USERPROFILE%%\\edge-cdp\\profile\")", args.cdp_port
            )
            sys.exit(1)

        browser_ws_url = get_browser_ws_url(args.cdp_port)
        if not browser_ws_url:
            logger.error("Could not get browser WebSocket URL.")
            sys.exit(1)

    # ── Step 2: Create output directory ──
    dir_keyword = args.keyword if args.source == "ieee" else f"{args.keyword}_{args.source}"
    output_dir = generate_output_dir(args.output, dir_keyword)
    logger.info("Output directory: %s", output_dir)

    # Set up file logger in output directory
    log_file = output_dir / "reports" / "search_log.txt"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fh = __import__("logging").FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(logger.handlers[0].formatter)
    logger.addHandler(fh)

    output = OutputManager(output_dir, dir_keyword, args.years, source=args.source)

    # ── Step 3: Connect to the source ──
    # Browser-backed sources ride on Playwright CDP; API sources need neither
    # Playwright nor a debug browser, so the whole session is skipped for them.
    pw_cm = None
    if needs_browser:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error(
                "Playwright is not installed. Run: pip install playwright && playwright install chromium"
            )
            sys.exit(1)
        pw_cm = sync_playwright()
    else:
        from contextlib import nullcontext
        pw_cm = nullcontext(None)

    with pw_cm as pw:
        context = None
        if needs_browser:
            logger.info("Connecting to Edge via CDP...")
            browser_obj = pw.chromium.connect_over_cdp(browser_ws_url)
            context = browser_obj.contexts[0]

            # Find an existing tab of the target site among open tabs
            page = None
            for p in context.pages:
                if any(p.url.startswith(h) for h in adapter_cls.hosts):
                    page = p
                    logger.info("Using existing %s tab: %s", adapter_name, p.url[:90])
                    break
            if page is None:
                logger.error(
                    "No %s tab found among open tabs. Open %s in the debug browser and sign in first.",
                    adapter_name, adapter_cls.hosts[0],
                )
                sys.exit(1)

            auto = adapter_cls(page, browser_ws_url)
        else:
            auto = adapter_cls(None, None)
            logger.info("Using HTTP API transport (no browser, no login required)")

        # ── Step 4: Verify login (hard check only when the source requires it) ──
        try:
            logged_in = auto.check_login_status()
        except NotImplementedError:
            logged_in = True
        if login_required and not logged_in:
            logger.error(
                "Not signed in to %s. Please sign in in the debug browser.",
                adapter_name,
            )
            sys.exit(1)
        if not logged_in and not login_required:
            logger.warning(
                "Login status not confirmed for %s - continuing (metadata-only mode).",
                adapter_name,
            )

        # ── Step 5: Execute search ──
        logger.info("Searching: '%s' (%s) [max %d papers]",
                    args.keyword, args.years, args.count)
        auto.navigate_to_search(args.keyword, args.years, args.sort)

        # ── Step 6: Collect results with pagination ──
        all_papers: list[dict] = []
        seen_papers: set[str] = set()
        page_num = 1

        while len(all_papers) < args.count:
            logger.info("--- Page %d ---", page_num)
            papers = auto.extract_result_list()
            if not papers:
                logger.warning("No results found on page %d", page_num)
                break

            added = 0
            for paper in papers:
                if len(all_papers) >= args.count:
                    break
                dedupe_key = (paper.get("arnumber") or paper.get("doi")
                              or paper.get("pii") or paper.get("uid")
                              or paper.get("url") or paper.get("title", "").lower())
                if dedupe_key in seen_papers:
                    continue
                seen_papers.add(dedupe_key)
                paper["index"] = len(all_papers) + 1
                all_papers.append(paper)
                added += 1

            logger.info("Collected %d/%d papers so far", len(all_papers), args.count)

            if len(all_papers) >= args.count:
                break
            if not auto.go_next_page():
                logger.info("No more pages available")
                break
            page_num += 1

        logger.info("Total candidates from search: %d", len(all_papers))

        if not all_papers:
            logger.error("No papers collected. Check search parameters.")
            sys.exit(1)

        # ── Step 7: Extract full metadata from detail pages ──
        logger.info("Extracting full metadata from detail pages...")
        cookies = auto.get_cookies()
        bib_entries: list[str] = []
        pending: list[dict] = []

        for i, paper in enumerate(all_papers):
            idx = paper["index"]
            url = paper.get("url", "")
            if not url:
                continue

            logger.info("[%03d/%d] Opening detail: %s", idx, len(all_papers),
                        paper.get("title", "")[:60])

            if not auto.open_detail(url):
                pending.append({"index": idx, "title": paper.get("title", ""),
                                "reason": "Failed to open detail page", "url": url})
                continue

            detail = auto.extract_detail()

            # Merge detail into paper
            for key in ("abstract", "keywords", "doi", "arxiv", "arnumber", "pii", "uid",
                        "citation_count", "access_status", "pdf_urls"):
                if key in detail and detail[key]:
                    paper[key] = detail[key]

            # Classify priority
            paper["priority"] = classify_priority(paper, idx)

            # Extract citations (fallback: generate a minimal BibTeX entry)
            bib = auto.extract_bibtex()
            ris = auto.extract_ris()
            if not bib:
                bib = build_bibtex(paper)
            if bib:
                bib_entries.append(bib)
            output.save_citation(idx, paper, bib, ris)

            # Save abstract
            output.save_abstract(idx, paper)

            # Progress save every 5 papers
            if idx % 5 == 0:
                output.save_candidates(all_papers)
                logger.info("  Progress saved (%d/%d)", idx, len(all_papers))

        # ── Step 8: Final save ──
        output.save_candidates(all_papers)
        output.save_priority_split(all_papers)
        output.save_urls_csv(all_papers)
        if bib_entries:
            output.save_references_bib(bib_entries)
            output.save_all_references_bib(bib_entries)
        output.save_all_abstracts(all_papers)

        if pending:
            output.save_pending_csv(pending)
            logger.warning("%d papers marked as pending", len(pending))

        # ── Step 9: Download PDFs (full-text sources only) ──
        download_summary = None
        if args.download_pdf and getattr(adapter_cls, "has_pdf", True):
            logger.info("Downloading PDFs...")
            downloader = PDFDownloader(output.pdfs_dir, cookies, output_dir.parent,
                                       browser_context=context)
            for paper in all_papers:
                idx = paper["index"]
                pdf_urls = paper.get("pdf_urls", [])
                pdf_url = pdf_urls[0] if pdf_urls else ""
                result = downloader.download(idx, paper, pdf_url)
                if result.get("skipped"):
                    continue
                if not result["success"] and pdf_url:
                    pending.append({
                        "index": idx,
                        "title": paper.get("title", ""),
                        "reason": result.get("error", "Unknown"),
                        "url": pdf_url,
                    })
            downloader.save_log()
            download_summary = downloader.summary()
            output.save_pending_csv(pending)
        elif args.download_pdf:
            logger.warning(
                "Source '%s' is a citation index without hosted full text - skipping PDF download.",
                adapter_name,
            )

        # ── Step 10: Generate reports ──
        logger.info("Generating reports...")
        stats = generate_statistics(all_papers, download_summary)
        save_json(output.reports_dir / "statistics.json", stats)

        generate_html_report(
            all_papers, stats, args.keyword, args.years,
            output.reports_dir / "summary.html",
        )
        generate_search_log(
            args.keyword, args.years, args.count, stats,
            download_summary, output.reports_dir / "search_log.txt",
        )

        # ── Done ──
        logger.info("=" * 60)
        logger.info("Done! Output: %s", output_dir)
        logger.info("  Papers:  %d collected", len(all_papers))
        if download_summary:
            logger.info("  PDFs:    %d downloaded, %d skipped, %d failed",
                        download_summary["success"], download_summary.get("skipped", 0), download_summary["failed"])
        logger.info("  Pending: %d items", len(pending))
        logger.info("=" * 60)


if __name__ == "__main__":
    main()
