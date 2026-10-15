"""Chat tools that answer comparative questions with the app's deterministic analytics (M7.6).

Each result carries the method in words, what it covers and what it could not measure, and a
registry id for the page that shows the same figures. When an input is missing the tool says so
and returns no figure; the model is told to report that rather than estimate."""

from __future__ import annotations

import datetime as dt
from typing import Any

import asyncpg

from steward.chat import envelope as env
from steward.decision import service as decision_service
from steward.decision.themes import METHOD as THEME_METHOD
from steward.decision.themes import ThemeDefinition, ThemeSelector, theme_exposure
from steward.events import feed as events_feed
from steward.events.paths import event_paths
from steward.journal import service as journal_service
from steward.lookthrough.read import UNCLASSIFIED
from steward.performance import service as performance_service
from steward.performance.settings import load_settings
from steward.research.sources import Registry
from steward.xray import reports as xray_reports
from steward.xray import service as xray_service
from steward.xray.views import build_view

SELECTOR_KINDS = [
    "sector", "industry", "country", "domicile", "listing", "currency", "revenue_country",
    "commodity", "trade_product", "issuer",
]  # fmt: skip

MAX_AMOUNT = 1e9  # the same ceiling as the REST routes for a deposit or a trade size
UNAVAILABLE = {"unavailable": "There is no portfolio snapshot yet, so nothing can be computed."}


async def conversation_owner(pool: asyncpg.Pool, conversation_id: int) -> str | None:
    value = await pool.fetchval(
        "select created_by::text from public.chat_conversation where id = $1", conversation_id
    )
    return str(value) if value else None


async def stress_test(pool: asyncpg.Pool, registry: Registry, owner_id: str) -> dict[str, Any]:
    """The named shocks (equity fall, rate rise, wider credit spreads, a fall in each foreign
    currency) applied to today's holdings."""
    try:
        table = await decision_service.run_standard(pool, owner_id)
    except decision_service.NoHoldings:
        return UNAVAILABLE
    sid = registry.add(
        "observation",
        f"stress:{table.as_of}",
        f"Stress test of {table.as_of}",
        app_path="/risk/stress",
        tier=1,
        snippet="; ".join(f"{line.name}: {line.pnl_pct:+.1%}" for line in table.lines)[:500],
        as_of=table.as_of,
    )
    return {
        "source_id": sid,
        "as_of": table.as_of,
        "base_currency": table.base_currency,
        "total_value": table.total_value,
        "formula": "Each holding moves by its sensitivity to the shock (measured beta where the "
        "history supports one, otherwise an owner value or the stated fallback); the parts "
        "reconcile to the shocked total.",
        "coverage": "unshocked_weight is the share of the portfolio with no valid sensitivity; "
        "it stays unchanged and is not counted as safe.",
        "lines": [
            {
                "name": line.name,
                "pnl_pct": line.pnl_pct,
                "pnl": line.pnl,
                "unshocked_weight": line.unshocked_weight,
            }
            for line in table.lines
        ],
        "notes": table.notes,
    }


async def risk_forecast(pool: asyncpg.Pool, registry: Registry) -> dict[str, Any]:
    """The forecast volatility of today's holdings, where it comes from and what was left out."""
    from steward.portfolio.exante import ex_ante_risk

    report = await ex_ante_risk(pool)
    if report.volatility_forecast is None:
        return {"unavailable": report.reason or "the model has nothing to work from"}
    sid = registry.add(
        "observation",
        f"exante:{report.as_of}",
        f"Forecast risk of {report.as_of}",
        app_path="/risk/ex-ante",
        tier=1,
        snippet=f"forecast volatility {report.volatility_forecast:.1%} a year",
        as_of=str(report.as_of),
    )
    top = sorted(
        (p for p in report.positions if p.risk_share is not None),
        key=lambda p: p.risk_share or 0,
        reverse=True,
    )[:5]
    return {
        "source_id": sid,
        "as_of": str(report.as_of),
        "method": report.method,
        "volatility_forecast": report.volatility_forecast,
        "volatility_forecast_if_left_out_were_average": report.volatility_forecast_scaled,
        "realized_volatility_last_year": report.realized_volatility,
        "effective_bets": report.effective_bets,
        "diversification_ratio": report.diversification_ratio,
        "largest_risk_contributors": [
            {"ticker": p.ticker, "risk_share": p.risk_share, "weight": p.weight} for p in top
        ],
        "by_tier": [g.model_dump() for g in report.by_tier],
        "left_out_weight": report.coverage.uncovered_weight if report.coverage else None,
        "warnings": report.warnings,
    }


