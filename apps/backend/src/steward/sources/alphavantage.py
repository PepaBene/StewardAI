"""Alpha Vantage's free Company Overview endpoint for a limited fundamentals fallback."""

from typing import Any

import httpx

from steward.sources.base import AccessState
from steward.sources.free_market import classify_alpha_message

OVERVIEW_URL = "https://www.alphavantage.co/query"


class AlphaVantageError(Exception):
    """`state` is the typed access outcome when the provider's HTTP-200 message makes it
    unambiguous (premium-only endpoint, daily quota, bad key); None otherwise."""

    def __init__(
        self,
        message: str,
        *,
        state: AccessState | None = None,
        status_code: int | None = None,
        provider_charged: bool = True,
    ) -> None:
        super().__init__(message)
        self.state = state
        self.status_code = status_code
        self.provider_charged = provider_charged


async def company_overview(http: httpx.AsyncClient, api_key: str, symbol: str) -> dict[str, Any]:
    """Fetch one overview. Alpha Vantage returns API/rate-limit failures as JSON messages."""
    try:
        response = await http.get(
            OVERVIEW_URL,
            params={"function": "OVERVIEW", "symbol": symbol, "apikey": api_key},
        )
    except httpx.HTTPError as exc:
        # The API key is a query parameter; do not let request URLs leak into job details.
        raise AlphaVantageError(
            f"Alpha Vantage request failed ({type(exc).__name__})", provider_charged=False
        ) from exc
    if response.status_code != 200:
        raise AlphaVantageError(
            f"Alpha Vantage returned HTTP {response.status_code}",
            status_code=response.status_code,
            state=AccessState.QUOTA_EXHAUSTED if response.status_code == 429 else None,
        )
    try:
        data: dict[str, Any] = response.json()
    except ValueError as exc:
        raise AlphaVantageError("Alpha Vantage sent no JSON") from exc
    for key in ("Error Message", "Note", "Information"):
        if data.get(key):
            text = str(data[key])
            raise AlphaVantageError(text[:300], state=classify_alpha_message(text, key=key))
    return data
