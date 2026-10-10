"""Named what-if cases on today's holdings (plan M6.5).

A case is converted to a proposed portfolio (the X-Ray what-if of Phase 5, applied to a copy), and,
where it implies trades, to the pre-trade check. Nothing is written to the broker or to a stored
holding. A case that is an assumption about the market (an FX move) or about a benchmark is run on
the shock engine or the risk report instead. Saved cases keep the inputs and the result as it
stood, with the method version.
"""

from __future__ import annotations

from typing import Literal

import asyncpg
from pydantic import BaseModel, Field, field_validator

from steward.decision import pretrade as pt
from steward.decision import service as svc
from steward.decision.risk_report import RiskReport, build_report
from steward.decision.series import load_bundle
from steward.decision.shocks import ShockAssumptions, ShockScenarioResult, ShockSet
from steward.xray import service as xray_service
from steward.xray.scenario import ProposalError, XrayProposal

CaseKind = Literal[
    "new_security",
    "contribution",
    "withdrawal",
    "reinvest_dividends",
    "fx_move",
    "benchmark_change",
    "thesis_exit",
]


class WhatIfCase(BaseModel):
    kind: CaseKind
    ticker: str | None = Field(default=None, max_length=32)
    weight: float | None = Field(
        default=None, gt=0, le=1, description="New security: share of total."
    )
    fund_from: Literal["cash", "pro_rata"] = "cash"
    amount: float | None = Field(
        default=None,
        gt=0,
        le=1e12,
        description="Base currency, for a deposit, withdrawal or dividends.",
    )
    split: dict[str, float] = Field(
        default_factory=dict,
        description="Ticker -> share of the amount (adds to at most 1); empty keeps a deposit in "
        "cash and spreads dividends over the current weights.",
    )
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    move: float | None = Field(default=None, ge=-0.9, le=2, description="FX move against base.")
    benchmark_symbol: str | None = Field(default=None, max_length=32)
    name: str | None = Field(default=None, max_length=120)

    @field_validator("split")
    @classmethod
    def _split_shares(cls, split: dict[str, float]) -> dict[str, float]:
        """Each share is a fraction of the amount: a negative one would turn a purchase into a
        sale (or a withdrawal into a purchase)."""
        for ticker, share in split.items():
            if not 0 < share <= 1:
                raise ValueError(f"The share for {ticker} must be above 0 and at most 1.")
        if sum(split.values()) > 1 + 1e-9:
            raise ValueError("The split adds to more than 100%.")
        return split


class WhatIfResult(BaseModel):
    kind: CaseKind
    summary: list[str]
    proposal: XrayProposal | None
    shock: ShockScenarioResult | None
    risk: RiskReport | None
    risk_current: RiskReport | None
    pretrade: pt.PretradeResult | None
    assumptions: list[str]
    notes: list[str]


class CaseError(ValueError):
    pass


def _need(value: object, what: str) -> None:
    if value is None:
        raise CaseError(f"This case needs {what}.")


def withdrawal_sales(
    shortfall: float, values: dict[str, float], split: dict[str, float]
) -> tuple[dict[str, float], float]:
    """Sales (ticker -> base-currency value) that raise `shortfall`, and how much of it was sold
    pro rata beyond the owner's split. The split is followed first; what it leaves uncovered (a
    split under 100%, or a holding too small for its share) is sold pro rata from what remains."""
    if shortfall <= 0:
        return {}, 0.0
    invested = sum(values.values())
    if invested < shortfall - 1e-6:
        raise CaseError("The withdrawal is larger than the portfolio.")
    unheld = sorted(t for t in split if t not in values)
    if unheld:
        raise CaseError(f"Not held, so it cannot be sold: {', '.join(unheld)}.")
    shares = dict(split) or {t: v / invested for t, v in values.items()}
    sales = {t: min(shortfall * share, values[t]) for t, share in shares.items()}
    rest = shortfall - sum(sales.values())
    if rest > 1e-6:
        left = {t: v - sales.get(t, 0.0) for t, v in values.items()}
        remaining = sum(left.values())
        for t, v in left.items():
            sales[t] = sales.get(t, 0.0) + rest * v / remaining
    return sales, max(0.0, rest)