async def past_episodes(pool: asyncpg.Pool, registry: Registry) -> dict[str, Any]:
    """Today's holdings run through past market episodes."""
    from steward.portfolio.replay import stress_replay

    report = await stress_replay(pool)
    if report.reason:
        return {"unavailable": report.reason}
    sid = registry.add(
        "observation",
        f"replay:{report.as_of}",
        f"Past episodes replayed on holdings of {report.as_of}",
        app_path="/risk/replay",
        tier=1,
        snippet="; ".join(
            f"{e.label}: {e.loss:+.1%}" for e in report.episodes if e.loss is not None
        )[:500],
        as_of=str(report.as_of),
    )
    return {
        "source_id": sid,
        "method": report.method,
        "drawdown_limit": report.drawdown_limit,
        "episodes": [
            {
                "label": e.label,
                "from": str(e.start),
                "to": str(e.end),
                "status": e.status,
                "loss": e.loss,
                "pnl": e.pnl,
                "breaches_limit": e.breaches_limit,
                "unavailable_weight": e.unavailable_weight,
                "caution": e.caution,
                "reason": e.reason,
            }
            for e in report.episodes
        ],
        "warnings": report.warnings,
    }


async def factor_tilts(pool: asyncpg.Pool, registry: Registry, region: str) -> dict[str, Any]:
    """What the portfolio is leaning on: market, size, value, momentum, currency and rates."""
    from steward.portfolio.factors import factor_exposures

    report = await factor_exposures(pool, region)
    if report.portfolio is None:
        return {"unavailable": report.reason or "no factor data yet"}
    sid = registry.add(
        "observation",
        f"factors:{region}:{report.as_of}",
        f"Factor exposures of {report.as_of} ({region})",
        app_path="/risk/factors",
        tier=1,
        snippet=f"R² {report.portfolio.r_squared:.2f} over {report.portfolio.observations} weeks",
        as_of=str(report.as_of),
    )
    return {
        "source_id": sid,
        "region": region,
        "method": report.method,
        "r_squared": report.portfolio.r_squared,
        "weeks": report.portfolio.observations,
        "loadings": [
            {"factor": b.label, "beta": b.beta, "t_stat": b.t_stat} for b in report.portfolio.betas
        ],
        "tilts_against_benchmark": [
            {"factor": t.label, "tilt": t.tilt, "flagged": t.flagged, "note": t.note}
            for t in report.tilts
        ],
        "warnings": report.warnings,
    }


async def model_check(pool: asyncpg.Pool, registry: Registry) -> dict[str, Any]:
    """Whether the risk model's forecasts held up against what happened."""
    from steward.portfolio.validation import model_validation

    report = await model_validation(pool)
    if not report.available:
        return {"unavailable": report.reason}
    sid = registry.add(
        "observation",
        f"modelcheck:{report.as_of}",
        f"Risk model back-test to {report.as_of}",
        app_path="/risk/validation",
        tier=1,
        snippet=report.verdict[:500],
        as_of=report.as_of,
    )
    return {
        "source_id": sid,
        "confidence": report.confidence,
        "verdict": report.verdict,
        "weeks_tested": report.weeks_tested,
        "backtests": [b.model_dump() for b in report.backtests],
        "calibration": report.calibration.model_dump() if report.calibration else None,
        "warnings": report.warnings,
        "method": report.method,
    }


async def position_risk(pool: asyncpg.Pool, registry: Registry, ticker: str) -> dict[str, Any]:
    """One holding's risk contribution, cluster and stress losses."""
    from steward.portfolio.position_pack import position_risk_pack

    name = (await decision_service.resolve_tickers(pool, [ticker])).get(ticker.strip(), ticker)
    pack = await position_risk_pack(pool, name.strip())
    if not pack.available:
        return {"unavailable": pack.reason}
    sid = registry.add(
        "observation",
        f"positionrisk:{pack.ticker}",
        f"Risk of {pack.ticker} in the portfolio",
        app_path="/risk/ex-ante",
        tier=1,
        snippet=pack.summary[:500],
    )
    return {"source_id": sid, **pack.model_dump()}


