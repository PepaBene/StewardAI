"""P8.6: an answer built from stored, verified evidence, never from a model's memory.

Retrieval reads verified claims and observations (`asof.py`, so only what was known at the date) and
keeps those that share a word with the question. The text is a deterministic template over that
evidence: each sentence names its source and date, and the answer states what it could not find.
No repo LLM helper is used, so there is nothing to phrase with; if one is added later it may only
rephrase, and `numbers_grounded` must pass on its output before it is shown. A total is never
computed here: figures are quoted from the filing as stored.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from typing import Any

import asyncpg

from steward.disclosure import asof

type Pool = asyncpg.Pool | asyncpg.Connection

_WORD = re.compile(r"[a-z][a-z0-9&-]{3,}")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_STOP = frozenset(
    "what which when where does have been from that this with their about into than "
    "over under were your".split()
)
MAX_EVIDENCE = 12


def keywords(question: str) -> set[str]:
    return {w for w in _WORD.findall(question.lower()) if w not in _STOP}


def numbers_in(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUMBER.findall(text)}


def numbers_grounded(text: str, evidence_texts: Iterable[str]) -> bool:
    """Every number in `text` must occur in some retrieved evidence string."""
    allowed: set[str] = set()
    for e in evidence_texts:
        allowed |= numbers_in(e)
    return numbers_in(text) <= allowed


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
    hits.sort(key=lambda t: (-t[0], t[1]["known_at"], t[1]["id"]))
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
