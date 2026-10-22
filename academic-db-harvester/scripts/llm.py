#!/usr/bin/env python3
"""LLM client and the three decision prompts of the multi-round loop.

The harvester never guesses. Three decisions in the pipeline are judgement
calls, and this module lets an LLM make them inside a single run instead of
the operator shuttling files back and forth:

    round 1   goal        -> English seed terms
    round 2   candidates  -> which expansion terms to run
    round 4   corpus      -> RELEVANT / MARGINAL / OFF_TOPIC / INVALID per paper

Everything degrades to the file-based workflow when the LLM is unavailable:
no key, no endpoint, a bad response, a rate limit - each one falls back
rather than failing the run, and the run says which path it took.

Configuration comes from the environment, never from this file:

    OPENAI_BASE_URL   e.g. https://opencode.ai/zen/go/v1
    OPENAI_API_KEY    the key for that endpoint
    OPENAI_MODEL      e.g. mimo-v2.5, glm-5.3

Any OpenAI-compatible /v1 endpoint works. The `x-opencode-session` header is
sent on every request because the opencode gateway refuses to route without
it; other gateways ignore unknown headers.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils import setup_logger

logger = setup_logger("llm")

RETRY = 3
TIMEOUT = 180
ABSTRACT_CHARS = 320


def _redact(text: str) -> str:
    return re.sub(r"(sk-|oc_sk_|key-)[A-Za-z0-9_\-]{8,}", r"\1<redacted>", str(text))


class LLM:
    """A minimal OpenAI-compatible chat client."""

    def __init__(self, base_url: str, api_key: str, model: str,
                 session: str = "", timeout: int = TIMEOUT):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or ""
        self.session = session or "harv-%d" % int(time.time())
        self.timeout = timeout
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    @classmethod
    def from_env(cls, base_url: str = "", api_key: str = "", model: str = "",
                 session: str = "") -> "LLM":
        return cls(base_url or os.environ.get("OPENAI_BASE_URL", ""),
                   api_key or os.environ.get("OPENAI_API_KEY", ""),
                   model or os.environ.get("OPENAI_MODEL", ""),
                   session or os.environ.get("LLM_SESSION_ID", ""))

    @property
    def available(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    def _endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/chat/completions"

    def chat(self, system: str, user: str, max_tokens: int = 2000,
             temperature: float = 0.1) -> str:
        """One chat completion. Returns the assistant text, or "" on failure."""
        if not self.available:
            raise RuntimeError("LLM not configured (need OPENAI_BASE_URL, "
                               "OPENAI_API_KEY and OPENAI_MODEL)")
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "application/json",
            "x-opencode-session": self.session,
        }
        last = None
        for attempt in range(RETRY):
            req = urllib.request.Request(self._endpoint(), data=body, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.loads(r.read().decode("utf-8"))
                self.calls += 1
                usage = data.get("usage") or {}
                self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
                self.completion_tokens += int(usage.get("completion_tokens") or 0)
                return (data["choices"][0]["message"].get("content") or "").strip()
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode("utf-8", "replace")[:300]
                except Exception:
                    pass
                last = "HTTP %s %s" % (e.code, detail)
                if e.code in (400, 401, 403):
                    break                       # not worth retrying
                time.sleep(1.5 * (attempt + 1))
            except Exception as e:
                last = "%s: %s" % (type(e).__name__, e)
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(_redact("LLM call failed (%s)" % last))

    def chat_json(self, system: str, user: str, max_tokens: int = 2000,
                  temperature: float = 0.1) -> dict:
        """A chat completion that must come back as JSON."""
        text = self.chat(system, user, max_tokens, temperature)
        return parse_json(text)

    def stats(self) -> dict:
        return {"calls": self.calls, "model": self.model,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens}


def parse_json(text: str) -> dict:
    """Pull a JSON object out of a reply that may be fenced or chatty."""
    if not text:
        raise RuntimeError("empty LLM reply")
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start < 0:
        raise RuntimeError("no JSON object in LLM reply: %s" % _redact(text[:200]))
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError as exc:
                    raise RuntimeError("malformed JSON from LLM: %s" % exc)
    raise RuntimeError("unterminated JSON object in LLM reply")


# ── decision 1: goal -> seed terms ──────────────────────────────────────

PLAN_TERMS_SYSTEM = (
    "You are a research librarian preparing a systematic literature search. "
    "You answer with JSON only."
)

PLAN_TERMS_USER = """Research goal:
{goal}
{extra}
The target index is English-language and indexes English literature. If the goal
is not in English, translate it into the research field's own English terminology.

