#!/usr/bin/env python3
"""Query planning for multi-round literature retrieval.

Implements the three-round loop behind ``--multi-round``:

  R1  seed query formulation
      Decompose a natural-language research goal into concrete search
      strings: the goal verbatim, plus the most distinctive n-grams.

  R2  adjacency expansion  (pseudo-relevance feedback / co-word analysis)
      Mine the papers already retrieved for author keywords and title
      terms, keep the ones that co-occur with the seed topic but are not
      covered by the seed queries, rank them by association (precision x
      lift x term weight) and turn the strongest, mutually non-redundant
      ones into new queries. This is the classic PRF / Rocchio expansion
      step, restricted to controlled vocabulary (author keywords, IEEE
      terms, Keywords Plus) so the output terms are *professional*
      terminology rather than free-text noise.

  R3  citation snowballing  (backward + forward)
      Take the best papers by priority/citations/DOI availability, follow
      their references (backward) and the works citing them (forward)
      through OpenAlex, and merge the new records into the corpus.
      Backward/forward snowballing follows Wohlin (2014), "Guidelines for
      snowballing in systematic literature studies".

The plan is written to ``reports/query_plan.{json,md}`` so a run can be
reproduced and the generated terms can be cited in a methods section.
"""

import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

# ── vocabulary ──────────────────────────────────────────────────────────

STOPWORDS = set("""
a about above after again against all also am an and any are as at be because been
before being below between both but by can cannot could did do does doing down during
each few for from further had has have having he her here hers herself him himself his
how however i if in into is it its itself just may me might more most must my myself
no nor not now of off on once only or other our ours ourselves out over own same she
should so some such than that the their theirs them themselves then there these they
this those through to too under until up very was we were what when where which while
who whom why will with within without would you your yours yourself yourselves
""".split())

# Terms that appear in nearly every title and carry no domain signal.
GENERIC = set("""
paper papers article articles study studies research approach approaches method methods
result results analysis review survey system systems model models framework frameworks
application applications case cases work works problem problems issue issues challenge
challenges solution solutions technique techniques evaluation experiment experiments
dataset datasets performance based using toward towards via new novel recent
""".split())

GENERIC_PHRASES = set("""
this paper these papers our approach our method in this we propose we present
state art literature review systematic review case study experimental results
""".split())

# Short but highly specific tokens that the length filter would otherwise drop.
SHORT_TERMS = set("""
5g 6g ai llm llms sdn nfv iot vm vms k8s bgp dns tcp udp gpu cpu tpu nat acl dnn gnn
rag kg smt sat csp pki ids ips apt bot ddos ml ir nlp asr tts
""".split())

CATEGORY_CODE = re.compile(r"^[a-z]{2}\.[a-z]{2}$", re.I)
YEARISH = re.compile(r"^(19|20)\d{2}$")

# Leading words that signal intent rather than topic. A phrase that starts
# with one of these makes a poor search string ("reduce orchestration time"
# retrieves papers about reducing anything).
INTENT_VERBS = set("""
reduce improve enhance build design develop evaluate compare analyse analyze investigate
explore study understand enable achieve ensure guarantee support provide propose present
toward towards using use make create generate optimize optimise accelerate
""".split())


# ── text utilities ──────────────────────────────────────────────────────


def has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in (text or ""))


