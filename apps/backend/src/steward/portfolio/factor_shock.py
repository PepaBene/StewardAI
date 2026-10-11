"""Hypothetical factor shocks on today's holdings, and saved scenarios (quant plan Q3.2, Q3.3).

A shock states how far some factors move: the equity market, the size, value, profitability,
investment and momentum styles, the US dollar and the euro against the base currency, and the
US 10-year yield. Each holding is moved by its measured loadings (`portfolio.factors`):

    move_i = sum_f beta_if * shock_f        floored at -100 %

Where a holding has no measured loadings, the owner's market beta for it is used if one is
set (labelled `owner`), else the owner's fallback beta (labelled `fallback`), else it is left
flat and listed as `unshocked`. An owner beta replaces only the market loading; the holding's
other measured loadings still apply. Loadings come from weekly regressions, so a large shock is
an extrapolation: the result says so rather than pretending to precision.

The result reconciles by construction: `after = before + the sum of the positions' P&L`.
Saved scenarios keep the shock and its assumptions; each run is saved with the loadings it used
(`risk_report_snapshot`, kind `shock`).
"""

import datetime as dt
import hashlib
import json
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.config.store import load_active
from steward.config.strategy import StrategyConfig
from steward.portfolio.factors import FactorExposures, factor_exposures
from steward.portfolio.risk_snapshots import save_snapshot

METHOD_VERSION = "factor-shock-1"
METHOD = (
    "Each holding moves by the sum of its measured factor loadings times the stated factor "
    "moves, never below -100 %. Loadings come from two years of weekly regressions, so a "
    "large shock is an extrapolation. Owner betas replace only the market loading."
)

# shock field -> factor code
FIELD_FACTOR = {
    "market": "MKT_RF",
    "size": "SMB",
    "value": "HML",
    "profitability": "RMW",
    "investment": "CMA",
    "momentum": "MOM",
    "usd": "USD",
    "eur": "EUR",
    "rates_pp": "RATE10",
}


class ShockSpec(BaseModel):
    """The factor moves of one scenario. Every move is optional; an empty set shocks nothing."""

    market: float | None = Field(
        default=None, ge=-0.95, le=2, description="Equity market move, fraction (-0.2 = -20 %)."
    )
    size: float | None = Field(default=None, ge=-0.5, le=0.5, description="Small minus big.")
    value: float | None = Field(default=None, ge=-0.5, le=0.5, description="Value minus growth.")
    profitability: float | None = Field(default=None, ge=-0.5, le=0.5)
    investment: float | None = Field(default=None, ge=-0.5, le=0.5)
    momentum: float | None = Field(default=None, ge=-0.5, le=0.5)
    usd: float | None = Field(
        default=None, ge=-0.5, le=0.5, description="US dollar against the base currency."
    )
    eur: float | None = Field(
        default=None, ge=-0.5, le=0.5, description="Euro against the base currency."
    )
    rates_pp: float | None = Field(
        default=None, ge=-5, le=5, description="Change in the US 10-year yield, points."
    )
    other_fx: dict[str, float] = Field(
        default_factory=dict,
        description="Any other currency code (GBP, CHF) -> move against the base currency, "
        "fraction. Applies to holdings with a measured loading on that currency.",
    )
    owner_betas: dict[str, float] = Field(
        default_factory=dict, description="Held ticker -> market beta the owner assumes."
    )
    unmeasured_beta: float | None = Field(
        default=1.0,
        ge=0,
        le=4,
        description="Market beta for a holding with no measured loadings; null leaves it flat.",
    )


class ShockDriver(BaseModel):
    factor: str
    label: str
    contribution: float = Field(description="Part of the holding's move from this factor.")


class ShockPosition(BaseModel):
    ticker: str
    name: str | None
    weight: float
    basis: Literal["measured", "owner", "fallback", "unshocked"]
    move: float | None
    pnl: float
    drivers: list[ShockDriver]


class ShockResult(BaseModel):
    region: str
    as_of: dt.date | None
    base_currency: str | None
    method_version: str
    method: str
    spec: ShockSpec
    before: float
    after: float | None
    pnl: float | None
    loss: float | None
    breaches_limit: bool | None
    drawdown_limit: float | None
    unshocked_weight: float
    positions: list[ShockPosition]
    worst: list[ShockPosition]
    warnings: list[str]
    reason: str | None


