"""Loads what the ranking needs and runs it (plan Q8.3 to Q8.5). The formulas are in `ranking`
and `expected_return`; this module reads the theses, the stored closes, the cash rate and the
book, and runs each suggested switch through the pre-trade evaluation."""

from __future__ import annotations

import datetime as dt
import logging
import time

import asyncpg

from steward.analytics.versions import version
from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.store import load_active
from steward.config.thesis import ThesesConfig, ThesisConfig, ThesisScenarios
from steward.decision import expected_return as er
from steward.decision import pretrade as pt
from steward.decision import ranking as rk
from steward.decision import service as svc
from steward.earnings.check import local_today
from steward.fx.convert import price_in_unit
from steward.portfolio import exante as ea
from steward.portfolio.risk import _risk_free, portfolio_risk
from steward.reference.price_series import choose_series, holding_symbols
from steward.reference.read import load_series

log = logging.getLogger(__name__)

HISTORY_DAYS = 3 * 366
SERIES_TTL_SECONDS = 120.0
_Closes = dict[str, list[tuple[dt.date, float]]]
_series_cache: dict[tuple[int, tuple[str, ...]], tuple[float, _Closes]] = {}


async def _series(pool: asyncpg.Pool, tickers: list[str]) -> dict[str, list[tuple[dt.date, float]]]:
    """The stored daily closes of each ticker (the series its readers use), last three years.

    Kept for two minutes, so the board, the switch scan and a chat question that follow one
    another read the prices once. Closes change once a day; the copy is never edited."""
    key = (id(pool), tuple(sorted(tickers)))
    hit = _series_cache.get(key)
    if hit is not None and time.monotonic() - hit[0] < SERIES_TTL_SECONDS:
        return hit[1]
    out = await _load_series(pool, tickers)
    if len(_series_cache) > 32:
        _series_cache.clear()
    _series_cache[key] = (time.monotonic(), out)
    return out


async def _load_series(
    pool: asyncpg.Pool, tickers: list[str]
) -> dict[str, list[tuple[dt.date, float]]]:
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    symbols = await holding_symbols(
        pool, tickers, profiles.body if profiles else HoldingProfilesConfig()
    )
    chosen = await choose_series(pool, symbols)
    since = local_today() - dt.timedelta(days=HISTORY_DAYS)
    out: dict[str, list[tuple[dt.date, float]]] = {}
    for ticker, ref in chosen.items():
        if ref is None:
            continue
        rows = [(d, float(p)) for d, p in await load_series(pool, ref) if d >= since]
        if rows:
            out[ticker] = rows
    return out


async def _report_dates(pool: asyncpg.Pool, tickers: list[str]) -> dict[str, list[dt.date]]:
    """Past report dates from the earnings checks, by ticker."""
    rows = await pool.fetch(
        "select distinct params ->> 'ticker' as ticker, (params ->> 'report_date')::date as day"
        " from public.job where job_type = 'earnings_check'"
        " and params ->> 'ticker' = any($1::text[]) and params ->> 'report_date' is not null",
        tickers,
    )
    out: dict[str, list[dt.date]] = {}
    for r in rows:
        out.setdefault(r["ticker"], []).append(r["day"])
    return out


async def _marginal_risks(pool: asyncpg.Pool) -> dict[str, float]:
    """Stage B marginal risk of each held stock the ex-ante model covers."""
    try:
        book, strategy = await ea.book_from_snapshot(pool)
        if book is None or strategy is None:
            return {}
        report = ea.build_report(book, strategy, None)
    except Exception as exc:  # the ranking must still come back on stage A
        log.warning("ex-ante marginal risk unavailable: %s", exc)
        return {}
    return {
        p.ticker: p.marginal_risk
        for p in report.positions
        if p.marginal_risk is not None and p.status != "excluded"
    }