def pro_rata_purchase(
    ticker: str, spend: float, values: dict[str, float]
) -> tuple[dict[str, float], list[pt.TradeLeg], float]:
    """Values after buying `spend` of `ticker`, funded by trimming every holding by the same
    factor, the trade legs, and that factor. The trim comes first and the purchase is added on
    top, so a ticker that is already held ends at its trimmed value plus the spend and gets one
    net leg, never a buy and a sell of the same ticker."""
    invested = sum(values.values())
    if spend >= invested:
        raise CaseError("The new weight is larger than all current positions together.")
    factor = (invested - spend) / invested
    after = {t: v * factor for t, v in values.items()}
    after[ticker] = after.get(ticker, 0.0) + spend
    legs: list[pt.TradeLeg] = []
    for t, value in after.items():
        net = value - values.get(t, 0.0)
        if net > 1e-6:
            legs.append(pt.TradeLeg(side="buy", ticker=t, value_base=net))
        elif net < -1e-6:
            legs.append(pt.TradeLeg(side="sell", ticker=t, value_base=-net))
    return after, legs, factor


async def run_case(pool: asyncpg.Pool, owner_id: str, case: WhatIfCase) -> WhatIfResult:
    loaded = await xray_service.load_cached(pool, owner_id)
    portfolio = loaded.data.portfolio
    if portfolio is None:
        raise svc.NoHoldings
    total = sum(p.value_base for p in portfolio.positions) + portfolio.cash_base
    values = {p.ticker: p.value_base for p in portfolio.positions}
    held = set(values)
    base = portfolio.base_currency
    summary: list[str] = []
    assumptions: list[str] = []
    notes: list[str] = list(loaded.notes)
    legs: list[pt.TradeLeg] = []
    deposit = 0.0
    after = dict(values)

    if case.kind == "fx_move":
        _need(case.currency, "a currency")
        _need(case.move, "a move")
        assert case.currency is not None and case.move is not None
        run = await svc.run_shock(
            pool,
            owner_id,
            ShockSet(fx={case.currency: case.move}),
            ShockAssumptions(),
            loaded=loaded,
        )
        summary.append(
            f"{case.currency} {case.move:+.1%} against {base}: the portfolio moves "
            f"{run.result.pnl_pct:+.2%} ({run.result.pnl:+,.0f} {base})."
        )
        assumptions.append(
            "Only leaves quoted in that currency translate; economic exposure is not modelled."
        )
        return WhatIfResult(
            kind=case.kind, summary=summary, proposal=None, shock=run.result, risk=None,
            risk_current=None, pretrade=None, assumptions=assumptions, notes=notes + run.notes,
        )  # fmt: skip

    if case.kind == "benchmark_change":
        _need(case.benchmark_symbol, "a benchmark symbol")
        current = build_report(await load_bundle(pool, svc.SERIES_DAYS))
        chosen = build_report(await load_bundle(pool, svc.SERIES_DAYS, case.benchmark_symbol))
        summary.append(
            f"Beta, tracking error and correlation are measured against {case.benchmark_symbol} "
            f"instead of {current.benchmark_symbol or 'no benchmark'}."
        )
        if chosen.benchmark_note:
            notes.append(chosen.benchmark_note)
        return WhatIfResult(
            kind=case.kind, summary=summary, proposal=None, shock=None, risk=chosen,
            risk_current=current, pretrade=None,
            assumptions=["Prices are the stored closes, converted at each day's reference rate."],
            notes=notes,
        )  # fmt: skip

    if case.kind == "new_security":
        _need(case.ticker, "a ticker")
        _need(case.weight, "a weight")
        assert case.ticker is not None and case.weight is not None
        spend = case.weight * total
        if case.fund_from == "pro_rata":
            after, legs, factor = pro_rata_purchase(case.ticker, spend, values)
            summary.append(
                f"Buy {case.ticker} at {case.weight:.1%}, funded by trimming every position by "
                f"{1 - factor:.1%}."
            )
        else:
            if spend > portfolio.cash_base + 1e-6:
                raise CaseError(
                    f"Cash is {portfolio.cash_base:,.0f} {base}; the purchase needs {spend:,.0f}."
                )
            after[case.ticker] = after.get(case.ticker, 0.0) + spend
            legs.append(pt.TradeLeg(side="buy", ticker=case.ticker, value_base=spend))
            summary.append(f"Buy {case.ticker} at {case.weight:.1%} of the portfolio, from cash.")

    elif case.kind in ("contribution", "reinvest_dividends"):
        _need(case.amount, "an amount")
        assert case.amount is not None
        deposit = case.amount
        split = dict(case.split)
        if case.kind == "reinvest_dividends":
            assumptions.append(
                "The dividend amount is the one you enter; no payout is forecast from the holdings."
            )
            if not split:
                invested = sum(values.values())
                split = {t: v / invested for t, v in values.items()} if invested > 0 else {}
        if sum(split.values()) > 1 + 1e-9:
            raise CaseError("The split adds to more than 100%.")
        for t, share in split.items():
            if deposit * share <= 1e-6:
                continue
            after[t] = after.get(t, 0.0) + deposit * share
            legs.append(pt.TradeLeg(side="buy", ticker=t, value_base=deposit * share))
        kept = deposit * (1 - sum(split.values()))
        label = "Dividends reinvested" if case.kind == "reinvest_dividends" else "Contribution"
        summary.append(
            f"{label}: {deposit:,.0f} {base} added; {deposit - kept:,.0f} invested, "
            f"{kept:,.0f} kept in cash."
        )

    elif case.kind == "withdrawal":
        _need(case.amount, "an amount")
        assert case.amount is not None
        deposit = -case.amount
        shortfall = max(0.0, case.amount - portfolio.cash_base)
        sales, rest = withdrawal_sales(shortfall, values, case.split)
        rest_note = (
            f" ({rest:,.0f} of it pro rata, beyond the split you gave)" if rest > 1e-6 else ""
        )
        for t, sale in sales.items():
            if sale > 1e-6:
                after[t] = values[t] - sale
                legs.append(pt.TradeLeg(side="sell", ticker=t, value_base=sale))
        summary.append(
            f"Withdraw {case.amount:,.0f} {base}: {min(case.amount, portfolio.cash_base):,.0f} "
            f"from cash, {shortfall:,.0f} from sales{rest_note}."
        )

    elif case.kind == "thesis_exit":
        _need(case.ticker, "a ticker")
        assert case.ticker is not None
        if case.ticker not in values:
            raise CaseError(f"{case.ticker} is not held.")
        legs.append(pt.TradeLeg(side="sell", ticker=case.ticker, value_base=values[case.ticker]))
        after[case.ticker] = 0.0
        summary.append(
            f"Exit {case.ticker} in full ({values[case.ticker] / total:.1%} of the portfolio) to "
            "cash, because its thesis no longer holds."
        )
        assumptions.append("A full sale at today's value; the thesis review is yours to record.")

    proposal: XrayProposal | None = None
    try:
        request = svc.proposal_from_values(after, held, total, deposit)
        proposal = await xray_service.propose(pool, owner_id, request, loaded=loaded)
    except ProposalError as err:
        notes.append(f"The X-Ray after the change could not be built: {err}")

    check: pt.PretradeResult | None = None
    if legs:
        try:
            check = await svc.run_pretrade(
                pool, owner_id, svc.PretradeRequest(trades=legs, deposit=deposit)
            )
        except svc.NoStrategy:
            notes.append("No strategy is stored, so the pre-trade check was not run.")
    return WhatIfResult(
        kind=case.kind, summary=summary, proposal=proposal, shock=None, risk=None,
        risk_current=None, pretrade=check, assumptions=assumptions, notes=notes,
    )  # fmt: skip
