#!/usr/bin/env python3
"""OpenAlex graph operations behind the multi-round retrieval loop.

Two consumers live here:

  round 2  adjacent_terms()
           Map the harvested corpus onto OpenAlex's controlled subject tree
           (domain > field > subfield > topic), report the neighbouring
           sub-fields, and pull the professional vocabulary that belongs to
           *those* neighbours -- so expansion queries read like the field's
           own terminology instead of free text.

  round 3  snowball()
           Backward: the references of the seed papers.
           Forward : the works citing the seed papers.
           Wohlin (2014), "Guidelines for snowballing in systematic
           literature studies", EASE'14, is the methodological anchor.

Both are read-only against the public API and both *raise* on failure, so the
caller (harvester.run_multi_round) can fall back to the co-word pass in
query_planner.mine_adjacent_terms, which needs no network at all.

API notes (verified against api.openalex.org):
  filter=ids.openalex:W1|W2     resolve OpenAlex ids in batches of 50
  filter=doi:10.x|10.y          resolve DOIs in batches of 50
  filter=cites:W1|W2            forward snowball (works citing the seeds)
  sort=cited_by_count:desc      most influential citing works first
Set OPENALEX_MAILTO for the polite pool and OPENALEX_API_KEY for a private
rate budget; both are appended automatically when present.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import query_planner as qp                      # read-only dependency
from sites.openalex import OpenAlexSite
from utils import setup_logger

logger = setup_logger("oa_graph")

API = "https://api.openalex.org/works"
PER_PAGE_MAX = 200          # OpenAlex hard cap per page
BATCH = 50                  # pipe-OR filter values per request
WID = re.compile(r"^W\d+$")
_REDACT = re.compile(r"(api_key=)[^&\s]+", re.I)

SELECT_TAXONOMY = "id,doi,primary_topic,topics,keywords,publication_year,cited_by_count"

SELECT_SNOWBALL = (
    "id,doi,title,display_name,publication_year,publication_date,cited_by_count,"
    "authorships,primary_location,best_oa_location,open_access,type,"
    "abstract_inverted_index,keywords,ids,primary_topic,referenced_works"
)

# breadth_rank: which level of the subject tree makes a better query
LEVEL_RANK = {"keyword": 1, "topic": 2, "subfield": 3, "field": 4, "domain": 5}


# ── plumbing ────────────────────────────────────────────────────────────


def chunk(seq: list, n: int) -> list[list]:
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def redact(text: str) -> str:
    """Strip a query-string key out of anything that could be logged.

    OpenAlex only accepts the key as a URL parameter, so it lives in a string
    that could one day end up in an exception message, a log line or a report
    file. Every error leaving this module passes through here first.
    """
    return _REDACT.sub(r"\1<redacted>", str(text))


def _get(params: dict) -> dict:
    """GET /works with polite identity + the retry/backoff of the adapter."""
    site = OpenAlexSite()
    p = dict(params)
    mailto = os.environ.get("OPENALEX_MAILTO", "").strip()
    if mailto:
        p.setdefault("mailto", mailto)
    api_key = os.environ.get("OPENALEX_API_KEY", "").strip()
    if api_key:
        p.setdefault("api_key", api_key)
    url = API + "?" + urllib.parse.urlencode(p)
    last = None
    for attempt in range(3):
        try:
            return json.loads(site._http_get(url).decode("utf-8"))
        except ValueError as exc:
            # A dropped connection can yield a partial body without raising, so
            # r.read() hands back half a document and json.loads is the first
            # thing to notice. Retrying is far cheaper than mis-parsing.
            last = exc
            time.sleep(1.5 * (attempt + 1))
        except Exception as exc:
            # HTTPError/URLError carry no URL in their str(), but do not rely
            # on that: nothing raw from urllib is allowed to escape here.
            raise RuntimeError(redact("%s: %s" % (type(exc).__name__, exc))) from None
    raise RuntimeError(redact("unparsable OpenAlex response after 3 attempts: %s" % last))


def _wid(work: dict) -> str:
    return (work.get("id") or "").rsplit("/", 1)[-1]


def _doi(work_or_paper: dict) -> str:
    raw = (work_or_paper.get("doi") or "").strip()
    return raw.lower().replace("https://doi.org/", "").strip()


def to_paper(work: dict) -> dict:
    """OpenAlex work -> the harvester's record shape (shared mapping)."""
    return OpenAlexSite()._to_paper(work)


def record_key(paper: dict) -> str:
    """Identity used to map a harvested record back onto an OpenAlex work."""
    uid = (paper.get("uid") or "").strip()
    if WID.match(uid):
        return uid
    return _doi(paper)


