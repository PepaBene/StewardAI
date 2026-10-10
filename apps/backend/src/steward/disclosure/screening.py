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
from steward.disclosure import deal_read, estimate_read, insider_analysis
from steward.disclosure.note_store import CONCEPT_PREFIX, NOTE_SOURCE
from steward.free_source_reads import status_as_of_sql

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
class Missing:
    """A field that could not be computed, and why. Never scored, never zero."""

    reason: str


@dataclass(frozen=True)
class Universe:
    ticker: str
    weight: Decimal
    value: Decimal
    fields: Mapping[str, FieldValue | Missing] = field(default_factory=dict)


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
            if fv is None or isinstance(fv, Missing):
                data_counts[key][1] += 1
                why = "" if fv is None else f" ({fv.reason})"
                reasons.append(
                    {
                        "criterion": key,
                        "kind": "missing",
                        "reason": f"No verified value for {label.lower()}{why}; not scored.",
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
                            "value": str(fv.value),
                            "unit": CRITERIA[k][3],
                            "period": fv.period,
                            "known_at": fv.known_at,
                            "source": fv.source,
                        }
                        for k in active
                        if isinstance(fv := u.fields[CRITERIA[k][0]], FieldValue)
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


def _base(row: Mapping[str, Any]) -> Decimal:
    """The row's value in base units: `scale` is a power of ten (6 = millions) when present."""
    return Decimal(row["value"]) * Decimal(10) ** int(row.get("scale") or 0)


def ratio_field(
    rows: Sequence[Mapping[str, Any]], numerator: str, denominator: str, source: str
) -> FieldValue | Missing:
    """Numerator / denominator for the LATEST period that has a numerator, in base units.

    `rows` are verified observations for one ticker (metric, period_start, period_end, value,
    known_at, optional scale). When a period has several versions the latest-known one is used.
    If that latest period's denominator is missing or not positive the result is Missing with a
    reason: an older period is never used instead, because that would present stale data as
    current.
    """
    best: dict[tuple[str, Any, Any], Mapping[str, Any]] = {}
    for r in rows:
        key = (r["metric"], r["period_start"], r["period_end"])
        if key not in best or r["known_at"] > best[key]["known_at"]:
            best[key] = r
    nums = sorted(
        {(e, s) for (m, s, e) in best if m == numerator},
        key=lambda p: (p[0], str(p[1])),
        reverse=True,
    )
    if not nums:
        return Missing(f"no verified {numerator}")
    end, start = nums[0]
    num = best[(numerator, start, end)]
    den = best.get((denominator, start, end))
    if den is None:
        return Missing(f"{denominator} is not reported for the latest period {end.isoformat()}")
    d = _base(den)
    if d <= 0:
        return Missing(
            f"{denominator} is {d} in the latest period {end.isoformat()}; the ratio is undefined"
        )
    return FieldValue(
        (_base(num) / d).quantize(Decimal("0.0001")),
        end.isoformat(),
        max(num["known_at"], den["known_at"]),
        source,
    )


def debt_maturity_field(
    cash_rows: Sequence[Mapping[str, Any]], note_rows: Sequence[Mapping[str, Any]]
) -> FieldValue | Missing:
    """Cash / debt due in year 1, from the latest debt-maturity note known.

    `note_rows` are the year_1 bucket rows of the debt_maturity note table for one ticker
    (accession, known_at, period_end, value, status, dash), `cash_rows` verified `cash`
    observations. The latest filing decides: a year-1 bucket awaiting review makes the field
    Missing rather than falling back to an older filing. Zero or dashed year-1 debt is Missing with
    the reason "no debt due in year 1", never 0 and never silently dropped."""
    if not note_rows:
        return Missing("no debt maturity note is stored")
    latest = max(note_rows, key=lambda r: (r["known_at"], r["accession"]))
    note = [r for r in note_rows if r["accession"] == latest["accession"]][-1]
    if note.get("status") == "review":
        return Missing("the year-1 debt bucket of the latest filing is awaiting review")
    value = note["value"]
    if value is None:
        if note.get("dash") in (True, "true"):
            return Missing("no debt due in year 1")
        return Missing("the year-1 debt bucket of the latest filing is not readable")
    due = Decimal(value)
    if due == 0:
        return Missing("no debt due in year 1")
    if due < 0:
        return Missing("the year-1 debt bucket is negative; the ratio is undefined")
    cash_here = [
        r for r in cash_rows if r["metric"] == "cash" and r["period_end"] == note["period_end"]
    ]
    if not cash_here:
        return Missing(f"no verified cash for the note's period {note['period_end']}")
    cash = max(cash_here, key=lambda r: r["known_at"])
    return FieldValue(
        (_base(cash) / due).quantize(Decimal("0.0001")),
        note["period_end"].isoformat(),
        max(cash["known_at"], note["known_at"]),
        "verified cash and debt maturity note",
    )


def segment_surprise_field(events: Sequence[Mapping[str, Any]]) -> FieldValue | None:
    """Latest scored segment surprise as a FRACTION (0.05 = 5 %). The events carry percent; the
    latest period wins and, within it, the segment with the largest absolute surprise (ties by
    segment name), so the choice is deterministic."""
    scored = [
        e
        for e in events
        if e.get("segment") and e.get("status") == "scored" and e.get("surprise_pct") is not None
    ]
    if not scored:
        return None
    latest = max(e["period_end"] or dt.date.min for e in scored)
    pick = max(
        (e for e in scored if (e["period_end"] or dt.date.min) == latest),
        key=lambda e: (abs(Decimal(e["surprise_pct"])), str(e["segment"])),
    )
    return FieldValue(
        Decimal(pick["surprise_pct"]) / Decimal(100),
        pick["fiscal_period"],
        pick["estimate_known_at"],
        f"published estimate against reported actual ({pick['segment']})",
    )


OBS_SQL = """
select n.t212_ticker as ticker, o.metric, o.period_start, o.period_end, o.value, o.scale,
       o.known_at
  from public.instrument n
  join public.issuer i on i.cik = n.cik
  join public.disclosure_observation o on o.issuer_id = i.id
 where n.t212_ticker = any($1::text[]) and o.known_at <= $2 and o.review_state = 'verified'
   and o.value_state = 'reported' and o.segment is null and o.dimensions = '{}'::jsonb
   and o.metric in ('operating_cash_flow', 'net_income', 'cash')
"""

# The year-1 bucket of the debt maturity note table, as scan_debt_maturity reads it.
DEBT_NOTE_SQL = """
select ticker, accession, known_at, period_end, value,
       dimensions #>> '{note,status}' as status, dimensions #>> '{note,dash}' as dash
  from public.financial_statement_fact
 where ticker = any($1::text[]) and statement = 'note' and source = $2 and concept = $3
   and known_at <= $4 and dimensions #>> '{note,bucket}' = 'year_1'
 order by known_at, id
"""

# The latest control-stake filing that is about the held share class (CUSIP matched). A prior
# filing of another class, or one not yet known, gives no prior figure: a class change is not a
# stake change.
STAKE_SQL = """
select n.t212_ticker as ticker, c.stake_percent,
       case when p.known_at <= $2
             and lower(btrim(coalesce(p.class_title, '')))
                 = lower(btrim(coalesce(c.class_title, '')))
            then p.stake_percent end as prior_percent,
       c.filed, c.known_at
  from public.instrument n
  join lateral (select f.* from public.control_stake_filing f
                  join public.control_stake_issuer_link l on l.accession = f.accession
                 where f.issuer_cik = n.cik and f.known_at <= $2
                   and l.ticker = n.t212_ticker and l.class_status = 'cusip_match'
                 order by f.filed desc, f.accession desc limit 1) c on true
  left join public.control_stake_filing p on p.accession = c.prior_accession
 where n.t212_ticker = any($1::text[])
"""

_STATUS_AT = status_as_of_sql("relationship_disclosure", "d", "$2", log=True, decided=True)

# The largest single customer's share of total revenue. Only claims about one counterparty count
# (a group such as "three customers 45 %" is not the largest customer), only confirmed ones as
# they stood at the date, and the latest period is picked AFTER the denominator filter, so a newer
# receivables line cannot hide the revenue share.
CUSTOMER_SQL = f"""
select distinct on (ticker) ticker, reported_share, fiscal_period, known_at
  from (select s.*, rank() over (
                    partition by s.ticker,
                      coalesce(s.counterparty_key, s.counterparty_name, s.claim_label, s.id::text)
                    order by s.period_end desc nulls last, s.fiscal_period desc nulls last) as rk
          from (select d.id, d.ticker, d.counterparty_key, d.counterparty_name, d.claim_label,
                       d.period_end, d.fiscal_period, d.reported_share, d.known_at,
                       {_STATUS_AT} as status_at
                  from public.relationship_disclosure d
                 where d.ticker = any($1::text[]) and d.relationship = 'customer'
                   and d.known_at <= $2
                   and coalesce(d.claim_shape, case when d.anonymous_counterparty
                                                    then 'anonymous' else 'named' end)
                       in ('named', 'anonymous')
                   and (d.denominator_kind = 'total_revenue'
                        or (d.denominator_kind is null and d.share_basis = 'issuer_revenue'))) s
         where s.status_at = 'confirmed') r
 where rk = 1 and reported_share is not null
 order by ticker, reported_share desc, id
"""  # noqa: S608 - the only interpolated part is a fixed SQL fragment


async def load_fields(
    pool: Pool, universe: Sequence[cr.Holding], when: dt.datetime, insider_window_days: int
) -> dict[str, dict[str, FieldValue | Missing]]:
    tickers = [h.ticker for h in universe]
    out: dict[str, dict[str, FieldValue | Missing]] = defaultdict(dict)
    if not tickers:
        return out
    obs: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in await pool.fetch(OBS_SQL, tickers, when):
        obs[r["ticker"]].append(dict(r))
    notes: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in await pool.fetch(
        DEBT_NOTE_SQL, tickers, NOTE_SOURCE, CONCEPT_PREFIX + "debt_maturity", when
    ):
        notes[r["ticker"]].append(dict(r))
    for t, rows in obs.items():
        out[t]["cash_conversion"] = ratio_field(
            rows, "operating_cash_flow", "net_income", "verified observations"
        )
    for t in notes.keys() | obs.keys():
        out[t]["debt_maturity_coverage"] = debt_maturity_field(obs.get(t, []), notes.get(t, []))
    for t in tickers:
        # The same count the insider view makes: overrides, supersession as of the date, bundled
        # exercise-and-sell lines left out, and a buy with no plan indicator counted.
        buys, filed_at = await insider_analysis.discretionary_buys(
            pool, t, when, insider_window_days
        )
        if filed_at is None:
            # a Form 4 known by the date with no readable transaction line is a stated zero, not an
            # unknown; only a holding with no known filing at all has no figure
            filed_at = await pool.fetchval(
                "select max(known_at) from public.insider_filing"
                " where ticker = $1 and known_at <= $2 and superseded_by_accession is null",
                t,
                when,
            )
            buys = 0
        if filed_at is not None:
            out[t]["discretionary_buys"] = FieldValue(
                buys, f"last {insider_window_days} days", filed_at, "Form 4 filings"
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
        seg = segment_surprise_field(view["events"])
        if seg is not None:
            out[t]["segment_surprise_pct"] = seg
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
