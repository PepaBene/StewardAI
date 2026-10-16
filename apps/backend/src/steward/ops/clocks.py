# ruff: noqa: E501
"""Retrospective of the insight clocks (insight I14.5).

Three features only become checkable with time: the dividend forecast (was the forecast paid?),
the analyst consensus (was the target reached?) and the fair-value history (is there enough of
it to read?). Each clock starts on its first stored snapshot. At 3, 6 and 12 months after that
start, this report gives:

- dividend forecast accuracy: the snapshot taken that many months ago against the payments of
  the following months (hit rate and mean per-share error), as `dividends.accuracy`;
- consensus hit rate: across holdings, the share of snapshots at least that old whose target was
  reached within the period, as `consensus_view.track_record`;
- the share of holdings with a usable fair-value history (at least 8 recorded dates) as of the
  checkpoint.
A checkpoint not yet reached says when it will be.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any

import asyncpg
from pydantic import BaseModel, Field

from steward.analytics.versions import version

HORIZONS = (3, 6, 12)
FV_USABLE_POINTS = 8


def add_months(d: dt.date, months: int) -> dt.date:
    from steward.sources.consensus_view import _add_months

    return _add_months(d, months)


class ClockStart(BaseModel):
    clock: str
    label: str
    started: dt.date | None


class Checkpoint(BaseModel):
    horizon_months: int
    reached: bool
    available_from: dt.date | None = Field(
        description="When the slowest clock reaches this checkpoint."
    )
    dividend_rows: int = 0
    dividend_hit_rate: float | None = None
    dividend_mean_error: float | None = None
    dividend_note: str | None = None
    consensus_judged: int = 0
    consensus_hit_rate: float | None = None
    consensus_note: str | None = None
    fair_value_holdings: int = 0
    fair_value_usable: int = 0
    fair_value_share: float | None = None
    fair_value_note: str | None = None


class ClockRetrospective(BaseModel):
    as_of: dt.date
    clocks: list[ClockStart]
    checkpoints: list[Checkpoint]
    method_version: str


def fair_value_share(
    points: dict[str, list[dt.date]], tickers: list[str], as_of: dt.date
) -> tuple[int, int]:
    """(usable, holdings): holdings with at least `FV_USABLE_POINTS` recorded dates by `as_of`."""
    usable = sum(
        1 for t in tickers if len([d for d in points.get(t, []) if d <= as_of]) >= FV_USABLE_POINTS
    )
    return usable, len(tickers)


async def clock_retrospective(
    pool: asyncpg.Pool, owner_id: Any, today: dt.date | None = None
) -> ClockRetrospective:
    from steward.company.service import load_market
    from steward.dividends import accuracy as acc
    from steward.dividends.service import load_context
    from steward.sources.consensus_view import _SELECT, best_per_day, track_record

    today = today or dt.date.today()
    ctx = await load_context(pool, today)
    tickers = sorted(ctx.holdings)
    div_start = await pool.fetchval("select min(as_of) from public.dividend_forecast_snapshot")
    cons_rows = await pool.fetch(
        _SELECT + " where ticker = any($1::text[]) order by ticker, as_of, source", tickers
    )
    fv_rows = await pool.fetch(
        "select ticker, array_agg(distinct as_of order by as_of) as days from public.fair_value_point"
        " where owner_id = $1 and ticker = any($2::text[]) group by ticker",
        owner_id,
        tickers,
    )
    fv_points: dict[str, list[dt.date]] = {r["ticker"]: list(r["days"]) for r in fv_rows}
    fv_start = min((d[0] for d in fv_points.values() if d), default=None)
    by_ticker: dict[str, list[Any]] = {}
    for r in cons_rows:
        by_ticker.setdefault(r["ticker"], []).append(r)
    histories = {t: best_per_day(rows) for t, rows in by_ticker.items()}
    cons_start = min((h[0].as_of for h in histories.values() if h), default=None)
    closes: dict[str, tuple[list[tuple[dt.date, Decimal]], str | None]] = {}
    for t in histories:
        got = await load_market(pool, t)
        closes[t] = (got[1].closes, got[1].currency) if got else ([], None)

    checkpoints: list[Checkpoint] = []
    for h in HORIZONS:
        days = round(h * 365.25 / 12)
        # The checkpoint waits for the slowest clock: one that has not started holds it back.
        starts = [div_start, cons_start, fv_start]
        available_from = (
            add_months(max(s for s in starts if s is not None), h)
            if all(s is not None for s in starts)
            else None
        )
        cp = Checkpoint(
            horizon_months=h,
            reached=available_from is not None and available_from <= today,
            available_from=available_from,
        )
        # dividends
        as_of = await pool.fetchval(
            "select max(as_of) from public.dividend_forecast_snapshot where as_of <= $1",
            today - dt.timedelta(days=days),
        )
        bodies: dict[str, dict[str, Any]] = {}
        if as_of is not None:
            for r in await pool.fetch(
                "select ticker, schedule from public.dividend_forecast_snapshot where as_of = $1",
                as_of,
            ):
                bodies[r["ticker"]] = (
                    json.loads(r["schedule"]) if isinstance(r["schedule"], str) else r["schedule"]
                )
        a = acc.forecast_accuracy(
            bodies,
            as_of,
            div_start,
            ctx.payments,
            today,
            ctx.rules.dividends.match_window_days,
            horizon_days=days,
        )
        cp.dividend_rows = a.rows
        cp.dividend_hit_rate = None if a.hit_rate is None else float(a.hit_rate)
        cp.dividend_mean_error = None if a.mean_error is None else float(a.mean_error)
        cp.dividend_note = a.note or (
            f"snapshot of {as_of.isoformat()} against the {h} months after it" if as_of else None
        )
        # consensus
        judged = reached = 0
        starts_at: dt.date | None = None
        for t, hist in histories.items():
            series, currency = closes.get(t, ([], None))
            tr = track_record(hist, series, currency, today, months=h)
            if tr.starts is not None:
                starts_at = tr.starts if starts_at is None else min(starts_at, tr.starts)
            for row in tr.rows:
                if row.reached is not None:
                    judged += 1
                    reached += int(row.reached)
        cp.consensus_judged = judged
        cp.consensus_hit_rate = reached / judged if judged else None
        cp.consensus_note = (
            None if judged
            else "no consensus snapshot stored yet" if starts_at is None
            else f"needs snapshots {h} months old with prices after them; starts {starts_at.isoformat()}"
        )  # fmt: skip
        # fair value
        point = add_months(fv_start, h) if fv_start else None
        if point is None:
            cp.fair_value_note = "no fair value recorded yet"
        else:
            usable, n = fair_value_share(fv_points, tickers, min(point, today))
            cp.fair_value_usable, cp.fair_value_holdings = usable, n
            cp.fair_value_share = usable / n if n else None
            cp.fair_value_note = (
                f"as of {point.isoformat()}"
                if point <= today
                else f"so far; the checkpoint is {point.isoformat()}"
            )
        checkpoints.append(cp)
    return ClockRetrospective(
        as_of=today,
        clocks=[
            ClockStart(
                clock="dividend_forecast", label="Dividend forecast snapshots", started=div_start
            ),
            ClockStart(clock="consensus", label="Analyst consensus snapshots", started=cons_start),
            ClockStart(clock="fair_value", label="Fair-value history", started=fv_start),
        ],
        checkpoints=checkpoints,
        method_version=version("clock_retrospective"),
    )
