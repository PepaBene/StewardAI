"""Opportunity notices (E3): a stock that newly passes one of the owner's own saved screens.

The owner writes the screen, so the criteria are the owner's; nothing here ranks ideas or
forms an opinion. Each day the saved stock screens run as they would from the Discover page, and
a stock that matches now, did not match at the last look and is not already held becomes one
line in "Needs you" ("2 new stocks pass Quality at a fair price"). The first look at a screen
only records what matches, so a new screen does not announce its whole result list.

The screen runner lives in the API layer (`api.discover_routes`), which is imported only when
the step runs.
"""

import datetime as dt
import json
import logging
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import asyncpg

from steward.discovery.read import holding_tickers
from steward.ops import tasks

logger = logging.getLogger("steward.ops")

KIND = "opportunity"
MAX_SCREENS = 10
MAX_NAMED = 6

# (owner id, saved definition) -> (matched tickers, {ticker: name})
Runner = Callable[[asyncpg.Pool, Any, dict[str, Any]], Awaitable[tuple[list[str], dict[str, str]]]]


async def api_runner(
    pool: asyncpg.Pool, owner_id: Any, definition: dict[str, Any]
) -> tuple[list[str], dict[str, str]]:
    from api.discover_routes import StockRequest, _run_stock

    result = await _run_stock(pool, owner_id, StockRequest.model_validate(definition))
    return [r.key for r in result.rows], {r.key: r.name or r.key for r in result.rows}


# (owner id, saved definition) -> one line saying how the screen did in the past
Backtester = Callable[[asyncpg.Pool, Any, dict[str, Any]], Awaitable[str]]
BACKTEST_YEARS = 3


def backtest_line(
    net: Decimal | None,
    drag: Decimal | None,
    benchmark: Decimal | None,
    rebalances: int,
    warnings: list[str],
) -> str:
    """One plain sentence about a screen's past, or why there is none."""
    if net is None or rebalances < 2:
        why = warnings[0] if warnings else "too little price history"
        return f"Not backtested: {why}."
    parts = [f"{float(net):+.0%} after costs"]
    if drag is not None:
        parts.append(f"costs took {float(drag):.1%}")
    if benchmark is not None:
        parts.append(f"benchmark {float(benchmark):+.0%}")
    return (
        f"Backtest of this screen over {rebalances} yearly rebalances (equal weight, point in "
        f"time, 10 bps cost): {', '.join(parts)}. A description of the past, not a forecast."
    )


async def api_backtester(pool: asyncpg.Pool, owner_id: Any, definition: dict[str, Any]) -> str:
    from types import SimpleNamespace

    from api.discover_routes import BacktestRequest, backtest_screen

    today = dt.date.today()
    request = BacktestRequest.model_validate(
        {
            "filters": definition.get("filters", []),
            "sort_by": definition.get("sort_by"),
            "descending": definition.get("descending"),
            "missing": definition.get("missing", "exclude"),
            "start": today - dt.timedelta(days=365 * BACKTEST_YEARS),
            "end": today,
            "frequency": "annual",
            "max_names": 10,
            "cost_bps": 10,
        }
    )
    result = await backtest_screen(request, pool, SimpleNamespace(user_id=owner_id))  # type: ignore[arg-type]
    return backtest_line(
        result.cumulative_net,
        result.cost_drag,
        result.benchmark_cumulative,
        len(result.periods),
        result.warnings,
    )


async def backtest_once_a_month(
    pool: asyncpg.Pool,
    owner_id: Any,
    screen_id: int,
    definition: dict[str, Any],
    backtest: Backtester,
    now: dt.datetime,
) -> str:
    """The screen's backtest line, computed at most once a month per screen."""
    key = f"opportunity_backtest:{screen_id}"
    month = now.strftime("%Y-%m")
    raw = await pool.fetchval("select value from public.sync_state where key = $1", key)
    stored = json.loads(raw) if isinstance(raw, str) else raw or {}
    if stored.get("month") == month and isinstance(stored.get("text"), str):
        return str(stored["text"])
    try:
        text = await backtest(pool, owner_id, definition)
    except Exception as exc:  # a screen that cannot run over history is reported, not fatal
        text = f"Not backtested: {type(exc).__name__}."
        logger.warning("backtest of screen %s failed: %s", screen_id, exc)
    await pool.execute(
        "insert into public.sync_state (key, value) values ($1, $2::jsonb)"
        " on conflict (key) do update set value = excluded.value",
        key,
        json.dumps({"month": month, "text": text}),
    )
    return text


