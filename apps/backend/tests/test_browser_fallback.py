"""The browser fallback: when it is used, and that it never gets past a refusal it should not."""

import httpx
import pytest

from steward.fundamentals.ir import IrError
from steward.research.browser import BrowserRenderer
from steward.research.polite import PoliteFetcher, is_js_shell

SHELL = (
    b"<html><head><script src='app.js'></script></head><body><div id='root'></div></body></html>"
)
REAL = (
    b"<html><body>"
    + b"<a href='/r'>Results</a> " * 4
    + b"Financial calendar and reports</body></html>"
)
RENDERED = (
    "<html><body><a href='/q1'>Q1 results 2027</a> Financial calendar for investors.</body></html>"
)


class FakeBrowser(BrowserRenderer):
    def __init__(self, html: str | None) -> None:
        super().__init__("test")
        self.html = html
        self.visited: list[str] = []

    async def render(self, url: str) -> str | None:
        self.visited.append(url)
        return self.html


def server(status: int, body: bytes) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(status, content=body, headers={"content-type": "text/html"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_a_refused_page_is_retried_in_the_browser() -> None:
    browser = FakeBrowser(RENDERED)
    fetcher = PoliteFetcher(server(403, b"denied"), browser=browser)
    doc = await fetcher.get("https://ir.example.com/results", 100_000)
    assert b"Q1 results 2027" in doc.body and browser.visited == ["https://ir.example.com/results"]


async def test_an_empty_javascript_shell_is_rendered() -> None:
    assert is_js_shell(
        await PoliteFetcher(server(200, SHELL)).get("https://ir.example.com/", 100_000)
    )
    browser = FakeBrowser(RENDERED)
    doc = await PoliteFetcher(server(200, SHELL), browser=browser).get(
        "https://ir.example.com/", 100_000
    )
    assert b"Financial calendar" in doc.body


async def test_a_normal_page_never_starts_the_browser() -> None:
    browser = FakeBrowser(RENDERED)
    doc = await PoliteFetcher(server(200, REAL), browser=browser).get(
        "https://ir.example.com/", 100_000
    )
    assert doc.body == REAL and browser.visited == []


async def test_other_errors_are_not_retried_in_the_browser() -> None:
    browser = FakeBrowser(RENDERED)
    with pytest.raises(IrError, match="404"):
        await PoliteFetcher(server(404, b"nope"), browser=browser).get(
            "https://ir.example.com/x", 100_000
        )
    assert browser.visited == []


async def test_a_challenge_page_or_a_failed_render_is_recorded_as_blocked() -> None:
    for html in (None, "<html><title>Just a moment...</title>Checking your browser</html>"):
        with pytest.raises(IrError, match="blocked"):
            await PoliteFetcher(server(429, b"slow down"), browser=FakeBrowser(html)).get(
                "https://ir.example.com/x", 100_000
            )


async def test_robots_still_apply_before_the_browser() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /")
        return httpx.Response(200, content=REAL)

    browser = FakeBrowser(RENDERED)
    fetcher = PoliteFetcher(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)), browser=browser
    )
    with pytest.raises(IrError, match="robots"):
        await fetcher.get("https://ir.example.com/x", 100_000)
    assert browser.visited == []
