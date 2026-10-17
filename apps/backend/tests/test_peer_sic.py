"""The SIC lookup and the peer rule that uses it."""

import asyncio

import httpx

from steward.approvals.ai import names_competitor, sic_of
from steward.sources.edgar import EdgarClient


def _edgar(body: dict[str, object], status: int = 200) -> tuple[EdgarClient, list[str]]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(status, json=body)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return EdgarClient(http, "StewardAI test@example.com"), seen


def test_sic_is_read_from_the_submissions_file() -> None:
    edgar, seen = _edgar({"sic": "7372", "name": "X"})
    assert asyncio.run(edgar.sic("789019")) == "7372"
    assert seen == ["https://data.sec.gov/submissions/CIK0000789019.json"]


def test_a_missing_or_odd_sic_is_none() -> None:
    assert asyncio.run(_edgar({})[0].sic("1")) is None
    assert asyncio.run(_edgar({"sic": ""})[0].sic("1")) is None
    assert asyncio.run(_edgar({"sic": "n/a"})[0].sic("1")) is None


def test_sic_of_caches_and_swallows_an_unreachable_edgar() -> None:
    edgar, seen = _edgar({"sic": "3571"})
    cache: dict[int, str | None] = {}

    async def twice() -> tuple[str | None, str | None]:
        return await sic_of(edgar, cache, 320193), await sic_of(edgar, cache, 320193)

    assert asyncio.run(twice()) == ("3571", "3571")
    assert len(seen) == 1
    broken, _ = _edgar({}, status=503)
    assert asyncio.run(sic_of(broken, {}, 1)) is None


def test_names_competitor_ignores_case_punctuation_and_suffixes() -> None:
    assert names_competitor("MICROSOFT CORP", ["Microsoft"])
    assert names_competitor("Alphabet Inc.", ["alphabet", "Meta Platforms"])
    assert not names_competitor("Microsoft Teams Ltd", ["Microsoft"])
    assert not names_competitor("Oracle Corp", [])


def test_proposed_peers_are_kept_only_when_they_name_a_real_other_company() -> None:
    # Production, 17 Oct 2026: Finnhub's peers of GOOGL were Snap, Reddit and Grindr; the
    # proposals fill in the large competitors, but each must be a listed symbol.
    from steward.approvals.ai import ProposedPeer, ProposedPeers, proposal_rows

    known = {
        "GOOGL": {"cik": 1652044, "title": "Alphabet Inc."},
        "GOOG": {"cik": 1652044, "title": "Alphabet Inc."},
        "MSFT": {"cik": 789019, "title": "MICROSOFT CORP"},
        "META": {"cik": 1326801, "title": "Meta Platforms, Inc."},
    }
    answer = ProposedPeers(
        peers=[
            ProposedPeer(ticker=t, name=t, reason="r")
            for t in ("msft", "META", "GOOG", "AMZNX", "BIDU.HK", "not a ticker", "GOOGL")
        ]
    )
    keep, dropped = proposal_rows(answer, "GOOGL", known, {"META"})
    assert [p.ticker for p in keep] == ["MSFT", "BIDU.HK"]
    assert "META: already recorded" in dropped and "GOOG: the same issuer" in dropped
    assert "AMZNX: not on the SEC's company list" in dropped
    assert any(d.startswith("NOT A TICKER") for d in dropped)
