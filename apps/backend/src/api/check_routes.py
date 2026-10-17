# ruff: noqa: E501 - response text stays on one line each
"""Company checks, the portfolio scorecard and the Checks rules (insight plan I3.9, I3.10, I4.1).

All owner-only reads; the only write is a new version of the `checks` rules.
"""

import datetime as dt
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import Pool
from api.routes import BaseVersionParam, NoteParam, _as_dict, _not_found, save_versioned
from steward.analytics.versions import version
from steward.checks.contract import AXIS_LABELS, SCORED_AXES, AxisScore, score_text
from steward.checks.service import CompanyChecks, company_checks, latest_scores
from steward.checks.strips import PortfolioStrips, portfolio_strips
from steward.config.checks import ChecksConfig
from steward.config.store import load_active
from steward.valuation.fair_value_view import Basis

router = APIRouter()
TickerPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,32}$")]


class ChecksConfigVersion(BaseModel):
    id: int
    created_at: dt.datetime
    note: str | None
    body: ChecksConfig


@router.get("/config/checks", tags=["config"])
async def get_checks_config(pool: Pool, _owner: Owner) -> ChecksConfigVersion:
    """The saved check limits, dividend rules and fair-value rules. 404 means the defaults apply."""
    stored = await load_active(pool, "checks", ChecksConfig)
    if stored is None:
        raise _not_found("checks")
    return ChecksConfigVersion(**_as_dict(stored))


@router.put("/config/checks", tags=["config"])
async def save_checks_config(
    body: ChecksConfig,
    pool: Pool,
    owner: Owner,
    note: NoteParam = None,
    base_version_id: BaseVersionParam = None,
) -> ChecksConfigVersion:
    """Save a new version of the rules. Cached check results carry the rules in their input
    hash, so the next read recomputes them under the new limits."""
    stored = await save_versioned(
        pool, "checks", body, created_by=owner.user_id, note=note, base_version_id=base_version_id
    )
    return ChecksConfigVersion(**_as_dict(stored))


@router.post("/config/checks/validate", tags=["config"])
async def validate_checks(body: ChecksConfig, _owner: Owner) -> ChecksConfig:
    """Validate the rules without saving. 422 lists every error."""
    return body


@router.get("/config/checks/defaults", tags=["config"])
async def checks_defaults(_owner: Owner) -> ChecksConfig:
    """The built-in rules, for "reset to default"."""
    return ChecksConfig()


@router.get("/companies/{ticker}/checks", response_model=CompanyChecks, tags=["companies"])
async def get_company_checks(ticker: TickerPath, pool: Pool, owner: Owner) -> CompanyChecks:
    """Five scored axes (value, future, past, health, dividend; six checks each) and the risk
    list. Each check is pass, fail, not applicable or unknown with its sentence and basis; a score
    counts passes over applicable checks only. Cached by input hash; no LLM."""
    result = await company_checks(pool, owner.user_id, ticker)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{ticker} is not in the covered universe")
    return result


# ------------------------------------------------------------------------- scorecard


class ScoreLine(BaseModel):
    ticker: str
    name: str | None
    weight: Decimal = Field(description="Share of the invested value (cash left out).")
    score: Decimal | None
    passes: int
    applicable: int
    text: str


class AxisAggregate(BaseModel):
    axis: str
    label: str
    score: Decimal | None = Field(
        description="Value-weighted mean of the covered holdings' 0-6 scores."
    )
    holdings_covered: int
    holdings_total: int
    value_covered: Decimal | None = Field(
        description="Share of the stock value the covered holdings make up."
    )
    coverage_text: str
    explanation: str
    lifting: list[ScoreLine]
    holding_back: list[ScoreLine]


class HoldingMark(BaseModel):
    ticker: str
    name: str | None
    weight: Decimal
    axes: dict[str, Decimal | None]
    risk_flags: int
    checked_at: dt.datetime | None


class PortfolioScorecard(BaseModel):
    status: Literal["complete", "partial", "unavailable"]
    as_of: dt.date | None
    method_version: str
    axes: list[AxisAggregate]
    holdings: list[HoldingMark]
    unchecked: list[str] = Field(description="Holdings without a cached check result yet.")
    note: str = (
        "Each axis is the value-weighted mean of holdings' scores over applicable checks only; "
        "a holding whose axis cannot be scored is left out of that axis and counted in its coverage."
    )


EXPLAIN = {
    "value": "How the prices compare with peers, your fair values and analyst targets.",
    "future": "What published forecasts say about growth and returns.",
    "past": "How earnings, returns and margins developed over the filed years.",
    "health": "Whether assets, cash flow and earnings cover the obligations.",
    "dividend": "How safe, steady and covered the dividends are.",
}


def aggregate(
    axis: str, rows: list[tuple[str, str | None, Decimal, AxisScore | None]]
) -> AxisAggregate:
    covered = [(t, n, w, s) for t, n, w, s in rows if s is not None and s.score is not None]
    total_w = sum((w for _, _, w, _ in rows), Decimal(0))
    cov_w = sum((w for _, _, w, _ in covered), Decimal(0))
    score = (
        sum((w * s.score for _, _, w, s in covered if s.score is not None), Decimal(0)) / cov_w
        if cov_w > 0
        else None
    )
    lines = [
        ScoreLine(
            ticker=t,
            name=n,
            weight=w.quantize(Decimal("0.0001")),
            score=s.score,
            passes=s.passes,
            applicable=s.applicable,
            text=score_text(s),
        )
        for t, n, w, s in covered
        if s is not None
    ]
    lines.sort(key=lambda x: (x.score or 0, x.weight), reverse=True)
    share = cov_w / total_w if total_w > 0 else None
    return AxisAggregate(
        axis=axis, label=AXIS_LABELS[axis], score=score.quantize(Decimal("0.01")) if score is not None else None,
        holdings_covered=len(covered), holdings_total=len(rows),
        value_covered=share.quantize(Decimal("0.0001")) if share is not None else None,
        coverage_text=f"{len(covered)} of {len(rows)} holdings" + (f", {share:.0%} of value" if share is not None else ""),
        explanation=EXPLAIN[axis], lifting=lines[:5], holding_back=list(reversed(lines[-5:])) if len(lines) > 0 else [],
    )  # fmt: skip


