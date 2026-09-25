"""Read-only Trading212 Public API client.

This client has no write path by construction:
- the only request method it implements is GET, through `_get`;
- an httpx request hook rejects any other HTTP method before it leaves the process,
  so even a future edit that calls `post` on the underlying client fails;
- the API key should be created with read scopes only, so the server refuses writes too.

Rate limits are per endpoint and strict. The client reads the `x-ratelimit-*` headers
Trading212 returns on every response and waits out an exhausted window before the next
call to the same endpoint, and it retries 429 and 5xx responses with backoff.
"""

import asyncio
import base64
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from decimal import Decimal
from typing import Any, Literal, Self

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

from steward.trading212.models import (
    AccountSummary,
    HistoricalOrder,
    HistoryDividend,
    HistoryTransaction,
    Instrument,
    Position,
)

logger = logging.getLogger(__name__)

BASE_URLS: dict[str, str] = {
    "demo": "https://demo.trading212.com/api/v0",
    "live": "https://live.trading212.com/api/v0",
}

Sleep = Callable[[float], Awaitable[None]]


class Trading212Error(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ReadOnlyViolation(Trading212Error):
    """Raised if anything tries to send a non-GET request through this client."""


async def _reject_non_get(request: httpx.Request) -> None:
    if request.method != "GET":
        raise ReadOnlyViolation(
            f"Trading212 client is read-only; refused {request.method} {request.url.path}"
        )


class Trading212Client:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        env: Literal["demo", "live"] = "demo",
        *,
        timeout: float = 20.0,
        max_retries: int = 4,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if not api_key or not api_secret:
            raise ValueError("Trading212 API key and secret are both required")
        credentials = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
        self._http = httpx.AsyncClient(
            base_url=BASE_URLS[env],
            headers={"Authorization": f"Basic {credentials}", "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
            event_hooks={"request": [_reject_non_get]},
        )
        self.env = env
        self._max_retries = max_retries
        self._sleep = sleep
        # Earliest wall-clock time the next call to a path may be sent.
        self._not_before: dict[str, float] = {}

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get_account_summary(self) -> AccountSummary:
        return _parse(AccountSummary, await self._get("/equity/account/summary"))

    async def get_positions(self) -> list[Position]:
        return _parse_list(Position, await self._get("/equity/positions"))

    async def get_instruments(self) -> list[Instrument]:
        return _parse_list(Instrument, await self._get("/equity/metadata/instruments"))

    # History endpoints are paginated (newest first) and rate-limited to 6 requests a
    # minute each; iterate page by page so the caller can stop once it reaches known items.

    def iter_order_history(self) -> AsyncIterator[list[HistoricalOrder]]:
        return self._pages("/equity/history/orders", HistoricalOrder)

    def iter_dividend_history(self) -> AsyncIterator[list[HistoryDividend]]:
        return self._pages("/equity/history/dividends", HistoryDividend)

    def iter_cash_history(self) -> AsyncIterator[list[HistoryTransaction]]:
        return self._pages("/equity/history/transactions", HistoryTransaction)

    async def _pages[M: BaseModel](self, path: str, model: type[M]) -> AsyncIterator[list[M]]:
        next_path: str | None = f"{path}?limit={HISTORY_PAGE_SIZE}"
        seen: set[str] = set()
        while next_path:
            if next_path in seen:
                raise Trading212Error(f"GET {path} pagination repeated {next_path}")
            seen.add(next_path)
            payload = await self._get(next_path)
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                raise Trading212Error(f"Unexpected {path} page shape")
            yield _parse_list(model, payload["items"])
            next_path = _relative_page_path(payload.get("nextPagePath"))

    async def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        # Rate limits are per endpoint: key them by path without the query string.
        path, _, query = path.partition("?")
        url = f"{path}?{query}" if query else path
        attempt = 0
        while True:
            await self._wait_for_window(path)
            try:
                response = await self._http.get(url, params=params)
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise Trading212Error(f"GET {path} failed: {exc!r}") from exc
                attempt += 1
                await self._sleep(_backoff(attempt))
                continue

            self._record_window(path, response)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt >= self._max_retries:
                    raise Trading212Error(
                        f"GET {path} returned {response.status_code} after {attempt} retries",
                        response.status_code,
                    )
                attempt += 1
                delay = max(self._not_before.get(path, 0.0) - time.time(), _backoff(attempt))
                logger.warning(
                    "Trading212 GET %s returned %s; retry %s in %.1fs",
                    path,
                    response.status_code,
                    attempt,
                    delay,
                )
                await self._sleep(delay)
                continue
            if response.status_code in (401, 403):
                hint = (
                    "check the key and secret"
                    if response.status_code == 401
                    else ("the key lacks the read scope for this endpoint")
                )
                raise Trading212Error(
                    f"GET {path} returned {response.status_code}: {hint}", response.status_code
                )
            if response.is_error:
                raise Trading212Error(
                    f"GET {path} returned {response.status_code}: {response.text[:200]}",
                    response.status_code,
                )
            try:
                return json.loads(response.content, parse_float=Decimal)
            except json.JSONDecodeError as exc:
                raise Trading212Error(f"GET {path} returned invalid JSON") from exc

    async def _wait_for_window(self, path: str) -> None:
        delay = self._not_before.get(path, 0.0) - time.time()
        if delay > 0:
            logger.info("Trading212 rate limit on %s: waiting %.1fs", path, delay)
            await self._sleep(delay)

    def _record_window(self, path: str, response: httpx.Response) -> None:
        remaining = response.headers.get("x-ratelimit-remaining")
        reset = response.headers.get("x-ratelimit-reset")
        retry_after = response.headers.get("retry-after")
        if response.status_code == 429 and retry_after and retry_after.isdigit():
            self._not_before[path] = time.time() + int(retry_after)
        elif (remaining == "0" or response.status_code == 429) and reset and reset.isdigit():
            self._not_before[path] = float(reset)


HISTORY_PAGE_SIZE = 50
_API_PREFIX = "/api/v0"


def _relative_page_path(next_page_path: object) -> str | None:
    """nextPagePath comes back as '/api/v0/equity/...?cursor=..'; the client's base URL
    already ends in /api/v0."""
    if not next_page_path or not isinstance(next_page_path, str):
        return None
    if next_page_path.startswith(_API_PREFIX + "/"):
        return next_page_path[len(_API_PREFIX) :]
    if next_page_path.startswith("/"):
        return next_page_path
    raise Trading212Error(f"Unexpected nextPagePath {next_page_path!r}")


def _backoff(attempt: int) -> float:
    return float(min(2**attempt, 60))


def _parse[M: BaseModel](model: type[M], payload: Any) -> M:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise Trading212Error(f"Unexpected {model.__name__} response shape: {exc}") from exc


def _parse_list[M: BaseModel](model: type[M], payload: Any) -> list[M]:
    try:
        return TypeAdapter(list[model]).validate_python(payload)  # type: ignore[valid-type]
    except ValidationError as exc:
        raise Trading212Error(f"Unexpected {model.__name__} list response shape: {exc}") from exc
