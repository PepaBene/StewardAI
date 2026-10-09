"""P8.6: an answer built from stored, verified evidence, never from a model's memory.

Retrieval reads verified claims and observations (`asof.py`, so only what was known at the date) and
keeps those that share a word with the question. The text is a deterministic template over that
evidence: each sentence names its source and date, and the answer states what it could not find.
`phrase` may rewrite that text with a configured model, but only reword it: its output is shown
only if `numbers_grounded` passes, otherwise the template stays. A total is never computed here:
figures are quoted from the filing as stored.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

import asyncpg

from steward.disclosure import asof

type Pool = asyncpg.Pool | asyncpg.Connection

_WORD = re.compile(r"[a-z][a-z0-9&-]{3,}")
_STOP = frozenset(
    "what which when where does have been from that this with their about into than "
    "over under were your".split()
)
MAX_EVIDENCE = 12


def keywords(question: str) -> set[str]:
    return {w for w in _WORD.findall(question.lower()) if w not in _STOP}


_MONTHS = (
    "january february march april may june july august september october november december".split()
)
_MONTH_RX = "|".join(m[:3] + r"[a-z]*\.?" for m in _MONTHS)
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_TEXT_DATE_MDY = re.compile(
    rf"\b({_MONTH_RX})\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.IGNORECASE
)
_TEXT_DATE_DMY = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_RX})\s+(\d{{4}})\b", re.IGNORECASE
)
_QUANTITY = re.compile(
    r"(?P<open>\()?(?P<neg>(?<![\w.])-)?[$€£]?(?P<num>\d[\d,]*(?:\.\d+)?)(?P<close>\))?"
    r"(?:\s?(?P<mag>thousand|million|billion|trillion|bn|mm|m|k)(?![A-Za-z]))?",
    re.IGNORECASE,
)
_MAGNITUDE = {
    "thousand": Decimal(10) ** 3,
    "k": Decimal(10) ** 3,
    "million": Decimal(10) ** 6,
    "mm": Decimal(10) ** 6,
    "m": Decimal(10) ** 6,
    "billion": Decimal(10) ** 9,
    "bn": Decimal(10) ** 9,
    "trillion": Decimal(10) ** 12,
}
_AMBIGUOUS_COMMA = re.compile(r"\d+,\d{1,2}")

type Token = tuple[str, Decimal | str]


def _month_number(word: str) -> int | None:
    prefix = word.lower()[:3]
    for i, m in enumerate(_MONTHS, 1):
        if m[:3] == prefix:
            return i
    return None


def _iso(y: str, m: int | None, d: str) -> str | None:
    if m is None:
        return None
    try:
        return dt.date(int(y), m, int(d)).isoformat()
    except ValueError:
        return None


def dates_in(text: str) -> tuple[set[str], str]:
    """Calendar dates in `text` as ISO strings, and the text with those spans blanked out so their
    digits are never read as separate numbers. A date is one token: it must appear whole."""
    found: set[str] = set()

    def blank(m: re.Match[str], iso: str | None) -> str:
        if iso:
            found.add(iso)
        return " " * len(m.group(0))

    text = _ISO_DATE.sub(lambda m: blank(m, _iso(m[1], int(m[2]), m[3])), text)
    text = _TEXT_DATE_MDY.sub(lambda m: blank(m, _iso(m[3], _month_number(m[1]), m[2])), text)
    text = _TEXT_DATE_DMY.sub(lambda m: blank(m, _iso(m[3], _month_number(m[2]), m[1])), text)
    return found, text


def number_tokens(text: str) -> set[Token]:
    """Quantities in `text` with sign (parentheses mean negative) and magnitude word applied, so
    "(1.5 billion)" is -1500000000. Dates are separate whole tokens. "12,5" (one comma, one or two
    digits after it) could be 12.5 or 12 500, so it is kept as its exact string."""
    out: set[Token] = set()
    dates, rest = dates_in(text)
    out |= {("date", d) for d in dates}
    for m in _QUANTITY.finditer(rest):
        raw = m.group("num").rstrip(".")
        if _AMBIGUOUS_COMMA.fullmatch(raw):
            out.add(("raw", raw))
            continue
        value = Decimal(raw.replace(",", ""))
        value *= _MAGNITUDE.get((m.group("mag") or "").lower(), Decimal(1))
        if m.group("neg") or (m.group("open") and m.group("close")):
            value = -value
        out.add(("num", value))
    return out


def numbers_in(text: str) -> set[str]:
    return {str(v) for _, v in number_tokens(text)}


def numbers_grounded(text: str, evidence_texts: Iterable[str]) -> bool:
    """Every number and date in `text` must occur, with the same sign and magnitude, in some
    retrieved evidence string."""
    allowed: set[Token] = set()
    for e in evidence_texts:
        allowed |= number_tokens(e)
    return number_tokens(text) <= allowed


def _evidence_row(kind: str, r: dict[str, Any]) -> dict[str, Any]:
    env = r["envelope"]
    label = r["metric"] if kind == "observation" else (r["claim_type"] or "claim")
    counter = r.get("object_label") or r.get("subject_label")
    value = r.get("original_string") if kind == "observation" else None
    return {
        "kind": kind,
        "id": r["id"],
        "label": label,
        "counterparty": counter,
        "value": value,
        "period_end": r.get("period_end"),
        "quote": r.get("quote"),
        "section": r.get("section"),
        "known_at": r["known_at"],
        "source": env["source"],
        "document_url": env["document_url"],
        "anonymous": bool(r.get("object_anonymous")),
    }


def select(question: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    words = keywords(question)
    if not words:
        return []
    hits = []
    for r in rows:
        hay = " ".join(
            str(r.get(k) or "") for k in ("label", "counterparty", "quote", "section")
        ).lower()
        score = sum(w in hay for w in words)
        if score:
            hits.append((score, r))
    hits.sort(key=lambda t: (-t[0], -t[1]["known_at"].timestamp(), -int(t[1]["id"])))
    return [r for _, r in hits[:MAX_EVIDENCE]]


def compose(
    question: str, ticker: str, as_of: dt.datetime, evidence: list[dict[str, Any]]
) -> dict[str, Any]:
    """The deterministic answer: sentences from evidence only, plus what is not covered."""
    limits = [
        "Only verified facts known by the date are used; machine suggestions are not.",
        "Consensus or estimates are not asserted here; see the estimates page for stored sources.",
    ]
    if not evidence:
        return {
            "text": (
                f"No verified stored evidence for {ticker} matches this question "
                f"as of {as_of.date()}."
            ),
            "limits": [*limits, "Absence of evidence here is not proof that nothing exists."],
            "grounded": True,
        }
    lines = []
    for e in evidence:
        who = "an undisclosed counterparty" if e["anonymous"] else (e["counterparty"] or "")
        val = f" reported as {e['value']}" if e["value"] else ""
        subject = f"{e['label']}{(' with ' + who) if who else ''}"
        lines.append(f"{subject}{val}, from {e['source']}, known {e['known_at'].date()}.")
    text = f"For {ticker} as of {as_of.date()}: " + " ".join(lines)
    quoted = [
        " ".join(str(e[k] or "") for k in ("value", "quote", "counterparty", "label", "source"))
        for e in evidence
    ]
    dates = [f"{e['known_at'].date()} {as_of.date()}" for e in evidence]
    grounded = numbers_grounded(text, [*quoted, *dates, ticker])
    return {"text": text if grounded else None, "limits": limits, "grounded": grounded}


PHRASE_SYSTEM = (
    "You rewrite a short factual answer so it reads smoothly. Use only the facts, numbers, dates "
    "and sources in the text you are given. Do not add, infer, compute or round any figure, and do "
    "not add any fact. Keep every source and date. Reply with the rewritten text only."
)


async def phrase(
    llm: Any, model: str, composed: dict[str, Any], evidence: list[dict[str, Any]]
) -> str | None:
    """Rephrase the templated answer with a model, or None to keep the template.

    The model may only reword: its output is used only if every number in it appears in the
    retrieved evidence or the template, and it adds no number the evidence lacks. A model that is
    not configured, a call that fails or an answer that fails the check all fall back to the
    template, so the shown text is never less grounded than the template."""
    from steward.llm.types import LLMError, Message, Request

    template = composed.get("text")
    if not template or not evidence:
        return None
    try:
        if not llm.configured(model):
            return None
        done = await llm.complete(
            Request(
                model=model,
                system=PHRASE_SYSTEM,
                messages=(Message(role="user", text=template),),
                max_tokens=600,
            )
        )
    except (LLMError, ValueError):
        return None
    text = (done.text or "").strip()
    if not text:
        return None
    quoted = [
        " ".join(str(e[k] or "") for k in ("value", "quote", "counterparty", "label", "source"))
        for e in evidence
    ]
    if not numbers_grounded(text, [template, *quoted]):
        return None
    return text


async def answer(
    pool: Pool, ticker: str, issuer_id: int | None, question: str, as_of: dt.datetime
) -> dict[str, Any]:
    if issuer_id is None:
        return {
            "evidence": [],
            **compose(question, ticker, as_of, []),
            "retrieved": 0,
            "note": "This ticker is not linked to an issuer, so no evidence can be read.",
        }
    claims = await asof.claims_as_of(pool, issuer_id, as_of, verified_only=True, limit=200)
    obs = await asof.observations_as_of(pool, issuer_id, as_of, verified_only=True, limit=200)
    rows = [_evidence_row("claim", c) for c in claims] + [
        _evidence_row("observation", o) for o in obs
    ]
    chosen = select(question, rows)
    return {
        "evidence": chosen,
        **compose(question, ticker, as_of, chosen),
        "retrieved": len(rows),
        "note": None,
    }
