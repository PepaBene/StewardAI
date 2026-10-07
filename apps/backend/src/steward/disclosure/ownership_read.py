"""P3.6: read the ownership lanes for one held ticker as of a date.

Reads the existing 13F, 13D/G and N-PORT tables (nothing is copied) and hands them to the pure
logic in `ownership.py`. Only filings known by `as_of` are visible.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import asyncpg

from steward.disclosure.ownership import (
    Filing13F,
    Holding13F,
    LaneEntry,
    OwnershipView,
    build_view,
    effective_positions,
    effective_reports,
    manager_name_changes,
)

type Pool = asyncpg.Pool | asyncpg.Connection


def _aware(value: dt.datetime | None) -> dt.datetime:
    now = value or dt.datetime.now(dt.UTC)
    return now if now.tzinfo else now.replace(tzinfo=dt.UTC)


async def _lane_13f(
    pool: Pool, ticker: str, when: dt.datetime
) -> tuple[list[LaneEntry], dict[str, list[tuple[dt.date, str]]], dict[str, Any] | None]:
    rows = await pool.fetch(
        """select f.accession, f.cik, f.period, f.filed, f.known_at, f.submission_type,
                  f.amendment_type, f.manager_name, f.confidential_treatment
             from public.form13f_filing f
            where f.known_at <= $2
              and f.cik in (select g.cik from public.form13f_filing g
                              join public.institutional_holding h on h.accession = g.accession
                             where h.ticker = $1)""",
        ticker,
        when,
    )
    # Every filing of every manager that has ever held the ticker, so a later report that no
    # longer lists it (an exit) is seen and replaces the older holding instead of hiding behind it.
    filings = [Filing13F(**dict(r)) for r in rows]
    reports = effective_reports(filings, when)
    latest: dict[str, Any] = {}
    for (cik, period), rep in reports.items():
        if cik not in latest or period > latest[cik].period:
            latest[cik] = rep
    periods = sorted({p for (_, p) in reports})
    trend_periods = periods[-2:]
    accessions = sorted(
        {a for rep in latest.values() for a in rep.accessions}
        | {a for (_, p), rep in reports.items() if p in trend_periods for a in rep.accessions}
    )
    held = await pool.fetch(
        """select accession, cusip, shares, value_usd, put_call, title_of_class
             from public.institutional_holding where ticker = $1 and accession = any($2::text[])""",
        ticker,
        accessions,
    )
    holdings = [Holding13F(**dict(r)) for r in held]
    by_filing = {f.accession: f for f in filings}
    entries: list[LaneEntry] = []
    for cik, rep in sorted(latest.items()):
        lines = [p for p in effective_positions(rep, holdings) if not p.is_option]
        if not lines:
            continue  # the effective report holds none of this ticker (e.g. restated without it)
        known = [p.shares for p in lines if p.shares is not None]
        name = next(
            (by_filing[a].manager_name for a in rep.accessions if by_filing[a].manager_name), cik
        )
        base = by_filing[rep.accessions[0]]
        entries.append(
            LaneEntry(
                name or cik,
                sum(known, Decimal(0)) if known else None,
                None,
                rep.period,
                base.filed,
                rep.known_at,
                rep.amended,
            )
        )
    entries.sort(key=lambda e: (-(e.shares or Decimal(0)), e.holder))

    # Holder books for the last two quarters any manager reported, for the rank-flow view. A
    # report that no longer lists the ticker leaves the book: that is the exit.
    trend: dict[str, Any] | None = None
    if len(trend_periods) == 2:
        books: dict[dt.date, dict[str, str]] = {p: {} for p in trend_periods}
        for (cik, period), rep in reports.items():
            if period not in books:
                continue
            lines = [p for p in effective_positions(rep, holdings) if not p.is_option]
            known = [p.shares for p in lines if p.shares is not None]
            if known:
                label = next(
                    (
                        by_filing[a].manager_name
                        for a in rep.accessions
                        if by_filing[a].manager_name
                    ),
                    cik,
                )
                books[period][label or cik] = str(sum(known, Decimal(0)))
        trend = {
            "prev_period": trend_periods[0],
            "cur_period": trend_periods[1],
            "prev": books[trend_periods[0]],
            "cur": books[trend_periods[1]],
        }
    return entries, manager_name_changes(filings), trend


async def _lane_13dg(pool: Pool, ticker: str, when: dt.datetime) -> list[LaneEntry]:
    rows = await pool.fetch(
        """select distinct on (coalesce(f.filer_cik, f.accession))
                  f.accession, f.filer_cik, f.issuer_name, f.stake_shares, f.stake_percent,
                  f.event_date, f.filed, f.known_at, f.is_amendment
             from public.control_stake_filing f
             join public.control_stake_issuer_link l on l.accession = f.accession
            where l.ticker = $1 and f.known_at <= $2 and l.class_status = 'cusip_match'
            order by coalesce(f.filer_cik, f.accession), f.known_at desc""",
        ticker,
        when,
    )
    out = []
    for r in rows:
        holder = await pool.fetchval(
            """select person_name from public.control_stake_holder where accession = $1
               order by aggregate_shares desc nulls last, row_index limit 1""",
            r["accession"],
        )
        out.append(
            LaneEntry(
                holder or r["filer_cik"] or r["accession"],
                r["stake_shares"],
                r["stake_percent"],
                r["event_date"],
                r["filed"],
                r["known_at"],
                r["is_amendment"],
            )
        )
    return sorted(out, key=lambda e: (-(e.shares or Decimal(0)), e.holder))


async def _lane_nport(pool: Pool, ticker: str, when: dt.datetime) -> list[LaneEntry]:
    rows = await pool.fetch(
        """select distinct on (f.series_id)
                  f.series_id, coalesce(f.series_name, f.registrant_name, f.series_id) name,
                  f.report_period_end, f.filed, f.known_at, f.form,
                  sum(h.balance) filter (where h.units = 'NS' and not h.is_derivative) shares
             from public.nport_filing f
             join public.nport_holding h on h.accession = f.accession
            where h.held_ticker = $1 and f.known_at <= $2 and f.reconcile_status = 'confirmed'
            group by f.accession, f.series_id, f.series_name, f.registrant_name,
                     f.report_period_end, f.filed, f.known_at, f.form
            order by f.series_id, f.report_period_end desc, f.known_at desc""",
        ticker,
        when,
    )
    return sorted(
        (
            LaneEntry(
                r["name"],
                r["shares"],
                None,
                r["report_period_end"],
                r["filed"],
                r["known_at"],
                r["form"].endswith("/A"),
            )
            for r in rows
        ),
        key=lambda e: (-(e.shares or Decimal(0)), e.holder),
    )


async def ownership(pool: Pool, ticker: str, as_of: dt.datetime | None = None) -> dict[str, Any]:
    when = _aware(as_of)
    den = await pool.fetchrow(
        """select shares, as_of, method from public.shares_outstanding
            where ticker = $1 and filed is not null and filed <= $2::timestamptz::date
            order by as_of desc, id desc limit 1""",
        ticker,
        when,
    )
    f13, renames, trend = await _lane_13f(pool, ticker, when)
    view: OwnershipView = build_view(
        {
            "institutional_13f": f13,
            "control_13dg": await _lane_13dg(pool, ticker, when),
            "fund_nport": await _lane_nport(pool, ticker, when),
        },
        den["shares"] if den else None,
        den["as_of"] if den else None,
        not_covered=["national_voting"],
    )
    return {
        "ticker": ticker,
        "as_of": when,
        "denominator": None if den is None else str(den["shares"]),
        "denominator_as_of": None if den is None else den["as_of"],
        "denominator_method": None if den is None else den["method"],
        "lanes": [
            {
                "key": ln.key,
                "title": ln.title,
                "basis": ln.basis,
                "status": ln.status,
                "entries": [
                    {
                        "holder": e.holder,
                        "shares": None if e.shares is None else str(e.shares),
                        "reported_percent": None
                        if e.reported_percent is None
                        else str(e.reported_percent),
                        "report_date": e.report_date,
                        "filed": e.filed,
                        "known_at": e.known_at,
                        "lag_days": e.lag_days,
                        "amended": e.amended,
                    }
                    for e in ln.entries
                ],
                "total_shares": None if ln.total_shares is None else str(ln.total_shares),
                "percent_of_denominator": None
                if ln.percent_of_denominator is None
                else str(round(ln.percent_of_denominator, 4)),
                "remainder_shares": None
                if ln.remainder_shares is None
                else str(ln.remainder_shares),
                "remainder_percent": None
                if ln.remainder_percent is None
                else str(round(ln.remainder_percent, 4)),
                "overlaps_with": list(ln.overlaps_with),
                "notes": list(ln.notes),
            }
            for ln in view.lanes
        ],
        "warnings": list(view.warnings),
        "trend": trend,
        "manager_renames": [
            {"cik": cik, "names": [{"from": str(p), "name": n} for p, n in names]}
            for cik, names in renames.items()
        ],
    }