def apply_shock(
    exposures: FactorExposures, spec: ShockSpec, drawdown_limit: float | None = None
) -> ShockResult:
    """Move today's holdings by the shock (pure given the measured exposures)."""
    if exposures.total_value is None or exposures.portfolio is None:
        return ShockResult(
            region=exposures.region, as_of=None, base_currency=exposures.base_currency,
            method_version=METHOD_VERSION, method=METHOD, spec=spec, before=0.0, after=None,
            pnl=None, loss=None, breaches_limit=None, drawdown_limit=drawdown_limit,
            unshocked_weight=0.0, positions=[], worst=[], warnings=exposures.warnings,
            reason=exposures.reason or "no factor exposures to shock",
        )  # fmt: skip
    total = exposures.total_value
    stated = {
        factor: getattr(spec, name)
        for name, factor in FIELD_FACTOR.items()
        if getattr(spec, name) is not None
    }
    stated.update({code.upper(): move for code, move in spec.other_fx.items()})
    out: list[ShockPosition] = []
    for p in exposures.positions:
        value = p.weight * total
        betas: dict[str, float] = {}
        labels: dict[str, str] = {}
        basis: Literal["measured", "owner", "fallback", "unshocked"]
        if p.fit is not None:
            betas = {b.factor: b.beta for b in p.fit.betas}
            labels = {b.factor: b.label for b in p.fit.betas}
            basis = "measured"
        elif p.ticker in spec.owner_betas or spec.unmeasured_beta is not None:
            basis = "owner" if p.ticker in spec.owner_betas else "fallback"
        else:
            basis = "unshocked"
        if p.ticker in spec.owner_betas:
            betas["MKT_RF"] = spec.owner_betas[p.ticker]
            labels.setdefault("MKT_RF", "Market")
            basis = "owner"
        elif p.fit is None and spec.unmeasured_beta is not None:
            betas["MKT_RF"] = spec.unmeasured_beta
            labels.setdefault("MKT_RF", "Market")
        if basis == "unshocked" or not betas:
            out.append(ShockPosition(
                ticker=p.ticker, name=p.name, weight=p.weight, basis="unshocked", move=None,
                pnl=0.0, drivers=[],
            ))  # fmt: skip
            continue
        drivers = [
            ShockDriver(factor=f, label=labels.get(f, f), contribution=betas[f] * s)
            for f, s in stated.items()
            if f in betas
        ]
        move = max(-1.0, sum(d.contribution for d in drivers))
        drivers.sort(key=lambda d: -abs(d.contribution))
        out.append(
            ShockPosition(
                ticker=p.ticker, name=p.name, weight=p.weight, basis=basis, move=move,
                pnl=value * move, drivers=drivers[:3],
            )
        )  # fmt: skip
    pnl = sum(p.pnl for p in out)
    loss = pnl / total if total > 0 else None
    warnings = list(exposures.warnings)
    missing = [f for f in stated if f not in {b.factor for b in exposures.portfolio.betas}]
    if missing:
        warnings.append(
            "No loading is measured for " + ", ".join(sorted(missing)) + ", so that part of the "
            "shock moves nothing."
        )
    return ShockResult(
        region=exposures.region,
        as_of=exposures.as_of,
        base_currency=exposures.base_currency,
        method_version=METHOD_VERSION,
        method=METHOD,
        spec=spec,
        before=total,
        after=total + pnl,
        pnl=pnl,
        loss=loss,
        breaches_limit=None if loss is None or drawdown_limit is None else loss <= -drawdown_limit,
        drawdown_limit=drawdown_limit,
        unshocked_weight=sum(p.weight for p in out if p.basis == "unshocked"),
        positions=sorted(out, key=lambda p: (p.pnl, p.ticker)),
        worst=sorted((p for p in out if p.pnl < 0), key=lambda p: p.pnl)[:3],
        warnings=warnings,
        reason=None,
    )


async def run_shock(
    pool: asyncpg.Pool, spec: ShockSpec, region: str = "developed", scope_id: int | None = None
) -> ShockResult:
    """Run a shock on the latest book. A run of a saved scenario (`scope_id`) is saved."""
    exposures = await factor_exposures(pool, region)
    stored = await load_active(pool, "strategy", StrategyConfig)
    limit = stored.body.risk.max_drawdown if stored else None
    result = apply_shock(exposures, spec, float(limit) if limit is not None else None)
    if scope_id is not None and result.loss is not None:
        digest = hashlib.sha256(spec.model_dump_json().encode()).hexdigest()[:10]
        await save_snapshot(
            pool, "shock", result, result.as_of, result.method_version,
            scope=f"{scope_id}:{region}:{digest}",
        )  # fmt: skip
    return result


# ---------------------------------------------------------------------------
# Saved scenarios
# ---------------------------------------------------------------------------


class ScenarioIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    spec: ShockSpec


class Scenario(BaseModel):
    id: int
    name: str
    spec: ShockSpec
    created_at: dt.datetime


def _scenario(row: asyncpg.Record) -> Scenario:
    spec = row["spec"]
    return Scenario(
        id=row["id"],
        name=row["name"],
        spec=ShockSpec.model_validate(json.loads(spec) if isinstance(spec, str) else spec),
        created_at=row["created_at"],
    )


async def list_scenarios(pool: asyncpg.Pool) -> list[Scenario]:
    rows = await pool.fetch(
        "select id, name, spec, created_at from public.risk_scenario order by name"
    )
    return [_scenario(r) for r in rows]


async def get_scenario(pool: asyncpg.Pool, scenario_id: int) -> Scenario | None:
    row = await pool.fetchrow(
        "select id, name, spec, created_at from public.risk_scenario where id = $1", scenario_id
    )
    return _scenario(row) if row else None


async def save_scenario(pool: asyncpg.Pool, body: ScenarioIn) -> Scenario:
    """Create a scenario, or replace the one with the same name."""
    row = await pool.fetchrow(
        "insert into public.risk_scenario (name, spec) values ($1, $2::jsonb)"
        " on conflict (name) do update set spec = excluded.spec"
        " returning id, name, spec, created_at",
        body.name,
        body.spec.model_dump_json(),
    )
    assert row is not None
    return _scenario(row)


async def delete_scenario(pool: asyncpg.Pool, scenario_id: int) -> bool:
    result = await pool.execute("delete from public.risk_scenario where id = $1", scenario_id)
    return str(result).endswith(" 1")
