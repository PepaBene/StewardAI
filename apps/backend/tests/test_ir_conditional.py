"""IR pages are fetched with the previous response's validators; a 304 is recorded as unchanged
without downloading or parsing the page."""

from typing import Any

import httpx
import pytest

from steward.fundamentals import ir

URL = "https://ir.example.com/guidance"


class _Pool:
    def __init__(self) -> None:
        self.executed: list[tuple[Any, ...]] = []

    async def execute(self, sql: str, *args: Any) -> None:
        self.executed.append((sql, *args))


@pytest.fixture(autouse=True)
def _any_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ir, "assert_fetchable", lambda url: None)


async def test_fetch_returns_validators_and_sends_them_back() -> None:
    seen: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers)
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(
            200,
            content=b"page",
            headers={"ETag": '"v1"', "Last-Modified": "Wed, 21 Oct 2026 07:00:00 GMT"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        first = await ir.fetch_document(http, URL)
        assert (first.etag, first.last_modified_header) == (
            '"v1"',
            "Wed, 21 Oct 2026 07:00:00 GMT",
        )
        with pytest.raises(ir.NotModified):
            await ir.fetch_document(
                http, URL, etag=first.etag, modified_since=first.last_modified_header
            )
    assert "if-none-match" not in seen[0]
    assert seen[1]["if-modified-since"] == "Wed, 21 Oct 2026 07:00:00 GMT"


async def test_a_304_marks_the_source_unchanged() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(304)

    pool = _Pool()
    source = ir.SourceRow(1, "FB_US_EQ", URL, "guidance", None, "abc", '"v1"', None)
    detail: dict[str, Any] = {}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await ir.check_source(pool, http, source, detail)
    assert detail == {"unchanged": True, "not_modified": True}
    assert "last_status='unchanged'" in pool.executed[0][0]


async def test_a_source_without_a_stored_version_is_fetched_unconditionally() -> None:
    seen: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers)
        return httpx.Response(500)

    source = ir.SourceRow(1, "FB_US_EQ", URL, "guidance", None, None, '"v1"', None)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await ir.check_source(_Pool(), http, source, {})
    assert "if-none-match" not in seen[0]
