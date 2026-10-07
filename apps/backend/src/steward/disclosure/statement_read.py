"""P2.7: read as-filed statement history for one issuer, as of a date.

Reads `financial_statement_fact` (the existing point-in-time store; nothing is copied) and runs the
pure logic in `financials.py`. Every derived number names its inputs and formula version, so the
screen can show where it came from. A value that failed a check is returned with the check, never
dropped and never silently corrected.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any

import asyncpg

from steward.disclosure.financials import (
    TAXONOMY_VERSION,
    Check,
    FiledFact,
    HistoryPoint,
    SegmentMember,
    build_segment_bridge,
    check_free_cash_flow,
    check_gross_profit,
    metric_for,
    release_gate,
    statement_history,
)

type Pool = asyncpg.Pool | asyncpg.Connection

FORMULA_VERSION = "formulas-1"
_STATEMENTS = ("income", "cash_flow", "balance_sheet", "shares")


def _aware(value: dt.datetime | None) -> dt.datetime:
    now = value or dt.datetime.now(dt.UTC)
    return now if now.tzinfo else now.replace(tzinfo=dt.UTC)


def _check_json(c: Check) -> dict[str, Any]:
    return {
        "name": c.name,
        "status": c.status,
        "detail": c.detail,
        "expected": None if c.expected is None else str(c.expected),
        "actual": None if c.actual is None else str(c.actual),
        "tolerance": str(c.tolerance),
    }


def _point_json(p: HistoryPoint) -> dict[str, Any]:
    return {
        "metric": p.metric,
        "period_end": p.period_end.isoformat(),
        "value": None if p.value is None else str(p.value),
        "accession": p.accession,
        "known_at": p.known_at.isoformat(),
        "restated": p.restated,
        "original_value": None if p.original_value is None else str(p.original_value),
    }


async def statements(
    pool: Pool, issuer_id: int, as_of: dt.datetime | None = None
) -> dict[str, Any]:
    """Annual as-filed history, derived free cash flow and arithmetic checks, known by `as_of`."""
    when = _aware(as_of)
    rows = await pool.fetch(
        """select concept, period_start, period_end, value, known_at, accession
             from public.financial_statement_fact
            where issuer_id = $1 and known_at <= $2 and value is not null
              and statement = any($3::text[])
              and coalesce(dimensions->'members', '[]'::jsonb) = '[]'::jsonb""",
        issuer_id,
        when,
        list(_STATEMENTS),
    )
    facts: list[FiledFact] = []
    unmapped = 0
    for r in rows:
        metric = metric_for(r["concept"])
        if metric is None:
            unmapped += 1
            continue
        facts.append(
            FiledFact(
                metric.key,
                r["period_start"],
                r["period_end"],
                Decimal(str(r["value"])),
                _aware(r["known_at"]),
                r["accession"],
            )
        )
    history = statement_history(facts, when)
    by_period: dict[dt.date, dict[str, HistoryPoint]] = {}
    for p in history:
        by_period.setdefault(p.period_end, {})[p.metric] = p

    derived: list[dict[str, Any]] = []
    checks: dict[str, list[dict[str, Any]]] = {}
    gate: dict[str, bool] = {}
    for end, metrics in sorted(by_period.items()):
        val = {k: v.value for k, v in metrics.items()}
        period_checks = [
            check_gross_profit(
                val.get("revenue"), val.get("cost_of_revenue"), val.get("gross_profit")
            )
        ]
        op, cap = val.get("operating_cash_flow"), val.get("capex")
        if op is not None and cap is not None and cap >= 0:
            fcf = op - cap
            derived.append(
                {
                    "metric": "free_cash_flow",
                    "period_end": end.isoformat(),
                    "value": str(fcf),
                    "formula": "operating_cash_flow - capex",
                    "formula_version": FORMULA_VERSION,
                    "inputs": [
                        metrics["operating_cash_flow"].accession,
                        metrics["capex"].accession,
                    ],
                }
            )
            if "free_cash_flow" in val:
                period_checks.append(check_free_cash_flow(op, cap, val["free_cash_flow"]))
        elif op is not None and cap is not None:
            period_checks.append(check_free_cash_flow(op, cap, op - cap))  # sign needs a reviewer
        checks[end.isoformat()] = [_check_json(c) for c in period_checks]
        gate[end.isoformat()] = release_gate(period_checks)[0]

    return {
        "issuer_id": issuer_id,
        "as_of": when.isoformat(),
        "taxonomy_version": TAXONOMY_VERSION,
        "formula_version": FORMULA_VERSION,
        "history": [_point_json(p) for p in history],
        "derived": derived,
        "checks": checks,
        "usable_for_valuation": gate,
        "unmapped_concepts": unmapped,
    }


async def segment_bridge(
    pool: Pool, issuer_id: int, period_end: dt.date, as_of: dt.datetime | None = None
) -> dict[str, Any]:
    """Segment members for one annual period, each axis bridged to reported revenue on its own."""
    when = _aware(as_of)
    total_row = await pool.fetchrow(
        """select value from public.financial_statement_fact
            where issuer_id=$1 and period_end=$2 and known_at<=$3 and statement='income'
              and concept = any($4::text[])
              and coalesce(dimensions->'members', '[]'::jsonb) = '[]'::jsonb
            order by known_at desc limit 1""",
        issuer_id,
        period_end,
        when,
        ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
    )
    if total_row is None or total_row["value"] is None:
        return {
            "period_end": period_end.isoformat(),
            "total": None,
            "axes": [],
            "notes": [
                "No reported revenue is known for this period, so segments cannot be bridged."
            ],
        }
    seg = await pool.fetch(
        """select distinct on (concept, dimensions::text) dimensions::text dims, value
             from public.financial_statement_fact
            where issuer_id=$1 and period_end=$2 and known_at<=$3 and statement='segment'
              and value is not null
            order by concept, dimensions::text, known_at desc""",
        issuer_id,
        period_end,
        when,
    )
    members: list[SegmentMember] = []
    for r in seg:
        pairs = json.loads(r["dims"]).get("members") or []
        if len(pairs) != 1:  # a cross-tab (two axes at once) is never placed on a single axis
            continue
        axis, member = pairs[0]
        label = str(member)
        members.append(
            SegmentMember(
                axis=str(axis),
                member=label,
                value=Decimal(str(r["value"])),
                eliminations="elimination" in label.lower() or "reconcil" in label.lower(),
            )
        )
    bridge = build_segment_bridge(Decimal(str(total_row["value"])), members)
    return {
        "period_end": period_end.isoformat(),
        "total": str(bridge.total),
        "axes": [
            {
                "axis": a.axis,
                "members": [
                    {"member": m.member, "value": str(m.value), "eliminations": m.eliminations}
                    for m in a.members
                ],
                "members_sum": str(a.members_sum),
                "eliminations": str(a.eliminations),
                "unallocated": str(a.unallocated),
                "reconciles": a.reconciles,
            }
            for a in bridge.axes
        ],
        "notes": list(bridge.notes),
    }
