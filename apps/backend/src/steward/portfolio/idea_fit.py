"""Does a candidate idea reduce the portfolio's dominant factor tilt? (plan Q4.4, principle 7c.)

The candidate is added to a copy of today's book at a stated weight (taken from cash, so the
total is unchanged) and the factor exposures are computed again with the same machinery as the
Factors page. The relief is how much the distance from the benchmark's style loadings shrinks:
the Euclidean norm of the style tilts (market, size, value, profitability, investment, momentum)
before minus after. A positive relief means the idea makes the book look more like the
benchmark; it is a tie-breaker between comparable ideas, never a reason to take a weaker one.

The candidate needs stored daily prices (the prices job stores them for holdings, benchmarks and
proxies); one without them is listed with the reason and no figure."""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import replace
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.fx.convert import normalise_currency
from steward.portfolio import exante as ea
from steward.portfolio.factors import (
    STYLE_FACTORS,
    FactorBeta,
    FactorExposures,
    build_exposures,
    load_factor_context,
)
from steward.quant import french
from steward.quant import returns as rt
from steward.reference.price_series import PriceSymbols, choose_one
from steward.reference.read import load_series

METHOD_VERSION = "idea-fit-1"
METHOD = (
    "The idea is added to a copy of today's holdings at the stated weight, paid for from cash, "
    "and the factor regressions of the Factors page are run again. Relief is the distance of "
    "the style tilts from the benchmark before minus after (the Euclidean norm of the market, "
    "size, value, profitability, investment and momentum tilts). Positive relief means the idea "
    "moves the book towards the benchmark's style."
)
MAX_IDEAS = 10
MIN_WEIGHT = 0.005
MAX_WEIGHT = 0.20


class IdeaTilt(BaseModel):
    factor: str
    label: str
    before: float
    after: float


class IdeaFit(BaseModel):
    symbol: str
    status: Literal["ok", "no_prices", "no_fit"]
    reason: str | None = None
    history_weeks: int | None = None
    imbalance_before: float | None = Field(
        default=None, description="Distance of the style tilts from the benchmark, before."
    )
    imbalance_after: float | None = None
    relief: float | None = Field(
        default=None,
        description="Before minus after: positive means the idea moves the book towards the "
        "benchmark's style.",
    )
    dominant_factor: str | None = Field(
        default=None, description="The style factor with the largest tilt before the idea."
    )
    dominant_label: str | None = None
    dominant_before: float | None = None
    dominant_after: float | None = None
    tilts: list[IdeaTilt] = Field(default_factory=list)
    own_betas: list[FactorBeta] = Field(default_factory=list)
    note: str | None = None


class IdeaFitReport(BaseModel):
    region: str
    as_of: dt.date | None
    weight: float
    method_version: str = METHOD_VERSION
    method: str = METHOD
    ideas: list[IdeaFit]
    warnings: list[str] = Field(default_factory=list)
    reason: str | None = None


def imbalance(report: FactorExposures) -> tuple[float, dict[str, float]]:
    """The norm of the style tilts and the tilts themselves."""
    tilts = {
        t.factor: t.tilt for t in report.tilts if t.factor in STYLE_FACTORS and t.tilt is not None
    }
    return math.sqrt(sum(v * v for v in tilts.values())), tilts


def score_idea(
    symbol: str, before: FactorExposures, after: FactorExposures, history_weeks: int
) -> IdeaFit:
    """Compare the factor tilts before and after adding the idea (pure)."""
    if after.portfolio is None or before.portfolio is None:
        return IdeaFit(
            symbol=symbol,
            status="no_fit",
            reason=after.reason or before.reason or "too few weeks with both prices and factors",
        )
    norm_before, tilts_before = imbalance(before)
    norm_after, tilts_after = imbalance(after)
    if not tilts_before:
        return IdeaFit(symbol=symbol, status="no_fit", reason="no benchmark to compare tilts with")
    dominant = max(tilts_before, key=lambda f: abs(tilts_before[f]))
    labels = {t.factor: t.label for t in before.tilts}
    own = next((p for p in after.positions if p.ticker == symbol), None)
    relief = norm_before - norm_after
    dom_before, dom_after = (
        tilts_before[dominant],
        tilts_after.get(dominant, tilts_before[dominant]),
    )
    direction = "towards" if abs(dom_after) < abs(dom_before) else "further from"
    note = (
        f"Main tilt: {labels.get(dominant, dominant)} ({dom_before:+.2f}). Adding the idea moves "
        f"it {direction} the benchmark ({dom_after:+.2f}); overall distance "
        f"{norm_before:.2f} to {norm_after:.2f}."
    )
    return IdeaFit(
        symbol=symbol,
        status="ok",
        history_weeks=history_weeks,
        imbalance_before=norm_before,
        imbalance_after=norm_after,
        relief=relief,
        dominant_factor=dominant,
        dominant_label=labels.get(dominant, dominant),
        dominant_before=dom_before,
        dominant_after=dom_after,
        tilts=[
            IdeaTilt(
                factor=f,
                label=labels.get(f, f),
                before=tilts_before[f],
                after=tilts_after.get(f, 0.0),
            )
            for f in tilts_before
        ],
        own_betas=own.fit.betas if own is not None and own.fit is not None else [],
        note=note,
    )