@router.get("/portfolio/scorecard", response_model=PortfolioScorecard, tags=["portfolio"])
async def portfolio_scorecard(pool: Pool, owner: Owner) -> PortfolioScorecard:
    """Per axis: the value-weighted mean of the holdings' cached axis scores, with coverage and
    the five holdings lifting and holding back the score. Method `portfolio_scorecard` v1."""
    rows = await pool.fetch(
        """select p.ticker, p.value_account, p.snapshot_date, i.name
             from public.position_snapshot p left join public.instrument i on i.t212_ticker = p.ticker
            where p.snapshot_date = (select max(snapshot_date) from public.position_snapshot)
              and p.quantity > 0 and p.value_account is not null and p.value_account > 0"""
    )
    as_of = rows[0]["snapshot_date"] if rows else None
    total = sum((r["value_account"] for r in rows), Decimal(0))
    cached = await latest_scores(pool, owner.user_id, [r["ticker"] for r in rows])
    holdings: list[HoldingMark] = []
    per_axis: dict[str, list[tuple[str, str | None, Decimal, AxisScore | None]]] = {
        a: [] for a in SCORED_AXES
    }
    for r in rows:
        w = r["value_account"] / total if total > 0 else Decimal(0)
        c = cached.get(r["ticker"])
        for a in SCORED_AXES:
            per_axis[a].append((r["ticker"], r["name"], w, c.axes.get(a) if c else None))
        holdings.append(HoldingMark(
            ticker=r["ticker"], name=r["name"], weight=w.quantize(Decimal("0.0001")),
            axes={a: (c.axes[a].score if c and a in c.axes else None) for a in SCORED_AXES},
            risk_flags=c.risk_flags if c else 0, checked_at=c.computed_at if c else None,
        ))  # fmt: skip
    axes = [aggregate(a, per_axis[a]) for a in SCORED_AXES]
    unchecked = [r["ticker"] for r in rows if r["ticker"] not in cached]
    status_: Literal["complete", "partial", "unavailable"] = (
        "unavailable"
        if not rows or all(x.score is None for x in axes)
        else "complete"
        if not unchecked and all(x.holdings_covered == x.holdings_total for x in axes)
        else "partial"
    )
    holdings.sort(key=lambda h: h.weight, reverse=True)
    return PortfolioScorecard(
        status=status_,
        as_of=as_of,
        method_version=version("portfolio_scorecard"),
        axes=axes,
        holdings=holdings,
        unchecked=unchecked,
    )


@router.get("/portfolio/strips", response_model=PortfolioStrips, tags=["portfolio"])
async def get_portfolio_strips(
    pool: Pool,
    owner: Owner,
    basis: Annotated[
        Basis, Query(description="Fair-value basis of the valuation strip; saves nothing.")
    ] = "headline",
) -> PortfolioStrips:
    """Ranked dot strips (insight I4.5): every holding on one scale per metric, with the
    portfolio's value-weighted mean as the reference line. Read from cached checks, stored fair
    values and the dividend forecast; a holding without a value is listed, never placed at zero."""
    return await portfolio_strips(pool, owner.user_id, basis)


class MarkOut(BaseModel):
    tickers: list[str]
    axes: dict[str, Decimal | None]
    covered: dict[str, int]


async def aggregate_mark(pool: Pool, owner_id: object, tickers: list[str]) -> MarkOut:
    """Equal-weight mean of the members' cached axis scores (a saved screen's mark, I4.6)."""
    cached = await latest_scores(pool, owner_id, tickers)
    axes: dict[str, Decimal | None] = {}
    covered: dict[str, int] = {}
    for a in SCORED_AXES:
        vals = [
            c.axes[a].score for c in cached.values() if a in c.axes and c.axes[a].score is not None
        ]
        nums = [v for v in vals if v is not None]
        axes[a] = (sum(nums, Decimal(0)) / len(nums)).quantize(Decimal("0.01")) if nums else None
        covered[a] = len(nums)
    return MarkOut(tickers=tickers, axes=axes, covered=covered)


class MarksIn(BaseModel):
    tickers: list[Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,32}$")]] = Field(max_length=500)


class MarksOut(BaseModel):
    marks: dict[str, dict[str, Decimal | None]]
    aggregate: MarkOut


@router.post("/checks/marks", response_model=MarksOut, tags=["companies"])
async def check_marks(body: MarksIn, pool: Pool, owner: Owner) -> MarksOut:
    """The cached five-axis mark of each listed ticker (no recomputation) and their mean, for
    holdings rows, Discover results and saved screens. A ticker never checked has no mark."""
    cached = await latest_scores(pool, owner.user_id, body.tickers)
    marks = {
        t: {a: (c.axes[a].score if a in c.axes else None) for a in SCORED_AXES}
        for t, c in cached.items()
    }
    return MarksOut(marks=marks, aggregate=await aggregate_mark(pool, owner.user_id, body.tickers))
