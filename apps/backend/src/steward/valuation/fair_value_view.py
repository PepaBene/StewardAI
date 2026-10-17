# ruff: noqa: E501
"""Portfolio valuation against fair value (insight plan I5.1, I5.6), read from `fair_value_point`.

Fair value in the account currency is the position value x fair value / price, so the broker's
own conversion of the position applies and only the fair value is converted into the price's
currency (with the latest stored fixing when the currencies differ). A holding without a fair
value on the chosen basis is excluded and counted, never valued at zero.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.analytics.versions import version
from steward.valuation.drafted import OWNED_POINT_SQL

BASES = (
    "dcf_default",
    "dcf_base",
    "multiple_base",
    "thesis_target",
    "consensus_target",
    "peer_multiple",
)
Basis = Literal[
    "headline", "dcf_default", "dcf_base", "multiple_base", "thesis_target", "consensus_target",
    "peer_multiple",
]  # fmt: skip
# The one fair value a holding shows wherever a single figure is wanted (company header, holdings
# table, drawer, portfolio totals): your own scenario first, then the thesis target, the app's
# default DCF, the analyst consensus and the peer multiple. "headline" picks it per holding.
HEADLINE = "headline"
HEADLINE_ORDER = (
    "dcf_base",
    "multiple_base",
    "thesis_target",
    "dcf_default",
    "consensus_target",
    "peer_multiple",
)
BASIS_LABELS: dict[str, str] = {
    HEADLINE: "Best available",
    "dcf_default": "Default DCF (filed growth)",
    "dcf_base": "Your DCF scenario",
    "multiple_base": "Your multiple-path scenario",
    "thesis_target": "Thesis target",
    "consensus_target": "Analyst consensus",
    "peer_multiple": "Peer-median P/E",
}
# Recomputed daily from filings: stale when the latest fiscal year ended this long ago (a missed
# annual filing), since the basis carries no owner-set date of its own.
FILED_STALE_DAYS = 456
STALE_TASK = "fair_value_stale"
Q4 = Decimal("0.0001")
Q2 = Decimal("0.01")


class StepPoint(BaseModel):
    as_of: dt.date
    value: Decimal


class HoldingFairValue(BaseModel):
    ticker: str
    name: str | None
    quantity: Decimal
    price: Decimal | None
    price_currency: str | None
    value_account: Decimal | None
    fair_value: Decimal | None = Field(description="In the price's currency and unit.")
    fair_value_native: Decimal | None
    native_currency: str | None
    fair_value_account: Decimal | None
    upside: Decimal | None = Field(description="Fair value / price - 1.")
    basis: str
    basis_label: str
    method_version: str | None
    scenario_version: int | None
    as_of: dt.date | None
    inputs_as_of: dt.date | None
    age_days: int | None
    stale: bool
    stale_reasons: list[str]
    reason: str | None = Field(description="Why there is no fair value, when there is none.")
    history: list[StepPoint]
    last_event: str | None = None
    return_pct: Decimal | None = None


class HistoryRow(BaseModel):
    date: dt.date
    portfolio_value: Decimal
    covered_value: Decimal
    fair_value: Decimal


class PortfolioFairValue(BaseModel):
    status: Literal["complete", "partial", "unavailable"]
    basis: str
    basis_label: str
    bases: dict[str, str]
    account_currency: str | None
    as_of: dt.date | None
    portfolio_value: Decimal
    covered_value: Decimal
    fair_value: Decimal | None
    difference: Decimal | None = Field(description="Fair value / covered value - 1.")
    upside_amount: Decimal | None
    overvalued_weight: Decimal
    undervalued_weight: Decimal
    stale_count: int
    covered: int
    total: int
    uncovered: list[str]
    holdings: list[HoldingFairValue]
    history: list[HistoryRow]
    history_starts: dt.date | None
    method_version: str
    note: str = (
        "Fair value in the account currency is the position value times fair value over price, so the "
        "broker's own conversion applies. A holding without a fair value on this basis is left out and "
        "counted, never valued at zero. History starts on the first recorded day."
    )


async def latest_fx(pool: asyncpg.Pool, account: str | None) -> tuple[str, dict[str, Decimal]]:
    """(home currency, latest fixing per currency): CNB for a CZK account, else ECB."""
    source = "cnb" if account == "CZK" else "ecb"
    rows = await pool.fetch(
        "select distinct on (currency) currency, rate from public.fx_rate where source = $1 order by currency, rate_date desc",
        source,
    )
    return ("CZK" if source == "cnb" else "EUR"), {r["currency"]: r["rate"] for r in rows}


def convert(
    amount: Decimal, src: str, dst: str, home: str, rates: dict[str, Decimal]
) -> Decimal | None:
    """Convert with the latest fixings (rates are home-currency units per one unit)."""
    from steward.fx.convert import normalise_currency

    si, sf = normalise_currency(src)
    di, df = normalise_currency(dst)
    if si == di:
        return amount * sf / df
    s = Decimal(1) if si == home else rates.get(si)
    d = Decimal(1) if di == home else rates.get(di)
    if s is None or d is None or d == 0:
        return None
    return amount * sf * s / (d * df)


def _detail(point: Any) -> dict[str, Any]:
    d = point["detail"]
    return json.loads(d) if isinstance(d, str) else dict(d or {})


def staleness(
    basis: str, point: Any, first_hash: str | None, today: dt.date, stale_after: int
) -> tuple[int | None, list[str]]:
    """(age in days of the basis inputs, reasons it is stale)."""
    if point is None:
        return None, []
    detail = _detail(point)
    reasons: list[str] = []
    ref: dt.date | None
    if point["source"] == "saved_scenario" and detail.get("saved_at"):
        ref, limit, what = (
            dt.datetime.fromisoformat(detail["saved_at"]).date(),
            stale_after,
            "scenario saved",
        )
    elif basis in ("thesis_target", "consensus_target"):
        ref, limit = point["inputs_as_of"], stale_after
        what = "target set" if basis == "thesis_target" else "consensus read"
    else:
        ref, limit, what = point["inputs_as_of"], FILED_STALE_DAYS, "latest fiscal year ended"
    age = (today - ref).days if ref else None
    if age is not None and age > limit:
        reasons.append(f"{what} {age} days ago (stale after {limit})")
    if (today - point["as_of"]).days > 7:
        reasons.append(f"not recorded since {point['as_of'].isoformat()}")
    if first_hash and detail.get("statements_hash") and detail["statements_hash"] != first_hash:
        reasons.append("filed fundamentals changed since the scenario was first recorded")
    return age, reasons


async def portfolio_fair_value(
    pool: asyncpg.Pool, owner_id: Any, basis: str, today: dt.date | None = None
) -> PortfolioFairValue:
    from steward.config.checks import load_checks

    today = today or dt.date.today()
    rules, _ = await load_checks(pool)
    pos = await pool.fetch(
        """select p.ticker, p.quantity, p.current_price, p.instrument_currency, p.value_account, p.cost_account,
                  p.account_currency, p.snapshot_date, i.name
             from public.position_snapshot p left join public.instrument i on i.t212_ticker = p.ticker
            where p.snapshot_date = (select max(snapshot_date) from public.position_snapshot) and p.quantity > 0
            order by p.value_account desc nulls last"""
    )
    account = next((r["account_currency"] for r in pos if r["account_currency"]), None)
    home, rates = await latest_fx(pool, account)
    tickers = [r["ticker"] for r in pos]
    wanted = list(HEADLINE_ORDER) if basis == HEADLINE else [basis]
    pts = await pool.fetch(
        f"""select * from public.fair_value_point where owner_id = $1 and basis = any($2::text[]) and ticker = any($3::text[])
             and as_of >= $4 and {OWNED_POINT_SQL} order by ticker, as_of""",  # noqa: S608
        owner_id, wanted, tickers, today - dt.timedelta(days=730),
    )  # fmt: skip
    by_basis: dict[tuple[str, str], list[Any]] = {}
    for p in pts:
        by_basis.setdefault((p["ticker"], p["basis"]), []).append(p)
    chosen = {t: next((b for b in wanted if (t, b) in by_basis), wanted[0]) for t in tickers}
    by_ticker = {t: by_basis.get((t, chosen[t]), []) for t in tickers}
    events = {
        r["ticker"]: r
        for r in await pool.fetch(
            """select distinct on (ticker) ticker, event_type, event_state, announced_at from public.company_event
                where ticker = any($1::text[]) and status <> 'rejected' and announced_at is not null
                order by ticker, announced_at desc""",
            tickers,
        )
    }
    holdings: list[HoldingFairValue] = []
    for r in pos:
        t = r["ticker"]
        rows = by_ticker.get(t, [])
        last = rows[-1] if rows else None
        first_hash = None
        if last is not None and last["scenario_id"]:
            same = [x for x in rows if x["scenario_id"] == last["scenario_id"]]
            first_hash = _detail(same[0]).get("statements_hash")
        price, pccy = r["current_price"], r["instrument_currency"]
        used = chosen[t]
        fv = fv_acct = up = None
        reason = None
        if last is None:
            reason = (
                "no fair value recorded on any basis yet"
                if basis == HEADLINE
                else f"no {BASIS_LABELS.get(basis, basis)} value recorded"
            )
        elif price is None or price <= 0 or not pccy:
            reason = "no current price"
        else:
            ncy = last["currency"] or pccy
            fv = convert(last["value"], ncy, pccy, home, rates)
            if fv is None:
                reason = f"no FX rate from {ncy} to {pccy}"
            else:
                up = fv / price - 1
                fv_acct = (
                    r["value_account"] * fv / price if r["value_account"] is not None else None
                )
        age, reasons = staleness(used, last, first_hash, today, rules.fair_value.stale_after_days)
        hist: list[StepPoint] = []
        for p in rows:
            v = convert(p["value"], p["currency"] or pccy, pccy, home, rates) if pccy else None
            if v is not None and (not hist or hist[-1].value != v.quantize(Q4)):
                hist.append(StepPoint(as_of=p["as_of"], value=v.quantize(Q4)))
        ev = events.get(t)
        holdings.append(HoldingFairValue(
            ticker=t, name=r["name"], quantity=r["quantity"], price=price, price_currency=pccy, value_account=r["value_account"],
            fair_value=fv.quantize(Q4) if fv is not None else None, fair_value_native=last["value"] if last else None,
            native_currency=last["currency"] if last else None, fair_value_account=fv_acct.quantize(Q2) if fv_acct is not None else None,
            upside=up.quantize(Q4) if up is not None else None, basis=used if last is not None else basis,
            basis_label=BASIS_LABELS.get(used if last is not None else basis, basis),
            method_version=last["method_version"] if last else None, scenario_version=last["scenario_version"] if last else None,
            as_of=last["as_of"] if last else None, inputs_as_of=last["inputs_as_of"] if last else None, age_days=age,
            stale=bool(reasons), stale_reasons=reasons, reason=reason, history=hist,
            last_event=f"{ev['event_type']} {ev['event_state']} ({ev['announced_at'].isoformat()})" if ev else None,
            return_pct=(r["value_account"] / r["cost_account"] - 1).quantize(Q4) if r["value_account"] is not None and r["cost_account"] else None,
        ))  # fmt: skip
    return summarise(
        holdings,
        basis,
        account,
        pos[0]["snapshot_date"] if pos else None,
        *(await history(pool, owner_id, basis, home, rates, chosen if basis == HEADLINE else None)),
    )


def summarise(holdings: list[HoldingFairValue], basis: str, account: str | None, as_of: dt.date | None,
              hist: list[HistoryRow], starts: dt.date | None) -> PortfolioFairValue:  # fmt: skip
    total = sum((h.value_account or Decimal(0) for h in holdings), Decimal(0))
    covered = [
        h for h in holdings if h.fair_value_account is not None and h.value_account is not None
    ]
    cov_value = sum((h.value_account or Decimal(0) for h in covered), Decimal(0))
    fair = (
        sum((h.fair_value_account or Decimal(0) for h in covered), Decimal(0)) if covered else None
    )
    over = sum((h.value_account or Decimal(0) for h in covered if (h.upside or 0) < 0), Decimal(0))
    under = cov_value - over
    status: Literal["complete", "partial", "unavailable"] = (
        "unavailable" if not covered else "complete" if len(covered) == len(holdings) else "partial"
    )
    return PortfolioFairValue(
        status=status, basis=basis, basis_label=BASIS_LABELS.get(basis, basis), bases=dict(BASIS_LABELS), account_currency=account,
        as_of=as_of, portfolio_value=total, covered_value=cov_value, fair_value=fair.quantize(Q2) if fair is not None else None,
        difference=(fair / cov_value - 1).quantize(Q4) if fair is not None and cov_value > 0 else None,
        upside_amount=(fair - cov_value).quantize(Q2) if fair is not None else None,
        overvalued_weight=(over / total).quantize(Q4) if total > 0 else Decimal(0),
        undervalued_weight=(under / total).quantize(Q4) if total > 0 else Decimal(0),
        stale_count=sum(1 for h in holdings if h.stale), covered=len(covered), total=len(holdings),
        uncovered=[h.ticker for h in holdings if h.fair_value_account is None], holdings=holdings,
        history=hist, history_starts=starts, method_version=version("portfolio_fair_value"),
    )  # fmt: skip


async def history(
    pool: asyncpg.Pool,
    owner_id: Any,
    basis: str,
    home: str,
    rates: dict[str, Decimal],
    chosen: dict[str, str] | None = None,
) -> tuple[list[HistoryRow], dt.date | None]:
    """Portfolio value and the fair value of the covered positions per stored portfolio day,
    starting at the first recorded fair value (no back-fill). With `chosen` (the headline
    basis), each holding is valued on the basis it shows today."""
    wanted = sorted(set(chosen.values())) if chosen else [basis]
    pts = await pool.fetch(
        "select ticker, basis, as_of, value, currency from public.fair_value_point"  # noqa: S608
        f" where owner_id = $1 and basis = any($2::text[]) and {OWNED_POINT_SQL} order by as_of",
        owner_id,
        wanted,
    )
    series: dict[str, list[tuple[dt.date, Decimal, str | None]]] = {}
    for p in pts:
        if chosen is not None and chosen.get(p["ticker"]) != p["basis"]:
            continue
        series.setdefault(p["ticker"], []).append((p["as_of"], p["value"], p["currency"]))
    start = min((s[0][0] for s in series.values() if s), default=None)
    if start is None:
        return [], None
    pos = await pool.fetch(
        """select snapshot_date, ticker, current_price, instrument_currency, value_account from public.position_snapshot
            where snapshot_date >= $1 and quantity > 0 order by snapshot_date""",
        start,
    )
    days: dict[dt.date, list[Any]] = {}
    for r in pos:
        days.setdefault(r["snapshot_date"], []).append(r)
    out: list[HistoryRow] = []
    for d, rows in sorted(days.items()):
        total = cov = fv = Decimal(0)
        for r in rows:
            v = r["value_account"] or Decimal(0)
            total += v
            s = [x for x in series.get(r["ticker"], []) if x[0] <= d]
            if not s or not r["current_price"] or not r["instrument_currency"]:
                continue
            _, val, ccy = s[-1]
            conv = convert(
                val, ccy or r["instrument_currency"], r["instrument_currency"], home, rates
            )
            if conv is None:
                continue
            cov += v
            fv += v * conv / r["current_price"]
        out.append(
            HistoryRow(date=d, portfolio_value=total, covered_value=cov, fair_value=fv.quantize(Q2))
        )
    return out, start


async def stale_alerts(
    pool: asyncpg.Pool, owner_id: Any, today: dt.date | None = None
) -> list[str]:
    """One snoozable Needs-you line per holding whose saved fair value is stale, or moved by more
    than the configured share since its scenario was first recorded (I5.6)."""
    from steward.config.checks import load_checks
    from steward.ops import tasks

    today = today or dt.date.today()
    rules, _ = await load_checks(pool)
    seen: set[str] = set()
    for basis in ("dcf_base", "multiple_base", "thesis_target"):
        view = await portfolio_fair_value(pool, owner_id, basis, today)
        for h in view.holdings:
            if h.fair_value is None or h.ticker in seen:
                continue
            reasons = list(h.stale_reasons)
            first = h.history[0].value if h.history else None
            if first and abs(h.fair_value / first - 1) > Decimal(str(rules.fair_value.move_alert)):
                reasons.append(
                    f"value moved {h.fair_value / first - 1:+.0%} since first recorded ({first} to {h.fair_value} {h.price_currency or ''})"
                )
            if not reasons:
                continue
            seen.add(h.ticker)
            await tasks.see(
                pool, STALE_TASK, h.ticker, title=f"Review the fair value of {h.name or h.ticker}",
                detail=f"{h.basis_label}: " + "; ".join(reasons), href=f"/companies/{h.ticker}/valuation",
                weight=0.3, status="needs_owner",
            )  # fmt: skip
    await tasks.settle(pool, STALE_TASK, seen)
    return sorted(seen)