async def idea_factor_fit(
    pool: asyncpg.Pool,
    registry: Registry,
    symbols: list[str],
    currency: str,
    weight: float,
) -> dict[str, Any]:
    """Which candidate ideas would reduce the portfolio's dominant style tilt."""
    from steward.portfolio.idea_fit import idea_fit

    report = await idea_fit(pool, symbols, currency, "developed", weight)
    if report.reason:
        return {"unavailable": report.reason}
    sid = registry.add(
        "observation",
        f"ideafit:{report.as_of}:{','.join(symbols)}",
        f"Factor fit of ideas against holdings of {report.as_of}",
        app_path="/risk/factors",
        tier=1,
        snippet="; ".join(
            f"{i.symbol}: relief {i.relief:+.2f}" for i in report.ideas if i.relief is not None
        )[:500],
        as_of=str(report.as_of),
    )
    return {
        "source_id": sid,
        "method": report.method,
        "weight": report.weight,
        "ideas": [i.model_dump(exclude={"tilts", "own_betas"}) for i in report.ideas],
        "use": "A tie-breaker between comparable ideas (principle 7c). Never take a weaker idea "
        "only because its relief is higher.",
    }


async def risk_whatif(
    pool: asyncpg.Pool, registry: Registry, trades: list[dict[str, Any]], deposit: float
) -> dict[str, Any]:
    """What proposed trades or a deposit do to the ex-ante risk of the book (plan Q5.1)."""
    from steward.decision.pretrade import TradeLeg
    from steward.portfolio.trade_risk import trade_risk

    if not trades and not deposit:
        return {"error": "give at least one trade or a deposit"}
    if not isinstance(deposit, int | float) or not 0 <= deposit <= MAX_AMOUNT:
        return {"error": f"the deposit must be between 0 and {MAX_AMOUNT:,.0f}"}
    try:
        names = await decision_service.resolve_tickers(
            pool, [str(t.get("ticker", "")) for t in trades]
        )
        legs = [
            TradeLeg(
                side=t["side"],
                ticker=names.get(str(t["ticker"]).strip(), str(t["ticker"]).strip()),
                value_base=float(t["value_base"]),
            )
            for t in trades
        ]
        if any(not 0 < leg.value_base <= MAX_AMOUNT for leg in legs):
            raise ValueError(f"value_base must be above 0 and at most {MAX_AMOUNT:,.0f}")
    except (KeyError, ValueError, TypeError, AttributeError) as err:
        return {"error": f"each trade needs side (buy or sell), ticker and value_base: {err}"}
    report = await trade_risk(pool, legs, deposit)
    if not report.available:
        return {"unavailable": report.reason}
    sid = registry.add(
        "observation",
        f"tradeRisk:{report.as_of}:{','.join(leg.ticker for leg in legs)}",
        f"Ex-ante risk before and after the proposed trades ({report.as_of})",
        app_path="/risk/pretrade",
        tier=1,
        snippet=f"volatility {report.volatility_before:.1%} to {report.volatility_after:.1%}",
        as_of=str(report.as_of),
    )
    return {
        "source_id": sid,
        "trades": [leg.model_dump() for leg in legs],
        "deposit": deposit,
        **report.model_dump(exclude={"lines"}),
        "largest_lines": [x.model_dump() for x in report.lines[:8]],
        "use": "Quote the before and after figures and any limit marked new or deeper. The "
        "Expected Shortfall and drawdown-probability limits are tested but their figures are not "
        "shown here.",
    }


