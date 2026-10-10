"""P2.7: read as-filed statement history for one issuer, as of a date.

Reads `financial_statement_fact` (the existing point-in-time store; nothing is copied) and runs the
pure logic in `financials.py`, `formulas.py` and `note_store.py`. Every derived number names its
inputs and formula version, so the screen can show where it came from. A value that failed a check
is returned with the check, never dropped and never silently corrected.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from decimal import Decimal
from typing import Any

import asyncpg

from steward.disclosure.financials import (
    SEGMENT_METRICS,
    TAXONOMY_VERSION,
    AxisBridge,
    Check,
    FiledFact,
    HistoryPoint,
    MetricBridge,
    SegmentFact,
    build_segment_report,
    check_free_cash_flow,
    check_gross_profit,
    concept_choice,
    metric_for,
    release_gate,
    segment_metric_for,
    statement_history,
)
from steward.disclosure.formulas import FormulaInput, FormulaResult, compute_formulas
from steward.disclosure.note_store import StoredNote, load_note_rows, note_checks

type Pool = asyncpg.Pool | asyncpg.Connection

FORMULA_VERSION = "formulas-1"
_STATEMENTS = ("income", "cash_flow", "balance_sheet", "shares")
_FORMULA_METRICS = (
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "pretax_income",
    "income_tax",
    "operating_cash_flow",
    "total_equity",
    "total_debt",
    "cash",
)


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


def _no_note_data() -> dict[str, Any]:
    """An explicit not-run entry. It is not a pass; it does not by itself block the gate, because
    a filing without the notes is a coverage gap, not a failed check."""
    return {
        "name": "note_data",
        "status": "needs_review",
        "detail": "not run: no note data (debt, leases and repurchases were not checked)",
        "expected": None,
        "actual": None,
        "tolerance": "0",
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
        "fact_id": p.fact_id,
    }


def _formula_json(end: dt.date, r: FormulaResult) -> dict[str, Any]:
    return {
        "formula": r.formula,
        "version": r.version,
        "table_version": r.table_version,
        "expression": r.expression,
        "rule": r.rule,
        "period_end": end.isoformat(),
        "value": None if r.value is None else str(r.value),
        "reason": r.reason,
        "coverage": {"present": r.coverage_present, "required": r.coverage_required},
        "inputs": [
            {
                "metric": i.metric,
                "value": None if i.value is None else str(i.value),
                "accession": i.accession,
                "fact_id": i.fact_id,
                "source": i.source,
            }
            for i in r.inputs
        ],
    }


def _year_one_debt(notes: list[StoredNote]) -> FormulaInput | None:
    for n in notes:
        if n.schedule == "debt_maturity" and n.bucket == "year_1" and n.status == "extracted":
            return FormulaInput("debt_due_year_1", n.value, n.accession, n.fact_id, "note")
    return None


async def statements(
    pool: Pool, issuer_id: int, as_of: dt.datetime | None = None
) -> dict[str, Any]:
    """Annual as-filed history, derived figures, checks, formulas and the valuation gate, known by
    `as_of`."""
    when = _aware(as_of)
    rows = await pool.fetch(
        """select id, concept, period_start, period_end, value, known_at, accession, currency
             from public.financial_statement_fact
            where issuer_id = $1 and known_at <= $2 and value is not null
              and statement = any($3::text[])
              and coalesce(dimensions->'members', '[]'::jsonb) = '[]'::jsonb
            order by known_at, accession, id""",
        issuer_id,
        when,
        list(_STATEMENTS),
    )
    # A filer can tag one line in two currencies (a 20-F with a convenience translation). The
    # history uses the currency most of its money facts are reported in and drops the others,
    # so two currencies are never read as a restatement or added together.
    currencies = Counter(r["currency"] for r in rows if r["currency"])
    reporting = currencies.most_common(1)[0][0] if currencies else None
    other_currency = sum(
        1 for r in rows if r["currency"] and reporting and r["currency"] != reporting
    )
    rows = [r for r in rows if not (r["currency"] and r["currency"] != reporting)]
    facts: list[FiledFact] = []
    unmapped = 0
    # Two concepts for one metric and period (net income, debt, leases) would make "latest"
    # arbitrary and read as a restatement: keep the metric's preferred concept per period.
    choice = concept_choice(
        (r["concept"], (m.key, r["period_start"], r["period_end"]))
        for r in rows
        if (m := metric_for(r["concept"])) is not None
    )
    for r in rows:
        metric = metric_for(r["concept"])
        if metric is None:
            unmapped += 1
            continue
        slot = (metric.key, r["period_start"], r["period_end"])
        if choice.get(slot, r["concept"]) != r["concept"]:
            continue
        facts.append(
            FiledFact(
                metric.key,
                r["period_start"],
                r["period_end"],
                Decimal(str(r["value"])),
                _aware(r["known_at"]),
                r["accession"],
                fact_id=int(r["id"]),
            )
        )
    history = statement_history(facts, when)
    by_period: dict[dt.date, dict[str, HistoryPoint]] = {}
    for p in history:
        by_period.setdefault(p.period_end, {})[p.metric] = p
    balances: dict[dt.date, dict[str, HistoryPoint]] = {}
    for p in statement_history(
        [f for f in facts if f.period_start is None], when, kinds=("instant",)
    ):
        balances.setdefault(p.period_end, {})[p.metric] = p
    notes_by_period = await load_note_rows(pool, issuer_id, when)

    derived: list[dict[str, Any]] = []
    checks: dict[str, list[dict[str, Any]]] = {}
    gate: dict[str, bool] = {}
    valuation_gate: dict[str, dict[str, Any]] = {}
    formulas: list[dict[str, Any]] = []
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
        note_rows = notes_by_period.get(end, [])
        note_results = note_checks(note_rows)
        period_checks.extend(note_results)
        key = end.isoformat()
        checks[key] = [_check_json(c) for c in period_checks]
        if not note_results:
            checks[key].append(_no_note_data())
        ok, blockers = release_gate(period_checks)
        gate[key] = ok
        valuation_gate[key] = {"usable_for_valuation": ok, "reasons": blockers}

        inputs: dict[str, FormulaInput] = {}
        for name in _FORMULA_METRICS:
            point = metrics.get(name) or balances.get(end, {}).get(name)
            if point is not None:
                inputs[name] = FormulaInput(name, point.value, point.accession, point.fact_id)
        if (due := _year_one_debt(note_rows)) is not None:
            inputs["debt_due_year_1"] = due
        formulas.extend(_formula_json(end, r) for r in compute_formulas(inputs))

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
        "reporting_currency": reporting,
        "other_currency_facts_dropped": other_currency,
        "valuation_gate": valuation_gate,
        "formulas": formulas,
    }


async def with_valuation_gate(
    pool: Pool, rows: list[dict[str, Any]], as_of: dt.datetime | None = None
) -> list[dict[str, Any]]:
    """Add `valuation_gate` ({usable_for_valuation, reasons}) to stored fact rows, read-only. A
    fact with no issuer link or whose period has no annual gate gets None and says why, so a
    consumer never reads a missing gate as a pass."""
    gates: dict[int, dict[str, dict[str, Any]]] = {}
    for issuer_id in {r["issuer_id"] for r in rows if r.get("issuer_id") is not None}:
        gates[int(issuer_id)] = (await statements(pool, int(issuer_id), as_of))["valuation_gate"]
    out: list[dict[str, Any]] = []
    for r in rows:
        issuer_id = r.get("issuer_id")
        if issuer_id is None:
            gate = {"usable_for_valuation": None, "reasons": ["fact is not linked to an issuer"]}
        else:
            end = r["period_end"]
            gate = gates[int(issuer_id)].get(
                end.isoformat() if isinstance(end, dt.date) else str(end),
                {
                    "usable_for_valuation": None,
                    "reasons": ["no annual statement gate exists for this period"],
                },
            )
        out.append({**r, "valuation_gate": gate})
    return out


def _axis_json(a: AxisBridge) -> dict[str, Any]:
    return {
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


def _metric_json(m: MetricBridge) -> dict[str, Any]:
    return {
        "metric": m.metric,
        "total": None if m.total is None else str(m.total),
        "axes": [_axis_json(a) for a in (m.bridge.axes if m.bridge else ())],
        "unbridged": [
            {
                "axis": u.axis,
                "members": [
                    {"member": x.member, "value": str(x.value), "eliminations": x.eliminations}
                    for x in u.members
                ],
                "members_sum": str(u.members_sum),
                "eliminations": str(u.eliminations),
            }
            for u in m.unbridged
        ],
        "cross_axis": [
            {"axes": list(c.axes), "members": list(c.members), "value": str(c.value)}
            for c in m.cross_axis
        ],
        "notes": list(m.notes),
    }


_ALL_CONCEPTS = sorted({c for cs in SEGMENT_METRICS.values() for c in cs})


def _rank(metric: str, concept: str) -> int:
    cs = SEGMENT_METRICS[metric]
    return cs.index(concept) if concept in cs else len(cs)


_bare_metric = segment_metric_for


async def segment_bridge(
    pool: Pool, issuer_id: int, period_end: dt.date, as_of: dt.datetime | None = None
) -> dict[str, Any]:
    """Segment revenue, operating profit, assets and capex for one period. Each metric is bridged
    to its own consolidated total, each axis on its own; cross-axis facts come back as their own
    breakdown. `axes`, `total` and `notes` keep describing revenue, as before."""
    when = _aware(as_of)
    # A 10-Q reports one period end for several durations (3 months and year to date). One bridge
    # uses one duration: the longest one reported (the earliest start). Instant facts (assets)
    # carry no start and always belong.
    start: dt.date | None = await pool.fetchval(
        """select min(period_start) from public.financial_statement_fact
            where issuer_id=$1 and period_end=$2 and known_at<=$3 and period_start is not null
              and value is not null and concept = any($4::text[])""",
        issuer_id,
        period_end,
        when,
        _ALL_CONCEPTS,
    )
    total_rows = await pool.fetch(
        """select concept, value from public.financial_statement_fact
            where issuer_id=$1 and period_end=$2 and known_at<=$3
              and statement in ('income','balance_sheet','cash_flow') and value is not null
              and concept = any($4::text[])
              and (period_start is null or period_start = $5)
              and coalesce(dimensions->'members', '[]'::jsonb) = '[]'::jsonb
            order by known_at desc""",
        issuer_id,
        period_end,
        when,
        _ALL_CONCEPTS,
        start,
    )
    totals: dict[str, Decimal | None] = {}
    best: dict[str, int] = {}
    for r in total_rows:  # latest known first; a preferred concept beats a later-listed one
        metric = _bare_metric(r["concept"])
        if metric is None:
            continue
        rank = _rank(metric, r["concept"].removeprefix("us-gaap:"))
        if metric not in best or rank < best[metric]:
            best[metric] = rank
            totals[metric] = Decimal(str(r["value"]))
    seg = await pool.fetch(
        """select distinct on (concept, dimensions::text) concept, dimensions::text dims, value
             from public.financial_statement_fact
            where issuer_id=$1 and period_end=$2 and known_at<=$3 and statement='segment'
              and value is not null and concept = any($4::text[])
              and (period_start is null or period_start = $5)
            order by concept, dimensions::text, known_at desc""",
        issuer_id,
        period_end,
        when,
        _ALL_CONCEPTS,
        start,
    )
    chosen: dict[tuple[str, tuple[str, ...], tuple[str, ...]], tuple[int, Decimal]] = {}
    for r in seg:
        metric = _bare_metric(r["concept"])
        pairs = json.loads(r["dims"]).get("members") or []
        if metric is None or not pairs:
            continue
        axes = tuple(str(p[0]) for p in pairs)
        members = tuple(str(p[1]) for p in pairs)
        rank = _rank(metric, r["concept"].removeprefix("us-gaap:"))
        k = (metric, axes, members)
        if k not in chosen or rank < chosen[k][0]:
            chosen[k] = (rank, Decimal(str(r["value"])))
    report = build_segment_report(
        totals, [SegmentFact(m, a, mem, v) for (m, a, mem), (_, v) in chosen.items()]
    )
    metrics = [_metric_json(m) for m in report.metrics]
    revenue = next((m for m in report.metrics if m.metric == "revenue"), None)
    cross_axis = [
        {
            "metric": m.metric,
            "axes": list(c.axes),
            "members": list(c.members),
            "value": str(c.value),
        }
        for m in report.metrics
        for c in m.cross_axis
    ]
    if revenue is None or revenue.total is None or revenue.bridge is None:
        return {
            "period_end": period_end.isoformat(),
            "total": None,
            "axes": [],
            "notes": [
                "No reported revenue is known for this period, so segments cannot be bridged."
            ],
            "metrics": metrics,
            "cross_axis": cross_axis,
        }
    return {
        "period_end": period_end.isoformat(),
        "total": str(revenue.total),
        "axes": [_axis_json(a) for a in revenue.bridge.axes],
        "notes": list(revenue.notes),
        "metrics": metrics,
        "cross_axis": cross_axis,
    }
