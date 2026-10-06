import httpx
import pytest

from steward.config.holding_profiles import HoldingProfile
from steward.fundamentals.ir import IrError
from steward.fundamentals.ir_discovery import drop_url
from steward.research.polite import PoliteFetcher

PAGE = b"<html><head><title>Investors</title></head><body>Results and reports</body></html>"


def client(robots: tuple[int, str]) -> httpx.AsyncClient:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(robots[0], text=robots[1])
        return httpx.Response(200, content=PAGE, headers={"content-type": "text/html"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http.seen = seen  # type: ignore[attr-defined]
    return http


async def test_robots_disallow_blocks_the_fetch() -> None:
    http = client((200, "User-agent: *\nDisallow: /private/"))
    fetcher = PoliteFetcher(http)
    assert (await fetcher.get("https://ir.example.com/results", 10_000)).body == PAGE
    with pytest.raises(IrError, match=r"robots\.txt"):
        await fetcher.get("https://ir.example.com/private/page", 10_000)
    # robots.txt is read once per host
    assert http.seen.count("/robots.txt") == 1  # type: ignore[attr-defined]


async def test_an_exempt_site_is_fetched_despite_robots_and_others_are_not() -> None:
    http = client((200, "User-agent: *\nDisallow: /"))
    fetcher = PoliteFetcher(http, robots_exempt=("example.com",))
    assert (await fetcher.get("https://investors.example.com/", 10_000)).body == PAGE
    with pytest.raises(IrError, match=r"robots\.txt"):
        await fetcher.get("https://investors.other.org/", 10_000)


async def test_missing_robots_means_no_restriction() -> None:
    fetcher = PoliteFetcher(client((404, "")))
    assert (await fetcher.get("https://ir.example.com/x", 10_000)).content_type == "text/html"


async def test_unreadable_robots_closes_the_host() -> None:
    fetcher = PoliteFetcher(client((503, "")))
    with pytest.raises(IrError, match=r"robots\.txt"):
        await fetcher.get("https://ir.example.com/x", 10_000)


async def test_literal_private_address_is_refused() -> None:
    fetcher = PoliteFetcher(client((404, "")))
    with pytest.raises(IrError):
        await fetcher.get("https://127.0.0.1/x", 10_000)


def profile() -> HoldingProfile:
    return HoldingProfile.model_validate(
        {
            "ticker": "AAA_US_EQ",
            "ir_url": "https://ir.example.com/",
            "financial_calendar_url": "https://ir.example.com/calendar",
            "results_urls": ["https://ir.example.com/results", "https://ir.example.com/other"],
        }
    )


def test_drop_url_removes_only_that_value() -> None:
    trimmed = drop_url(profile(), "https://ir.example.com/results")
    assert trimmed is not None
    assert [str(u) for u in trimmed.results_urls] == ["https://ir.example.com/other"]
    assert str(trimmed.ir_url) == "https://ir.example.com/"
    calendar = drop_url(profile(), "https://ir.example.com/calendar")
    assert calendar is not None and calendar.financial_calendar_url is None


def test_drop_url_leaves_a_value_the_owner_changed() -> None:
    assert drop_url(profile(), "https://elsewhere.example.org/") is None
