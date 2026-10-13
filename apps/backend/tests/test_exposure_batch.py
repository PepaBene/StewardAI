"""Cost levers E2/G2: the exposure and dependency reads of a run are prefetched as one batch."""

from typing import Any, cast

import pytest

from steward.budget_guard import BudgetExceeded
from steward.exposures.ai_steps import prefetch_batch
from steward.llm.cache import ResultCache
from steward.llm.client import LLM
from steward.llm.routing import RoutingClient


def _client(with_cache: bool) -> RoutingClient:
    cache = cast(ResultCache, object()) if with_cache else None
    return RoutingClient(cast(LLM, object()), cache)


async def test_no_cache_or_a_single_read_asks_nothing() -> None:
    seen: list[str] = []

    async def ask(client: RoutingClient) -> None:
        seen.append("x")

    assert await prefetch_batch(_client(False), 5, [ask, ask]) == 0
    assert await prefetch_batch(_client(True), 5, [ask]) == 0
    assert seen == []


async def test_every_read_runs_on_a_client_sharing_the_cache() -> None:
    client = _client(True)
    used: list[RoutingClient] = []

    async def ask(c: RoutingClient) -> None:
        used.append(c)

    assert await prefetch_batch(client, 5, [ask, ask, ask]) == 3
    assert len(used) == 3
    assert all(c is not client and c.cache is client.cache for c in used)
    assert client.collector is None


async def test_a_failing_read_is_left_for_its_own_step() -> None:
    ran: list[int] = []

    async def bad(c: RoutingClient) -> Any:
        raise ValueError("unreadable")

    async def good(c: RoutingClient) -> None:
        ran.append(1)

    assert await prefetch_batch(_client(True), 5, [bad, good]) == 2
    assert ran == [1]


async def test_a_budget_stop_is_not_swallowed() -> None:
    async def stop(c: RoutingClient) -> Any:
        raise BudgetExceeded("cap")

    async def good(c: RoutingClient) -> None:
        return None

    with pytest.raises(BudgetExceeded):
        await prefetch_batch(_client(True), 5, [stop, good])
