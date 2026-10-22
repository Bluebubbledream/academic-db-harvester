#!/usr/bin/env python3
"""Round 4: AI review of the harvested corpus against the retrieval goal.

Rounds 1-3 maximise recall; nothing so far checks precision. A co-word term can
be on-topic and still drag in publisher's notes, journal front matter and
proceedings prefaces, and a citation snowball will happily collect a survey's
entire bibliography. Only a reader - human or agent - can tell "this paper is
about the goal" from "this paper was merely retrieved".

The script never guesses a verdict. It does two mechanical jobs and stops:

    build_pack()  render the corpus as a compact, batched reading pack. Only
                  what a relevance judgement actually needs is included -
                  title, venue, keywords and the first ~300 characters of the
                  abstract - because a 400-paper corpus of full abstracts does
                  not fit in any sane review budget.

    ingest()      read the verdicts back, annotate the corpus, and emit the
                  final selection plus a per-query precision table, which is
                  how you find out which expansion query was worth running.

Verdicts:

    RELEVANT   answers the retrieval goal
    MARGINAL   adjacent; belongs in the bibliography, not the core set
    OFF_TOPIC  retrieved, but not about the goal
    INVALID    not a usable paper at all - publisher's note, editorial, front
               matter, a journal title scraped as a record, retracted work
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils import save_json, save_csv, setup_logger

logger = setup_logger("paper_review")

ABSTRACT_CHARS = 300
VERDICTS = ("RELEVANT", "MARGINAL", "OFF_TOPIC", "INVALID")
# A paper on the fence is better kept than lost: only these two count as "in".
IN_SELECTION = ("RELEVANT", "MARGINAL")


def _abstract(paper: dict) -> str:
    text = (paper.get("abstract") or "").strip()
    if len(text) <= ABSTRACT_CHARS:
        return text
    cut = text[:ABSTRACT_CHARS]
    return cut.rsplit(" ", 1)[0] + " ..."


def compact(paper: dict) -> dict:
    """The minimum a reviewer needs, plus the provenance needed to act on it.

    The relevance measurement travels in the JSON for audit, but is kept out
    of the human-readable pack: a number next to a title anchors the reader
    towards agreeing with it, and the point of round 4 is that the reader
    judges from the content.
    """
    relevance = paper.get("relevance") or {}
    return {
        "index": paper.get("index", 0),
        "round": paper.get("round", ""),
        "via": paper.get("via", ""),
        "query": paper.get("query", ""),
        "title": (paper.get("title") or "").strip(),
        "year": str(paper.get("year") or ""),
        "journal": (paper.get("journal") or "").strip(),
        "doi": (paper.get("doi") or "").strip(),
        "keywords": [str(k) for k in (paper.get("keywords") or [])][:6],
        "abstract": _abstract(paper),
        "cov": relevance.get("cov", ""),
    }


# ── build the reading pack ──────────────────────────────────────────────


def build_pack(papers: list[dict], goal: str, reports_dir: Path,
               batch_size: int = 30, limit: int | None = None) -> int:
    """Write paper_review.{json,md}. Returns the number of papers packed."""
    selected = list(papers) if not limit else list(papers)[:limit]
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    entries = [dict(compact(p), verdict="", reason="") for p in selected]
    save_json(reports_dir / "paper_review.json",
              {"goal": goal, "total": len(papers), "packed": len(entries),
               "verdicts": list(VERDICTS), "entries": entries})

    lines = ["# 第 4 轮 · 全量复核 / Round 4 corpus review", "",
             "检索目的 / Retrieval goal: " + (goal or "(未提供)"),
             "",
             "复核者 / reviewer：**调用本 skill 的 agent**（脚本不猜判定，只出题）",
             "",
             f"语料共 {len(papers)} 篇，本包 {len(entries)} 篇"
             + (f"（--review-limit 截断）" if limit and limit < len(papers) else "")
             + "。", "",
             "判定 / verdict：`RELEVANT` 直接回答检索目的 · `MARGINAL` 相邻可留作背景 · "
             "`OFF_TOPIC` 检索到了但不相关 · `INVALID` 不是可用论文"
             "（Publisher's Note / 编者按 / 会议前言 / 把期刊名当成记录 / 已撤稿）", ""]

    for start in range(0, len(entries), batch_size):
        batch = entries[start:start + batch_size]
        lines.append(f"## Batch {start // batch_size + 1}"
                     f" / {-(-len(entries) // batch_size)}  （第 {start + 1}–{start + len(batch)} 篇）")
        lines.append("")
        for e in batch:
            head = f"[{e['index']}] (round {e['round']} · {e['via']}) {e['title']}"
            lines.append(head)
            meta = " · ".join(x for x in (e["year"], e["journal"],
                                          ("doi:" + e["doi"]) if e["doi"] else "") if x)
            if meta:
                lines.append("    " + meta)
            if e["keywords"]:
                lines.append("    kw: " + "; ".join(e["keywords"]))
            if e["abstract"]:
                lines.append("    ab: " + e["abstract"])
            lines.append("")
        lines.append("---")
        lines.append("")

    (reports_dir / "paper_review.md").write_text("\n".join(lines), encoding="utf-8")
    logger.info("Round 4 pack written: %s (%d of %d papers, %d batch(es))",
                reports_dir / "paper_review.md", len(entries), len(papers),
                max(1, -(-len(entries) // max(1, batch_size))))
    return len(entries)


# ── ingest verdicts ─────────────────────────────────────────────────────


def _normalise(verdict: str) -> str:
    v = (verdict or "").strip().upper().replace("-", "_").replace(" ", "_")
    return v if v in VERDICTS else ""


def _lookup(entries: list[dict]):
    """Match verdicts back to papers: DOI first, then title, then index.

    The verdict file has to survive a re-run: a fresh round 1 can return the
    same papers in a different order, so the positional index is only a last
    resort.
    """
    by_doi: dict = {}
    by_title: dict = {}
    by_index: dict = {}
    for e in entries or []:
        doi = (e.get("doi") or "").strip().lower()
        if doi:
            by_doi[doi] = e
        title = (e.get("title") or "").strip().lower()
        if title:
            by_title[title] = e
        idx = e.get("index")
        if idx is not None:
            try:
                by_index[int(idx)] = e
            except (TypeError, ValueError):
                pass

    def find(paper: dict):
        doi = (paper.get("doi") or "").strip().lower()
        if doi and doi in by_doi:
            return by_doi[doi]
        title = (paper.get("title") or "").strip().lower()
        if title and title in by_title:
            return by_title[title]
        try:
            return by_index.get(int(paper.get("index")))
        except (TypeError, ValueError):
            return None

    return find


def ingest_verdicts(entries: list[dict], papers: list[dict]) -> dict:
    """Annotate the corpus with verdicts and compute the statistics."""
    find = _lookup(entries)
    matched = unknown = 0
    by_round, by_query = Counter(), Counter()
    hit_round, hit_query = Counter(), Counter()
    rel_round, rel_query = Counter(), Counter()

    for paper in papers:
        entry = find(paper)
        if not entry:
            paper["ai_verdict"] = "UNREVIEWED"
            unknown += 1
            continue
        matched += 1
        verdict = _normalise(entry.get("verdict")) or "UNREVIEWED"
        paper["ai_verdict"] = verdict
        paper["ai_reason"] = (entry.get("reason") or "").strip()

        key = str(paper.get("round") or "-")
        by_round[key] += 1
        q = (paper.get("query") or "").strip() or "(no query)"
        by_query[q] += 1
        if verdict in IN_SELECTION:
            hit_round[key] += 1
            hit_query[q] += 1
        if verdict == "RELEVANT":
            rel_round[key] += 1
            rel_query[q] += 1

    stats = {
        "matched": matched, "unreviewed": unknown, "total": len(papers),
        "verdicts": dict(Counter(p.get("ai_verdict", "UNREVIEWED") for p in papers)),
        "per_round": {k: {"reviewed": v, "kept": hit_round.get(k, 0),
                          "relevant": rel_round.get(k, 0),
                          "precision": round(hit_round.get(k, 0) / v, 3),
                          "relevant_rate": round(rel_round.get(k, 0) / v, 3)}
                      for k, v in sorted(by_round.items())},
        "per_query": {k: {"reviewed": v, "kept": hit_query.get(k, 0),
                          "relevant": rel_query.get(k, 0),
                          "precision": round(hit_query.get(k, 0) / v, 3),
                          "relevant_rate": round(rel_query.get(k, 0) / v, 3)}
                      for k, v in sorted(by_query.items(), key=lambda kv: -kv[1])},
        "source": "llm" if entries and entries[0].get("by_llm") else "file",
    }
    logger.info("Round 4: %d/%d papers carry a verdict (%d unreviewed)",
                matched, len(papers), unknown)
    return stats


def ingest(verdict_path: str | Path, papers: list[dict]) -> dict:
    """Read a verdict file and ingest it."""
    path = Path(verdict_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("cannot read verdicts %s: %s" % (path, exc))
    raw = data.get("entries") if isinstance(data, dict) else data
    stats = ingest_verdicts(raw or [], papers)
    stats["source"] = str(path)
    return stats


def write_outputs(papers: list[dict], stats: dict, output) -> None:
    """Annotated corpus, final selection, and the precision report."""
    rows = [{"index": p.get("index", 0), "round": p.get("round", ""),
             "via": p.get("via", ""), "query": p.get("query", ""),
             "verdict": p.get("ai_verdict", ""), "reason": p.get("ai_reason", ""),
             "title": (p.get("title") or "")[:120], "doi": p.get("doi", ""),
             "year": p.get("year", ""), "journal": p.get("journal", "")}
            for p in papers]
    save_csv(output.metadata_dir / "ai_review.csv", rows,
             ["index", "round", "via", "query", "verdict", "reason",
              "title", "doi", "year", "journal"])

    kept = [p for p in papers if p.get("ai_verdict") in IN_SELECTION]
    save_json(output.metadata_dir / "selected_papers.json", kept)
    save_csv(output.metadata_dir / "selected_papers.csv", rows and [
        r for r in rows if r["verdict"] in IN_SELECTION],
        ["index", "round", "via", "query", "verdict", "reason",
         "title", "doi", "year", "journal"])

    md = ["# 第 4 轮复核结果 / Round 4 review result", "",
          f"- 语料 / corpus: {stats['total']} 篇",
          f"- 已复核 / reviewed: {stats['matched']} 篇"
          f"（未复核 {stats['unreviewed']}）",
          f"- 入选 / selected: {len(kept)} 篇（RELEVANT + MARGINAL）",
          f"- 判定分布 / verdicts: " + ", ".join(
              f"{k} {v}" for k, v in sorted(stats["verdicts"].items())), "",
          "## 按轮次的精确率 / Precision by round", "",
          "| 轮次 | 复核 | 入选(R+M) | 直接相关(R) | 入选率 | 相关率 |",
          "|---|---|---|---|---|---|"]
    for k, v in stats["per_round"].items():
        md.append("| %s | %d | %d | %d | %.0f%% | %.0f%% |"
                  % (k, v["reviewed"], v["kept"], v.get("relevant", 0),
                     v["precision"] * 100, v.get("relevant_rate", 0) * 100))
    md += ["", "## 按检索式的精确率 / Precision by query（相关率低的那几个就是白跑的）", "",
           "| 检索式 | 复核 | 入选(R+M) | 直接相关(R) | 入选率 | 相关率 |",
           "|---|---|---|---|---|---|"]
    for q, v in list(stats["per_query"].items())[:25]:
        md.append("| `%s` | %d | %d | %d | %.0f%% | %.0f%% |"
                  % (q, v["reviewed"], v["kept"], v.get("relevant", 0),
                     v["precision"] * 100, v.get("relevant_rate", 0) * 100))
    md += ["", "完整逐篇判定见 `metadata/ai_review.csv`，最终清单见 "
               "`metadata/selected_papers.{json,csv}`。"]

    (output.reports_dir / "round4_review_stats.md").write_text(
        "\n".join(md) + "\n", encoding="utf-8")
    save_json(output.reports_dir / "round4_review_stats.json", stats)
    logger.info("Round 4 outputs written: selected_papers (%d) + review stats", len(kept))