def new_matches(now: list[str], before: list[str] | None, held: set[str]) -> list[str]:
    """Tickers to announce: matched now, not at the last look, not held. The first look
    (`before` is None) announces nothing."""
    if before is None:
        return []
    seen = set(before)
    return [t for t in now if t not in seen and t not in held]


def describe(
    screen: str, found: list[str], names: dict[str, str], watched: set[str] | None = None
) -> tuple[str, str]:
    """The "Needs you" line. Watchlist names that start to pass are named first and said to be
    on the watchlist (insight I10.6): those are the ones the owner was already waiting on."""
    watched = watched or set()
    watch = [t for t in found if t in watched]
    rest = [t for t in found if t not in watched]
    n = len(found)
    if watch and not rest:
        lead = names.get(watch[0], watch[0])
        title = (
            f"{lead} from your watchlist now passes {screen}"
            if len(watch) == 1
            else f"{len(watch)} watchlist stocks now pass {screen}"
        )
    else:
        title = f"{n} new stock{'s' if n != 1 else ''} pass{'' if n != 1 else 'es'} {screen}"
    ordered = watch + rest
    named = ", ".join(
        f"{names.get(t, t)}{' (watchlist)' if t in watched else ''}" for t in ordered[:MAX_NAMED]
    )
    more = f" and {n - MAX_NAMED} more" if n > MAX_NAMED else ""
    return title, f"Newly matching your saved screen: {named}{more}. Open Discover to see why."


async def check_opportunities(
    pool: asyncpg.Pool,
    owner_id: Any,
    run: Runner = api_runner,
    backtest: Backtester | None = api_backtester,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    screens = await pool.fetch(
        "select s.id, s.name, v.definition from public.screen_definition s"
        " join public.screen_definition_version v on v.screen_id = s.id"
        "  and v.version = s.latest_version"
        " where s.owner_id = $1 and s.kind = 'stock' and not s.archived order by s.id limit $2",
        owner_id,
        MAX_SCREENS,
    )
    held = await holding_tickers(pool)
    from steward.discovery.read import watchlist_tickers

    watched = set(await watchlist_tickers(pool, owner_id))
    announced: dict[str, list[str]] = {}
    failed: dict[str, str] = {}
    for s in screens:
        key = f"opportunity_screen:{s['id']}"
        definition = s["definition"]
        if isinstance(definition, str):
            definition = json.loads(definition)
        try:
            matched, names = await run(pool, owner_id, {**definition, "limit": 200})
        except Exception as exc:  # a screen the data cannot run yet is skipped, not fatal
            failed[s["name"]] = f"{type(exc).__name__}: {exc}"[:200]
            continue
        raw = await pool.fetchval("select value from public.sync_state where key = $1", key)
        before = (json.loads(raw) if isinstance(raw, str) else raw or {}).get("tickers")
        found = new_matches(matched, before, held)
        if found:
            title, detail = describe(s["name"], found, names, watched)
            if backtest is not None:
                past = await backtest_once_a_month(
                    pool,
                    owner_id,
                    s["id"],
                    definition,
                    backtest,
                    now or dt.datetime.now(dt.UTC),
                )
                detail = f"{detail} {past}"
            await tasks.see(
                pool,
                KIND,
                f"screen:{s['id']}",
                title=title,
                detail=detail,
                href="/discover",
                weight=0.5 if watched & set(found) else 0.3,
                status="needs_owner",
            )
            announced[s["name"]] = found
        await pool.execute(
            "insert into public.sync_state (key, value) values ($1, $2::jsonb)"
            " on conflict (key) do update set value = excluded.value",
            key,
            json.dumps({"tickers": matched}),
        )
    return {"screens": len(screens), "announced": announced, "failed": failed}
