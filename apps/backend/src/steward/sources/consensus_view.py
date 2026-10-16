# ruff: noqa: E501 - reason strings read better unbroken
"""Analyst consensus read model (insight plan I8): the latest target and its spread, how much the
analysts agree, the stored history, and, once snapshots are a year old, how the targets fared.

Only what the `consensus_snapshot` job stored is shown. A holding whose sources are paid-only or
not covered is `unavailable` with the recorded reason in every place a target would appear; no
target is synthesised and no zero stands in for a missing one.
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.analytics.contract import rounded
from steward.analytics.versions import version

Coverage = Literal["complete", "partial", "unavailable"]
Agreement = Literal["close", "moderate", "wide"]
# With several sources on one day the one with the most fields wins: Finnhub carries the analyst
# count, FMP the median, Twelve Data neither.
SOURCE_ORDER = ("finnhub", "fmp", "twelve_data")
TRACK_MONTHS = 12
AGREEMENT_TEXT: dict[Agreement, str] = {
    "close": "Analysts' targets sit close together",
    "moderate": "Analysts' targets are moderately spread",
    "wide": "Analysts disagree widely on the target",
}


class SourceState(BaseModel):
    source: str
    access_state: str
    as_of: dt.date
    detail: str | None


class ConsensusPoint(BaseModel):
    as_of: dt.date
    source: str
    mean: Decimal | None
    median: Decimal | None
    high: Decimal | None
    low: Decimal | None
    count: int | None
    currency: str | None
    price: Decimal | None


class ConsensusLatest(BaseModel):
    point: ConsensusPoint
    upside: Decimal | None = Field(description="Mean target / price at the snapshot - 1.")
    dispersion: Decimal | None = Field(description="(High - low) / mean.")
    sd: Decimal | None = Field(
        default=None,
        description="Standard deviation of targets when the source gives one; null otherwise.",
    )
    agreement: Agreement | None
    agreement_text: str


class TrackRow(BaseModel):
    as_of: dt.date
    target: Decimal
    price_then: Decimal | None
    price_after: Decimal | None
    after_date: dt.date | None
    reached: bool | None
    error: Decimal | None = Field(description="Price 12 months later / target - 1.")


class TrackRecord(BaseModel):
    available: bool
    starts: dt.date | None = Field(description="First date a 12-month-old snapshot exists.")
    reason: str | None = None
    rows: list[TrackRow] = Field(default_factory=list)
    hit_rate: Decimal | None = None
    mean_error: Decimal | None = None
    n: int = 0


class ConsensusPrice(BaseModel):
    date: dt.date
    close: Decimal


class ConsensusFooter(BaseModel):
    as_of: dt.date
    source: str
    basis: str


class ConsensusView(BaseModel):
    ticker: str
    coverage: Coverage
    coverage_reason: str
    sources: list[SourceState]
    latest: ConsensusLatest | None
    history: list[ConsensusPoint]
    chart_available: bool
    chart_reason: str | None
    snapshots: int
    min_snapshots: int
    prices: list[ConsensusPrice]
    price_currency: str | None
    track: TrackRecord
    method_version: str
    footer: ConsensusFooter


class HoldingConsensus(BaseModel):
    ticker: str
    name: str | None
    coverage: Coverage
    reason: str
    source: str | None
    as_of: dt.date | None
    mean: Decimal | None
    count: int | None
    upside: Decimal | None
    agreement: Agreement | None


class PortfolioConsensus(BaseModel):
    holdings: list[HoldingConsensus]
    complete: int
    partial: int
    unavailable: int
    note: str = (
        "Targets are stored snapshots from the providers your plan can reach; none are synthesised."
    )


# ------------------------------------------------------------------------------- pure parts


def _num(v: Any) -> Decimal | None:
    return None if v is None else Decimal(v)


def to_point(r: Any) -> ConsensusPoint:
    return ConsensusPoint(
        as_of=r["as_of"],
        source=r["source"],
        mean=_num(r["target_mean"]),
        median=_num(r["target_median"]),
        high=_num(r["target_high"]),
        low=_num(r["target_low"]),
        count=r["analyst_count"],
        currency=r["target_currency"],
        price=_num(r["price_at_snapshot"]),
    )


def best_per_day(rows: Sequence[Any]) -> list[ConsensusPoint]:
    """One point per snapshot date: the first source in `SOURCE_ORDER` that has a mean target."""
    by_day: dict[dt.date, list[Any]] = {}
    for r in rows:
        if r["target_mean"] is not None or r["target_median"] is not None:
            by_day.setdefault(r["as_of"], []).append(r)
    order = {s: i for i, s in enumerate(SOURCE_ORDER)}
    out = []
    for day in sorted(by_day):
        best = min(by_day[day], key=lambda r: order.get(r["source"], 99))
        p = to_point(best)
        if p.mean is None:
            p = p.model_copy(update={"mean": p.median})
        out.append(p)
    return out


def agreement(dispersion: Decimal | None, close: float, wide: float) -> Agreement | None:
    if dispersion is None:
        return None
    if dispersion < Decimal(str(close)):
        return "close"
    return "wide" if dispersion >= Decimal(str(wide)) else "moderate"


def latest_of(
    p: ConsensusPoint, raw: dict[str, Any] | None, close: float, wide: float
) -> ConsensusLatest:
    upside = rounded(p.mean / p.price - 1, "ratio") if p.mean and p.price else None
    dispersion = (
        rounded((p.high - p.low) / p.mean, "ratio") if p.high and p.low and p.mean else None
    )
    sd = None
    if raw:
        for k in ("targetStdDev", "std_dev", "standard_deviation"):
            if raw.get(k) is not None:
                try:
                    sd = Decimal(str(raw[k]))
                except ArithmeticError:
                    sd = None
                break
    a = agreement(dispersion, close, wide)
    if a is None:
        text = "Agreement unknown: the source gives no high and low target"
    else:
        text = f"{AGREEMENT_TEXT[a]} (spread {float(dispersion or 0) * 100:.0f}% of the mean)"
    if p.count is not None:
        text += f"; {p.count} analysts"
    return ConsensusLatest(
        point=p, upside=upside, dispersion=dispersion, sd=sd, agreement=a, agreement_text=text
    )


def track_record(
    history: Sequence[ConsensusPoint],
    closes: Sequence[tuple[dt.date, Decimal]],
    price_currency: str | None,
    today: dt.date,
    months: int = TRACK_MONTHS,
) -> TrackRecord:
    """For each snapshot at least `months` (12) months old: was the target reached at any close
    within those months (above or below the price, whichever way the target pointed), and how
    far the price at the end sat from it. The clock retrospective also reads 3 and 6 months."""
    if not history:
        return TrackRecord(available=False, starts=None, reason="no consensus snapshot stored")
    first = history[0].as_of
    starts = _add_months(first, months)
    old = [h for h in history if _add_months(h.as_of, months) <= today]
    if not old:
        return TrackRecord(
            available=False,
            starts=starts,
            reason=f"needs snapshots at least {months} months old; starts {starts.isoformat()}",
        )
    rows: list[TrackRow] = []
    for h in old:
        if h.mean is None or (price_currency and h.currency and h.currency != price_currency):
            continue
        end = _add_months(h.as_of, months)
        window = [(d, c) for d, c in closes if h.as_of < d <= end]
        after = next(
            ((d, c) for d, c in reversed(closes) if d <= end and (end - d).days <= 10), None
        )
        then = h.price or next((c for d, c in reversed(closes) if d <= h.as_of), None)
        reached: bool | None = None
        if window and then is not None:
            reached = (
                max(c for _, c in window) >= h.mean
                if h.mean >= then
                else min(c for _, c in window) <= h.mean
            )
        rows.append(
            TrackRow(
                as_of=h.as_of,
                target=h.mean,
                price_then=then,
                price_after=after[1] if after else None,
                after_date=after[0] if after else None,
                reached=reached,
                error=rounded(after[1] / h.mean - 1, "ratio") if after else None,
            )
        )
    judged = [r for r in rows if r.reached is not None]
    errors = [float(r.error) for r in rows if r.error is not None]
    if not judged:
        return TrackRecord(
            available=False,
            starts=starts,
            reason=f"no stored prices cover the {months} months after the old snapshots",
            rows=rows,
        )
    return TrackRecord(
        available=True,
        starts=starts,
        rows=rows,
        n=len(judged),
        hit_rate=Decimal(str(round(sum(1 for r in judged if r.reached) / len(judged), 4))),
        mean_error=Decimal(str(round(statistics.fmean(errors), 4))) if errors else None,
    )


def _add_months(d: dt.date, months: int) -> dt.date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return dt.date(year, month, day)
        except ValueError:
            continue
    return dt.date(year, month, 28)


def coverage_of(
    states: Sequence[SourceState], history: Sequence[ConsensusPoint]
) -> tuple[Coverage, str]:
    if not states:
        return "unavailable", "no consensus snapshot has been taken for this holding yet"
    latest_day = max(s.as_of for s in states)
    today_states = [s for s in states if s.as_of == latest_day]
    if any(s.access_state == "complete" for s in today_states):
        src = next(s.source for s in today_states if s.access_state == "complete")
        return "complete", f"target and estimates from {src} on {latest_day.isoformat()}"
    if any(s.access_state == "partial" for s in today_states) or history:
        part = next((s.source for s in today_states if s.access_state == "partial"), None)
        return "partial", (
            f"only part of the consensus from {part} on {latest_day.isoformat()}"
            if part
            else f"no current target; last stored {history[-1].as_of.isoformat()}"
        )
    reasons = "; ".join(
        f"{s.source}: {s.access_state.replace('_', ' ')}{f' ({s.detail})' if s.detail else ''}"
        for s in today_states
    )
    return "unavailable", f"no source returned a target on {latest_day.isoformat()} ({reasons})"


# ------------------------------------------------------------------------------- loading

_SELECT = "select ticker, as_of, source, analyst_count, target_mean, target_median, target_high, target_low, target_currency, price_at_snapshot, access_state, detail, raw from public.consensus_snapshot"


async def consensus_view(
    pool: asyncpg.Pool,
    ticker: str,
    *,
    close: float,
    wide: float,
    min_snapshots: int,
    today: dt.date | None = None,
) -> ConsensusView:
    from steward.company import service

    today = today or dt.date.today()
    rows = await pool.fetch(
        _SELECT + " where ticker=$1 order by as_of, source",
        ticker,
    )
    states = [
        SourceState(
            source=r["source"], access_state=r["access_state"], as_of=r["as_of"], detail=r["detail"]
        )
        for r in rows
    ]
    latest_states: dict[str, SourceState] = {}
    for s in states:
        latest_states[s.source] = s
    history = best_per_day(rows)
    coverage, reason = coverage_of(list(latest_states.values()), history)
    latest = None
    if history:
        p = history[-1]
        raw = next(
            (r["raw"] for r in rows if r["as_of"] == p.as_of and r["source"] == p.source), None
        )
        raw = json.loads(raw) if isinstance(raw, str) else raw
        latest = latest_of(p, raw if isinstance(raw, dict) else None, close, wide)
    got = await service.load_market(pool, ticker)
    market = got[1] if got else None
    closes = market.closes if market else []
    weekly: dict[tuple[int, int], tuple[dt.date, Decimal]] = {}
    for d, c in closes:
        if d >= today - dt.timedelta(days=760):
            y, w, _ = d.isocalendar()
            weekly[(y, w)] = (d, c)
    prices = [ConsensusPrice(date=d, close=c) for d, c in (weekly[k] for k in sorted(weekly))]
    chart_ok = len(history) >= min_snapshots
    return ConsensusView(
        ticker=ticker,
        coverage=coverage,
        coverage_reason=reason,
        sources=sorted(latest_states.values(), key=lambda s: s.source),
        latest=latest,
        history=history,
        chart_available=chart_ok,
        chart_reason=None
        if chart_ok
        else f"{len(history)} snapshots with a target; the chart needs {min_snapshots}",
        snapshots=len(history),
        min_snapshots=min_snapshots,
        prices=prices,
        price_currency=market.currency if market else None,
        track=track_record(history, closes, market.currency if market else None, today),
        method_version=version("consensus_view"),
        footer=ConsensusFooter(
            as_of=today,
            source=", ".join(sorted({s.source for s in states})) or "no source",
            basis="Stored weekly snapshots; one source per day (Finnhub, then FMP, then Twelve Data)",
        ),
    )


async def portfolio_consensus(
    pool: asyncpg.Pool, *, close: float, wide: float
) -> PortfolioConsensus:
    from steward.reference.identity import holding_tickers

    tickers = sorted(await holding_tickers(pool))
    names = {
        r["ticker"]: r["name"]
        for r in await pool.fetch(
            "select ticker, name from public.investment_universe where ticker = any($1::text[])",
            tickers,
        )
    }
    rows = await pool.fetch(
        _SELECT + " where ticker = any($1::text[]) order by ticker, as_of, source",
        tickers,
    )
    by: dict[str, list[Any]] = {}
    for r in rows:
        by.setdefault(r["ticker"], []).append(r)
    out: list[HoldingConsensus] = []
    for t in tickers:
        mine = by.get(t, [])
        latest_states: dict[str, SourceState] = {}
        for r in mine:
            latest_states[r["source"]] = SourceState(
                source=r["source"],
                access_state=r["access_state"],
                as_of=r["as_of"],
                detail=r["detail"],
            )
        history = best_per_day(mine)
        coverage, reason = coverage_of(list(latest_states.values()), history)
        lat = latest_of(history[-1], None, close, wide) if history else None
        out.append(
            HoldingConsensus(
                ticker=t,
                name=names.get(t),
                coverage=coverage,
                reason=reason,
                source=lat.point.source if lat else None,
                as_of=lat.point.as_of if lat else None,
                mean=lat.point.mean if lat else None,
                count=lat.point.count if lat else None,
                upside=lat.upside if lat else None,
                agreement=lat.agreement if lat else None,
            )
        )
    return PortfolioConsensus(
        holdings=out,
        complete=sum(1 for h in out if h.coverage == "complete"),
        partial=sum(1 for h in out if h.coverage == "partial"),
        unavailable=sum(1 for h in out if h.coverage == "unavailable"),
    )
