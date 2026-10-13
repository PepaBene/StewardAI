"""The Stooq circuit breaker: a run that finds Stooq down stops asking it for every symbol."""

from typing import Any, cast

import asyncpg
import httpx
import pytest

from steward.reference import prices
from steward.reference.price_series import PriceSymbols


async def run(deps: prices.PriceDeps, symbols: PriceSymbols) -> tuple[dict[str, Any], str | None]:
    return await prices.collect_series(
        cast(asyncpg.Pool, None), deps, symbols, 5, True, {}, {}, "hint"
    )


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    seen: dict[str, list[str]] = {"stooq": [], "yahoo": []}

    async def stooq(_p: Any, _d: Any, symbol: str, _y: int, _n: Any) -> tuple[int, str | None]:
        seen["stooq"].append(symbol)
        return 0, "Stooq unreachable: timeout"

    async def yahoo(_p: Any, _d: Any, symbol: str, _y: int, _n: Any) -> tuple[int, str | None]:
        seen["yahoo"].append(symbol)
        return 5, None

    monkeypatch.setattr(prices, "collect_symbol", stooq)
    monkeypatch.setattr(prices, "collect_yahoo", yahoo)
    return seen


async def test_stooq_is_skipped_after_repeated_transport_failures(
    calls: dict[str, list[str]],
) -> None:
    deps = prices.PriceDeps(http=httpx.AsyncClient(), stooq_breaker_after=2)
    for n in range(4):
        used, gap = await run(deps, PriceSymbols(stooq=f"S{n}.US", yahoo=f"Y{n}"))
        assert gap is None and used["source"] == "yahoo"
    assert calls["stooq"] == ["S0.US", "S1.US"]  # the breaker opened after two failures
    assert len(calls["yahoo"]) == 4
    assert "skipped" in used["stooq_gap"]


async def test_a_stooq_answer_resets_the_count(monkeypatch: pytest.MonkeyPatch) -> None:
    replies = iter(["Stooq unreachable: x", "Stooq has no data for S1", "Stooq unreachable: x"])

    async def stooq(*_a: Any) -> tuple[int, str | None]:
        return 0, next(replies)

    async def yahoo(*_a: Any) -> tuple[int, str | None]:
        return 1, None

    monkeypatch.setattr(prices, "collect_symbol", stooq)
    monkeypatch.setattr(prices, "collect_yahoo", yahoo)
    deps = prices.PriceDeps(http=httpx.AsyncClient(), stooq_breaker_after=2)
    for n in range(3):
        await run(deps, PriceSymbols(stooq=f"S{n}", yahoo=f"Y{n}"))
    assert deps.stooq_failures == 1  # the "no data" answer in the middle reset it