# ── resolution ──────────────────────────────────────────────────────────


def resolve(papers: list[dict], select: str) -> tuple[dict, int]:
    """Fetch the OpenAlex slice for a set of harvested records.

    Returns ({record_key: work}, missed_count). OpenAlex ids are tried first
    (records already harvested from `--source openalex` carry a `W` uid and
    cost no extra lookup), everything else is resolved by DOI.
    """
    by_id: dict[str, str] = {}
    by_doi: dict[str, str] = {}
    for p in papers:
        uid = (p.get("uid") or "").strip()
        if WID.match(uid):
            by_id[record_key(p)] = uid
        else:
            d = _doi(p)
            if d:
                by_doi[record_key(p)] = d

    works: list[dict] = []
    if by_id:
        for part in chunk(sorted(set(by_id.values())), BATCH):
            data = _get({"filter": "ids.openalex:" + "|".join(part),
                         "per-page": str(len(part)), "select": select})
            works += data.get("results", [])
    if by_doi:
        for part in chunk(sorted(set(by_doi.values())), BATCH):
            try:
                data = _get({"filter": "doi:" + "|".join(part),
                             "per-page": str(len(part)), "select": select})
                works += data.get("results", [])
            except Exception as exc:            # one bad DOI poisons the pipe-OR
                if "429" in str(exc):
                    # The whole API is rate-limited, not this one DOI. Retrying
                    # every identifier one by one just burns minutes.
                    raise
                logger.warning("DOI batch failed (%s); retrying one by one", exc)
                for d in part:
                    try:
                        data = _get({"filter": "doi:" + d, "per-page": "1",
                                     "select": select})
                        works += data.get("results", [])
                    except Exception:
                        continue

    wmap = {_wid(w): w for w in works}
    dmap = {_doi(w): w for w in works if _doi(w)}
    out: dict[str, dict] = {}
    missed = 0
    for p in papers:
        key = record_key(p)
        work = wmap.get(key) or dmap.get(key)
        if work:
            out[key] = work
        else:
            missed += 1
    return out, missed


# ── round 2: subject-taxonomy adjacency ─────────────────────────────────


def adjacent_terms(papers: list[dict], seeds: list[str] | None = None,
                   budget: int | None = 3, min_support: int = 2) -> dict:
    """Neighbouring subject domains + the vocabulary of those neighbours.

    Returns {
      "terms":    [{term, level, support, context, rank}]  sorted, <= budget
      "domains":  [{name, level, count, share, dominant, top_keywords}]
      "resolved": int, "missed": int, "n": int
    }
    `domains` is always computed, even when budget is 0, because it is the part
    that tells the reader *where* the corpus drifted. `budget` is a hard cap:
    0 returns no terms at all, None returns every candidate.
    """
    mapping, missed = resolve(papers, SELECT_TAXONOMY)
    if not mapping:
        raise RuntimeError("none of the %d records resolved against OpenAlex" % len(papers))

    seed_norm = [qp.normalize_term(s) for s in (seeds or []) if s]
    n = len(mapping)

    field_c, subfield_c, domain_c = Counter(), Counter(), Counter()
    topic_c, primary_c = Counter(), Counter()
    kws = {"domain": defaultdict(Counter), "field": defaultdict(Counter),
           "subfield": defaultdict(Counter)}

    for work in mapping.values():
        pt = work.get("primary_topic") or {}
        names = {}
        if pt:
            primary_c[pt.get("display_name") or ""] += 1
            for level, node in (("domain", pt.get("domain")),
                                ("field", pt.get("field")),
                                ("subfield", pt.get("subfield"))):
                name = (node or {}).get("display_name") or ""
                names[level] = name
                if name:
                    {"domain": domain_c, "field": field_c, "subfield": subfield_c}[level][name] += 1
        for topic in work.get("topics") or []:
            tname = topic.get("display_name") or ""
            if tname:
                topic_c[tname] += 1
        for k in work.get("keywords") or []:
            kname = qp.normalize_term((k or {}).get("display_name") or "")
            if kname:
                for level, name in names.items():
                    if name:
                        kws[level][name][kname] += 1

    dominant = {}
    for level, counter in (("domain", domain_c), ("field", field_c),
                           ("subfield", subfield_c)):
        dominant[level] = counter.most_common(1)[0][0] if counter else ""

    # ── where did the corpus drift? (context, always computed) ──
    domains = []
    for level in ("subfield", "field", "domain"):
        counter = {"subfield": subfield_c, "field": field_c, "domain": domain_c}[level]
        for name, cnt in counter.most_common():
            if not name or name == dominant[level]:
                continue
            domains.append({
                "name": name, "level": level, "count": cnt,
                "share": round(cnt / max(1, n), 3),
                "dominant": dominant[level],
                "top_keywords": [k for k, _ in kws[level][name].most_common(8)],
            })

    dominant_topic = primary_c.most_common(1)[0][0] if primary_c else ""

    # ── candidate expansion terms ──
    raw: list[dict] = []

    def _offer(name: str, level: str, support: int, context: str) -> None:
        term = qp.normalize_term(name)
        if not term or support < min_support:
            return
        if not qp.is_technical_term(term):
            return
        if any(term == s or term in s or s in term for s in seed_norm):
            return
        raw.append({"term": term, "level": level, "support": support,
                    "context": context, "rank": LEVEL_RANK[level]})

    # 1. vocabulary of the neighbouring sub-fields (most specific first)
    for d in domains:
        if d["level"] != "subfield":
            continue
        for kw in d["top_keywords"]:
            _offer(kw, "keyword", kws["subfield"][d["name"]][kw],
                   "adjacent sub-field: " + d["name"])

    # 2. neighbouring topics inside the corpus's own sub-field
    for topic, cnt in topic_c.most_common():
        if not topic or topic == dominant_topic:
            continue
        _offer(topic, "topic", cnt, "adjacent topic (sub-field %s)"
               % (dominant["subfield"] or "-"))

    # 3. NOTE: sub-field / field / domain *names* are deliberately NOT offered
    #    as queries. "Computer Networks and Communications" is a taxonomy
    #    bucket, not a search string - querying it drags in journal titles and
    #    publisher's notes. They stay in `domains` as context for the reader.

    raw.sort(key=lambda t: (t["rank"], -t["support"], t["term"]))
    seen, terms = set(), []
    for t in raw:
        # Check *before* appending: budget 0 must yield no terms at all, and
        # the guard is what makes the caller's `budget = per_round - already`
        # arithmetic mean what it says.
        if budget is not None and len(terms) >= budget:
            break
        if t["term"] in seen:
            continue
        seen.add(t["term"])
        terms.append(t)

    logger.info("taxonomy: %d/%d records resolved, %d adjacent domain(s), %d candidate term(s)",
                n, len(papers), len(domains), len(raw))
    return {"terms": terms, "domains": domains, "resolved": n, "missed": missed, "n": n}


