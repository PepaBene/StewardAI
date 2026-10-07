"""P2.2: mirror a stored statement fact into the evidence tables with the iXBRL span offsets.

`financial_statement_fact` keeps a document-level quote. The foundation tables want a character
span in the parser's canonical text. This module matches a stored fact back to the parsed iXBRL fact
that produced it (concept, period, dimensions and value must all agree, or no match is returned),
and builds the `source_evidence` / `disclosure_observation` payload from it.

`disclosure_observation` has no legacy columns, so the link back to the old row lives on the
evidence row: `table_name='financial_statement_fact'` is the legacy table and `row_ref` the legacy
key (the fact id). No schema change is needed; the writer reuses `store_span` and
`record_observation` from `ingest.py`.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import asyncpg

from steward.disclosure.financials import metric_for
from steward.disclosure.ingest import record_observation, store_span
from steward.disclosure.parse import IxFact, ParsedDocument

LEGACY_TABLE = "financial_statement_fact"
EXTRACTION_METHOD = "ixbrl"
_CURRENCY = re.compile(r"[A-Z]{3}")


@dataclass(frozen=True)
class FactEvidence:
    legacy_table: str
    legacy_key: str
    span: tuple[int, int] | None  # None when the parser gave no offsets: a named gap
    evidence: dict[str, Any]  # keyword arguments for ingest.store_span
    observation: dict[str, Any]  # keyword arguments for ingest.record_observation
    gaps: tuple[str, ...] = ()


def _bare(concept: str) -> str:
    return concept.split(":", 1)[-1]


def _members(dimensions: Mapping[str, Any] | None) -> dict[str, str]:
    pairs = (dimensions or {}).get("members") or []
    return {str(p[0]): str(p[1]) for p in pairs}


def find_ix_fact(doc: ParsedDocument, row: Mapping[str, Any]) -> IxFact | None:
    """The one parsed fact matching a stored row on concept, period, dimensions and value.
    Zero or several matches give None: an ambiguous span is not evidence."""
    want_dims = _members(row.get("dimensions"))
    value = row.get("value")
    hits: list[IxFact] = []
    for fact in doc.facts:
        if _bare(fact.name) != _bare(str(row["concept"])) or fact.start is None:
            continue
        ctx = doc.contexts.get(fact.context_ref or "")
        if ctx is None:
            continue
        end = ctx.instant if ctx.instant is not None else ctx.end
        if end != row["period_end"] or (
            ctx.instant is None and ctx.start != row.get("period_start")
        ):
            continue
        if dict(ctx.dimensions) != want_dims:
            continue
        if value is not None and fact.value is not None and Decimal(str(value)) != fact.value:
            continue
        hits.append(fact)
    return hits[0] if len(hits) == 1 else None


def build_fact_evidence(row: Mapping[str, Any], ix: IxFact | None) -> FactEvidence:
    """Payload for one fact. `row` is a financial_statement_fact row (id, issuer_id, concept,
    period_start, period_end, value, value_text, unit, currency, basis, dimensions, known_at)."""
    key = str(row["id"])
    metric = metric_for(str(row["concept"]))
    gaps: list[str] = []
    span: tuple[int, int] | None = None
    if ix is None:
        gaps.append("no matching iXBRL fact: the stored row has no span offsets")
    elif ix.start is None or ix.end is None:
        gaps.append("the iXBRL fact has no character offsets")
    else:
        span = (ix.start, ix.end)
    value = row.get("value")
    text_value = row.get("value_text")
    if ix is not None and ix.nil:
        state = "null"
    elif value is None and text_value is None:
        state = "not_disclosed"
    else:
        state = "reported"
    observation: dict[str, Any] = {
        "metric": metric.key if metric else f"concept:{row['concept']}",
        "issuer_id": row["issuer_id"],
        "known_at": row["known_at"],
        "period_start": row.get("period_start"),
        "period_end": row["period_end"],
        "is_instant": row.get("period_start") is None,
        "unit": row["unit"],
        "value": None if value is None else Decimal(str(value)),
        "text_value": text_value,
        "value_state": state,
        "original_string": ix.raw_text if ix else None,
        "scale": ix.scale if ix else 0,
        "currency": row.get("currency"),
        "basis": row.get("basis"),
        "dimensions": _members(row.get("dimensions")),
    }
    evidence = {
        "method": EXTRACTION_METHOD,
        "table_name": LEGACY_TABLE,
        "row_ref": key,
    }
    return FactEvidence(LEGACY_TABLE, key, span, evidence, observation, tuple(gaps))


async def mirror_document_facts(
    pool: asyncpg.Pool | asyncpg.Connection,
    version_id: int,
    issuer_id: int,
    known_at: dt.datetime,
    doc: ParsedDocument,
) -> int:
    """Write evidence and observations straight from the filing's own inline facts, for the
    metrics the taxonomy map knows. This covers filings whose facts were never copied into
    `financial_statement_fact` (older filings). A fact with no number, no period, no unit or no
    offsets is skipped, and a company-extension concept is never mapped by guess. Safe to repeat:
    an existing observation, and an existing span, are reused. Returns the new observations."""
    written = 0
    for index, fact in enumerate(doc.facts):
        if fact.nil or fact.value is None or fact.start is None or fact.end is None:
            continue
        metric = metric_for(fact.name)
        ctx = doc.contexts.get(fact.context_ref or "")
        unit = doc.units.get(fact.unit_ref or "")
        if metric is None or ctx is None or unit is None:
            continue
        period_end = ctx.instant if ctx.instant is not None else ctx.end
        if period_end is None:
            continue
        row = {
            "id": fact.fact_id or f"ix-{index}",
            "issuer_id": issuer_id,
            "concept": fact.name,
            "period_start": None if ctx.instant is not None else ctx.start,
            "period_end": period_end,
            "value": fact.value,
            "value_text": None,
            "unit": unit,
            "currency": unit if _CURRENCY.fullmatch(unit) else None,
            "basis": "as_filed",
            "dimensions": {"members": [list(pair) for pair in ctx.dimensions]},
            "known_at": known_at,
        }
        payload = build_fact_evidence(row, fact)
        if payload.span is None:
            continue
        evidence_id = await store_span(
            pool, version_id, doc, payload.span[0], payload.span[1], **payload.evidence
        )
        if (
            await record_observation(
                pool,
                version_id=version_id,
                evidence_id=evidence_id,
                parser_version=doc.parser_version,
                **payload.observation,
            )
            is not None
        ):
            written += 1
    return written


async def mirror_fact(
    pool: asyncpg.Pool | asyncpg.Connection,
    version_id: int,
    doc: ParsedDocument,
    row: Mapping[str, Any],
) -> int | None:
    """Write the evidence span and the observation for one stored fact. None when no span can be
    found (the gap is in `build_fact_evidence(...).gaps`) or the observation already exists."""
    payload = build_fact_evidence(row, find_ix_fact(doc, row))
    if payload.span is None:
        return None
    evidence_id = await store_span(
        pool, version_id, doc, payload.span[0], payload.span[1], **payload.evidence
    )
    return await record_observation(
        pool,
        version_id=version_id,
        evidence_id=evidence_id,
        parser_version=doc.parser_version,
        **payload.observation,
    )