async def start_size_for(pool: asyncpg.Pool, registry: Registry, ticker: str) -> dict[str, Any]:
    """The risk-aware starting size and the quarter-Kelly bound for a stock (plan Q5.2, Q5.3)."""
    from steward.portfolio.sizing import start_size

    name = (await decision_service.resolve_tickers(pool, [ticker])).get(ticker.strip(), ticker)
    out = await start_size(pool, name)
    if out.status != "ok":
        return {"unavailable": out.reason, "ticker": name}
    sid = registry.add(
        "observation",
        f"startSize:{name}",
        f"Starting size for {name}",
        app_path="/risk/deposit",
        tier=1,
        snippet=f"suggested {out.suggested:.1%} (tier cap {out.tier_cap:.1%})"
        if out.suggested is not None and out.tier_cap is not None
        else "",
    )
    return {
        "source_id": sid,
        **out.model_dump(),
        "use": "The tier cap of principle 8b stays authoritative; the suggestion only lowers it. "
        "Kelly is an upper bound shown with its inputs, never a target.",
    }


async def deposit_allocation(
    pool: asyncpg.Pool, registry: Registry, amount: float
) -> dict[str, Any]:
    """Where a deposit should go under principle 8c (plan Q5.4)."""
    from steward.decision.allocator import allocate_deposit

    if not isinstance(amount, int | float) or not 0 < amount <= MAX_AMOUNT:
        return {"error": f"the amount must be above 0 and at most {MAX_AMOUNT:,.0f}"}
    try:
        plan = await allocate_deposit(pool, amount)
    except LookupError as err:
        return {"unavailable": str(err)}
    sid = registry.add(
        "observation",
        f"deposit:{amount:.0f}",
        f"Deposit allocation of {amount:,.0f} {plan.base_currency}",
        app_path="/risk/deposit",
        tier=1,
        snippet="; ".join(f"{x.ticker} x{x.shares}" for x in plan.lines)[:300],
    )
    return {"source_id": sid, **plan.model_dump()}


async def rotation_candidates(
    pool: asyncpg.Pool, registry: Registry, owner_id: str
) -> dict[str, Any]:
    """The thesis ranking and the switches it suggests (plan Q8.4, Q8.5, Q8.10)."""
    from steward.decision.ranking_service import switch_suggestions
    from steward.decision.service import NoHoldings, NoStrategy

    try:
        switches = await switch_suggestions(pool, owner_id)
    except (NoHoldings, NoStrategy) as err:
        return {"unavailable": str(err) or type(err).__name__}
    from steward.decision.ranking_service import expected_returns

    ranking = await expected_returns(pool)
    sid = registry.add(
        "observation",
        f"rotation:{ranking.as_of}",
        "Thesis ranking and suggested switches",
        app_path="/theses",
        tier=1,
        snippet="; ".join(
            f"{r.ticker} score {r.score:.2f}" for r in ranking.rows if r.score is not None
        )[:300],
        as_of=ranking.as_of,
    )
    return {
        "source_id": sid,
        "ranking": [r.model_dump(mode="json") for r in ranking.rows[:12]],
        "switches": [line.model_dump(mode="json") for line in switches.lines],
        "notes": [*ranking.notes, *switches.notes],
        "formula": ranking.formula,
        "disclaimer": switches.disclaimer,
    }


async def switch_effect(
    pool: asyncpg.Pool,
    registry: Registry,
    owner_id: str,
    sell: str,
    buy: str,
    sell_value_base: float | None,
) -> dict[str, Any]:
    """What selling one holding to buy another does: gap against the hurdle, tax, equal-risk size
    and the effect on the book's limits (plan Q8.10)."""
    from steward.decision.ranking_service import switch_pair
    from steward.decision.service import NoHoldings, NoStrategy

    resolved = await decision_service.resolve_tickers(pool, [sell, buy])
    sell = resolved.get(sell.strip(), sell.strip())
    buy = resolved.get(buy.strip(), buy.strip())
    try:
        line, plain, reason = await switch_pair(pool, owner_id, sell, buy, sell_value_base)
    except (NoHoldings, NoStrategy) as err:
        return {"unavailable": str(err) or type(err).__name__}
    if reason:
        return {"unavailable": reason}
    sid = registry.add(
        "observation",
        f"switch:{sell}:{buy}",
        f"Switching {sell} for {buy}",
        app_path="/risk/pretrade",
        tier=1,
        snippet=(line.checks[0] if line and line.checks else "pre-trade check")[:300],
    )
    if line is not None:
        return {"source_id": sid, "switch": line.model_dump(mode="json")}
    assert plain is not None
    return {
        "source_id": sid,
        "note": "One of the two stocks has no scenarios or no price history, so only the "
        "pre-trade checks ran at the same value on both sides.",
        "checks": [c.model_dump(mode="json") for c in plain.checks],
    }