def _row(
    thesis: ThesisConfig,
    *,
    name: str | None,
    held: bool,
    weight: float | None,
    price: float | None,
    price_date: dt.date | None,
    currency: str | None,
    series: list[tuple[dt.date, float]] | None,
    index: list[tuple[dt.date, float]],
    marginal: float | None,
    cash_rate: float | None,
    sharpe: float | None,
    hurdle: float | None,
    gate: float | None,
    today: dt.date,
) -> rk.RankingRow:
    target = thesis.price_target
    row = rk.RankingRow(
        ticker=thesis.ticker,
        name=name,
        held=held,
        weight=weight,
        price=price,
        price_date=price_date,
        currency=currency,
        volatility_band=target.volatility_band if target else None,
    )
    sc = target.scenarios if target else None
    if target is None:
        row.reasons.append("the thesis has no price target or scenarios")
    elif sc is None:
        row.reasons.append("the thesis has no bear, base and bull scenarios")
    else:
        row.scenarios = {
            "bear": sc.bear,
            "base": sc.base,
            "bull": sc.bull,
            "p_bear": sc.p_bear,
            "p_base": sc.p_base,
            "p_bull": sc.p_bull,
        }
    if series:
        row.risk = rk.risk_profile(series, index or None)
        if marginal is not None:
            row.risk.marginal_risk = marginal
            row.risk.marginal_stage = "B"
        if row.risk.volatility_1y is None:
            row.reasons.append("too little price history for a volatility (needs 20 trading days)")
    else:
        row.reasons.append("no stored price history for this stock")
    if price is None or price <= 0:
        row.reasons.append("no current price")
    elif sc is not None and target is not None:
        scaled = price_in_unit(price, currency, target.currency)
        if scaled is None:
            row.reasons.append(
                f"the scenarios are in {target.currency} but the price is in {currency}"
            )
        else:
            row.returns = _figures(sc, scaled, target.catalyst_date, today, hurdle, gate)
    if row.returns is not None and row.risk is not None:
        row.bear_loss_vs_history = rk.bear_vs_history(row.returns.loss, row.risk.max_drawdown)
        vol = row.risk.volatility_1y
        if cash_rate is None:
            row.reasons.append("no cash rate is stored for the base currency")
        elif vol:
            row.score = rk.risk_adjusted_score(row.returns.annualized, cash_rate, vol)
            row.score_basis = "(annualised expected return - cash rate) / one-year volatility"
            if sharpe is not None and row.risk.correlation is not None:
                row.improves_portfolio = rk.improves_portfolio(
                    row.score, sharpe, row.risk.correlation
                )
            elif sharpe is None:
                row.reasons.append("the portfolio's Sharpe ratio is not available yet")
            else:
                row.reasons.append("no correlation with the portfolio (too few shared days)")
    return row


def _figures(
    sc: ThesisScenarios,
    price: float,
    catalyst: dt.date | None,
    today: dt.date,
    hurdle: float | None,
    gate: float | None,
) -> rk.ReturnFigures:
    r = er.expected_return(
        sc, price, catalyst=catalyst, today=today, hurdle=hurdle, asymmetry_gate=gate
    )
    return rk.ReturnFigures(
        expected_value=r.expected_value,
        total=r.total,
        annualized=r.annualized,
        horizon_years=r.horizon_years,
        horizon_source=r.horizon_source,
        upside=r.upside,
        loss=r.loss,
        asymmetry=r.asymmetry,
        meets_hurdle=r.meets_hurdle,
        meets_asymmetry=r.meets_asymmetry,
    )


async def expected_returns(pool: asyncpg.Pool) -> rk.RankingResponse:
    strategy = await svc.load_strategy(pool)
    theses_stored = await load_active(pool, "theses", ThesesConfig)
    theses = theses_stored.body.theses if theses_stored else []
    positions = await svc._latest_positions(pool)
    held_values = {t: float(r["value_base"]) for t, r in positions.items()}
    invested = sum(held_values.values())
    tickers = sorted(set(positions) | {t.ticker for t in theses})
    series = await _series(pool, tickers)
    index = rk.portfolio_index(series, held_values)
    instruments = await svc._instruments(pool, [t for t in tickers if t not in positions])
    notes: list[str] = []

    base = strategy.base_currency
    cash_rate: float | None = None
    cash_label: str | None = None
    found = await _risk_free(pool, base, local_today() - dt.timedelta(days=30))
    if found is not None and found[0].latest_rate is not None:
        cash_rate, cash_label = found[0].latest_rate, found[0].label
    else:
        notes.append(f"No cash rate is stored for {base}; scores cannot be computed.")
    sharpe: float | None = None
    try:
        sharpe = (await portfolio_risk(pool, 365)).portfolio.sharpe
    except Exception as exc:  # a missing history must not hide the table
        log.warning("portfolio sharpe unavailable: %s", exc)
    marginal = await _marginal_risks(pool)
    report_dates = await _report_dates(pool, tickers)
    today = local_today()

    rows: list[rk.RankingRow] = []
    for thesis in theses:
        rec = positions.get(thesis.ticker)
        ins = instruments.get(thesis.ticker)
        s = series.get(thesis.ticker)
        price: float | None
        price_date: dt.date | None
        if rec is not None:
            price, price_date = float(rec["current_price"]), rec["snapshot_date"]
            currency = rec["instrument_currency"]
        elif s:
            price, price_date = s[-1][1], s[-1][0]
            currency = ins["currency"] if ins else None
        else:
            price, price_date = None, None
            currency = ins["currency"] if ins else None
        rows.append(
            _row(
                thesis,
                name=(rec["name"] if rec else (ins["name"] if ins else None)),
                held=rec is not None,
                weight=held_values[thesis.ticker] / invested if rec and invested > 0 else None,
                price=price,
                price_date=price_date,
                currency=currency,
                series=s,
                index=index,
                marginal=marginal.get(thesis.ticker),
                cash_rate=cash_rate,
                sharpe=sharpe,
                hurdle=strategy.pretrade.return_hurdle,
                gate=strategy.pretrade.asymmetry_gate,
                today=today,
            )
        )
    for r in rows:
        series_r = series.get(r.ticker)
        if series_r and r.ticker in report_dates:
            r.earnings_moves = rk.earnings_moves(series_r, report_dates[r.ticker])
    rows.sort(key=lambda r: (r.score is None, -(r.score or 0.0), r.ticker))
    rank = 0
    for r in rows:
        if r.score is not None:
            rank += 1
            r.rank = rank
    if not any(t.price_target and t.price_target.scenarios for t in theses):
        notes.append(
            "No thesis has bear, base and bull scenarios yet. Add them in the theses editor "
            "to rank the holdings and the bench."
        )
    return rk.RankingResponse(
        method_version=version("ranking"),
        as_of=today,
        base_currency=base,
        cash_rate=cash_rate,
        cash_rate_label=cash_label,
        portfolio_sharpe=sharpe,
        return_hurdle=strategy.pretrade.return_hurdle,
        asymmetry_gate=strategy.pretrade.asymmetry_gate,
        rows=rows,
        bench_count=sum(1 for r in rows if not r.held),
        notes=notes,
    )


