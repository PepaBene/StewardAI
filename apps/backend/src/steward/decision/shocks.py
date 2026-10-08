"""Scenario shocks over a complete portfolio (plan M6.2).

A shock is a stated move in one risk factor: the equity market, interest rates, credit spreads,
a currency against the base currency, a sector's demand or a country's demand. Each leaf of the
X-Ray (a direct holding or a fund row) is moved by the sensitivities that apply to it:

- equity market: `beta x move`, where beta is measured on the holding's own history when the sample
  and fit are good enough (`analytics.risk.regression_sensitivity`), else the owner's value, else
  the owner's stated fallback for unmeasured holdings, else the leaf is not shocked and listed;
- interest rates: `-duration x change`, with a duration the owner or the fund facts record;
- credit spreads: `-spread duration x change`, the same;
- currency: a leaf quoted in the shocked currency moves one for one (translation only; the
  trading currency is not the company's economic exposure, and the page says so);
- sector and country demand: the stated move applies to leaves labelled with that sector or
  country, on top of the market move. Labels the data does not carry are never guessed.

Weights are shares of total portfolio value. A leaf with no applicable sensitivity is left at its
value and reported in `unshocked` with the reason, so the shocked total is always a complete
portfolio: `after = before + the sum of the shocked leaves' P&L`, and the totals reconcile by
construction. BIS/ECB/OECD/World Bank/Comtrade series describe a scenario; they are never a
sensitivity by themselves.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, Field

from steward.analytics.versions import version
from steward.lookthrough.read import CASH, NOT_LOOKED, UNKNOWN_FUND
from steward.xray.leaves import Leaf, LeafSet

Source = Literal["measured", "owner", "recorded", "fallback", "mechanical", "stated"]

# An equity-like leaf is moved by the market; a fixed-income or other leaf by rates and credit.
EQUITY = "equity"


class ShockSet(BaseModel):
    """The factor moves of one scenario. Every field is optional; an empty set shocks nothing."""

    equity_pct: float | None = Field(
        default=None, ge=-0.95, le=2, description="Equity market move, fraction (-0.2 = -20 %)."
    )
    rate_bp: float | None = Field(
        default=None, ge=-1000, le=1000, description="Parallel change in rates, basis points."
    )
    credit_bp: float | None = Field(
        default=None, ge=-1000, le=3000, description="Change in credit spreads, basis points."
    )
    fx: dict[str, float] = Field(
        default_factory=dict,
        description="Currency code -> move against the base currency, fraction (USD -0.1).",
    )
    sector: dict[str, float] = Field(
        default_factory=dict, description="Sector -> demand move on top of the market, fraction."
    )
    country: dict[str, float] = Field(
        default_factory=dict,
        description="Country (ISO code) -> demand move on top of the market, fraction.",
    )


class ShockAssumptions(BaseModel):
    """Sensitivities the owner sets. A measured value is used unless the owner overrides it."""

    equity_beta: dict[str, float] = Field(
        default_factory=dict, description="Held ticker -> equity beta the owner assumes."
    )
    unmeasured_equity_beta: float | None = Field(
        default=1.0,
        ge=0,
        le=4,
        description="Beta used for a holding with no valid measurement; null leaves it unshocked.",
    )
    duration_years: dict[str, float] = Field(
        default_factory=dict, description="Held ticker -> effective duration the owner assumes."
    )
    spread_duration_years: dict[str, float] = Field(
        default_factory=dict, description="Held ticker -> spread duration the owner assumes."
    )


class Sensitivity(BaseModel):
    value: float
    source: Source
    detail: str = ""


class MeasuredInputs(BaseModel):
    """Sensitivities taken from history or stored facts, keyed by held ticker."""

    equity_beta: dict[str, Sensitivity] = Field(default_factory=dict)
    duration_years: dict[str, Sensitivity] = Field(default_factory=dict)
    equity_beta_reasons: dict[str, str] = Field(
        default_factory=dict, description="Why a holding has no measured beta."
    )


class ShockedPosition(BaseModel):
    key: str = Field(description="Held ticker, or the bucket name for non-position weight.")
    label: str
    weight: float
    shocked_weight: float = Field(description="Weight of the leaves that were moved.")
    pnl: float = Field(description="Change in value, as a share of total portfolio value.")
    return_pct: float | None = Field(description="pnl / weight; null with no weight.")
    effects: dict[str, float] = Field(
        description="P&L by factor: equity, rates, credit, fx, sector, country (cross terms kept "
        "in `interaction`)."
    )
    sensitivities: list[str] = Field(description="Each sensitivity used, with its source.")


class Unshocked(BaseModel):
    label: str
    weight: float
    reason: str


class ShockScenarioResult(BaseModel):
    method_version: str
    shocks: ShockSet
    assumptions: ShockAssumptions
    before_total: float = Field(description="Total portfolio value, base currency.")
    after_total: float
    pnl: float = Field(description="after_total - before_total, base currency.")
    pnl_pct: float
    positions: list[ShockedPosition]
    by_factor: dict[str, float] = Field(description="P&L share by factor; adds to pnl_pct.")
    shocked_weight: float
    unshocked_weight: float
    unshocked: list[Unshocked]
    cash_weight: float
    reconciles: bool = Field(description="after - before equals the sum of the P&L lines.")
    warnings: list[str]
    method: str


METHOD = (
    "Each leaf of the X-Ray is moved by the sensitivities that apply to it; a leaf with no "
    "valid sensitivity stays at its value and is listed with the reason, so the shocked "
    "portfolio is complete and its total equals the current total plus the P&L of the moved "
    "leaves. Equity moves use a beta measured on the holding's own history when the sample and "
    "fit meet the thresholds, else the owner's value, else the stated fallback. Rates and credit "
    "use a recorded or owner duration. A currency move translates only the leaves quoted in "
    "that currency (trading currency, not economic exposure). Sector and country moves apply "
    "to leaves carrying that label and are on top of the market move. Historical sensitivities "
    "describe the sample; they are not forecasts."
)

FACTORS = ("equity", "rates", "credit", "fx", "sector", "country", "interaction")


def _position_key(leaf: Leaf, leafset: LeafSet) -> tuple[str, str]:
    """The held position a leaf belongs to: a direct stock is its own position, a fund row
    belongs to the fund."""
    if leaf.channel_kind == "direct":
        return (leaf.ticker or leaf.name or "direct", leaf.name or leaf.ticker or "Direct holding")
    info = leafset.channels.get(leaf.channel_key)
    ticker = (info.ticker if info else None) or leaf.channel_key
    return ticker, leaf.channel_label


def _channel_ticker(leaf: Leaf, leafset: LeafSet) -> str | None:
    """Ticker that keys the sensitivity tables for this leaf."""
    if leaf.channel_kind == "direct":
        return leaf.ticker
    info = leafset.channels.get(leaf.channel_key)
    return info.ticker if info else None


def _beta(
    ticker: str | None, assumptions: ShockAssumptions, measured: MeasuredInputs
) -> Sensitivity | None:
    if ticker is None:
        return None
    if ticker in assumptions.equity_beta:
        return Sensitivity(
            value=assumptions.equity_beta[ticker], source="owner", detail="owner's beta"
        )
    if ticker in measured.equity_beta:
        return measured.equity_beta[ticker]
    if assumptions.unmeasured_equity_beta is not None:
        reason = measured.equity_beta_reasons.get(ticker, "no measured history")
        return Sensitivity(
            value=assumptions.unmeasured_equity_beta,
            source="fallback",
            detail=f"owner's fallback for unmeasured holdings ({reason})",
        )
    return None


def _duration(
    ticker: str | None, table: dict[str, float], recorded: dict[str, Sensitivity]
) -> Sensitivity | None:
    if ticker is None:
        return None
    if ticker in table:
        return Sensitivity(value=table[ticker], source="owner", detail="owner's duration")
    return recorded.get(ticker)


def apply_shocks(
    leafset: LeafSet,
    shocks: ShockSet,
    assumptions: ShockAssumptions,
    measured: MeasuredInputs,
    *,
    cash_currency: str | None = None,
) -> ShockScenarioResult:
    """Shock every leaf, group by held position and reconcile to the whole portfolio."""
    base = leafset.base_currency
    positions: dict[str, ShockedPosition] = {}
    unshocked_by_key: dict[tuple[str, str], float] = defaultdict(float)
    warnings: list[str] = []
    cash_weight = 0.0
    total_pnl = 0.0
    by_factor: dict[str, float] = dict.fromkeys(FACTORS, 0.0)
    shocked_weight = 0.0

    for leaf in leafset.leaves:
        weight = leaf.weight
        if leaf.special == CASH:
            cash_weight += weight
            if cash_currency and base and cash_currency != base and cash_currency in shocks.fx:
                warnings.append(
                    f"Cash is held in {cash_currency}, not {base}; its currency move is not "
                    "applied because the cash currency split is unknown."
                )
            continue
        key, label = _position_key(leaf, leafset)
        pos = positions.setdefault(
            key,
            ShockedPosition(
                key=key,
                label=label,
                weight=0.0,
                shocked_weight=0.0,
                pnl=0.0,
                return_pct=None,
                effects=dict.fromkeys(FACTORS, 0.0),
                sensitivities=[],
            ),
        )
        pos.weight += weight
        ticker = _channel_ticker(leaf, leafset)
        effects, used, why = _leaf_effects(leaf, ticker, shocks, assumptions, measured, base)
        if effects is None:
            unshocked_by_key[(key, why)] += weight
            continue
        local = effects.pop("_local")
        fx_term = effects.pop("_fx")
        total = (1 + local) * (1 + fx_term) - 1
        parts = sum(effects.values())
        effects["interaction"] = total - parts
        pnl = weight * total
        pos.pnl += pnl
        pos.shocked_weight += weight
        shocked_weight += weight
        total_pnl += pnl
        for name, value in effects.items():
            pos.effects[name] += weight * value
            by_factor[name] += weight * value
        for text in used:
            if text not in pos.sensitivities:
                pos.sensitivities.append(text)

    unshocked = [
        Unshocked(label=_label_for(positions, key), weight=w, reason=reason)
        for (key, reason), w in sorted(unshocked_by_key.items(), key=lambda kv: -kv[1])
    ]
    for pos in positions.values():
        pos.return_pct = pos.pnl / pos.weight if pos.weight > 0 else None
    unshocked_weight = sum(u.weight for u in unshocked)
    total_weight = leafset.total
    before = total_weight
    after = before * (1 + total_pnl)
    ordered = sorted(positions.values(), key=lambda p: p.pnl)
    reconciles = abs((after - before) - before * sum(p.pnl for p in ordered)) < 1e-6 * max(
        1.0, before
    )
    if unshocked_weight > 0.0001:
        warnings.append(
            f"{unshocked_weight:.1%} of the portfolio has no valid sensitivity to the shocks "
            "applied and stays at its current value; the result is a floor on the exposure "
            "that was measurable, not a full stress."
        )
    if shocks.model_dump(exclude_defaults=True) == {}:
        warnings.append("No shock was set; nothing moved.")
    return ShockScenarioResult(
        method_version=version("scenario_shock"),
        shocks=shocks,
        assumptions=assumptions,
        before_total=before,
        after_total=after,
        pnl=after - before,
        pnl_pct=total_pnl,
        positions=ordered,
        by_factor=by_factor,
        shocked_weight=shocked_weight,
        unshocked_weight=unshocked_weight,
        unshocked=unshocked,
        cash_weight=cash_weight,
        reconciles=reconciles,
        warnings=warnings,
        method=METHOD,
    )


def _label_for(positions: dict[str, ShockedPosition], key: str) -> str:
    pos = positions.get(key)
    return pos.label if pos else key


def _leaf_effects(
    leaf: Leaf,
    ticker: str | None,
    shocks: ShockSet,
    assumptions: ShockAssumptions,
    measured: MeasuredInputs,
    base: str | None,
) -> tuple[dict[str, float] | None, list[str], str]:
    """(effects, sensitivities used, reason it was not shocked)."""
    used: list[str] = []
    effects = dict.fromkeys(FACTORS, 0.0)
    effects.update({"_local": 0.0, "_fx": 0.0})
    asked = (
        shocks.equity_pct is not None
        or shocks.rate_bp is not None
        or shocks.credit_bp is not None
        or bool(shocks.fx)
        or bool(shocks.sector)
        or bool(shocks.country)
    )
    if not asked:
        return effects, used, ""

    if leaf.asset_class == "cash":
        return None, used, "the fund's own cash: it does not move with these factors"
    if leaf.asset_class == "derivative":
        return None, used, "a derivative position: delta is not published"

    is_equity = leaf.asset_class == EQUITY and leaf.special is None
    lookthrough_missing = leaf.special in (NOT_LOOKED, UNKNOWN_FUND)
    any_effect = False

    if is_equity or lookthrough_missing:
        if shocks.equity_pct is not None:
            beta = _beta(ticker, assumptions, measured)
            if beta is not None and (is_equity or beta.source in ("measured", "owner")):
                move = beta.value * shocks.equity_pct
                effects["equity"] = move
                effects["_local"] += move
                used.append(f"equity beta {beta.value:.2f} ({beta.source}: {beta.detail})")
                any_effect = True
            elif lookthrough_missing:
                pass
            else:
                reason = (
                    "equity shock: no valid beta and no fallback set"
                    if ticker
                    else "equity shock: the holding is not identified"
                )
                return None, used, reason
    if is_equity:
        sector_move = shocks.sector.get(leaf.sector or "")
        if sector_move is not None:
            effects["sector"] = sector_move
            effects["_local"] += sector_move
            used.append(f"sector {leaf.sector}: stated {sector_move:+.1%}")
            any_effect = True
        country_move = shocks.country.get((leaf.country or "").upper())
        if country_move is not None:
            effects["country"] = country_move
            effects["_local"] += country_move
            used.append(f"country {leaf.country}: stated {country_move:+.1%}")
            any_effect = True
        if (leaf.sector is None and shocks.sector) or (leaf.country is None and shocks.country):
            # The label is absent: the demand shock cannot be placed on this leaf.
            pass
    else:
        if shocks.rate_bp is not None and not lookthrough_missing:
            dur = _duration(ticker, assumptions.duration_years, measured.duration_years)
            if dur is not None:
                move = -dur.value * shocks.rate_bp / 10_000
                effects["rates"] = move
                effects["_local"] += move
                used.append(f"duration {dur.value:.1f}y ({dur.source}: {dur.detail})")
                any_effect = True
        if shocks.credit_bp is not None and not lookthrough_missing:
            sd = assumptions.spread_duration_years.get(ticker or "")
            if sd is not None:
                move = -sd * shocks.credit_bp / 10_000
                effects["credit"] = move
                effects["_local"] += move
                used.append(f"spread duration {sd:.1f}y (owner)")
                any_effect = True

    # Currency translation: a leaf quoted in the shocked currency moves one for one.
    cur = (leaf.currency or "").upper()
    if cur and cur != (base or "") and cur in shocks.fx and not lookthrough_missing:
        fx_move = shocks.fx[cur]
        effects["fx"] = fx_move
        effects["_fx"] = fx_move
        used.append(f"{cur} {fx_move:+.1%} against {base} (translation of its quoted value)")
        any_effect = True

    if any_effect:
        return effects, used, ""
    return None, used, _not_moved_reason(leaf, shocks, lookthrough_missing)


def _not_moved_reason(leaf: Leaf, shocks: ShockSet, lookthrough_missing: bool) -> str:
    if lookthrough_missing:
        return "fund holdings are not stored: the composition is unknown"
    if leaf.asset_class != EQUITY:
        return "no duration or spread duration recorded for a rates or credit shock"
    wanted = []
    if shocks.sector and leaf.sector is None:
        wanted.append("sector")
    if shocks.country and leaf.country is None:
        wanted.append("country")
    if wanted:
        return f"no {' or '.join(wanted)} label to place the stated move on"
    return "no shock in this scenario applies to the holding"


def standard_set(base_currency: str | None, foreign: list[str]) -> list[tuple[str, ShockSet]]:
    """The named shocks the page offers as starting points. Every figure is a stated assumption
    that the owner can edit; none is a forecast."""
    fx_move = {c: -0.10 for c in foreign if c != base_currency}
    out = [
        ("Equities fall 20 %", ShockSet(equity_pct=-0.20)),
        ("Equities fall 35 %", ShockSet(equity_pct=-0.35)),
        ("Rates up 200 bp", ShockSet(rate_bp=200)),
        ("Credit spreads widen 300 bp", ShockSet(credit_bp=300)),
    ]
    if fx_move:
        label = ", ".join(sorted(fx_move))
        out.append((f"{label} fall 10 % against {base_currency}", ShockSet(fx=fx_move)))
    return out