async def over_cap_positions(
    pool: asyncpg.Pool, registry: Registry, monthly_deposit: float | None
) -> dict[str, Any]:
    """Positions above the single-stock cap and the hold-and-dilute or trim paths (plan Q5.5)."""
    from steward.portfolio.concentration import concentration_report

    report = await concentration_report(pool, monthly_deposit)
    if report.reason:
        return {"unavailable": report.reason}
    sid = registry.add(
        "observation",
        f"overCap:{report.cap}",
        "Positions above the single-stock cap",
        app_path="/risk/deposit",
        tier=1,
        snippet="; ".join(f"{p.ticker} {p.weight:.1%}" for p in report.positions)[:300],
    )
    return {
        "source_id": sid,
        **report.model_dump(),
        "use": "Whether the thesis still projects growth is the owner's judgement (principle "
        "4f); present both paths and do not choose between them.",
    }


async def theme(
    pool: asyncpg.Pool, registry: Registry, owner_id: str, kind: str, value: str
) -> dict[str, Any]:
    """How much of the portfolio sits in one sector, country, currency, revenue country,
    commodity, trade product or issuer, directly and through funds."""
    if kind not in SELECTOR_KINDS:
        return {"error": f"kind must be one of {', '.join(SELECTOR_KINDS)}"}
    loaded = await xray_service.load_cached(pool, owner_id)
    if loaded.data.portfolio is None:
        return UNAVAILABLE
    # A value the portfolio does not carry would read as 0 % exposure at "high" confidence; check
    # it against the labels the portfolio has, as the theme picker does.
    ctx = xray_service.view_context(loaded)
    labels = {
        b.key: b.label
        for b in build_view(kind, ctx).buckets  # type: ignore[arg-type]
        if b.kind == "classified" and b.key != UNCLASSIFIED
    }
    wanted = value.strip().casefold()
    match = next(
        (k for k, label in labels.items() if wanted in (str(k).casefold(), str(label).casefold())),
        None,
    )
    if match is None:
        sample = ", ".join(str(k) for k in list(labels)[:15])
        return {
            "error": f"The portfolio has no {kind} labelled {value!r}, so no exposure was "
            f"computed. Values it carries: {sample or 'none'}."
        }
    value = str(match)
    definition = ThemeDefinition(
        name=f"{kind}: {value}",
        selectors=[ThemeSelector(kind=kind, value=value)],
    )
    result = theme_exposure(definition, ctx)
    sid = registry.add(
        "observation",
        f"theme:{kind}:{value}",
        f"Exposure to {kind} {value}",
        app_path="/risk/themes",
        tier=1,
        snippet=f"known {result.exposed_weight:.1%}, upper bound {result.upper_bound:.1%}, "
        f"{result.confidence} confidence",
    )
    return {
        "source_id": sid,
        "theme": f"{kind}: {value}",
        "exposed_weight": result.exposed_weight,
        "direct_weight": result.direct_weight,
        "via_funds_weight": result.fund_weight,
        "unknown_weight": result.unknown.weight,
        "upper_bound": result.upper_bound,
        "confidence": result.confidence,
        "confidence_reason": result.confidence_reason,
        "largest_issuers": [
            {"issuer": i.issuer, "exposed": i.exposed, "direct": i.direct, "via_funds": i.via_funds}
            for i in result.issuers[:8]
        ],
        "formula": THEME_METHOD,
        "warnings": result.warnings,
    }