# ----------------------------------------------------------------------------------- switches


def _check(result: pt.PretradeResult, check_id: str) -> pt.PretradeCheck | None:
    return next((c for c in result.checks if c.id == check_id), None)


def _limits_held(result: pt.PretradeResult) -> bool:
    return not any(c.status == "flag" and c.id.startswith("after.") for c in result.checks)


async def switch_suggestions(pool: asyncpg.Pool, owner_id: str) -> rk.SwitchResponse:
    """For each holding with a scored expected return, the best-ranked idea that beats it, the
    gap against the switching hurdle and the suggested size, checked on the post-trade book."""
    ranking = await expected_returns(pool)
    margin = (await svc.load_strategy(pool)).pretrade.switch_safety_margin
    positions = await svc._latest_positions(pool)
    scored = [r for r in ranking.rows if r.score is not None and r.returns is not None]
    ideas = [r for r in scored if not r.held]
    lines: list[rk.SwitchLine] = []
    notes: list[str] = []
    if not ideas:
        notes.append("No idea outside the portfolio has scenarios and a price history yet.")
    for held in (r for r in scored if r.held):
        better = [i for i in ideas if (i.score or 0) > (held.score or 0)]
        if not better:
            continue
        idea = better[0]  # rows are sorted by score
        value = float(positions[held.ticker]["value_base"])
        lines.append(await _switch_line(pool, owner_id, held, idea, value, margin))
    return rk.SwitchResponse(
        method_version=version("ranking"),
        as_of=ranking.as_of,
        base_currency=ranking.base_currency,
        lines=lines,
        notes=notes,
    )


async def switch_pair(
    pool: asyncpg.Pool, owner_id: str, sell: str, buy: str, sell_value: float | None = None
) -> tuple[rk.SwitchLine | None, pt.PretradeResult | None, str | None]:
    """One named switch: the suggestion line when both stocks are ranked, otherwise the plain
    pre-trade evaluation of selling `sell_value` (default the whole position) into `buy`.
    The last item is the reason nothing could be computed."""
    ranking = await expected_returns(pool)
    positions = await svc._latest_positions(pool)
    rec = positions.get(sell)
    if rec is None:
        return None, None, f"{sell} is not held, so there is nothing to switch out of"
    held_value = float(rec["value_base"])
    value = min(sell_value, held_value) if sell_value else held_value
    margin = (await svc.load_strategy(pool)).pretrade.switch_safety_margin
    rows = {r.ticker: r for r in ranking.rows}
    a, b = rows.get(sell), rows.get(buy)
    if a and b and a.returns and b.returns:
        line = await _switch_line(pool, owner_id, a, b, held_value, margin, forced_value=value)
        return line, None, None
    try:
        result = await _run(pool, owner_id, sell, buy, value, value)
    except Exception as exc:
        return None, None, f"the evaluation failed: {exc}"
    return None, result, None


