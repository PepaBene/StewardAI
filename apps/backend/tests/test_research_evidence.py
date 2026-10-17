"""Claim evidence for web citations, links inside claims, and retries of overloaded sections."""

import asyncio
from typing import Any

import pytest

from steward.config.verification import ClaimVerifierSettings
from steward.research import run as run_mod
from steward.research.page_evidence import attach_page_text, best_passages
from steward.research.run import retry_transient, transient_failure
from steward.research.sources import Registry
from steward.verify.claims import Claim, check_claims, strip_links

PAGE = "\n".join(
    [
        "Alphabet Inc. Form 10-Q",
        "Unrelated boilerplate about forward-looking statements.",
        "Google Services revenues were $94,502 million in the second quarter.",
        "Google Cloud revenues were $24,830 million.",
        "Operating cash flow was $84,859 million for six months.",
        "Purchases of property and equipment were $80,598 million.",
        "Signature page.",
    ]
)


def test_links_in_a_claim_are_not_figures() -> None:
    text = "Revenue grew ([sec.gov](https://www.sec.gov/Archives/edgar/data/1652044/000165204426000071/x.htm))."
    assert strip_links(text) == "Revenue grew (sec.gov)."
    assert strip_links("See https://a.example/000123456789 for it.") == "See for it."


async def test_a_claim_with_a_link_is_checked_on_its_words_not_the_address() -> None:
    claim = Claim(
        0,
        "Cash flow was 84,859 million ([sec.gov](https://sec.gov/a/000165204426000071/x.htm)).",
        ("Operating cash flow was $84,859 million for six months.",),
    )
    settings = ClaimVerifierSettings()
    out = await check_claims([claim], settings, None, __import__("datetime").date(2026, 10, 13))
    # No figure from the address is missing; only the model check remains (no verifier here).
    assert out.results[0].status == "unchecked"


async def test_todays_date_is_the_answers_as_of_not_an_unsourced_claim() -> None:
    import datetime as dt

    evidence = ("Q2 revenue was $12.56 billion.",)
    today = Claim(0, "As of October 17, 2026, Q2 revenue was $12.56 billion.", evidence)
    other = Claim(1, "On October 3, 2026, Q2 revenue was $12.56 billion.", evidence)
    out = await check_claims([today, other], ClaimVerifierSettings(), None, dt.date(2026, 10, 17))
    assert out.results[0].status == "unchecked"
    assert out.results[1].status == "unsupported"


def test_passages_are_copied_lines_that_share_the_sentences_figures() -> None:
    got = best_passages(PAGE, ["Operating cash flow was 84,859 million and capex 80,598 million."])
    assert "84,859" in got and "80,598" in got
    assert "Alphabet Inc. Form 10-Q" not in got and len(got) < len(PAGE)


def _registry() -> tuple[Registry, str]:
    reg = Registry.load([])
    sid = reg.add(
        "search",
        "https://www.sec.gov/x",
        "goog-20260630",
        url="https://www.sec.gov/x",
        tier=1,
        snippet="goog-20260630",
    )
    return reg, sid


async def test_the_cited_page_becomes_the_sources_snippet() -> None:
    reg, sid = _registry()
    asked: list[str] = []

    async def fetch(url: str) -> str | None:
        asked.append(url)
        return PAGE

    read = await attach_page_text(reg, {sid: ["Operating cash flow was 84,859 million."]}, fetch)
    assert read == 1 and asked == ["https://www.sec.gov/x"]
    snippet = reg.get(sid).snippet  # type: ignore[union-attr]
    assert snippet and "84,859" in snippet


async def test_a_page_that_cannot_be_read_keeps_its_title() -> None:
    reg, sid = _registry()

    async def broken(url: str) -> str | None:
        raise RuntimeError("boom")

    assert await attach_page_text(reg, {sid: ["x 12"]}, broken) == 0
    assert reg.get(sid).snippet == "goog-20260630"  # type: ignore[union-attr]

    async def empty(url: str) -> str | None:
        return None

    assert await attach_page_text(reg, {sid: ["x 12"]}, empty) == 0


async def test_only_search_sources_and_the_page_limit_are_used() -> None:
    reg = Registry.load([])
    ids = [
        reg.add(
            "search",
            f"https://a.example/{i}",
            f"t{i}",
            url=f"https://a.example/{i}",
            tier=1,
            snippet=f"t{i}",
        )
        for i in range(5)
    ]
    fact = reg.add("fact", "f1", "a fact", snippet="x" * 300)
    calls = 0

    async def fetch(url: str) -> str | None:
        nonlocal calls
        calls += 1
        return "Revenue was 55 million in the quarter."

    cited = {i: ["Revenue was 55 million."] for i in [*ids, fact]}
    assert await attach_page_text(reg, cited, fetch, max_pages=2) == 2
    assert calls == 2


def test_only_a_temporary_provider_failure_is_retried() -> None:
    down = {
        "sentences": [],
        "note": "not written: the call failed: openai call failed: HTTP 503: x",
    }
    slow = {
        "sentences": [],
        "note": "not written: the call failed: openai call failed: ReadTimeout: ",
    }
    assert transient_failure(down) and transient_failure(slow)
    assert not transient_failure({"sentences": [{"text": "a"}], "note": down["note"]})
    assert not transient_failure({"sentences": [], "note": run_mod.NO_BUDGET_NOTE})
    assert not transient_failure(
        {"sentences": [], "note": "not written: the call failed: HTTP 400"}
    )


async def test_an_overloaded_section_is_asked_again_after_a_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    answers: list[dict[str, Any]] = [
        {
            "sentences": [],
            "steps": 1,
            "searches": 0,
            "new_sources": [],
            "note": "not written: the call failed: HTTP 503: overloaded",
        },
        {"sentences": [{"text": "ok"}], "steps": 2, "searches": 1, "new_sources": [], "note": None},
    ]

    async def attempt() -> dict[str, Any]:
        return answers.pop(0)

    out = await retry_transient(attempt)
    assert out["sentences"] == [{"text": "ok"}]
    assert out["steps"] == 3 and slept == [15.0]


async def test_a_section_that_keeps_failing_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    tries = 0

    async def attempt() -> dict[str, Any]:
        nonlocal tries
        tries += 1
        return {
            "sentences": [],
            "steps": 1,
            "searches": 0,
            "new_sources": [],
            "note": "not written: the call failed: HTTP 503: overloaded",
        }

    out = await retry_transient(attempt)
    assert tries == 3 and not out["sentences"]