async def event_reach(
    pool: asyncpg.Pool,
    registry: Registry,
    owner_id: str,
    symbol: str,
    direction: str,
    state: str,
) -> dict[str, Any]:
    """Where an event at one company reaches the portfolio along the supply-chain links."""
    if direction not in ("downstream", "upstream"):
        return {"error": "direction must be downstream or upstream"}
    if state not in ("confirmed", "reported", "rumoured"):
        return {"error": "state must be confirmed, reported or rumoured"}
    chain, indicators = await events_feed.chain_and_indicators(pool, owner_id)
    wanted = symbol.split("_")[0].strip().upper()
    if not wanted:
        return {"error": "give a company symbol, e.g. TSM"}
    impact = event_paths(chain, wanted, direction, state, indicators)  # type: ignore[arg-type]
    sid = registry.add(
        "observation",
        f"event_paths:{wanted}:{direction}:{state}",
        f"Event at {wanted}, {direction}",
        app_path="/xray/supply-chain",
        tier=1,
        snippet=f"{len(impact.paths)} path(s), reaching {impact.reached_weight:.1%} of holdings",
    )
    return {
        "source_id": sid,
        "symbol": wanted,
        "direction": direction,
        "state": state,
        "reached_weight": impact.reached_weight,
        "own_weight": impact.own_weight,
        "paths": [
            {
                "start": p.start,
                "links": [f"{e.supplier} supplies {e.customer} ({e.product})" for e in p.edges],
                "end": p.end,
                "end_weight": p.weight,
                "confidence": p.confidence,
                "severity": p.severity,
                "revenue_share": p.revenue_share,
                "indicators": p.indicators,
            }
            for p in impact.paths[:8]
        ],
        "notes": impact.notes,
        "formula": impact.method,
    }


async def event_feed(
    pool: asyncpg.Pool,
    registry: Registry,
    owner_id: str,
    days: int,
    ticker: str | None,
    limit: int,
) -> dict[str, Any]:
    """Recent classified news about companies linked to the holdings along the supply chain,
    most serious first. Lists what reached the book; sends nothing."""
    days = max(1, min(int(days), 90))
    limit = env.clamp_limit(limit, default=10, maximum=25)
    today = dt.datetime.now(dt.UTC).date()
    since = today - dt.timedelta(days=days)
    chain, indicators = await events_feed.chain_and_indicators(pool, owner_id)
    stories = await events_feed.load_stories(pool, since)
    feed = events_feed.build(stories, chain, indicators, today, since)
    wanted = ticker.split("_")[0].strip().upper() if ticker else None
    items = [
        i
        for i in feed.items
        if wanted is None or wanted in (i.story.ticker.split("_")[0].upper(), i.top.end.upper())
    ]
    shown = items[:limit]
    sid = registry.add(
        "observation",
        f"event_feed:{since}:{wanted or 'all'}",
        f"Event feed since {since}",
        app_path="/xray/events",
        tier=2,
        snippet="; ".join(f"{i.story.ticker}: {i.story.headline[:80]}" for i in shown[:5])[:500],
        as_of=str(today),
    )
    return {
        "source_id": sid,
        "since": str(since),
        "stories_read": feed.stories_read,
        "items": [
            {
                "headline": i.story.headline,
                "url": i.story.url,
                "outlet": i.story.outlet,
                "published": i.story.published_at.date().isoformat(),
                "event_type": i.story.event_type,
                "company": i.story.ticker,
                "state": i.state,
                "direction": i.direction,
                "reached_weight": env.datum(
                    i.impact.reached_weight, unit="fraction", source_id=sid, as_of=today
                ),
                "would_alert": i.would_alert,
                "top_path": f"{i.top.start} > {i.top.end}",
                "confidence": i.top.confidence,
            }
            for i in shown
        ],
        "page": env.page(shown, limit, total=len(items)),
        "note": feed.note,
    }