async def _run(
    pool: asyncpg.Pool, owner_id: str, held: str, idea: str, sell: float, buy: float
) -> svc.PretradeResponse:
    request = svc.PretradeRequest(
        trades=[
            pt.TradeLeg(side="sell", ticker=held, value_base=sell),
            pt.TradeLeg(side="buy", ticker=idea, value_base=buy),
        ]
    )
    return await svc.run_pretrade(pool, owner_id, request)


async def _switch_line(
    pool: asyncpg.Pool,
    owner_id: str,
    held: rk.RankingRow,
    idea: rk.RankingRow,
    value: float,
    safety_margin: float,
    forced_value: float | None = None,
) -> rk.SwitchLine:
    assert held.returns is not None and idea.returns is not None
    vol_h = held.risk.volatility_1y if held.risk else None
    vol_i = idea.risk.volatility_1y if idea.risk else None
    base = dict(
        sell=held.ticker,
        buy=idea.ticker,
        sell_name=held.name,
        buy_name=idea.name,
        holding_return=held.returns.total,
        idea_return=idea.returns.total,
        holding_volatility=vol_h,
        idea_volatility=vol_i,
    )
    empty = dict(
        return_gap=None, hurdle=None, tax=None, costs=None, safety_margin=None,
        portfolio_improves=None, limits_held=None,
    )  # fmt: skip
    if not vol_h or not vol_i:
        return rk.SwitchLine(
            **base, **empty, action="hold", share=0.0, sell_value=value, buy_value=0.0,
            remainder=value, flagged=False,
            reasons=["a one-year volatility is missing for one of the two stocks"],
        )  # fmt: skip

    def sizes(share: float) -> tuple[float, float]:
        sell = value * share
        return sell, min(rk.equal_risk_value(sell, vol_h, vol_i), sell)

    # A named switch is judged at the size asked for; a scan starts from the whole position.
    asked = forced_value / value if forced_value else None
    sell, buy = sizes(asked or 1.0)
    try:
        result = await _run(pool, owner_id, held.ticker, idea.ticker, sell, buy)
    except Exception as exc:  # one failing pair must not hide the others
        log.warning("switch %s -> %s failed: %s", held.ticker, idea.ticker, exc)
        return rk.SwitchLine(
            **base, **empty, action="hold", share=0.0, sell_value=value, buy_value=0.0,
            remainder=value, flagged=False, reasons=[f"the pre-trade evaluation failed: {exc}"],
        )  # fmt: skip
    gap_check = _check(result, "switch.gap")
    values = gap_check.values if gap_check else {}
    gap, hurdle = values.get("gap"), values.get("hurdle")
    reasons: list[str] = []
    if not isinstance(gap, float) or not isinstance(hurdle, float):
        return rk.SwitchLine(
            **base, **empty, action="hold", share=0.0, sell_value=value, buy_value=0.0,
            remainder=value, flagged=False,
            reasons=[gap_check.detail if gap_check else "the switching hurdle was not computed"],
        )  # fmt: skip
    action, share = rk.switch_action(gap, hurdle)
    if asked is not None:
        share = asked if action != "hold" else 0.0
    elif action == "partial":
        sell, buy = sizes(share)
        result = await _run(pool, owner_id, held.ticker, idea.ticker, sell, buy)
        gap_check = _check(result, "switch.gap")
        values = gap_check.values if gap_check else values
    tax = values.get("tax") if isinstance(values.get("tax"), float) else None
    costs = values.get("costs") if isinstance(values.get("costs"), float) else None
    portfolio = _check(result, "switch.portfolio")
    improves = (
        True if portfolio and portfolio.status == "pass"
        else False if portfolio and portfolio.status == "flag"
        else None
    )  # fmt: skip
    limits = _limits_held(result)
    if gap_check and gap_check.status == "unavailable":
        reasons.append("the tax on the lots sold could not be estimated, so the hurdle is unproven")
    if improves is None and action != "hold":
        reasons.append(
            "the switch trades expected return against portfolio risk; judge it yourself"
        )
    if not limits:
        reasons.append("the switch would break or deepen a rule, cap or risk limit")
    wait = _check(result, "switch.wait")
    flagged = (
        action != "hold" and limits and improves is True
        and not (gap_check and gap_check.status == "unavailable")
    )  # fmt: skip
    return rk.SwitchLine(
        **base,
        action=action,
        share=share,
        sell_value=sell,
        buy_value=buy,
        remainder=max(sell - buy, 0.0),
        return_gap=gap,
        hurdle=hurdle,
        tax=tax,
        costs=costs,
        safety_margin=safety_margin,
        portfolio_improves=improves,
        limits_held=limits,
        flagged=flagged,
        wait_note=wait.detail if wait else None,
        checks=[
            f"{c.status}: {c.title}"
            for c in result.checks
            if c.id.startswith(("switch.", "after."))
        ],
        reasons=reasons,
    )
