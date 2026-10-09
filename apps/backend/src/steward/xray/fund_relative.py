"""Fund-level benchmark-relative view (plan M5.6): each held fund against the benchmark you
recorded for it, and the benchmark proxy fund against its own.

The figure is the annualised tracking difference the fund's performance page shows, computed only
when the fund and its benchmark are on the same basis and currency. A fund with no recorded
benchmark, or no prices, says why instead of showing a number.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING

import asyncpg
from pydantic import BaseModel, Field

from steward.fundresearch import service as fund_service
from steward.fundresearch.data import load_fund
from steward.fundresearch.performance import tracking_headline
from steward.lookthrough.read import is_fund_type

if TYPE_CHECKING:
    from steward.xray.service import Loaded

MAX_FUNDS = 40


class FundRelativeRow(BaseModel):
    isin: str
    ticker: str | None
    name: str | None
    weight: float | None = Field(
        description="Share of the portfolio; None for the proxy fund alone."
    )
    benchmark: str | None
    tracking_difference: float | None = Field(
        description="Annualised, fund minus benchmark, same basis and currency only."
    )
    reason: str | None = Field(description="Why there is no figure.")
    is_proxy: bool = Field(description="The fund the X-Ray uses as its benchmark proxy.")


class FundRelative(BaseModel):
    as_of: dt.date
    rows: list[FundRelativeRow]
    covered_weight: float = Field(description="Portfolio weight of the held funds with a figure.")
    fund_weight: float = Field(description="Portfolio weight of all held funds.")
    average: float | None = Field(
        description="Weight-averaged tracking difference over the funds with a figure."
    )
    notes: list[str]


def weighted_average(pairs: list[tuple[float, float]]) -> float | None:
    """Weight-averaged value over (weight, value) pairs; None without any weight."""
    total = sum(w for w, _ in pairs)
    return sum(w * v for w, v in pairs) / total if total > 0 else None


async def _row(
    pool: asyncpg.Pool,
    owner_id: str,
    isin: str,
    ticker: str | None,
    name: str | None,
    weight: float | None,
    as_of: dt.date,
    is_proxy: bool,
) -> FundRelativeRow:
    data = await load_fund(pool, isin, owner_id, as_of)
    if data is None:
        return FundRelativeRow(
            isin=isin,
            ticker=ticker,
            name=name,
            weight=weight,
            benchmark=None,
            tracking_difference=None,
            reason="the fund is not in the fund register",
            is_proxy=is_proxy,
        )
    view = await fund_service.performance_view(pool, data, None, None, as_of)
    diff, reason = tracking_headline(view)
    label = view.benchmark.label if view.benchmark else None
    return FundRelativeRow(
        isin=isin,
        ticker=ticker,
        name=name,
        weight=weight,
        benchmark=label,
        tracking_difference=float(diff) if isinstance(diff, Decimal) else None,
        reason=None if diff is not None else reason,
        is_proxy=is_proxy,
    )


async def build(pool: asyncpg.Pool, owner_id: str, loaded: Loaded) -> FundRelative:
    pf = loaded.data.portfolio
    total = loaded.leafset.total
    settings = loaded.settings
    proxy_isins = {p.fund_isin for p in settings.benchmark_proxies}
    held = [
        p
        for p in (pf.positions if pf else [])
        if p.isin and (settings.fund(p.isin) is not None or is_fund_type(p.security_type))
    ]
    held.sort(key=lambda p: -p.value_base)
    notes: list[str] = []
    if len(held) > MAX_FUNDS:
        notes.append(f"Only the {MAX_FUNDS} largest of {len(held)} funds were computed.")
    rows: list[FundRelativeRow] = []
    seen: set[str] = set()
    for p in held[:MAX_FUNDS]:
        weight = float(p.value_base / total) if total > 0 else None
        assert p.isin is not None
        seen.add(p.isin)
        rows.append(
            await _row(
                pool, owner_id, p.isin, p.ticker, p.name, weight, loaded.day, p.isin in proxy_isins
            )
        )
    for isin in sorted(proxy_isins - seen):
        rows.append(await _row(pool, owner_id, isin, None, None, None, loaded.day, True))
    fund_weight = sum(r.weight or 0.0 for r in rows if r.weight is not None)
    figured = [
        (r.weight, r.tracking_difference)
        for r in rows
        if r.weight is not None and r.tracking_difference is not None
    ]
    if not any(r.benchmark for r in rows):
        notes.append(
            "No held fund has a benchmark recorded. Record one on the fund's page (a benchmark "
            "symbol, its basis and its currency) to see how far it strays from it."
        )
    notes.append(
        "Tracking difference is fund minus benchmark. It is shown only where both are on the same "
        "basis (price or total return) and currency; otherwise the reason is given."
    )
    return FundRelative(
        as_of=loaded.day,
        rows=rows,
        covered_weight=sum(w for w, _ in figured),
        fund_weight=fund_weight,
        average=weighted_average(figured),
        notes=notes,
    )