async def fund_overlap(
    pool: asyncpg.Pool, registry: Registry, owner_id: str, ticker: str
) -> dict[str, Any]:
    """Which held funds also hold a stock, and how much of the portfolio reaches it twice."""
    loaded = await xray_service.load_cached(pool, owner_id)
    if loaded.data.portfolio is None:
        return UNAVAILABLE
    wanted = ticker.split("_")[0].strip().upper()
    if not wanted:
        return {"error": "give a ticker, e.g. AAPL or AAPL_US_EQ"}
    report = xray_reports.intersection_response(loaded)
    hits = [
        i
        for i in report.issuers
        if any((s.ticker or "").split("_")[0].upper() == wanted for s in i.securities)
        or i.label.upper() == wanted
    ]
    if not hits:
        return {
            "ticker": wanted,
            "found": False,
            "note": "The ticker is not among the largest issuers the overlap report lists "
            "(it covers the top 100) or no held fund reports it.",
            "funds_looked_through": len(report.funds),
        }
    issuer = hits[0]
    sid = registry.add(
        "observation",
        f"overlap:{wanted}",
        f"Overlap in {issuer.label}",
        app_path="/xray/overlap",
        tier=1,
        snippet=f"{issuer.weight:.2%} of the portfolio, {issuer.overlap_weight:.2%} reached twice",
        as_of=report.as_of,
    )
    return {
        "source_id": sid,
        "ticker": wanted,
        "found": True,
        "issuer": issuer.label,
        "total_weight": issuer.weight,
        "direct_weight": issuer.direct_weight,
        "via_funds_weight": issuer.fund_weight,
        "reached_twice_weight": issuer.overlap_weight,
        "channels": [
            {"kind": c.kind, "label": c.label, "weight": c.weight} for c in issuer.channels
        ],
        "formula": "Securities of one issuer are merged. Weight is direct holding plus each "
        "fund's holding of it times the fund's share of the portfolio; overlap is the weight "
        "beyond the largest single channel.",
        "coverage": f"{len(report.funds)} funds looked through; weight of funds not looked "
        "through is in the unknown bucket and is not counted here.",
    }


async def performance_explain(
    pool: asyncpg.Pool, registry: Registry, owner_id: str, days: int = 365
) -> dict[str, Any]:
    """Where the gain, or the shortfall against the benchmark, came from over the last `days`."""
    settings, _ = await load_settings(pool, owner_id)
    report = await performance_service.attribution_report(pool, settings, max(7, min(days, 3650)))
    if report.status == "unavailable" or report.bridge is None:
        return {"unavailable": report.gap or "attribution is not available"}
    ranked = sorted(report.holdings, key=lambda h: h.total)
    sid = registry.add(
        "observation",
        f"attribution:{report.end}:{report.days}",
        f"Performance attribution to {report.end}",
        app_path="/performance/attribution",
        tier=1,
        snippet=f"gain {report.bridge.gain.value}; residual {report.bridge.residual}",
        as_of=str(report.end),
    )
    out: dict[str, Any] = {
        "source_id": sid,
        "start": str(report.start),
        "end": str(report.end),
        "base_currency": report.base_currency,
        "gain": report.bridge.gain.value,
        "components": report.bridge.components,
        "residual": report.bridge.residual,
        "net_external_flows": report.bridge.net_external_flows,
        "unattributed_tickers": report.bridge.unattributed_tickers,
        "largest_detractors": [
            {"ticker": h.ticker, "name": h.name, "total": h.total}
            for h in ranked[:5]
            if h.total < 0
        ],
        "largest_contributors": [
            {"ticker": h.ticker, "name": h.name, "total": h.total}
            for h in reversed(ranked[-5:])
            if h.total > 0
        ],
        "formula": report.explain.formula,
    }
    rel = report.benchmark
    if rel is None:
        out["versus_benchmark"] = "No benchmark comparison is available for these dates."
    else:
        out["versus_benchmark"] = {
            "benchmark": rel.label,
            "benchmark_return": rel.benchmark_return,
            "active_total": rel.active_total,
            "like_for_like": rel.like_for_like,
            "notes": rel.notes,
        }
    return out


async def decision_scorecard(
    pool: asyncpg.Pool, registry: Registry, owner_id: str
) -> dict[str, Any]:
    """How the owner's recorded decisions have done, with sample sizes."""
    card = await journal_service.scorecard(pool, owner_id)
    sid = registry.add(
        "observation",
        f"scorecard:{card.until}",
        f"Decision scorecard to {card.until}",
        app_path="/journal/scorecard",
        tier=1,
        snippet=f"{card.entries} decisions since {card.since}",
        as_of=str(card.until),
    )
    return {
        "source_id": sid,
        "since": str(card.since),
        "until": str(card.until),
        "decisions": card.entries,
        "method": card.method,
        "sections": [
            {
                "title": s.title,
                "status": s.status,
                "decisions": s.n,
                "note": s.note,
                "rows": [r.model_dump(exclude_none=True) for r in s.rows],
            }
            for s in card.sections
        ],
        "suggestions": card.suggestions,
    }
