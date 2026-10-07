"""P8.4: screen the held universe on verified disclosure fields.

Each criterion reads one field that comes from a stored, cited record. A holding with no value for a
chosen field is EXCLUDED with that reason: it is never scored zero and never ranked. A holding that
has the value but fails the bar is excluded as failed. The result always carries the universe size,
so "matched" is read against how many holdings could be tested at all.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import asyncpg

from steward.disclosure import coverage_report as cr
from steward.disclosure import deal_read, estimate_read

type Pool = asyncpg.Pool | asyncpg.Connection

METHOD_VERSION = "screen.v1"
ZERO = Decimal(0)

#: criterion key -> (field it reads, comparison, label, unit)
CRITERIA: Mapping[str, tuple[str, str, str, str]] = {
    "min_cash_conversion": (
        "cash_conversion",
        "ge",
        "Cash conversion (operating cash flow / net income)",
        "ratio",
    ),
    "min_debt_maturity_coverage": (
        "debt_maturity_coverage",
        "ge",
        "Cash covering debt due in year 1",
        "ratio",
    ),
    "min_discretionary_buys": (
        "discretionary_buys",
        "ge",
        "Discretionary insider open-market buys",
        "count",
    ),
    "min_stake_change_pct": (
        "stake_change_pct",
        "abs_ge",
        "Change in the latest filed stake (points)",
        "percentage points",
    ),
    "min_customer_concentration": (
        "customer_concentration",
        "ge",
        "Largest confirmed customer share",
        "fraction",
    ),
    "min_segment_surprise_pct": (
        "segment_surprise_pct",
        "abs_ge",
        "Latest segment surprise (absolute)",
        "fraction",
    ),
    "deal_exposure": ("deal_exposure", "true", "Has a deal known and active", "flag"),
}
FIELD_KEYS = tuple(v[0] for v in CRITERIA.values())


@dataclass(frozen=True)
class FieldValue:
    value: Decimal | int | bool
    period: str | None = None
    known_at: dt.datetime | None = None
    source: str = ""


@dataclass(frozen=True)
class Universe:
    ticker: str
    weight: Decimal
    value: Decimal
    fields: Mapping[str, FieldValue] = field(default_factory=dict)


def _num(v: Decimal | int | bool) -> Decimal:
    return Decimal(int(v)) if isinstance(v, bool | int) else v


def passes(kind: str, value: Decimal | int | bool, bar: Decimal | int | bool) -> bool:
    if kind == "true":
        return bool(value) is True
    if kind == "abs_ge":
        return abs(_num(value)) >= _num(bar)
    return _num(value) >= _num(bar)


def screen(
    universe: Sequence[Universe], criteria: Mapping[str, Decimal | int | bool | None]
) -> dict[str, Any]:
    """Apply the chosen criteria. A criterion set to None (or False for a flag) is not active."""
    active = {
        k: v
        for k, v in criteria.items()
        if k in CRITERIA and v is not None and not (CRITERIA[k][1] == "true" and v is False)
    }
    matched: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    data_counts = {k: [0, 0] for k in active}  # [with data, without data]
    for u in universe:
        reasons: list[dict[str, Any]] = []
        for key, bar in active.items():
            fld, kind, label, _unit = CRITERIA[key]
            fv = u.fields.get(fld)
            if fv is None:
                data_counts[key][1] += 1
                reasons.append(
                    {
                        "criterion": key,
                        "kind": "missing",
                        "reason": f"No verified value for {label.lower()}; not scored.",
                        "value": None,
                        "threshold": str(bar),
                    }
                )
                continue
            data_counts[key][0] += 1
            if not passes(kind, fv.value, bar):
                reasons.append(
                    {
                        "criterion": key,
                        "kind": "failed",
                        "reason": f"{label} is {fv.value}, outside the bar {bar}.",
                        "value": str(fv.value),
                        "threshold": str(bar),
                    }
                )
        if not active:
            continue
        if reasons:
            excluded.append(
                {
                    "ticker": u.ticker,
                    "weight": str(u.weight),
                    "kind": "missing" if any(r["kind"] == "missing" for r in reasons) else "failed",
                    "reasons": reasons,
                }
            )
        else:
            matched.append(
                {
                    "ticker": u.ticker,
                    "weight": str(u.weight),
                    "values": [
                        {
                            "criterion": k,
                            "label": CRITERIA[k][2],
                            "value": str(u.fields[CRITERIA[k][0]].value),
                            "unit": CRITERIA[k][3],
                            "period": u.fields[CRITERIA[k][0]].period,
                            "known_at": u.fields[CRITERIA[k][0]].known_at,
                            "source": u.fields[CRITERIA[k][0]].source,
                        }
                        for k in active
                    ],
                }
            )
    matched.sort(key=lambda m: m["ticker"])  # alphabetical: nothing here is a score
    excluded.sort(key=lambda m: m["ticker"])
    total = sum((u.value for u in universe), ZERO)
    by_ticker = {u.ticker: u for u in universe}
    matched_value = sum((by_ticker[m["ticker"]].value for m in matched), ZERO)
    return {
        "universe_size": len(universe),
        "denominator_value": str(total),
        "active_criteria": sorted(active),
        "matched": matched,
        "excluded": excluded,
        "matched_count": len(matched),
        "excluded_missing": sum(1 for e in excluded if e["kind"] == "missing"),
        "excluded_failed": sum(1 for e in excluded if e["kind"] == "failed"),
        "matched_value": str(matched_value),
        "matched_weight": str(matched_value / total) if total > 0 else None,
        "field_coverage": [
            {
                "criterion": k,
                "label": CRITERIA[k][2],
                "with_data": v[0],
                "without_data": v[1],
            }
            for k, v in data_counts.items()
        ],
        "method_version": METHOD_VERSION,
        "notes": (
            [
                "Ordered by ticker. A holding with no value for a chosen field is excluded as "
                "missing, never scored zero.",
            ]
            if active
            else ["Choose at least one criterion; the universe size is shown for reference."]
        ),
    }


# ------------------------------------------------------------------------------------- fields


def ratio_field(
    rows: Sequence[Mapping[str, Any]], numerator: str, denominator: str, source: str
) -> FieldValue | None:
    """Latest period with both inputs: numerator / denominator, only for a positive denominator.

    `rows` are verified observations for one ticker (metric, period_start, period_end, value,
    known_at). When a period has several versions the latest-known one is used.
    """
    best: dict[tuple[str, Any, Any], Mapping[str, Any]] = {}
    for r in rows:
        key = (r["metric"], r["period_start"], r["period_end"])
        if key not in best or r["known_at"] > best[key]["known_at"]:
            best[key] = r
    periods = sorted(
        {(s, e) for (m, s, e) in best if m == numerator}
        & {(s, e) for (m, s, e) in best if m == denominator},
        key=lambda p: p[1],
        reverse=True,
    )
    for start, end in periods:
        num, den = best[(numerator, start, end)], best[(denominator, start, end)]
        if Decimal(den["value"]) > 0:
            return FieldValue(
                (Decimal(num["value"]) / Decimal(den["value"])).quantize(Decimal("0.0001")),
                end.isoformat(),
                max(num["known_at"], den["known_at"]),
                source,
            )
    return None


OBS_SQL = """
select n.t212_ticker as ticker, o.metric, o.period_start, o.period_end, o.value, o.known_at
  from public.instrument n
  join public.issuer i on i.cik = n.cik
  join public.disclosure_observation o on o.issuer_id = i.id
 where n.t212_ticker = any($1::text[]) and o.known_at <= $2 and o.review_state = 'verified'
   and o.value_state = 'reported' and o.segment is null and o.dimensions = '{}'::jsonb
   and o.metric in ('operating_cash_flow', 'net_income', 'cash', 'debt_due_year_1')
