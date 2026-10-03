"""Partner breakdown of one product's trade: who the reporter trades it with, per flow.

Pure functions over `TradePoint` rows already scoped to one reporter and one product. The
World aggregate row is the reporter's total, never a partner. The part of that total not
carried by a reported partner row is `unmapped` and is kept apart: it is not folded into any
partner. Without a World row the total can only be the partner sum, so nothing is known to be
unmapped (`total_basis="partner_sum"`)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from steward.macro.geo import is_world
from steward.macro.providers import TradePoint

DEFAULT_HISTORY_PERIODS = 12


class TradePartner(BaseModel):
    partner: str = Field(description="Partner code as stored (UN M49 number for Comtrade).")
    partner_iso: str | None = Field(
        description="ISO alpha-2 when the code is a single country, else null (e.g. areas nes)."
    )
    value: Decimal = Field(description="Trade value in USD.")
    share: Decimal | None = Field(
        description="Fraction (0..1) of the product's reporter total for this flow and period."
    )


class TradePartnerPeriod(BaseModel):
    period: dt.date
    total: Decimal | None = Field(
        description="Reporter total in USD (World row, else partner sum)."
    )
    total_basis: Literal["world_row", "partner_sum"]
    mapped_value: Decimal = Field(description="Sum of the reported partner rows, USD.")
    unmapped_value: Decimal | None = Field(
        description="total - mapped_value (>= 0); null when no World total was reported."
    )
    unmapped_share: Decimal | None = Field(description="unmapped_value / total, a fraction.")
    partner_count: int
    hhi: Decimal | None = Field(
        description="Herfindahl index (0..1 fraction, x10,000 for the classic scale) over the "
        "reported partners' shares of mapped_value; the World aggregate is not a partner."
    )
    partners_exceed_total: bool = Field(
        default=False, description="Partner rows add up to more than the World row (bad data)."
    )


class TradeFlowPartners(BaseModel):
    flow: str
    period: dt.date = Field(description="Latest period with a value for this flow.")
    summary: TradePartnerPeriod
    partners: list[TradePartner] = Field(description="Latest period, largest first.")
    history: list[TradePartnerPeriod] = Field(
        description="Per-period totals, unmapped remainder and hhi, oldest first, bounded; "
        "includes the latest period."
    )


def _period_summary(
    period: dt.date, world: Decimal | None, partners: dict[str, Decimal]
) -> TradePartnerPeriod:
    mapped = sum(partners.values(), Decimal(0))
    total = world if world is not None else (mapped if partners else None)
    basis: Literal["world_row", "partner_sum"] = "world_row" if world is not None else "partner_sum"
    unmapped: Decimal | None = None
    unmapped_share: Decimal | None = None
    if world is not None:
        unmapped = max(Decimal(0), world - mapped)
        unmapped_share = unmapped / world if world else None
    hhi = sum(((v / mapped) ** 2 for v in partners.values()), Decimal(0)) if mapped > 0 else None
    return TradePartnerPeriod(
        period=period,
        total=total,
        total_basis=basis,
        mapped_value=mapped,
        unmapped_value=unmapped,
        unmapped_share=unmapped_share,
        partner_count=len(partners),
        hhi=hhi,
        partners_exceed_total=world is not None and mapped > world,
    )


def partner_breakdown(
    product: list[TradePoint], history_periods: int = DEFAULT_HISTORY_PERIODS
) -> list[TradeFlowPartners]:
    """One entry per flow of the product, flows sorted by name."""
    out: list[TradeFlowPartners] = []
    for flow in sorted({p.flow for p in product}):
        worlds: dict[dt.date, Decimal] = {}
        partners: dict[dt.date, dict[str, Decimal]] = {}
        isos: dict[str, str | None] = {}
        for row in product:
            if row.flow != flow or row.value is None:
                continue
            if is_world(row.partner, row.partner_iso):
                worlds[row.period] = worlds.get(row.period, Decimal(0)) + row.value
                continue
            bucket = partners.setdefault(row.period, {})
            bucket[row.partner] = bucket.get(row.partner, Decimal(0)) + row.value
            isos[row.partner] = row.partner_iso
        periods = sorted(worlds.keys() | partners.keys())
        if not periods:
            continue
        summaries = [_period_summary(p, worlds.get(p), partners.get(p, {})) for p in periods]
        latest = summaries[-1]
        rows = sorted(partners.get(latest.period, {}).items(), key=lambda kv: (-kv[1], kv[0]))
        out.append(
            TradeFlowPartners(
                flow=flow,
                period=latest.period,
                summary=latest,
                partners=[
                    TradePartner(
                        partner=code,
                        partner_iso=isos[code],
                        value=value,
                        share=value / latest.total if latest.total else None,
                    )
                    for code, value in rows
                ],
                history=summaries[-history_periods:] if history_periods > 0 else [],
            )
        )
    return out
