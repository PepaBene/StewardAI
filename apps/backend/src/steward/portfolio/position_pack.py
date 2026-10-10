"""The risk page of one holding's thesis review (quant plan Q7.2).

What the position adds to the portfolio's risk, who it moves with, and what the past episodes
would have done to it. Everything is read from the same models as the portfolio pages
(`exante`, `replay`); nothing new is estimated here. Expected Shortfall is not modelled yet
(plan Q2), and the pack says so instead of leaving a blank.
"""

import logging

import asyncpg
from pydantic import BaseModel, Field

from steward.portfolio import exante as ea
from steward.portfolio.replay import StressReplay, stress_replay

log = logging.getLogger(__name__)

CLUSTER_CORRELATION = 0.6
CLUSTER_MAX = 5
STRESS_LINES = 3
MIN_CORRELATION_WEEKS = 40


class ClusterMember(BaseModel):
    ticker: str
    correlation: float
    weight: float


class StressLine(BaseModel):
    episode: str
    move: float | None = Field(description="The position's own move over the episode.")
    pnl: float = Field(description="Gain or loss in the base currency.")
    basis: str = Field(description="real, proxied or unavailable.")


class PositionRiskPack(BaseModel):
    ticker: str
    available: bool
    reason: str | None
    weight: float | None = None
    risk_share: float | None = Field(
        default=None, description="Share of the portfolio's forecast variance (Euler)."
    )
    risk_to_weight: float | None = Field(
        default=None,
        description="Risk share divided by weight; above 1 it adds more than its size.",
    )
    standalone_volatility: float | None = None
    marginal_risk: float | None = None
    cluster: list[ClusterMember] = Field(
        default_factory=list, description="Holdings that move with it (weekly correlation 0.6+)."
    )
    cluster_weight: float | None = Field(
        default=None, description="The position plus its cluster, share of the portfolio."
    )
    stress: list[StressLine] = Field(default_factory=list, description="Worst past episodes.")
    expected_shortfall_contribution: float | None = None
    summary: str = ""
    notes: list[str] = Field(default_factory=list)


def build_pack(
    ticker: str,
    report: ea.ExAnteRisk,
    correlations: list[tuple[str, str, float, int]],
    replay: StressReplay | None,
) -> PositionRiskPack:
    """The pack from reports already computed (pure)."""
    row = next((p for p in report.positions if p.ticker == ticker), None)
    if row is None:
        return PositionRiskPack(
            ticker=ticker, available=False, reason=f"{ticker} is not in the latest portfolio"
        )
    weights = {p.ticker: p.weight for p in report.positions}
    cluster = sorted(
        (
            ClusterMember(
                ticker=b if a == ticker else a,
                correlation=c,
                weight=weights.get(b if a == ticker else a, 0.0),
            )
            for a, b, c, _weeks in correlations
            if ticker in (a, b) and c >= CLUSTER_CORRELATION
        ),
        key=lambda m: -m.correlation,
    )[:CLUSTER_MAX]
    stress: list[StressLine] = []
    if replay is not None:
        lines: list[StressLine] = []
        for ep in replay.episodes:
            if ep.status != "replayed":
                continue
            pos = next((x for x in ep.positions if x.ticker == ticker), None)
            if pos is not None and pos.basis != "unavailable":
                lines.append(
                    StressLine(episode=ep.label, move=pos.move, pnl=pos.pnl, basis=pos.basis)
                )
        stress = sorted(lines, key=lambda s: s.pnl)[:STRESS_LINES]
    ratio = row.risk_share / row.weight if row.risk_share is not None and row.weight > 0 else None
    parts: list[str] = []
    if row.risk_share is not None:
        parts.append(
            f"{ticker} is {row.weight:.1%} of the portfolio and {row.risk_share:.1%} of its risk"
        )
    if cluster:
        total = row.weight + sum(m.weight for m in cluster)
        names = ", ".join(m.ticker for m in cluster[:3])
        parts.append(f"it moves with {names} (together {total:.1%} of the portfolio)")
    if stress:
        worst = stress[0]
        money = f"{worst.pnl:+,.0f} {report.base_currency or ''}".rstrip()
        parts.append(f"worst past episode: {worst.episode}, {money}")
    return PositionRiskPack(
        ticker=ticker,
        available=row.risk_share is not None,
        reason=row.reason if row.risk_share is None else None,
        weight=row.weight,
        risk_share=row.risk_share,
        risk_to_weight=ratio,
        standalone_volatility=row.volatility,
        marginal_risk=row.marginal_risk,
        cluster=cluster,
        cluster_weight=(row.weight + sum(m.weight for m in cluster)) if cluster else None,
        stress=stress,
        summary="; ".join(parts) + ("." if parts else ""),
        notes=[
            "Expected Shortfall is not modelled yet, so there is no tail-loss contribution.",
            *(
                ["Part of the stress result is estimated through a proxy."]
                if any(s.basis == "proxied" for s in stress)
                else []
            ),
        ],
    )


async def position_risk_pack(pool: asyncpg.Pool, ticker: str) -> PositionRiskPack:
    book, strategy = await ea.book_from_snapshot(pool)
    if book is None or strategy is None:
        return PositionRiskPack(
            ticker=ticker,
            available=False,
            reason="no portfolio snapshot yet; run the portfolio sync",
        )
    report = ea.build_report(book, strategy)
    correlations = ea.weekly_pair_correlations(book, min_weeks=MIN_CORRELATION_WEEKS)
    replay: StressReplay | None = None
    try:
        replay = await stress_replay(pool)
    except Exception:
        log.exception("replay for the position pack failed")
    out = build_pack(ticker, report, correlations, replay)
    if replay is None:
        out.notes.append("The past-episode replay could not be computed.")
    return out