"""

INSIDER_SQL = """
select f.ticker,
       count(*) filter (where t.economic_class = 'open_market_buy'
                          and t.rule_10b5_1_plan is false and not t.derivative
                          and t.transaction_date > $2::date - $3::int
                          and t.transaction_date <= $2::date) as buys,
       max(f.known_at) as known_at
  from public.insider_filing f
  left join public.insider_transaction t on t.accession = f.accession
 where f.ticker = any($1::text[]) and f.known_at <= $2 and f.superseded_by_accession is null
 group by f.ticker
"""

STAKE_SQL = """
select n.t212_ticker as ticker, c.stake_percent, p.stake_percent as prior_percent, c.filed,
       c.known_at
  from public.instrument n
  join lateral (select * from public.control_stake_filing
                 where issuer_cik = n.cik and known_at <= $2
                 order by filed desc, accession desc limit 1) c on true
  left join public.control_stake_filing p on p.accession = c.prior_accession
 where n.t212_ticker = any($1::text[])
"""

CUSTOMER_SQL = """
select distinct on (ticker) ticker, reported_share, fiscal_period, known_at
  from public.relationship_disclosure
 where ticker = any($1::text[]) and status = 'confirmed' and relationship = 'customer'
   and reported_share is not null and known_at <= $2
 order by ticker, reported_share desc, id
