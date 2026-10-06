"""The rule-first checks (listing approval, claim quotes and dates, IR page probes) and the
batch collector that lets per-holding flows share one provider batch."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from steward.approvals.ai import Check, listing_rule
from steward.config.holding_profiles import HoldingProfile
from steward.fundamentals.ir_discovery import probe_urls
from steward.llm.batching import BatchCollector
from steward.llm.types import Completion, Request
from steward.verify.claims import dates_in, is_quote, missing_dates

# ------------------------------------------------------------------------------ listing rule


def _checks(suffix: bool | None, country: bool | None, symbol: bool | None) -> list[Check]:
    return [
        Check("market suffix", suffix, ""),
        Check("isin country", country, ""),
        Check("symbol", symbol, ""),
    ]


def test_listing_confirmed_only_when_every_check_passes() -> None:
    assert listing_rule(_checks(True, True, True)) == (
        "confirmed",
        "listing-exact-match",
        "market suffix, ISIN country and symbol agree",
    )
    assert listing_rule(_checks(True, None, True)) is None  # unknown never settles
    assert listing_rule(_checks(True, True, None)) is None


def test_listing_rejected_only_when_suffix_and_symbol_both_fail() -> None:
    rejected = listing_rule(_checks(False, None, False))
    assert rejected is not None and rejected[0] == "rejected"
    assert listing_rule(_checks(False, True, True)) is None  # same company, other venue
    assert listing_rule(_checks(True, True, False)) is None


# ----------------------------------------------------------------------------- claim rules


def test_dates_in_reads_the_three_forms() -> None:
    import datetime as dt

    text = "On 2026-11-12, then 5 May 2027 and also March 3, 2028; June 9 has no year."
    assert dates_in(text) == {dt.date(2026, 11, 12), dt.date(2027, 5, 5), dt.date(2028, 3, 3)}


def test_missing_dates_flags_a_date_no_snippet_states() -> None:
    evidence = ["Q3 results will be published on 12 November 2026."]
    assert missing_dates("Results come on November 12, 2026.", evidence) == []
    assert missing_dates("Results come on 13 November 2026.", evidence) == ["2026-11-13"]


def test_is_quote_needs_a_long_verbatim_run() -> None:
    snippet = "Revenue for the quarter rose 12% to $4.1 billion, driven by cloud demand."
    assert is_quote(
        "Revenue for the quarter rose 12% to $4.1 billion, driven by cloud demand", [snippet]
    )
    assert not is_quote("Revenue rose 12%", [snippet])  # too short to prove anything
    assert not is_quote(
        "Revenue for the quarter fell 12% to $4.1 billion, driven by cloud demand.", [snippet]
    )


# ------------------------------------------------------------------------------- IR probes


def test_probe_urls_use_known_domains_and_skip_denied_ones() -> None:
    profile = HoldingProfile(ticker="ABC_US_EQ", extra_domains=["abc.com", "wikipedia.org"])
    urls = probe_urls(profile, ["wikipedia.org"])
    assert "https://abc.com/investors" in urls
    assert "https://ir.abc.com/" in urls
    assert not any("wikipedia" in u for u in urls)
    assert len(urls) <= 8


# ----------------------------------------------------------------------- batch collector


class _FakeLLM:
    def __init__(self, fail_batch: bool = False) -> None:
        self.batches: list[int] = []
        self.direct = 0
        self._fail = fail_batch

    async def complete_batch(self, requests: list[Request], **_: Any) -> list[Completion | None]:
        self.batches.append(len(requests))
        return [None if self._fail else _done(r) for r in requests]

    async def complete(self, request: Request) -> Completion:
        self.direct += 1
        return _done(request)


def _done(request: Request) -> Completion:
    return Completion(
        model=request.model,
        provider="openai",
        text=request.messages[0].text,
        parsed=None,
        stop_reason="end",
        usage=SimpleNamespace(),
    )


def _request(text: str) -> Request:
    from steward.llm.types import Message

    return Request(model="gpt-5", system="s", messages=(Message("user", text),))


async def _flow(collector: BatchCollector, name: str, rounds: int) -> str:
    answer = name
    for n in range(rounds):
        done = await collector.submit(_request(f"{answer}:{n}"))
        answer = done.text
    return answer


def test_flows_share_one_batch_per_round() -> None:
    llm = _FakeLLM()
    collector = BatchCollector(llm)  # type: ignore[arg-type]

    async def run() -> list[Any]:
        return await collector.run([_flow(collector, c, 2) for c in "abc"])

    results = asyncio.run(run())
    assert results == ["a:0:1", "b:0:1", "c:0:1"]
    assert llm.batches == [3, 3]  # two rounds, three holdings each
    assert llm.direct == 0


def test_a_flow_that_finishes_early_does_not_stall_the_others() -> None:
    llm = _FakeLLM()
    collector = BatchCollector(llm)  # type: ignore[arg-type]

    async def run() -> list[Any]:
        return await collector.run([_flow(collector, "a", 0), _flow(collector, "b", 2)])

    assert asyncio.run(run()) == ["a", "b:0:1"]
    assert llm.batches == [1, 1]


def test_an_unanswered_request_is_asked_directly() -> None:
    llm = _FakeLLM(fail_batch=True)
    collector = BatchCollector(llm)  # type: ignore[arg-type]

    async def run() -> list[Any]:
        return await collector.run([_flow(collector, c, 1) for c in "ab"])

    assert asyncio.run(run()) == ["a:0", "b:0"]
    assert llm.direct == 2


def test_a_failing_flow_is_returned_not_raised() -> None:
    llm = _FakeLLM()
    collector = BatchCollector(llm)  # type: ignore[arg-type]

    async def boom() -> None:
        raise ValueError("bad")

    async def run() -> list[Any]:
        return await collector.run([boom(), _flow(collector, "b", 1)])

    out = asyncio.run(run())
    assert isinstance(out[0], ValueError) and out[1] == "b:0"


@pytest.mark.parametrize("n", [0])
def test_no_flows_returns_at_once(n: int) -> None:
    collector = BatchCollector(_FakeLLM())  # type: ignore[arg-type]
    assert asyncio.run(collector.run([])) == []
