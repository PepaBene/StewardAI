"""Opportunity notices (E3): a stock that newly passes one of the owner's own saved screens.

The owner writes the screen, so the criteria are the owner's; nothing here ranks ideas or
forms an opinion. Each day the saved stock screens run as they would from the Discover page, and
a stock that matches now, did not match at the last look and is not already held becomes one
line in "Needs you" ("2 new stocks pass Quality at a fair price"). The first look at a screen
only records what matches, so a new screen does not announce its whole result list.

The screen runner lives in the API layer (`api.discover_routes`), which is imported only when
the step runs.
"""

import json
import logging
from collections.abc import Awaitable, Callable
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


def new_matches(now: list[str], before: list[str] | None, held: set[str]) -> list[str]:
    """Tickers to announce: matched now, not at the last look, not held. The first look
    (`before` is None) announces nothing."""
    if before is None:
        return []
    seen = set(before)
    return [t for t in now if t not in seen and t not in held]


def describe(screen: str, found: list[str], names: dict[str, str]) -> tuple[str, str]:
    n = len(found)
    title = f"{n} new stock{'s' if n != 1 else ''} pass{'' if n != 1 else 'es'} {screen}"
    named = ", ".join(names.get(t, t) for t in found[:MAX_NAMED])
    more = f" and {n - MAX_NAMED} more" if n > MAX_NAMED else ""
    return title, f"Newly matching your saved screen: {named}{more}. Open Discover to see why."


async def check_opportunities(
    pool: asyncpg.Pool, owner_id: Any, run: Runner = api_runner
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
            title, detail = describe(s["name"], found, names)
            await tasks.see(
                pool,
                KIND,
                f"screen:{s['id']}",
                title=title,
                detail=detail,
                href="/discover",
                weight=0.3,
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
