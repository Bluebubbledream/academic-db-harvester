#!/usr/bin/env python3
"""Relevance measurement for the multi-round retrieval loop.

What this module does *not* do: decide anything. An earlier version scored
candidates against a fixed threshold (PASS / REVIEW / BLOCKED) and dropped
the BLOCKED ones. That threshold was one person's guess about a corpus they
had never seen, and it was wrong in both directions at once: it discarded
perfectly good papers whose vocabulary merely differed in morphology
(`containerized` vs `containerization`), and it silently threw away
references the reviewer would have wanted to see.

Judging relevance belongs to the reviewer: the agent picks which round-2
queries to run, and round 4 labels every paper. What is left here is the
mechanical part that helps that judgement without pre-empting it:

    cov = fraction of a paper's content tokens that occur in the round-1
          vocabulary, where a token qualifies once it appears in
          max(1, 5% of the round-1 papers)

`cov` is reported, never thresholded. It is used to order the review pack and
to flag outliers, and it is written to disk so a judgement can be audited
against the number that informed it.

Two things survive as *measurements* rather than decisions:

  * **the vocabulary**, built from the round-1 corpus (title + keywords +
    the opening of the abstract). Tokens are matched on a shared prefix of at
    least five characters, so `containerized` matches `containerization` and
    `orchestrating` matches `orchestration` - without that fold, morphology
    alone produces false "off-topic" readings on every good paper;

  * **the query anchor**, the round-1 token that every expansion query is
    scoped to (`<term> AND <core>`). This costs no recall and prevents an
    ambiguous term from escaping the topic - it is a narrowing of the query,
    never a removal of results.
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import query_planner as qp
from utils import setup_logger

logger = setup_logger("term_gate")

MIN_SHARE = 0.05      # a vocabulary token must appear in this share of round-1
MIN_PREFIX = 5        # two tokens are the same word if they share this prefix
ABSTRACT_CHARS = 800  # how much of the abstract defines the topic
# Below this corpus size no token is distinctive enough to narrow by: every
# topic word sits in most of the papers, so an anchor either excludes nothing
# or excludes too much.
MIN_CORPUS_FOR_ANCHOR = 20


@lru_cache(maxsize=20000)
def _prefixes(token: str) -> frozenset:
    """All prefixes of a token that are long enough to be distinctive.

    Morphology is the single biggest source of false "off-topic" readings:
    `containerized` and `containerization` are the same word, `orchestrating`
    and `orchestration` are the same word, and a vocabulary built from ten
    papers will contain one spelling and not the other. Matching on a shared
    prefix of at least five characters folds them together without needing a
    stemmer - and five characters is short enough that `security` still
    matches `secure` but `ai` never matches `air`.
    """
    if len(token) < MIN_PREFIX:
        return frozenset()
    return frozenset(token[:k] for k in range(MIN_PREFIX, len(token) + 1))


# ── anchors ─────────────────────────────────────────────────────────────


def _paper_text(paper: dict) -> str:
    """A paper's own topical declaration.

    Title + keywords + the opening of the abstract, and the *same* fields on
    both sides of the comparison. Scoring a short title against a long
    abstract vocabulary would be unfair; scoring a long abstract against a
    short title vocabulary would be equally unfair and would let any
    off-topic paper look related once it had enough words.
    """
    parts = [paper.get("title") or ""]
    parts += [str(k) for k in (paper.get("keywords") or [])]
    abstract = (paper.get("abstract") or "").strip()
    if abstract:
        parts.append(abstract[:ABSTRACT_CHARS])
    return " ".join(parts)


def _tokens(text: str) -> list[str]:
    return qp.content_tokens(text)


def build_vocabulary(anchors: list[dict], min_share: float = MIN_SHARE) -> dict:
    """Round-1 vocabulary with document frequency, and the df threshold used."""
    df: Counter = Counter()
    for paper in anchors or []:
        for token in set(_tokens(_paper_text(paper))):
            df[token] += 1
    need = max(1, int(math.ceil(min_share * len(anchors or []))))
    vocab = {t for t, c in df.items() if c >= need}
    prefixes: set = set()
    for token in vocab:
        prefixes.update(_prefixes(token))
    return {"df": df, "vocab": vocab, "prefixes": prefixes, "need": need}


# ── scoring ─────────────────────────────────────────────────────────────


def score_text(text: str, index: dict) -> dict:
    """Coverage of one text against the vocabulary. No verdict is attached."""
    vocab, prefixes = index["vocab"], index["prefixes"]
    tokens = _tokens(text)
    missing = []
    covered = 0
    for token in tokens:
        if token in vocab or (_prefixes(token) & prefixes):
            covered += 1
        elif len(missing) < 4:
            missing.append(token)
    total = len(tokens)
    return {
        "text": text, "tokens": tokens, "vocab_size": total, "covered": covered,
        "cov": round(covered / total, 3) if total else 0.0,
        "missing": missing, "df_min": index["need"],
    }


def score_texts(texts: list[str], anchors: list[dict]) -> list[dict]:
    """Score free-text candidates against the round-1 corpus."""
    index = build_vocabulary(anchors)
    hay = [qp.normalize_term(_paper_text(a)) for a in (anchors or [])]
    out = []
    for text in texts:
        s = score_text(text, index)
        phrase = qp.normalize_term(text)
        s["hits"] = sum(1 for h in hay if phrase and phrase in h)
        s["anchor_papers"] = len(anchors or [])
        out.append(s)
    return out


def score_terms(terms: list[str], anchors: list[dict]) -> list[dict]:
    """Round-2 candidate terms: a measurement to inform the choice, not a filter."""
    return score_texts(terms, anchors)


# ── annotator ───────────────────────────────────────────────────────────


class RelevanceAnnotator:
    """Attaches a coverage measurement to each retrieved paper.

    It never removes anything. The papers it scores stay in the corpus and go
    to the reviewer, who decides. The score ends up on the paper as
    `relevance`, and in `metadata/relevance_scores.csv`.
    """

    def __init__(self, anchors: list[dict], label: str = "", verbose: bool = False):
        self.index = build_vocabulary(anchors)
        self.label = label
        self.verbose = verbose
        self.scores: list[dict] = []

    def annotate(self, paper: dict) -> dict:
        score = score_text(_paper_text(paper), self.index)
        score["title"] = (paper.get("title") or "")[:120]
        score["doi"] = paper.get("doi", "")
        score["journal"] = (paper.get("journal") or "")[:60]
        self.scores.append(score)
        paper["relevance"] = {"cov": score["cov"], "label": self.label}
        if self.verbose:
            logger.info("  cov=%.2f  %s", score["cov"], score["title"][:60])
        return score

    def summary(self) -> str:
        if not self.scores:
            return "nothing scored"
        values = sorted(s["cov"] for s in self.scores)
        median = values[len(values) // 2]
        low = sum(1 for v in values if v < median)
        return ("%s: measured %d papers, median cov %.2f, %d below median "
                "(nothing dropped - the reviewer decides)"
                % (self.label or "relevance", len(values), median, low))

    def rows(self) -> list[dict]:
        return [{"label": self.label, "cov": s["cov"], "title": s["title"],
                 "doi": s["doi"], "journal": s["journal"],
                 "missing": "; ".join(s["missing"])} for s in self.scores]


def make_annotator(anchors: list[dict], label: str = "",
                    verbose: bool = False) -> RelevanceAnnotator:
    return RelevanceAnnotator(anchors, label, verbose)


# ── query anchoring ─────────────────────────────────────────────────────


def core_anchor(anchors: list[dict], seeds: list[str] | None = None,
                limit: int = 1, min_share: float = 0.10,
                max_share: float = 0.50,
                min_corpus: int = MIN_CORPUS_FOR_ANCHOR) -> list[str]:
    """The round-1 topic's central token(s), used to scope expansion queries.

    Anchoring is the one query-side measure that costs no recall: it turns
    every expansion into an intersection with the topic, so an ambiguous term
    narrows instead of escaping.

    The anchor has to narrow, though. A token that sits in almost every paper
    of the corpus ("language", in a corpus of LLM papers) excludes nothing and
    makes the anchored query equivalent to the bare term; a token in almost no
    paper excludes too much. So the anchor is chosen from a *band* - a seed
    token that appears in between 10% and 50% of the corpus - and the most
    frequent token inside that band wins.

    On a small corpus that band is empty by construction: with six papers on
    one topic every topic word is in 50-100% of them, and any anchor is either
    vacuous or arbitrary (and can exclude documents that never happen to use
    the anchor's exact wording). Below `min_corpus` no anchor is returned at
    all, and the expansion runs on the multi-word term alone.
    """
    if len(anchors or []) < max(1, min_corpus):
        return []

    seed_tokens: set = set()
    for s in seeds or []:
        seed_tokens.update(_tokens(s))
    df = build_vocabulary(anchors, min_share=0.0)["df"]
    n = len(anchors or []) or 1

    rows = [(t, c, c / n) for t, c in df.most_common() if len(t) >= 4]
    seed_rows = [e for e in rows if e[0] in seed_tokens]
    band = [e for e in seed_rows if min_share <= e[2] <= max_share]
    if not band:
        band = [e for e in rows if min_share <= e[2] <= max_share]
    pool = band or seed_rows or rows
    pool.sort(key=lambda e: -e[1])
    return [e[0] for e in pool[:limit]]


# ── review artefacts ────────────────────────────────────────────────────


def write_review(entries: list[dict], path_json: Path, path_md: Path,
                 heading: str, note: str = "",
                 domains: list[dict] | None = None) -> None:
    """Write the reviewable candidate list the operator edits and re-submits."""
    path_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {"heading": heading, "note": note, "entries": entries,
               "adjacent_domains": domains or []}
    path_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                         encoding="utf-8")

    lines = [f"# {heading}", ""]
    if note:
        lines += [note, ""]
    lines += ["| # | 候选 | 检索式 | cov | 命中第1轮 | 支撑论文 | approved |",
              "|---|------|--------|-----|----------|----------|----------|"]
    for i, e in enumerate(entries, 1):
        # The query is what the operator is approving, so it is never truncated.
        lines.append("| {} | {} | `{}` | {} | {} | {} | {} |".format(
            i, (e.get("term") or e.get("text") or "")[:45],
            e.get("query") or "", e.get("cov", "-"),
            e.get("hits", "-"),
            (e.get("evidence") or ["-"])[0] if isinstance(e.get("evidence"), list)
            else e.get("evidence", "-"),
            "true" if e.get("approved") else "false"))
    lines += ["", "本表是**候选**清单，不是判定：cov 只是供判断的一个数字。"
              "由调用本 skill 的 agent 决定要跑哪些检索式："
              "删掉不想跑的（把 `approved` 改成 false），"
              "再用 `--approve <该文件>` 重新运行。"]

    if domains:
        lines += ["", "## 相邻领域（OpenAlex 受控主题表） / Adjacent subject domains", "",
                  "语料在 OpenAlex 主题分类里落入的相邻领域——这是**背景信息**，"
                  "用来判断该往哪个方向扩，不是检索式。", "",
                  "| 领域 | 层级 | 篇数 | 占比 | 该领域的代表关键词 |",
                  "|------|------|------|------|------------------|"]
        for d in domains[:25]:
            lines.append("| {} | {} | {} | {:.0f}% | {} |".format(
                str(d.get("name", ""))[:44], d.get("level", ""),
                d.get("count", ""), float(d.get("share", 0)) * 100,
                ", ".join(d.get("top_keywords", [])[:6])))
        lines += ["", "这些领域名**不作为检索式**——它们是分类桶，太宽。"
                  "要扩某个方向，用上面的关键词自己组词。"]

    path_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Review written: %s (and .md)", path_json)


def load_approved(path: str | Path) -> list[dict]:
    """Read an operator-edited review file; returns entries marked approved."""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("cannot read approved list %s: %s" % (p, exc))
    entries = data.get("entries") if isinstance(data, dict) else data
    return [e for e in (entries or []) if e.get("approved")]
