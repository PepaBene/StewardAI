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
    choose_common_quarter,
    effective_positions,
    effective_reports,
    manager_name_changes,
    one_stake_per_position,
    reported_stake_delta,
)

type Pool = asyncpg.Pool | asyncpg.Connection


def _aware(value: dt.datetime | None) -> dt.datetime:
    now = value or dt.datetime.now(dt.UTC)
    return now if now.tzinfo else now.replace(tzinfo=dt.UTC)


async def _lane_13f(
    pool: Pool, ticker: str, when: dt.datetime
) -> tuple[
    list[LaneEntry],
    dict[str, list[tuple[dt.date, str]]],
    dict[str, Any] | None,
    dict[str, Any],
]:
    rows = await pool.fetch(
        """select f.accession, f.cik, f.period, f.filed, f.known_at, f.submission_type,
                  f.amendment_type, f.manager_name, f.confidential_treatment,
                  f.reported_line_count, f.reported_total_value_usd, f.omitted_holdings_notice
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
    all_reports = effective_reports(filings, when)
    # A report with no applied filing (only a 13F-HR/A that names no amendment type) has no
    # holdings to read. It does not count as filed; its flag is shown instead.
    unapplied = [
        {"cik": cik, "period": period, "flags": list(rep.flags)}
        for (cik, period), rep in sorted(all_reports.items())
        if not rep.accessions
    ]
    reports = {k: rep for k, rep in all_reports.items() if rep.accessions}
    # One common quarter for the whole lane: the latest that enough managers have filed. A manager
    # that has not filed it yet is named, never carried forward from an older quarter.
    choice = choose_common_quarter(reports)
    latest: dict[str, Any] = {
        cik: rep for (cik, period), rep in reports.items() if period == choice.period
    }
    # The trend compares the chosen quarter with the one before it, never with a newer quarter
    # that only the early filers have reported (that would read as a mass exit).
    periods = sorted({p for (_, p) in reports if choice.period is None or p <= choice.period})
    trend_periods = periods[-2:]
    accessions = sorted(
        {a for rep in latest.values() for a in rep.accessions}
        | {a for (_, p), rep in reports.items() if p in trend_periods for a in rep.accessions}
    )
    held = await pool.fetch(
        """select accession, cusip, shares, value_usd, put_call, title_of_class
             from public.institutional_holding
            where ticker = $1 and accession = any($2::text[])
              and coalesce(shares_type, 'SH') = 'SH'""",
        ticker,
        accessions,
    )
    holdings = [Holding13F(**dict(r)) for r in held]
    by_filing = {f.accession: f for f in filings}
    entries: list[LaneEntry] = []
    checks: list[dict[str, Any]] = []
    for cik, rep in sorted(latest.items()):
        all_lines = effective_positions(rep, holdings)
        lines = [p for p in all_lines if not p.is_option]
        base = by_filing[rep.accessions[0]]
        name = next(
            (by_filing[a].manager_name for a in rep.accessions if by_filing[a].manager_name), cik
        )
        checks.append(
            {
                "manager": name or cik,
                "report_date": rep.period,
                "lines_stored_for_ticker": len(all_lines),
                "lines_reported": base.reported_line_count,
                "total_value_reported_usd": (
                    None
                    if base.reported_total_value_usd is None
                    else str(base.reported_total_value_usd)
                ),
                "confidential_treatment": base.confidential_treatment,
            }
        )
        if not lines:
            continue  # the effective report holds none of this ticker (e.g. restated without it)
        known = [p.shares for p in lines if p.shares is not None]
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
    names = {f.cik: f.manager_name or f.cik for f in filings}
    info = {
        "period": choice.period,
        "managers_filed": len(choice.filed),
        "share_filed": str(round(choice.share_filed, 4)),
        "fell_back": choice.fell_back,
        "not_yet_filed": [names[c] for c in choice.not_yet_filed],
        "reports": checks,
        "unapplied_reports": unapplied,
    }
    return entries, manager_name_changes(filings), trend, info


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
    # A parent and its subsidiary (or the members of a group) can each file the same stake:
    # count it once.
    keep = one_stake_per_position(
        [
            (r["filer_cik"] or r["accession"], r["stake_shares"], r["stake_percent"],
             r["event_date"], r["known_at"])
            for r in rows
        ]
    )  # fmt: skip
    out = []
    for i, r in enumerate(rows):
        if i not in keep:
            continue
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


def group_summary(holders: list[dict[str, Any]]) -> dict[str, Any]:
    """What a filing says about its reporting persons as a group. `stated` only when a person's
    cover page ticks the group box; several persons with no box ticked are a joint filing whose
    group status is not stated, and the filing's stake is the largest single figure, never a sum
    (members report overlapping shares)."""
    n = len(holders)
    ticked = [h for h in holders if h["member_of_group"] is True]
    denied = [h for h in holders if h["member_of_group"] is False]
    if ticked:
        status = "stated_group"
    elif n > 1 and len(denied) == n:
        status = "stated_not_group"
    elif n > 1:
        status = "joint_filing_group_not_stated"
    else:
        status = "single_filer"
    return {
        "status": status,
        "members": n,
        "stated_members": len(ticked),
        "note": (
            "The stake shown is the largest single reporting person's figure, not a sum."
            if n > 1
            else None
        ),
    }


async def stake_chain(
    pool: Pool, ticker: str, filer_cik: str, as_of: dt.datetime | None = None
) -> dict[str, Any]:
    """Every 13D/G filing one filer made about a held share class, oldest first, as known at
    `as_of`. Each step states its reported change from the previous one only when both report a
    percentage for the same class; otherwise it says why there is no delta."""
    when = _aware(as_of)
    rows = await pool.fetch(
        """select f.accession, f.form, f.schedule, f.is_amendment, f.amendment_no, f.filed,
                  f.event_date, f.known_at, f.stake_percent, f.stake_shares, f.class_title,
                  f.intent_text, f.intent_hash, f.prior_accession, f.source_url
             from public.control_stake_filing f
             join public.control_stake_issuer_link l on l.accession = f.accession
            where l.ticker = $1 and l.class_status = 'cusip_match' and f.filer_cik = $2
              and f.known_at <= $3
            order by f.filed, f.known_at, f.accession""",
        ticker,
        filer_cik.zfill(10),
        when,
    )
    accessions = [r["accession"] for r in rows]
    holders = await pool.fetch(
        """select accession, person_name, member_of_group, aggregate_shares, percent_of_class,
                  sole_voting, shared_voting, sole_dispositive, shared_dispositive
             from public.control_stake_holder where accession = any($1::text[])
            order by accession, row_index""",
        accessions,
    )
    changes = await pool.fetch(
        """select accession, change_type, person_name, old_value, new_value, detail
             from public.control_stake_change where accession = any($1::text[])
            order by accession, id""",
        accessions,
    )
    exhibits = await pool.fetch(
        """select accession, exhibit_label, description, document_url
             from public.control_stake_exhibit where accession = any($1::text[])
            order by accession, row_index""",
        accessions,
    )
    steps: list[dict[str, Any]] = []
    prev: Any = None
    for r in rows:
        delta: Any = None
        why: str | None = "first filing in the chain"
        if prev is not None:
            delta, why = reported_stake_delta(
                prev["stake_percent"], prev["class_title"], r["stake_percent"], r["class_title"]
            )
        intent_changed = (
            None
            if prev is None or not prev["intent_hash"] or not r["intent_hash"]
            else prev["intent_hash"] != r["intent_hash"]
        )
        steps.append(
            {
                "accession": r["accession"],
                "form": r["form"],
                "schedule": r["schedule"],
                "is_amendment": r["is_amendment"],
                "amendment_no": r["amendment_no"],
                "filed": r["filed"],
                "event_date": r["event_date"],
                "known_at": r["known_at"],
                "class_title": r["class_title"],
                "stake_percent": None if r["stake_percent"] is None else str(r["stake_percent"]),
                "stake_shares": None if r["stake_shares"] is None else str(r["stake_shares"]),
                "reported_delta_points": None if delta is None else str(delta),
                "delta_note": why,
                "purpose": r["intent_text"],
                "purpose_changed": intent_changed,
                "prior_accession": r["prior_accession"],
                "linked_to_previous": prev is not None
                and r["prior_accession"] == prev["accession"],
                "source_url": r["source_url"],
                "group": group_summary(
                    [dict(h) for h in holders if h["accession"] == r["accession"]]
                ),
                "holders": [
                    {
                        "name": h["person_name"],
                        "member_of_group": h["member_of_group"],
                        "shares": None
                        if h["aggregate_shares"] is None
                        else str(h["aggregate_shares"]),
                        "percent": None
                        if h["percent_of_class"] is None
                        else str(h["percent_of_class"]),
                        "sole_voting": None if h["sole_voting"] is None else str(h["sole_voting"]),
                        "shared_voting": None
                        if h["shared_voting"] is None
                        else str(h["shared_voting"]),
                        "sole_dispositive": None
                        if h["sole_dispositive"] is None
                        else str(h["sole_dispositive"]),
                        "shared_dispositive": None
                        if h["shared_dispositive"] is None
                        else str(h["shared_dispositive"]),
                    }
                    for h in holders
                    if h["accession"] == r["accession"]
                ],
                "changes": [
                    {
                        "change_type": c["change_type"],
                        "person": c["person_name"],
                        "old": c["old_value"],
                        "new": c["new_value"],
                        "detail": c["detail"],
                    }
                    for c in changes
                    if c["accession"] == r["accession"]
                ],
                "exhibits": [
                    {
                        "label": e["exhibit_label"],
                        "description": e["description"],
                        "url": e["document_url"],
                    }
                    for e in exhibits
                    if e["accession"] == r["accession"]
                ],
            }
        )
        prev = r
    return {"ticker": ticker, "filer_cik": filer_cik.zfill(10), "as_of": when, "steps": steps}


async def ownership(pool: Pool, ticker: str, as_of: dt.datetime | None = None) -> dict[str, Any]:
    when = _aware(as_of)
    den = await pool.fetchrow(
        """select shares, as_of, method from public.shares_outstanding
            where ticker = $1 and filed is not null
              -- a filing is known at the end of its filing day (UTC), as every other lane
              and ((filed + 1)::timestamp at time zone 'UTC') <= $2
            order by as_of desc, id desc limit 1""",
        ticker,
        when,
    )
    f13, renames, trend, quarter = await _lane_13f(pool, ticker, when)
    view: OwnershipView = build_view(
        {
            "institutional_13f": f13,
            "control_13dg": await _lane_13dg(pool, ticker, when),
            "fund_nport": await _lane_nport(pool, ticker, when),
        },
        den["shares"] if den else None,
        den["as_of"] if den else None,
        not_covered=["national_voting"],
        as_of=when.date(),
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
        "quarter": quarter,
        "manager_renames": [
            {"cik": cik, "names": [{"from": str(p), "name": n} for p, n in names]}
            for cik, names in renames.items()
        ],
    }