"""


async def load_fields(
    pool: Pool, universe: Sequence[cr.Holding], when: dt.datetime, insider_window_days: int
) -> dict[str, dict[str, FieldValue]]:
    tickers = [h.ticker for h in universe]
    out: dict[str, dict[str, FieldValue]] = defaultdict(dict)
    if not tickers:
        return out
    obs: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in await pool.fetch(OBS_SQL, tickers, when):
        obs[r["ticker"]].append(dict(r))
    for t, rows in obs.items():
        cc = ratio_field(rows, "operating_cash_flow", "net_income", "verified observations")
        if cc:
            out[t]["cash_conversion"] = cc
        dm = ratio_field(rows, "cash", "debt_due_year_1", "verified observations and note table")
        if dm:
            out[t]["debt_maturity_coverage"] = dm
    for r in await pool.fetch(INSIDER_SQL, tickers, when, insider_window_days):
        out[r["ticker"]]["discretionary_buys"] = FieldValue(
            int(r["buys"]), f"last {insider_window_days} days", r["known_at"], "Form 4 filings"
        )
    for r in await pool.fetch(STAKE_SQL, tickers, when):
        if r["stake_percent"] is not None and r["prior_percent"] is not None:
            out[r["ticker"]]["stake_change_pct"] = FieldValue(
                Decimal(r["stake_percent"]) - Decimal(r["prior_percent"]),
                r["filed"].isoformat(),
                r["known_at"],
                "Schedule 13D/13G",
            )
    for r in await pool.fetch(CUSTOMER_SQL, tickers, when):
        out[r["ticker"]]["customer_concentration"] = FieldValue(
            Decimal(r["reported_share"]),
            r["fiscal_period"],
            r["known_at"],
            "confirmed relationship",
        )
    for t in tickers:
        view = await estimate_read.estimate_view(pool, t, when)
        events = [
            e for e in view["events"] if e.get("segment") and e.get("surprise_pct") is not None
        ]
        if events:
            e = max(events, key=lambda x: x["period_end"] or dt.date.min)
            out[t]["segment_surprise_pct"] = FieldValue(
                Decimal(e["surprise_pct"]),
                e["fiscal_period"],
                e["estimate_known_at"],
                "published estimate against reported actual",
            )
    for d in (await deal_read.portfolio_view(pool, when))["rows"]:
        out[d["ticker"]]["deal_exposure"] = FieldValue(True, None, None, "public deal documents")
    return out


async def screen_view(
    pool: Pool,
    criteria: Mapping[str, Decimal | int | bool | None],
    as_of: dt.datetime | None = None,
    insider_window_days: int = 90,
) -> dict[str, Any]:
    when = cr._aware(as_of)
    holdings, snapshot = await cr.load_holdings(pool, when)
    total = sum((h.value for h in holdings), ZERO)
    fields = await load_fields(pool, holdings, when, insider_window_days)
    universe = [
        Universe(
            h.ticker, h.value / total if total > 0 else ZERO, h.value, fields.get(h.ticker, {})
        )
        for h in holdings
    ]
    result = screen(universe, criteria)
    result["as_of"] = when
    result["snapshot_date"] = snapshot
    result["universe_basis"] = "Holdings in the latest position snapshot"
    result["insider_window_days"] = insider_window_days
    return result