# ── round 3: citation snowballing ───────────────────────────────────────


def snowball(papers: list[dict], backward: int = 60, forward: int = 40) -> dict:
    """Backward + forward citation expansion from a small set of seed papers.

    Returns {"backward": [record], "forward": [record], "seeds": [report]}.
    Backward refs are ordered by how many seeds cite them, then by their own
    citation count; forward works arrive sorted by citation count.
    """
    if not papers:
        return {"backward": [], "forward": [], "seeds": []}

    mapping, _ = resolve(papers, SELECT_SNOWBALL)
    if not mapping:
        raise RuntimeError("seed papers did not resolve against OpenAlex")
    seeds = list(mapping.values())

    # ── backward: references of the seeds ──
    ref_votes: Counter = Counter()
    for w in seeds:
        for ref in (w.get("referenced_works") or []):
            ref_votes[ref.rsplit("/", 1)[-1]] += 1

    backward_records: list[dict] = []
    if ref_votes and backward > 0:
        fetched: list[dict] = []
        for part in chunk(list(ref_votes), BATCH):
            data = _get({"filter": "ids.openalex:" + "|".join(part),
                         "per-page": str(len(part)), "select": SELECT_SNOWBALL})
            fetched += data.get("results", [])
        fetched.sort(key=lambda w: (-ref_votes.get(_wid(w), 0),
                                    -(w.get("cited_by_count") or 0)))
        backward_records = [to_paper(w) for w in fetched[:backward]]
        logger.info("backward snowball: %d references resolved, %d kept",
                    len(ref_votes), len(backward_records))

    # ── forward: works citing the seeds ──
    forward_records: list[dict] = []
    if forward > 0:
        data = _get({
            "filter": "cites:" + "|".join(_wid(w) for w in seeds),
            "per-page": str(min(forward, PER_PAGE_MAX)),
            "sort": "cited_by_count:desc",
            "select": SELECT_SNOWBALL,
        })
        forward_records = [to_paper(w) for w in data.get("results", [])[:forward]]
        logger.info("forward snowball: %d citing works kept", len(forward_records))

    report = [{
        "title": w.get("display_name") or "",
        "doi": _doi(w),
        "backward": len(w.get("referenced_works") or []),
        "forward": w.get("cited_by_count") or 0,
    } for w in seeds]

    return {"backward": backward_records, "forward": forward_records, "seeds": report}
