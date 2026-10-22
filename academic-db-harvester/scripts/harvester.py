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

Multi-round retrieval (see query_planner.py + oa_graph.py):
    python harvester.py --source openalex --multi-round --goal "cyber range orchestration" --count 50
    python harvester.py --source ieee --multi-round --keyword "cyber range" --goal "..." --count 30
"""

import json
import re
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
from storage import OutputManager, FIELD_ORDER
from reporter import generate_statistics, generate_html_report, generate_search_log
from utils import (setup_logger, generate_output_dir, sanitize_filename,
                   save_json, save_csv, build_bibtex)

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


def _round2_query(query: str, term: str, origin: str, rationale: str,
                  support="-", precision="-", lift="-", evidence=None) -> dict:
    """Normalise a round-2 expansion term into the shape the plan renderer wants."""
    return {
        "term": term,
        "terms": [term],
        "query": query,
        "origin": origin,
        "rationale": rationale,
        "support": support,
        "precision": precision,
        "lift": lift,
        "evidence": evidence or [],
    }


def load_terms_file(path: str) -> list[str]:
    """Read round-1 seed terms: one per line (# comments allowed) or a JSON list.

    This is the escape hatch for a goal written in a language the target
    index does not understand. OpenAlex is an English-language graph that
    also indexes Chinese-language literature, so a CJK goal searched verbatim
    lands in CNKI-sourced papers about water resources and power grids
    (measured: "网络靶场 大规模资源编排" returns 359 hits whose top results
    are all off-topic Chinese papers, while "cyber range" returns 409k
    whose top results are all on-topic). The agent translating the goal into
    the field's own English terms is not something a script can do, so the
    script takes the result.
    """
    p = Path(path)
    # utf-8-sig: PowerShell's `Out-File -Encoding UTF8` writes a BOM, which
    # would otherwise turn the first term into "﻿cyber range orchestration".
    text = p.read_text(encoding="utf-8-sig")
    if text.lstrip().startswith("["):
        data = json.loads(text)
        items = data if isinstance(data, list) else data.get("terms", [])
        out = []
        for item in items:
            if isinstance(item, str):
                out.append(item.strip())
            elif isinstance(item, dict) and item.get("term"):
                out.append(str(item["term"]).strip())
        return [t for t in out if t]
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


class Collector:
    """Accumulates papers across one query or across the whole multi-round loop.

    A single-round run is simply the degenerate case of one query. Detail
    extraction, PDF download and reporting only ever look at `self.papers`, so
    both modes behave identically and de-duplicate against each other - which
    is what makes a round-3 snowballed paper collide correctly with the same
    paper harvested in round 1 from a publisher site.
    """

    def __init__(self, args, auto, output, context):
        self.args = args
        self.auto = auto
        self.output = output
        self.context = context
        self.papers: list[dict] = []
        self.seen: set[str] = set()
        self.pending: list[dict] = []
        self.bibs: list[str] = []
        self.scored: list[dict] = []       # relevance measurements, per round
        self.download_summary = None

    # ── identity ─────────────────────────────────────────────────────

    @staticmethod
    def _keys(paper: dict) -> list[str]:
        """Every identifier a record carries; any one of them proves identity."""
        keys = []
        for field in ("doi", "arnumber", "pii", "uid", "url"):
            value = (paper.get(field) or "").strip().lower()
            if value:
                keys.append(f"{field}:{value}")
        title = (paper.get("title") or "").strip().lower()
        if title:
            keys.append(f"title:{title}")
        return keys

    def _register(self, paper: dict) -> bool:
        """Return False when this record duplicates one already collected."""
        keys = self._keys(paper) or ["empty"]
        if any(k in self.seen for k in keys):
            return False
        self.seen.update(keys)
        return True

    def _finalize(self, paper: dict, page_citation: bool = True,
                  bib: str | None = None, ris: str | None = None) -> None:
        """Priority tier + citation files for one already-complete record."""
        paper["keywords"] = fold_keywords(paper.get("keywords"))
        paper["priority"] = classify_priority(paper, paper.get("index", 0))
        if bib is None:
            bib, ris = "", ""
            if page_citation and self.auto is not None:
                # Only valid while the browser tab still sits on that record's
                # detail page, hence the flag: snowballed records have no page.
                bib = self.auto.extract_bibtex()
                ris = self.auto.extract_ris()
        if not bib:
            bib = build_bibtex(paper)
        if bib:
            self.bibs.append(bib)
        self.output.save_citation(paper.get("index", 0), paper, bib, ris or "")
        self.output.save_abstract(paper.get("index", 0), paper)

    def load_from(self, run_dir: Path) -> int:
        """Load a previous run's corpus so a run can continue instead of re-searching.

        Round 4 needs the same corpus the reviewer pack was built from, and
        re-running rounds 1-3 to get it back costs a full re-harvest and can
        return the same papers in a different order. This loads the corpus
        from disk instead.
        """
        path = Path(run_dir) / "metadata" / "candidates.json"
        try:
            papers = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("cannot resume: no corpus at %s (%s)" % (path, exc))
        if not isinstance(papers, list) or not papers:
            raise RuntimeError("cannot resume: %s holds no papers" % path)

        self.papers = papers
        for i, paper in enumerate(self.papers, 1):
            paper.setdefault("index", i)
            paper["keywords"] = fold_keywords(paper.get("keywords"))
            self.seen.update(self._keys(paper))
        rounds = sorted({p.get("round") for p in self.papers if p.get("round")})
        logger.info("Resumed %d papers from %s (rounds present: %s)",
                    len(self.papers), path, rounds or "-")
        return len(self.papers)

    # ── Steps 5-6: one search query ──────────────────────────────────

    def _api_error(self, exc: Exception, what: str) -> None:
        """Report a source failure without losing what was already collected."""
        msg = str(exc)
        if "429" in msg or "Too Many Requests" in msg:
            logger.error(
                "Rate limited by the source while %s (%s). Everything collected "
                "so far is kept and will be saved. On OpenAlex this is the "
                "shared per-IP daily budget: set OPENALEX_API_KEY for a private "
                "budget, or run again later.", what, msg[:90])
        else:
            logger.error("Source error while %s: %s", what, msg[:200])

    def search_query(self, query: str, limit: int, round_no: int = 1,
                     via: str = "seed_query", annotator=None) -> list[dict]:
        """Search one query. The annotator measures each retrieved paper
        against the round-1 topic; it never removes anything - that judgement
        belongs to the reviewer."""
        logger.info("Searching: '%s' (%s) [max %d papers]",
                    query, self.args.years, limit)
        try:
            self.auto.navigate_to_search(query, self.args.years, self.args.sort)
        except Exception as exc:
            self._api_error(exc, "searching '%s'" % query[:50])
            return []

        found: list[dict] = []
        page_num = 1
        while len(found) < limit:
            logger.info("--- Page %d ---", page_num)
            try:
                papers = self.auto.extract_result_list()
            except Exception as exc:
                self._api_error(exc, "extracting results")
                break
            if not papers:
                logger.warning("No results found on page %d", page_num)
                break

            for paper in papers:
                if len(found) >= limit:
                    break
                if annotator is not None:
                    annotator.annotate(paper)
                if not self._register(paper):
                    continue
                paper["index"] = len(self.papers) + 1
                paper["round"] = round_no
                paper["via"] = via
                paper["query"] = query
                self.papers.append(paper)
                found.append(paper)

            logger.info("Collected %d/%d papers this query (%d in corpus)",
                        len(found), limit, len(self.papers))

            if len(found) >= limit:
                break
            try:
                more = self.auto.go_next_page()
            except Exception as exc:
                self._api_error(exc, "fetching the next page")
                break
            if not more:
                logger.info("No more pages available")
                break
            page_num += 1
        return found

    # ── Step 7: detail pages ─────────────────────────────────────────

    def extract_details(self, new_papers: list[dict]) -> None:
        if not new_papers:
            return
        logger.info("Extracting full metadata from %d detail pages...", len(new_papers))
        for paper in new_papers:
            idx = paper["index"]
            url = paper.get("url", "")
            if not url:
                continue

            logger.info("[%03d/%d] Opening detail: %s", idx, len(self.papers),
                        paper.get("title", "")[:60])

            try:
                if not self.auto.open_detail(url):
                    self.pending.append({"index": idx, "title": paper.get("title", ""),
                                         "reason": "Failed to open detail page", "url": url})
                    continue
                detail = self.auto.extract_detail()
            except Exception as exc:
                self._api_error(exc, "opening detail %03d" % idx)
                self.pending.append({"index": idx, "title": paper.get("title", ""),
                                     "reason": "Source error: %s" % str(exc)[:120],
                                     "url": url})
                continue
            for key in ("abstract", "keywords", "doi", "arxiv", "arnumber", "pii", "uid",
                        "citation_count", "access_status", "pdf_urls"):
                if detail.get(key):
                    paper[key] = detail[key]

            # The citation export has to happen while the tab is still on the
            # document page, so it is taken *before* the keyword fetch below
            # (IEEE renders author keywords on a separate route).
            bib = ris = ""
            if self.auto is not None:
                try:
                    bib = self.auto.extract_bibtex()
                    ris = self.auto.extract_ris()
                except Exception as exc:
                    logger.debug("citation export failed for %03d: %s", idx, exc)

            if not paper.get("keywords") and hasattr(self.auto, "extract_keywords"):
                try:
                    paper["keywords"] = self.auto.extract_keywords()
                except Exception as exc:
                    logger.debug("keyword fetch failed for %03d: %s", idx, exc)

            self._finalize(paper, bib=bib, ris=ris)

            if idx % 5 == 0:
                self.save_all()
                logger.info("  Progress saved (%d/%d)", idx, len(self.papers))

    # ── records that arrive complete (round 3 snowball) ──────────────

    def append_records(self, records: list[dict], round_no: int, via: str,
                       annotator=None) -> int:
        """Commit records that arrive already complete (round 3 snowball)."""
        added = 0
        for rec in records:
            if annotator is not None:
                annotator.annotate(rec)
            if not self._register(rec):
                continue
            rec["index"] = len(self.papers) + 1
            rec["round"] = round_no
            rec["via"] = via
            rec.setdefault("query", "")
            self.papers.append(rec)
            self._finalize(rec, page_citation=False)
            added += 1
        logger.info("Round %d · %s: +%d new records (%d in corpus)",
                    round_no, via, added, len(self.papers))
        return added

    # ── Step 9 ───────────────────────────────────────────────────────

    def download_pdfs(self) -> None:
        logger.info("Downloading PDFs...")
        cookies = self.auto.get_cookies() if self.auto else []
        downloader = PDFDownloader(self.output.pdfs_dir, cookies,
                                   self.output.root.parent,
                                   browser_context=self.context)
        for paper in self.papers:
            pdf_urls = paper.get("pdf_urls") or []
            pdf_url = pdf_urls[0] if pdf_urls else ""
            result = downloader.download(paper["index"], paper, pdf_url)
            if result.get("skipped"):
                continue
            if not result["success"] and pdf_url:
                self.pending.append({
                    "index": paper["index"],
                    "title": paper.get("title", ""),
                    "reason": result.get("error", "Unknown"),
                    "url": pdf_url,
                })
        downloader.save_log()
        self.download_summary = downloader.summary()
        self.output.save_pending_csv(self.pending)

    # ── Step 8 ───────────────────────────────────────────────────────

    def record_scores(self, annotator, label: str) -> None:
        """Keep the relevance measurements, so a judgement can be audited
        against the number that informed it."""
        if annotator is None:
            return
        for row in annotator.rows():
            row = dict(row)
            row["round"] = label
            self.scored.append(row)

    def save_all(self) -> None:
        self.output.save_candidates(self.papers)
        self.output.save_priority_split(self.papers)
        self.output.save_urls_csv(self.papers)
        if self.bibs:
            self.output.save_references_bib(self.bibs)
            self.output.save_all_references_bib(self.bibs)
        self.output.save_all_abstracts(self.papers)
        if self.pending:
            self.output.save_pending_csv(self.pending)
        save_csv(
            self.output.metadata_dir / "provenance.csv",
            [{"index": p.get("index", 0), "round": p.get("round", ""),
              "via": p.get("via", ""), "query": p.get("query", ""),
              "title": p.get("title", "")[:80], "doi": p.get("doi", "")}
             for p in self.papers],
            ["index", "round", "via", "query", "title", "doi"],
        )
        # Always written, even when nothing was measured: an absent file and
        # an empty one mean very different things to an auditor.
        scores_path = self.output.metadata_dir / "relevance_scores.csv"
        if self.scored:
            save_csv(scores_path, self.scored,
                     ["round", "label", "cov", "title", "doi", "journal", "missing"])
        else:
            scores_path.write_text(
                "round,label,cov,title,doi,journal,missing\n", encoding="utf-8")

    def snapshot(self, round_no: int) -> None:
        subset = [p for p in self.papers if p.get("round") == round_no]
        save_json(self.output.metadata_dir / f"round{round_no}_candidates.json", subset)
        save_csv(self.output.metadata_dir / f"round{round_no}_candidates.csv",
                 subset, FIELD_ORDER)
        logger.info("Round %d snapshot: %d records", round_no, len(subset))


def run_multi_round(args, output, coll) -> None:
    """Drive the three-round retrieval loop described in SKILL.md.

    R1  seed queries derived from the research goal        (always runs)
    R2  adjacent fields + professional vocabulary          (--rounds, on by default)
    R3  backward + forward citation snowball               (--rounds, on by default)
    """
    import query_planner as qp
    import oa_graph
    import term_gate

    wanted = {int(x) for x in re.split(r"[,\s]+", args.rounds or "")
              if x.strip().isdigit()}
    plan = {
        "goal": args.goal or args.keyword,
        "keyword": args.keyword,
        "source": args.source,
        "years": args.years,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "rounds": [],
    }

    logger.info("=" * 60)
    logger.info("Multi-round retrieval: round 1 always, expansion rounds %s",
                sorted(wanted) or "none")
    logger.info("=" * 60)

    import llm as llm_mod

    llm = None
    if args.llm:
        llm = llm_mod.LLM.from_env(args.llm_base_url, args.llm_api_key, args.llm_model)
        if llm.available:
            logger.info("LLM orchestration enabled: %s via %s", llm.model, llm.base_url)
        else:
            logger.warning(
                "--llm was given but the endpoint is not configured (need "
                "OPENAI_BASE_URL, OPENAI_API_KEY and OPENAI_MODEL). Falling back "
                "to the file-based workflow.")
            llm = None

    # ── Round 1: goal -> seed queries -> harvest ─────────────────────
    if args.terms_file:
        terms = load_terms_file(args.terms_file)
        if not terms:
            logger.error("--terms-file %s holds no terms.", args.terms_file)
            sys.exit(1)
        seed_queries = [{"query": t, "terms": [t], "origin": "terms-file",
                         "rationale": "调用方给定的检索词（目标语言与索引语言不一致时的桥梁）"}
                        for t in terms]
    elif llm is not None:
        try:
            planned = llm_mod.plan_terms(llm, args.goal or args.keyword,
                                          args.keyword, n=args.llm_terms)
            seed_queries = [{"query": t["term"], "terms": [t["term"]], "origin": "llm",
                             "rationale": t.get("why", "")} for t in planned]
            logger.info("[Round 1] the LLM proposed %d seed term(s)", len(seed_queries))
        except Exception as exc:
            logger.warning("The LLM could not plan the seed terms (%s); using the "
                           "n-gram decomposition instead.", exc)
            seed_queries = qp.seed_queries(args.goal, args.keyword, limit=args.per_round)
    else:
        seed_queries = qp.seed_queries(args.goal, args.keyword, limit=args.per_round)
    if not seed_queries:
        logger.error("Round 1: no seed query could be derived from --goal/--keyword.")
        sys.exit(1)

    # Rounds already present in a resumed corpus are not re-run.
    present = {p.get("round") for p in coll.papers if p.get("round")}
    if present:
        logger.info("Resumed corpus already contains round(s) %s", sorted(present))

    if 1 not in present:
        # A CJK goal searched verbatim against an English-language index lands
        # in Chinese-language literature about unrelated fields. Measured: the
        # query "网络靶场 大规模资源编排" returns 359 OpenAlex hits whose top
        # results are all Chinese papers on water resources and power grids,
        # while "cyber range" returns 409k whose top results are all on-topic.
        # Drop the CJK seed rather than let it poison the corpus.
        if args.source in ("openalex", "arxiv"):
            kept_queries = []
            for q in seed_queries:
                if qp.has_cjk(q["query"]):
                    logger.warning(
                        "[Round 1] dropped CJK seed query '%s': the %s index is "
                        "English-language, and a verbatim CJK search returns "
                        "Chinese-language papers from unrelated fields. Supply the "
                        "field's English terms with --terms-file.",
                        q["query"], args.source)
                    continue
                kept_queries.append(q)
            seed_queries = kept_queries
            if not seed_queries:
                logger.error(
                    "Round 1: every seed query was CJK and the %s index is "
                    "English-language. Write the field's English terms into a file "
                    "(one per line) and pass --terms-file.", args.source)
                sys.exit(1)

        logger.info("[Round 1] seed query formulation -> %d query(ies)", len(seed_queries))
        new_round = []
        for q in seed_queries:
            logger.info("  R1 -> %s", q["query"])
            new_round += coll.search_query(q["query"], args.count, round_no=1,
                                           via="seed_query")
        if not coll.papers:
            logger.error("Round 1 returned no papers. Check the goal/keyword and the source login.")
            sys.exit(1)
        coll.extract_details(new_round)
        plan["rounds"].append({
            "round": 1,
            "method": "seed query formulation",
            "method_zh": "种子检索式生成",
            "note": ("目标切分为 2-3 元 n-gram 作为检索式；中文目标原样检索不做拉丁切分。\n"
                     "Goal decomposed into 2-3-gram search strings; CJK goals are used verbatim."),
            "queries": seed_queries,
            "harvested": len(new_round),
        })
        coll.save_all()
        coll.snapshot(1)

    # Everything from here on is measured against the round-1 topic.
    anchors = [p for p in coll.papers if p.get("round") == 1] or list(coll.papers)

    # ── Round 2: adjacent fields + professional vocabulary ───────────
    if 2 in wanted and 2 not in present:
        seeds = [q["query"] for q in seed_queries]
        budget = args.per_round

        # Fold the keyword vocabulary before mining, so "Large language
        # models" and "large language models" count as one term.
        folded = normalise_keywords(coll.papers)
        if folded:
            logger.info("[Round 2] folded the keyword vocabulary of %d records", folded)

        # Topical fit alone cannot stop an ambiguous term, so every expansion
        # query is anchored on a round-1 core token: the adjacent term is
        # searched as an *intersection* with the topic, never bare.
        core = term_gate.core_anchor(anchors, seeds,
                                     min_corpus=args.min_corpus_for_anchor)
        if core:
            logger.info("[Round 2] every expansion query anchored on: %s",
                        " AND ".join(core))
        else:
            logger.info("[Round 2] corpus too small (%d records) for a distinctive "
                        "anchor token - expansion queries run on the multi-word "
                        "term alone.", len(anchors))

        def r2_query(term, origin, rationale, support="-", precision="-",
                     lift="-", evidence=None):
            # The anchor narrows a term that could mean something else in the
            # wider corpus. When the term already contains the anchor, the
            # conjunction adds nothing - so the term is searched bare rather
            # than dropped. ("llm agents" AND "agent" == "llm agents".)
            terms = [term]
            if core and not any(c.lower() in term.lower() for c in core):
                terms = terms + core
            return _round2_query(qp.format_query(args.source, terms),
                                 term, origin, rationale, support, precision,
                                 lift, evidence)

        # Ask for a wide candidate pool: the mining ranks by document
        # frequency, so the specific multi-word terms ("directed acyclic
        # graph", "model selection") sit below the generic ones and would be
        # cut off before the coverage filter had a chance to remove the
        # generic ones.
        co_terms = qp.mine_adjacent_terms(coll.papers, seeds,
                                          limit=max(budget * 8, 24),
                                          min_support=args.min_support)

        # Two kinds of mined term are useless as an expansion:
        #   * one that already covers most of the corpus - searching it
        #     retrieves the corpus again ("large language models" in a corpus
        #     where 30 of 36 papers carry it);
        #   * one that contains the anchor token - the anchored query would be
        #     redundant ("large language models" AND "language").
        n_papers = len(coll.papers) or 1
        kept_terms, dropped_terms, notes = [], [], []
        seed_tokens = [set(qp.normalize_term(s).split()) for s in seeds]
        for t in co_terms:
            share = t.get("df", 0) / n_papers
            if share > args.max_term_coverage:
                dropped_terms.append((t["term"],
                                      "covers %.0f%% of the corpus" % (share * 100)))
                continue
            if any(c.lower() in t["term"].lower() for c in core):
                # Not a reason to drop the term - the anchor is simply
                # redundant for it, so it will be searched bare.
                notes.append((t["term"], "contains the anchor token - searched bare"))
                kept_terms.append(t)
                if len(kept_terms) >= budget:
                    break
                continue
            # A term that is nearly a seed query retrieves the same corpus
            # again - "llm orchestration" against the seed
            # "LLM agent orchestration" has a Jaccard overlap of 0.67 and
            # expands nothing.
            tt = set(qp.normalize_term(t["term"]).split())
            best = 0.0
            for st in seed_tokens:
                if tt and st:
                    best = max(best, len(tt & st) / len(tt | st))
            if best >= args.max_seed_overlap:
                dropped_terms.append((t["term"],
                                      "is %.0f%% the same as a seed query" % (best * 100)))
                continue
            kept_terms.append(t)
            if len(kept_terms) >= budget:
                break
        for term, why in dropped_terms:
            logger.info("  drop  %-42s %s", term, why)
        for term, why in notes:
            logger.info("  note  %-42s %s", term, why)

        r2_queries = [
            r2_query(t["term"], "co-word",
                     "共词/PRF 挖掘 support=%s lift=%s" % (t["support"], t["lift"]),
                     t["support"], t["precision"], t["lift"], t.get("evidence") or [])
            for t in kept_terms
        ]

        taxonomy = None
        if not args.no_taxonomy:
            try:
                taxonomy = oa_graph.adjacent_terms(
                    coll.papers, seeds=seeds,
                    budget=max(0, budget - len(r2_queries)),
                    min_support=args.min_support)
            except Exception as exc:
                logger.warning("[Round 2] OpenAlex subject taxonomy unavailable (%s); "
                               "continuing with co-word terms only.", exc)

        if taxonomy:
            for t in taxonomy["terms"]:
                r2_queries.append(r2_query(t["term"], "taxonomy", t["context"],
                                           t["support"], "-", "-", [t["context"]]))

        # Co-word and taxonomy routinely surface the same term; searching it
        # twice spends a whole query slot on a duplicate.
        seen_terms, deduped = set(), []
        for q in r2_queries:
            key = qp.normalize_term(q["term"])
            if not key or key in seen_terms:
                continue
            seen_terms.add(key)
            deduped.append(q)
        if len(deduped) != len(r2_queries):
            logger.info("[Round 2] de-duplicated %d term(s)", len(r2_queries) - len(deduped))
        r2_queries = deduped

        # ── pre-query candidate list ────────────────────────────────
        # Which expansion terms to run is a judgement call. It goes to the
        # reviewer: the LLM when --llm is on, otherwise the person editing
        # the candidate table. The score below is evidence, never a filter.
        scored = []
        if args.approve:
            r2_queries = [dict(q) for q in term_gate.load_approved(args.approve)]
            logger.info("[Round 2] executing %d reviewer-chosen term(s) from %s",
                        len(r2_queries), args.approve)
        else:
            if args.review != "off":
                scored = term_gate.score_terms([q["term"] for q in r2_queries], anchors)
                for q, s in zip(r2_queries, scored):
                    q["cov"] = s["cov"]
                    q["hits"] = s["hits"]
                    logger.info("  cand  cov=%.2f hits=%-3s %s",
                                s["cov"], s["hits"], (q["term"] or "")[:55])

            if llm is not None:
                try:
                    chosen = llm_mod.choose_terms(llm, args.goal or args.keyword,
                                                  r2_queries)
                except Exception as exc:
                    logger.warning("The LLM could not choose the expansion terms "
                                   "(%s); running all candidates.", exc)
                else:
                    logger.info("[Round 2] the LLM chose %d of %d candidate term(s): %s",
                                len(chosen), len(r2_queries), ", ".join(chosen))
                    r2_queries = [q for q in r2_queries if q["term"] in chosen]
            elif args.review == "gate":
                review_entries = []
                for q, s in zip(r2_queries, scored):
                    entry = dict(q)
                    entry.update(cov=s["cov"], hits=s["hits"], approved=True)
                    review_entries.append(entry)
                term_gate.write_review(
                    review_entries,
                    output.reports_dir / "term_review_round2.json",
                    output.reports_dir / "term_review_round2.md",
                    "第 2 轮检索式候选 / Round 2 query candidates",
                    "cov = 候选检索式里出现在第 1 轮词表中的词的比例，只供参考，不是判定。\n"
                    "cov is a measurement offered for the reviewer's decision, not a verdict.",
                    domains=(taxonomy or {}).get("domains", []))
                logger.warning(
                    "[Round 2] paused for review. Edit the JSON's `approved` flags "
                    "(false to skip a term), then re-run with --approve %s",
                    output.reports_dir / "term_review_round2.json")
                qp.write_plan(plan, output.reports_dir)
                return

        domains = (taxonomy or {}).get("domains", [])
        logger.info("[Round 2] %d query(ies) (%d co-word, %d taxonomy); %d adjacent domain(s)",
                    len(r2_queries), len(co_terms),
                    len(r2_queries) - len(co_terms), len(domains))

        # ── post-retrieval measurement ──────────────────────────────
        # Every retrieved paper is measured against the round-1 topic and
        # kept. The measurement is evidence for the reviewer, not a filter.
        annotator = None
        if args.review != "off":
            annotator = term_gate.make_annotator(anchors, label="round2")

        new_round = []
        for q in r2_queries:
            logger.info("  R2 -> %-45s [%s]", q["query"], q["origin"])
            new_round += coll.search_query(q["query"], args.count, round_no=2,
                                           via="adjacency", annotator=annotator)
        coll.extract_details(new_round)
        if annotator is not None:
            logger.info("  %s", annotator.summary())
            coll.record_scores(annotator, "round2")

        domain_note = ", ".join(
            "%s (%s, %.0f%%)" % (d["name"], d["level"], d["share"] * 100)
            for d in domains) or "无 / none"
        plan["rounds"].append({
            "round": 2,
            "method": "adjacency expansion (co-word mining + subject taxonomy)",
            "method_zh": "相邻领域与专业术语扩展",
            "note": ("共词/PRF 挖掘（作者关键词权重 3.0）+ OpenAlex 受控主题表 "
                     "domain > field > subfield > topic。\n"
                     "检测到的相邻领域 / adjacent domains detected: " + domain_note),
            "queries": r2_queries,
            "adjacent_domains": domains,
            "harvested": len(new_round),
        })
        coll.save_all()
        coll.snapshot(2)

    # ── Round 3: backward + forward citation snowball ────────────────
    if 3 in wanted and 3 not in present:
        pool = coll.papers
        if args.snowball_from == "round1":
            anchor = [p for p in coll.papers if p.get("round") == 1]
            if anchor:
                # Seeding from the whole corpus would let a round-2 term that
                # drifted drag its own reference list in behind it - and a
                # snowball amplifies that drift instead of correcting it.
                pool = anchor
                logger.info("[Round 3] seeding from round 1 only (%d on-topic papers)",
                            len(anchor))
        seed_papers = qp.select_citation_seeds(pool, limit=args.snowball_seeds)
        if not seed_papers:
            logger.warning("[Round 3] no seed paper carries a DOI - snowball skipped.")
        else:
            try:
                result = oa_graph.snowball(seed_papers,
                                           backward=args.snowball_backward,
                                           forward=args.snowball_forward)
            except Exception as exc:
                logger.error("[Round 3] citation snowball failed: %s", exc)
                result = None
            if result:
                # References are measured against the round-1 topic and kept;
                # the reviewer decides which of them belong.
                ref_annotator = None
                if args.review != "off":
                    ref_annotator = term_gate.make_annotator(anchors, label="round3")
                coll.append_records(result["backward"], 3, "backward",
                                    annotator=ref_annotator)
                coll.append_records(result["forward"], 3, "forward",
                                    annotator=ref_annotator)
                if ref_annotator is not None:
                    logger.info("  %s", ref_annotator.summary())
                    coll.record_scores(ref_annotator, "round3")
                plan["rounds"].append({
                    "round": 3,
                    "method": "citation snowballing (backward + forward)",
                    "method_zh": "引文雪球（后向引用 + 前向被引）",
                    "note": ("Wohlin (2014), Guidelines for snowballing in systematic "
                             "literature studies, EASE'14.\n"
                             "后向取种子论文的参考文献，前向取引用种子论文的工作；"
                             "按多少个种子共同引用排序后截断，再由主题闸门对照第 1 轮词表过滤。"),
                    "seeds": result["seeds"],
                    "harvested": len(result["backward"]) + len(result["forward"]),
                })
                coll.save_all()
                coll.snapshot(3)

    # ── Round 4: review the corpus against the retrieval goal ────────
    if 4 in wanted:
        import paper_review

        if args.verdicts:
            stats = paper_review.ingest(args.verdicts, coll.papers)
            paper_review.write_outputs(coll.papers, stats, output)
            coll.save_all()
            plan["rounds"].append({
                "round": 4,
                "method": "corpus review against the retrieval goal",
                "method_zh": "全量复核（人工/AI 判定是否符合检索目的）",
                "note": ("判定来源 %s。入选 = RELEVANT + MARGINAL；"
                         "INVALID 通常指 Publisher's Note、编者按、会议前言、"
                         "把期刊名当成记录这类不是论文的条目。" % stats["source"]),
                "stats": stats,
            })
            logger.info("[Round 4] review ingested: selected %d of %d",
                        sum(v["kept"] for v in stats["per_round"].values()),
                        stats["total"])
        else:
            packed = paper_review.build_pack(
                coll.papers, plan["goal"], output.reports_dir,
                batch_size=args.review_batch, limit=args.review_limit or None)
            plan["rounds"].append({
                "round": 4,
                "method": "corpus review against the retrieval goal",
                "method_zh": "全量复核（人工/AI 判定是否符合检索目的）",
                "note": "复核包已写出，等待 verdicts。", "packed": packed,
            })
            qp.write_plan(plan, output.reports_dir)
            logger.warning(
                "[Round 4] review pack written for %d of %d papers. Fill `verdict` "
                "in %s, then re-run with --verdicts %s",
                packed, len(coll.papers),
                output.reports_dir / "paper_review.json",
                output.reports_dir / "paper_review.json")
            return

    qp.write_plan(plan, output.reports_dir)
    logger.info("Query plan written: %s", output.reports_dir / "query_plan.md")


def finalize(args, output, coll, adapter_cls=None, adapter_name="") -> None:
    """Save, download PDFs, generate reports. Shared by fresh and resumed runs."""
    coll.save_all()
    if coll.pending:
        logger.warning("%d papers marked as pending", len(coll.pending))

    if args.download_pdf and (adapter_cls is None
                              or getattr(adapter_cls, "has_pdf", True)):
        coll.download_pdfs()
    elif args.download_pdf:
        logger.warning(
            "Source '%s' is a citation index without hosted full text - skipping PDF download.",
            adapter_name,
        )

    logger.info("Generating reports...")
    stats = generate_statistics(coll.papers, coll.download_summary)
    save_json(output.reports_dir / "statistics.json", stats)

    report_keyword = args.keyword.strip() or args.goal.strip()
    generate_html_report(coll.papers, stats, report_keyword, args.years,
                         output.reports_dir / "summary.html")
    generate_search_log(report_keyword, args.years, args.count, stats,
                        coll.download_summary, output.reports_dir / "search_log.txt")

    logger.info("=" * 60)
    logger.info("Done! Output: %s", output.root)
    logger.info("  Papers:  %d collected", len(coll.papers))
    if coll.download_summary:
        logger.info("  PDFs:    %d downloaded, %d skipped, %d failed",
                    coll.download_summary["success"],
                    coll.download_summary.get("skipped", 0),
                    coll.download_summary["failed"])
    logger.info("  Pending: %d items", len(coll.pending))
    if args.multi_round:
        logger.info("  Plan:    %s", output.reports_dir / "query_plan.md")
    logger.info("=" * 60)


def fold_keywords(kws: list) -> list:
    """One case, no trailing separator, no duplicates - for one record."""
    seen, out = set(), []
    for k in kws or []:
        s = " ".join(str(k).split()).strip().strip(",;").strip()
        key = s.lower()
        if not s or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def normalise_keywords(papers: list[dict]) -> int:
    """Fold the keyword vocabulary of a whole corpus to one case.

    IEEE's auto-generated index terms arrive with inconsistent casing - the
    same concept shows up as "Large language models" and "large language
    models" - which splits its document frequency in half and makes the
    co-word mining propose the same term twice. Folding the case first is
    what makes the mined terms comparable.
    """
    changed = 0
    for paper in papers:
        raw = paper.get("keywords") or []
        if not raw:
            continue
        folded = fold_keywords(raw)
        if len(folded) != len(raw):
            changed += 1
        paper["keywords"] = folded
    return changed


def _parse_rounds(text: str) -> set:
    """'2,3,4' -> {2, 3, 4}"""
    return {int(x) for x in re.split(r"[,\s]+", text or "") if x.strip().isdigit()}


def _rounds_in_corpus(run_dir: Path) -> set:
    """Which rounds the corpus on disk already contains."""
    path = Path(run_dir) / "metadata" / "candidates.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(data, list):
        return set()
    return {p.get("round") for p in data if isinstance(p, dict) and p.get("round")}


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
    parser.add_argument("--keyword", "-k", type=str, default="",
                        help="Search keyword(s) (required unless --multi-round --goal is given)")
    parser.add_argument("--multi-round", action="store_true", default=False,
                        help="Run the 3-round loop: seed queries -> adjacent terms -> citation snowball")
    parser.add_argument("--goal", "-g", type=str, default="",
                        help="Research goal in free text; round 1 decomposes it into seed queries")
    parser.add_argument("--terms-file", type=str, default="",
                        help="File of round-1 seed terms (one per line, or a JSON list); "
                             "use it when the goal is in a language the target index does not understand")
    parser.add_argument("--resume", type=str, default="",
                        help="Load the corpus from a previous run directory and continue from there")
    parser.add_argument("--rounds", type=str, default="2,3,4",
                        help="Expansion rounds to run after round 1 (default: 2,3,4)")
    parser.add_argument("--per-round", type=int, default=3,
                        help="Max queries executed per round (default: 3)")
    parser.add_argument("--min-support", type=int, default=2,
                        help="Round-2 minimum term support (default: 2)")
    parser.add_argument("--max-term-coverage", type=float, default=0.35,
                        help="Round 2: drop a mined term that already covers more than this share of the corpus. "
                             "Mined terms are ranked by frequency, so the top ones are the topic's own core "
                             "vocabulary and cannot expand it (default: 0.35)")
    parser.add_argument("--max-seed-overlap", type=float, default=0.6,
                        help="Round 2: drop a mined term whose Jaccard overlap with a seed query reaches this (default: 0.6)")
    parser.add_argument("--min-corpus-for-anchor", type=int, default=20,
                        help="Round 2: below this corpus size no anchor token is used, "
                             "because no token is distinctive enough to narrow by (default: 20)")
    parser.add_argument("--no-taxonomy", action="store_true", default=False,
                        help="Skip the OpenAlex subject-taxonomy pass in round 2")
    parser.add_argument("--snowball-seeds", type=int, default=5,
                        help="Round-3 seed papers (default: 5)")
    parser.add_argument("--snowball-from", type=str, default="round1",
                        choices=["round1", "all"],
                        help="Seed round 3 from round-1 papers only, or from the whole corpus (default: round1)")
    parser.add_argument("--review", type=str, default="annotate",
                        choices=["off", "annotate", "gate"],
                        help="Relevance measurement: off / annotate (default, measures and keeps everything) "
                             "/ gate (also pauses so the reviewer can choose the round-2 queries)")
    parser.add_argument("--approve", type=str, default="",
                        help="Operator-edited review JSON to execute instead of re-mining the terms")
    parser.add_argument("--verdicts", type=str, default="",
                        help="Round-4 verdict JSON (paper_review.json after filling `verdict`)")
    parser.add_argument("--review-limit", type=int, default=0,
                        help="Round-4: cap the review pack at N papers (0 = all)")
    parser.add_argument("--review-batch", type=int, default=30,
                        help="Round-4: papers per batch in the reading pack (default: 30)")
    parser.add_argument("--llm", action="store_true", default=False,
                        help="Let an LLM make the three judgement calls: round-1 seed terms, "
                             "round-2 term choice, round-4 per-paper verdicts")
    parser.add_argument("--llm-model", type=str, default="",
                        help="LLM model (default: $OPENAI_MODEL)")
    parser.add_argument("--llm-base-url", type=str, default="",
                        help="OpenAI-compatible endpoint (default: $OPENAI_BASE_URL)")
    parser.add_argument("--llm-api-key", type=str, default="",
                        help="LLM API key (default: $OPENAI_API_KEY)")
    parser.add_argument("--llm-batch", type=int, default=12,
                        help="Papers per LLM call in round 4 (default: 12)")
    parser.add_argument("--llm-terms", type=int, default=4,
                        help="Seed terms the LLM should produce in round 1 (default: 4)")
    parser.add_argument("--llm-max-calls", type=int, default=24,
                        help="Cost guard: maximum LLM calls per run (default: 24)")
    parser.add_argument("--snowball-backward", type=int, default=60,
                        help="Max references pulled in round 3 (default: 60)")
    parser.add_argument("--snowball-forward", type=int, default=40,
                        help="Max citing works pulled in round 3 (default: 40)")
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

    if not (args.keyword.strip() or args.goal.strip()):
        parser.error('--keyword is required (or pass --multi-round --goal "...")')

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

    # ── Step 2: Create output directory ──
    base_keyword = args.keyword.strip() or args.goal.strip() or "multi_round"
    dir_keyword = base_keyword if args.source == "ieee" else f"{base_keyword}_{args.source}"
    if args.resume:
        output_dir = Path(args.resume)
    else:
        output_dir = generate_output_dir(args.output, dir_keyword)
    logger.info("Output directory: %s", output_dir)

    # Set up file logger in output directory
    log_file = output_dir / "reports" / "search_log.txt"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fh = __import__("logging").FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(logger.handlers[0].formatter)
    logger.addHandler(fh)

    output = OutputManager(output_dir, dir_keyword, args.years, source=args.source)

    # ── Resume: work out whether the source is needed at all ──
    # A resumed run only needs the source if it still has to search. Round 4
    # (the review) needs nothing but the corpus on disk, so a resumed review
    # must not require Edge to be running.
    present_rounds = set()
    needs_search = True
    if args.resume:
        present_rounds = _rounds_in_corpus(output_dir)
        wanted = _parse_rounds(args.rounds)
        needs_search = bool((wanted & {1, 2, 3}) - present_rounds)
        logger.info("=" * 60)
        logger.info("Resuming run: %s", output_dir)
        logger.info("Rounds already in the corpus: %s", sorted(present_rounds) or "-")
        logger.info("Search needed: %s", "yes" if needs_search else "no")
        logger.info("=" * 60)

    browser_ws_url = None
    if needs_browser and needs_search:
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

    # ── Step 3: Connect to the source ──
    # Browser-backed sources ride on Playwright CDP; API sources need neither
    # Playwright nor a debug browser, so the whole session is skipped for them.
    pw_cm = None
    if needs_browser and needs_search:
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
        auto = None
        if not needs_search:
            logger.info("This resumed run only reviews the corpus - "
                        "the source is not contacted.")
        elif needs_browser:
            logger.info("Connecting to Edge via CDP...")
            browser_obj = pw.chromium.connect_over_cdp(browser_ws_url)

            # The user may have several Edge windows open, and the target tab
            # can live in any context - not just the first one. Searching only
            # contexts[0] fails the moment the user opens another window.
            context = None
            page = None
            for ctx in browser_obj.contexts:
                for p in ctx.pages:
                    url = p.url or ""
                    if url.startswith("chrome-error://"):
                        continue                       # a failed load, not a page
                    if any(url.startswith(h) for h in adapter_cls.hosts):
                        page, context = p, ctx
                        break
                if page is not None:
                    break

            if page is None:
                # No tab for this site. Open one instead of making the user do
                # it by hand: the dedicated profile carries the session, so a
                # fresh tab is still signed in. A new tab is used rather than
                # hijacking one of the user's own.
                if not browser_obj.contexts:
                    logger.error("The debug browser exposes no context to work in.")
                    sys.exit(1)
                context = browser_obj.contexts[0]
                logger.info("No %s tab is open - opening one.", adapter_name)
                try:
                    page = context.new_page()
                    page.goto(adapter_cls.hosts[0] + "/",
                              wait_until="domcontentloaded", timeout=60000)
                except Exception as exc:
                    logger.error("Could not open a %s tab: %s", adapter_name, exc)
                    sys.exit(1)
            else:
                logger.info("Using existing %s tab: %s", adapter_name, page.url[:90])

            auto = adapter_cls(page, browser_ws_url)
        else:
            auto = adapter_cls(None, None)
            logger.info("Using HTTP API transport (no browser, no login required)")

        # ── Step 4: Verify login (hard check only when the source requires it) ──
        if auto is not None:
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

        # ── Steps 5-8: harvest, de-duplicate, save ──
        # A single-round run is one query; --multi-round drives the sweep
        # sequence through the same collector so the rounds de-duplicate
        # against each other.
        coll = Collector(args, auto, output, context)
        if args.resume:
            coll.load_from(output_dir)

        if args.multi_round:
            run_multi_round(args, output, coll)
        else:
            found = coll.search_query(args.keyword, args.count, round_no=1,
                                      via="keyword")
            if not found:
                logger.error("No papers collected. Check search parameters.")
                sys.exit(1)
            coll.extract_details(found)

        finalize(args, output, coll, adapter_cls, adapter_name)


if __name__ == "__main__":
    main()