def normalize_term(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace."""
    t = (text or "").strip().lower()
    t = re.sub(r"[\(\)\[\]\{\}<>\"'“”‘’、,;:!?/\\|]+", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip(" .-")


def content_tokens(text: str) -> list[str]:
    """Alphanumeric tokens minus stopwords / generic words."""
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9\-\+.]*", (text or "").lower())
    out = []
    for w in words:
        w = w.strip(".-")
        if not w or YEARISH.match(w):
            continue
        if w in STOPWORDS or w in GENERIC:
            continue
        if len(w) < 3 and w not in SHORT_TERMS:
            continue
        out.append(w)
    return out


def ngrams(tokens: list, n: int) -> list[tuple]:
    return [tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


def is_technical_term(term: str) -> bool:
    """Filter out category codes, years, stopword-only and over-long phrases."""
    t = normalize_term(term)
    if not t or len(t) < 2:
        return False
    if CATEGORY_CODE.match(t) or YEARISH.match(t):
        return False
    words = t.split()
    if len(words) > 6:
        return False
    if all(w in STOPWORDS or w in GENERIC for w in words):
        return False
    if " ".join(words) in GENERIC_PHRASES:
        return False
    if t in SHORT_TERMS:
        return True
    return any(len(w) >= 4 and w not in GENERIC for w in words)


# ── round 1: seed queries ───────────────────────────────────────────────


def _mk_query(text: str, origin: str, rationale: str, terms=None) -> dict:
    return {
        "query": " ".join((text or "").split()),
        "terms": terms or [normalize_term(text)],
        "origin": origin,
        "rationale": rationale,
    }


def _phrase_candidates(goal: str) -> list[dict]:
    """Score contiguous 2-4-grams over the *content* token stream.

    Working on the stopword-free stream keeps "and"/"of"/"in" out of the
    generated queries, which is what breaks naive n-gram approaches.
    """
    toks = content_tokens(goal)
    cands = []
    for n in (3, 2):                       # 4+ grams read as sentences, not queries
        for i in range(len(toks) - n + 1):
            window = toks[i:i + n]
            if window[0] in INTENT_VERBS:
                continue
            score = len(window)
            score += 0.5 * sum(1 for w in window if len(w) >= 8)
            if i + n == len(toks):
                score += 0.3                # the goal's object usually sits at the end
            cands.append({"phrase": " ".join(window), "score": score,
                          "start": i, "end": i + n})
    cands.sort(key=lambda c: (-c["score"], c["start"], -(c["end"] - c["start"])))
    return cands


def _non_overlapping(cands: list[dict], limit: int) -> list[dict]:
    """Greedy: keep high-score phrases that do not share goal tokens."""
    picked = []
    for c in cands:
        if any(not (c["end"] <= p["start"] or c["start"] >= p["end"]) for p in picked):
            continue
        picked.append(c)
        if len(picked) >= limit:
            break
    return picked


def seed_queries(goal: str = "", keyword: str = "", limit: int = 3) -> list[dict]:
    """Turn a research goal into 1-3 concrete search strings.

    CJK goals are passed through verbatim: Chinese search strings work on
    IEEE / WoS / ScienceDirect, but Latin-script n-gram decomposition does
    not apply. Adjacency expansion (round 2) still works downstream because
    it mines the *retrieved* English records, not the goal.
    """
    out: list[dict] = []

    if keyword and keyword.strip():
        out.append(_mk_query(keyword, "user:keyword", "用户给定检索词 / user-supplied keyword"))

    goal = " ".join((goal or "").split())
    if goal and normalize_term(goal) != normalize_term(keyword):
        if has_cjk(goal):
            out.append(_mk_query(
                goal, "goal:verbatim",
                "中文目标原样检索（不做拉丁词切分）/ CJK goal used verbatim",
                [goal],
            ))
        else:
            if len(goal.split()) <= 8:
                out.append(_mk_query(
                    goal, "goal:verbatim",
                    "目标原句，高精度 / whole goal, high precision",
                ))
            budget = max(1, limit - len(out))
            for c in _non_overlapping(_phrase_candidates(goal), budget):
                out.append(_mk_query(
                    c["phrase"], "goal:phrase",
                    f"目标核心词组（得分 {c['score']:.1f}）/ scored goal phrase",
                ))

    seen, uniq = set(), []
    for q in out:
        key = normalize_term(q["query"])
        if key and key not in seen:
            seen.add(key)
            uniq.append(q)
    return uniq[:limit]


# ── round 2: adjacency mining (PRF / co-word analysis) ───────────────────


def paper_term_profile(paper: dict) -> dict:
    """term -> trust weight. Controlled vocabulary beats free text.

    3.0  author keywords / IEEE terms / Keywords Plus / OpenAlex keywords
    2.0  title 2- and 3-grams
    1.0  abstract bigrams (only when the record ships no keywords at all)
    """
    prof: dict[str, float] = {}

    for kw in paper.get("keywords") or []:
        t = normalize_term(kw)
        if is_technical_term(t):
            prof[t] = max(prof.get(t, 0.0), 3.0)

    title = paper.get("title") or ""
    ttoks = content_tokens(title)
    for n in (3, 2):
        for gram in ngrams(ttoks, n):
            t = " ".join(gram)
            if is_technical_term(t):
                prof[t] = max(prof.get(t, 0.0), 2.0)

    if not paper.get("keywords"):
        atoks = content_tokens((paper.get("abstract") or "")[:1500])
        for gram in ngrams(atoks, 2):
            t = " ".join(gram)
            if is_technical_term(t):
                prof.setdefault(t, 1.0)

    return prof


def _haystack(paper: dict) -> str:
    parts = [
        paper.get("title") or "",
        " ".join(paper.get("keywords") or []),
        (paper.get("abstract") or "")[:2000],
    ]
    return " " + normalize_term(" ".join(parts)) + " "


def mine_adjacent_terms(papers: list[dict], seed_queries_: list[str],
                        limit: int = 3, min_support: int = 2) -> list[dict]:
    """Find terms that co-occur with the seed topic but are not covered by it.

    score = precision(term is on-topic) x log(1+df) x vocabulary weight
    """
    if not papers:
        return []

    seeds = [normalize_term(q) for q in seed_queries_ if normalize_term(q)]
    seed_cores = set()
    for s in seeds:
        seed_cores.update(w for w in s.split() if w not in STOPWORDS and w not in GENERIC)

    n = len(papers)
    df: Counter = Counter()
    co: Counter = Counter()
    weight: dict[str, float] = {}
    evidence: dict[str, list] = defaultdict(list)
    on_topic_n = 0

    for p in papers:
        prof = paper_term_profile(p)
        hay = _haystack(p)
        on_topic = any(s in hay for s in seeds)
        if not on_topic and seed_cores:
            need = max(1, len(seed_cores) // 2)
            on_topic = sum(1 for t in seed_cores if t in hay) >= need
        if on_topic:
            on_topic_n += 1

        for t, w in prof.items():
            df[t] += 1
            weight[t] = max(weight.get(t, 0.0), w)
            if on_topic:
                co[t] += 1
                if len(evidence[t]) < 3:
                    evidence[t].append((p.get("title") or "")[:90])

    if not on_topic_n:
        return []

    cands = []
    for t, c in co.items():
        if c < min_support:
            continue
        if any(t == s or t in s or s in t for s in seeds):
            continue
        precision = c / df[t]
        lift = (c / n) / ((df[t] / n) * (on_topic_n / n))
        cands.append({
            "term": t,
            "support": c,
            "df": df[t],
            "precision": round(precision, 3),
            "lift": round(lift, 2),
            "weight": weight[t],
            "score": round(precision * math.log1p(df[t]) * weight[t], 3),
            "evidence": evidence[t],
        })

    cands.sort(key=lambda x: (-x["score"], -x["support"], x["term"]))

    # Diversity: adjacent *domains*, not three variants of the same phrase.
    picked, used = [], []
    for c in cands:
        toks = set(c["term"].split())
        if any(len(toks & u) / max(1, len(toks | u)) > 0.6 for u in used):
            continue
        picked.append(c)
        used.append(toks)
        if len(picked) >= limit:
            break
    return picked


# ── round 3: citation snowballing ───────────────────────────────────────


def select_citation_seeds(papers: list[dict], limit: int = 5) -> list[dict]:
    """Pick the papers worth snowballing: DOI first, then priority, then citations."""
    def score(p: dict):
        try:
            cites = int(str(p.get("citation_count") or "0").replace(",", "") or 0)
        except ValueError:
            cites = 0
        priority = {"high": 3, "medium": 2}.get(p.get("priority", "low"), 1)
        return (1 if p.get("doi") else 0, priority, cites)

    pool = [p for p in papers if (p.get("doi") or "").strip()]
    pool.sort(key=score, reverse=True)
    return pool[:limit]


# ── query formatting ────────────────────────────────────────────────────


def format_query(source: str, terms: list[str]) -> str:
    """Render a term list in the syntax the target source understands."""
    terms = [t.strip() for t in terms if t and t.strip()]
    if not terms:
        return ""
    if len(terms) == 1:
        return f'"{terms[0]}"' if " " in terms[0] else terms[0]
    if source == "openalex":
        return " ".join(terms)
    if source == "arxiv":
        return " AND ".join(f'all:"{t}"' for t in terms)
    return " AND ".join(f'"{t}"' for t in terms)


# ── plan rendering ──────────────────────────────────────────────────────


def render_plan_markdown(plan: dict) -> str:
    lines = [
        "# 多轮检索计划 / Multi-round retrieval plan",
        "",
        f"- 研究目标 / Goal: {(plan.get('goal') or '(未提供)')}",
        f"- 初始检索词 / Seed keyword: {(plan.get('keyword') or '(未提供)')}",
        f"- 数据源 / Source: {plan.get('source', '')}",
        f"- 生成时间 / Generated: {plan.get('generated_at', '')}",
        "",
    ]

    for rnd in plan.get("rounds", []):
        no = rnd.get("round")
        lines.append(f"## 第 {no} 轮 · {rnd.get('method_zh', '')} / Round {no} · {rnd.get('method', '')}")
        lines.append("")
        if rnd.get("note"):
            lines.append(rnd["note"])
            lines.append("")

        queries = rnd.get("queries") or []
        if queries:
            if no == 2:
                lines.append("| # | 相邻领域术语 | 检索式 | 共现论文 | 精确率 | lift | 代表论文 |")
                lines.append("|---|---|---|---|---|---|---|")
                for i, q in enumerate(queries, 1):
                    ev = (q.get("evidence") or [""])[0].replace("|", "/")
                    lines.append("| {} | {} | `{}` | {} | {} | {} | {} |".format(
                        i, q.get("term", ""), q.get("query", ""),
                        q.get("support", ""), q.get("precision", ""),
                        q.get("lift", ""), ev))
            else:
                lines.append("| # | 检索式 | 来源 | 说明 |")
                lines.append("|---|---|---|---|")
                for i, q in enumerate(queries, 1):
                    lines.append("| {} | `{}` | {} | {} |".format(
                        i, q.get("query", ""), q.get("origin", ""), q.get("rationale", "")))
            lines.append("")

        seeds = rnd.get("seeds") or []
        if seeds:
            lines.append("| # | 种子文献 | DOI | 后向新增 | 前向新增 |")
            lines.append("|---|---|---|---|---|")
            for i, s in enumerate(seeds, 1):
                lines.append("| {} | {} | {} | {} | {} |".format(
                    i, s.get("title", "")[:70], s.get("doi", ""),
                    s.get("backward", "-"), s.get("forward", "-")))
            lines.append("")

    return "\n".join(lines)


def write_plan(plan: dict, out_dir: Path) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "query_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "query_plan.md").write_text(
        render_plan_markdown(plan), encoding="utf-8")


# ── CLI (handy for dry runs) ────────────────────────────────────────────


if __name__ == "__main__":
    import argparse
    from pprint import pprint

    ap = argparse.ArgumentParser(description="Preview round-1 queries for a goal")
    ap.add_argument("--goal", default="")
    ap.add_argument("--keyword", default="")
    ap.add_argument("--limit", type=int, default=3)
    a = ap.parse_args()
    pprint(seed_queries(a.goal, a.keyword, a.limit))