def book_with_idea(
    book: ea.LoadedBook,
    symbol: str,
    closes: list[tuple[dt.date, float]],
    currency: str,
    weight: float,
    fx: rt.FxLookup,
) -> ea.LoadedBook:
    """A copy of the book with the idea held at `weight` of the total (pure)."""
    if book.start is None or book.end is None:
        raise ValueError("the book carries no window")
    inputs = [*book.inputs, rt.SeriesInput(symbol, closes, currency)]
    weekly = rt.build_return_matrix(inputs, fx, "weekly", book.start, book.end)
    position = ea.BookPosition(symbol, symbol, currency, weight * book.total_value)
    return replace(
        book,
        positions=[*book.positions, position],
        weekly=weekly.last(ea.WINDOW_WEEKS),
        daily_closes={**book.daily_closes, symbol: closes},
        inputs=inputs,
    )


async def idea_fit(
    pool: asyncpg.Pool,
    symbols: list[str],
    currency: str = "USD",
    region: str = "developed",
    weight: float = 0.05,
) -> IdeaFitReport:
    """Rank the ideas by how much each reduces the dominant factor tilt of today's book."""
    weight = min(max(weight, MIN_WEIGHT), MAX_WEIGHT)
    symbols = list(dict.fromkeys(s.strip() for s in symbols if s.strip()))[:MAX_IDEAS]
    ccy = normalise_currency(currency)[0]
    if region not in french.REGIONS:
        return IdeaFitReport(
            region="developed", as_of=None, weight=weight, ideas=[], reason="unknown region"
        )
    ctx, why = await load_factor_context(pool, region, extra_currencies=[ccy])
    if ctx is None:
        return IdeaFitReport(region=region, as_of=None, weight=weight, ideas=[], reason=why)
    base = build_exposures(ctx.book, ctx.daily, ctx.fx, region, ctx.preferences, ctx.yields)
    if base.portfolio is None:
        return IdeaFitReport(
            region=region, as_of=base.as_of, weight=weight, ideas=[], reason=base.reason
        )
    since = dt.date.today() - dt.timedelta(weeks=ea.WINDOW_WEEKS + 60)
    ideas: list[IdeaFit] = []
    for symbol in symbols:
        ref = await choose_one(pool, PriceSymbols(stooq=symbol, yahoo=symbol))
        closes = await load_series(pool, ref, since) if ref is not None else []
        if len(closes) < 2:
            ideas.append(
                IdeaFit(
                    symbol=symbol,
                    status="no_prices",
                    reason="no stored daily prices; the prices job stores them for holdings, "
                    "benchmarks and proxies, so add the symbol there first",
                )
            )
            continue
        try:
            book2 = book_with_idea(ctx.book, symbol, closes, ccy, weight, ctx.fx)
        except ValueError as exc:
            ideas.append(IdeaFit(symbol=symbol, status="no_fit", reason=str(exc)))
            continue
        after = build_exposures(book2, ctx.daily, ctx.fx, region, ctx.preferences, ctx.yields)
        ideas.append(score_idea(symbol, base, after, len(closes) // 5))
    ideas.sort(key=lambda i: (i.relief is None, -(i.relief or 0.0)))
    return IdeaFitReport(
        region=region,
        as_of=base.as_of,
        weight=weight,
        ideas=ideas,
        warnings=base.warnings,
    )
