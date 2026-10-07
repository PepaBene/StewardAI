"""P5: read dependency claims for one holding as of a date.

Reads `relationship_disclosure` in place. A claim's review state is resolved as of the date from
`graph_status_log`, so a claim confirmed after `as_of` is still a suggestion in an earlier view.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import asyncpg

from steward.disclosure import dependencies as dep

type Pool = asyncpg.Pool | asyncpg.Connection

STALE_DAYS = 548  # about 18 months: a claim this old is flagged, not hidden


def _aware(value: dt.datetime | None) -> dt.datetime:
    now = value or dt.datetime.now(dt.UTC)
    return now if now.tzinfo else now.replace(tzinfo=dt.UTC)


def _s(v: Decimal | None) -> str | None:
    return None if v is None else str(v)


def _shape(row: Any) -> str:
    if row["claim_shape"]:
        return str(row["claim_shape"])
    return "anonymous" if row["anonymous_counterparty"] else "named"


async def _status_as_of(pool: Pool, ids: list[int], as_of: dt.datetime) -> dict[int, str]:
    """Status of each claim at `as_of`; absent ids have never changed."""
    if not ids:
        return {}
    rows = await pool.fetch(
        """select row_id, from_status, to_status, changed_at
             from public.graph_status_log
            where table_name = 'relationship_disclosure' and row_id = any($1::bigint[])
            order by row_id, changed_at, id""",
        ids,
    )
    out: dict[int, str] = {}
    seen_first: set[int] = set()
    for r in rows:
        rid = int(r["row_id"])
        if r["changed_at"] <= as_of:
            out[rid] = r["to_status"]
        elif rid not in seen_first and rid not in out:
            out[rid] = r["from_status"] or "suggested"
        seen_first.add(rid)
    return out


async def dependency_view(
    pool: Pool, ticker: str, as_of: dt.datetime | None = None, *, shock: Decimal = Decimal("0.2")
) -> dict[str, Any]:
    when = _aware(as_of)
    rows = await pool.fetch(
        """select id, ticker, relationship, direction, counterparty_key, counterparty_name,
                  anonymous_counterparty, reported_share, fiscal_period, valid_from, valid_to,
                  evidence_id, passage, source_url, status, known_at, claim_shape,
                  counterparty_count, denominator_kind, period_end
             from public.relationship_disclosure
            where ticker = $1 and known_at <= $2
            order by id""",
        ticker,
        when,
    )
    states = await _status_as_of(pool, [int(r["id"]) for r in rows], when)
    deps: list[dep.Dependency] = []
    raw: dict[int, Any] = {}
    for r in rows:
        shape = _shape(r)
        d = dep.Dependency(
            id=int(r["id"]),
            reporter=r["ticker"],
            relationship=r["relationship"],
            shape=shape,  # type: ignore[arg-type]
            status=states.get(int(r["id"]), r["status"]),
            known_at=r["known_at"],
            passage=r["passage"],
            label=None if shape == "named" else r["counterparty_name"],
            counterparty_key=r["counterparty_key"] if shape == "named" else None,
            counterparty_name=r["counterparty_name"] if shape == "named" else None,
            counterparty_count=r["counterparty_count"],
            share=r["reported_share"],
            denominator=r["denominator_kind"],
            period_end=r["period_end"],
            fiscal_period=r["fiscal_period"],
            valid_from=r["valid_from"],
            valid_to=r["valid_to"],
            evidence_id=r["evidence_id"],
        )
        deps.append(d)
        raw[d.id] = r
    live = dep.visible(deps, when)
    claims = []
    for d in live:
        r = raw[d.id]
        age = max(0, (when - d.known_at).days)
        claims.append(
            {
                "id": d.id,
                "relationship": d.relationship,
                "shape": d.shape,
                "status": d.status,
                "counterparty": d.counterparty_name or d.label,
                "counterparty_key": d.counterparty_key,
                "counterparty_count": d.counterparty_count,
                "share": _s(d.share),
                "denominator": d.denominator,
                "period_end": d.period_end,
                "valid_from": d.valid_from,
                "valid_to": d.valid_to,
                "known_at": d.known_at,
                "passage": d.passage,
                "source_url": r["source_url"],
                "evidence_id": d.evidence_id,
                "problems": dep.problems(d),
                "screen": dep.screen_passage(d.passage),
                "stale": age > STALE_DAYS,
            }
        )
    conc = [
        {
            "relationship": c.relationship,
            "denominator": c.denominator,
            "period": c.period,
            "entries": [
                {
                    "id": e.id,
                    "label": e.label,
                    "shape": e.shape,
                    "share": str(e.share),
                    "evidence_id": e.evidence_id,
                    "known_at": e.known_at,
                }
                for e in c.entries
            ],
            "groups": [
                {"id": g.id, "label": g.label, "count": g.count, "share": str(g.share)}
                for g in c.groups
            ],
            "lower_bound": str(c.lower_bound),
            "reported_floor": str(c.reported_floor),
            "unknown_remainder": str(c.unknown_remainder),
            "largest": c.largest.label if c.largest else None,
            "exceeds_whole": c.exceeds_whole,
            "suggested_not_counted": c.suggested_not_counted,
            "notes": list(c.notes),
        }
        for c in dep.concentration(deps, when)
    ]
    # scenario edges: named customers of this issuer, by share of total revenue
    edges = [
        dep.Edge(
            d.id,
            d.reporter,
            d.counterparty_key,
            d.share if d.denominator == "total_revenue" else None,
            d.status,
            d.known_at,
            d.valid_to,
        )
        for d in live
        if d.relationship == "customer" and d.shape == "named" and d.counterparty_key
    ]
    scenarios = []
    for e in {x.customer for x in edges}:
        s = dep.shock_scenario(edges, e, shock, when)
        scenarios.append(
            {
                "origin": s.origin,
                "shock": str(s.shock),
                "impacts": [
                    {
                        "entity": i.entity,
                        "hop": i.hop,
                        "lower": str(i.lower),
                        "upper": str(i.upper),
                        "coverage": str(i.coverage),
                        "via": list(i.via),
                        "source_age_days": i.source_age_days,
                    }
                    for i in s.impacts
                ],
                "non_quantified": [
                    {
                        "supplier": n.supplier,
                        "customer": n.customer,
                        "edge_id": n.edge_id,
                        "note": n.note,
                    }
                    for n in s.non_quantified
                ],
                "notes": list(s.notes),
            }
        )
    notes: list[str] = []
    if not rows:
        notes.append("No relationship claims are known for this holding by that date.")
    return {
        "ticker": ticker,
        "as_of": when,
        "claims": claims,
        "concentration": conc,
        "scenarios": scenarios,
        "notes": notes,
    }