Give {n} search terms that would retrieve the literature a researcher on this goal
actually wants, ordered from most precise to broadest. Use the field's own
vocabulary (for example "cyber range", not "network security training
environment"), and keep each term short - a noun phrase, not a sentence.

Return JSON only, in this shape:
{{"terms": [{{"term": "...", "why": "..."}}], "reasoning": "..."}}"""


def plan_terms(llm: LLM, goal: str, keyword: str = "", n: int = 4) -> list[dict]:
    """Round 1: the goal turned into English seed terms by the LLM."""
    extra = ("An explicit keyword was also given: %s" % keyword) if keyword else ""
    data = llm.chat_json(PLAN_TERMS_SYSTEM,
                          PLAN_TERMS_USER.format(goal=goal, extra=extra, n=n))
    out = []
    for item in data.get("terms") or []:
        if isinstance(item, str):
            term, why = item.strip(), ""
        elif isinstance(item, dict):
            term = str(item.get("term") or "").strip()
            why = str(item.get("why") or "").strip()
        else:
            continue
        if term:
            out.append({"term": term, "why": why})
    if not out:
        raise RuntimeError("LLM returned no usable terms")
    return out[:n]


# ── decision 2: which expansion terms to run ────────────────────────────

CHOOSE_TERMS_SYSTEM = (
    "You decide which literature-search expansion terms are worth running next. "
    "You answer with JSON only."
)

CHOOSE_TERMS_USER = """Research goal:
{goal}

The first round already retrieved {k} papers. These are candidate expansion terms
mined from those papers, each with the papers that support it:

{candidates}

Choose which of these to search next. Prefer a term that opens a genuinely
adjacent part of the field over a near-synonym of what was already searched.
Drop a term that would retrieve the same population again, and drop a term that
is too generic to be worth a search slot.

Return JSON only, in this shape:
{{"chosen": ["term", ...], "reasoning": "..."}}"""


def choose_terms(llm: LLM, goal: str, candidates: list[dict], k: int = 3) -> list[str]:
    """Round 2: the LLM picks which candidate terms to actually run."""
    if not candidates:
        return []
    lines = []
    for c in candidates:
        ev = c.get("evidence") or []
        first = ev[0] if isinstance(ev, list) and ev else ""
        lines.append('- "%s"  (supported by %s paper(s); %s)'
                     % (c.get("term", ""), c.get("support", "?"),
                        (first or "")[:90]))
    data = llm.chat_json(CHOOSE_TERMS_SYSTEM,
                          CHOOSE_TERMS_USER.format(goal=goal, k=k,
                                                   candidates="\n".join(lines)))
    chosen = data.get("chosen") or data.get("terms") or []
    out = []
    known = {c.get("term", "").strip().lower() for c in candidates}
    for item in chosen:
        term = (item.get("term") if isinstance(item, dict) else item)
        term = str(term or "").strip()
        # Only accept a term the LLM did not invent.
        if term and term.lower() in known and term.lower() not in {t.lower() for t in out}:
            out.append(term)
    if not out:
        raise RuntimeError("LLM chose no candidate terms")
    return out


# ── decision 3: per-paper verdicts ──────────────────────────────────────

LABEL_SYSTEM = (
    "You screen retrieved academic literature for relevance to a research goal. "
    "You answer with JSON only."
)

LABEL_USER = """Research goal:
{goal}

For each paper below assign exactly one verdict:

RELEVANT  - the paper directly addresses the research goal
MARGINAL  - adjacent to the goal; worth keeping as background
OFF_TOPIC - retrieved, but not about the goal
INVALID   - not a usable paper at all: publisher's note, editorial, front
            matter, a journal title scraped as a record, a conference preface,
            or a retracted/withdrawn paper

Judge from the content - title, venue, keywords and abstract - not from whether
the title merely looks plausible.

Papers:
{papers}

Return JSON only, in this shape:
{{"verdicts": [{{"index": 1, "verdict": "RELEVANT", "reason": "one short clause"}}]}}
Use the same index numbers you were given. Label every paper."""


def _paper_block(papers: list[dict]) -> str:
    out = []
    for p in papers:
        bits = ["[%d] %s" % (p.get("index", 0), p.get("title", ""))]
        meta = " | ".join(x for x in (str(p.get("year") or ""),
                                      p.get("journal") or "",
                                      ("doi:" + p["doi"]) if p.get("doi") else "")
                         if x)
        if meta:
            bits.append("    " + meta)
        if p.get("keywords"):
            bits.append("    keywords: " + "; ".join(p["keywords"][:6]))
        abstract = (p.get("abstract") or "")[:ABSTRACT_CHARS]
        if abstract:
            bits.append("    abstract: " + abstract)
        out.append("\n".join(bits))
    return "\n".join(out)


def label_papers(llm: LLM, goal: str, papers: list[dict],
                 batch: int = 12) -> list[dict]:
    """Round 4: the LLM labels the corpus. Returns one verdict per paper."""
    verdicts: list[dict] = []
    for start in range(0, len(papers), batch):
        chunk = papers[start:start + batch]
        data = llm.chat_json(LABEL_SYSTEM,
                              LABEL_USER.format(goal=goal,
                                                papers=_paper_block(chunk)),
                              max_tokens=1200 + 60 * len(chunk))
        raw = data.get("verdicts") or data.get("papers") or []
        seen = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("index"))
            except (TypeError, ValueError):
                continue
            verdict = str(item.get("verdict") or "").strip().upper()
            if verdict not in ("RELEVANT", "MARGINAL", "OFF_TOPIC", "INVALID"):
                continue
            verdicts.append({"index": idx, "verdict": verdict,
                              "reason": str(item.get("reason") or "").strip()})
            seen.add(idx)
        # Papers the LLM skipped stay unreviewed rather than being guessed.
        for p in chunk:
            if p.get("index") not in seen:
                verdicts.append({"index": p.get("index"), "verdict": "UNREVIEWED",
                                 "reason": "not returned by the LLM"})
    return verdicts
