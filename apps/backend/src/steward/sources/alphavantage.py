"""Alpha Vantage's free Company Overview endpoint for a limited fundamentals fallback."""

from typing import Any

import httpx

OVERVIEW_URL = "https://www.alphavantage.co/query"


class AlphaVantageError(Exception):
    pass


async def company_overview(http: httpx.AsyncClient, api_key: str, symbol: str) -> dict[str, Any]:
    """Fetch one overview. Alpha Vantage returns API/rate-limit failures as JSON messages."""
    try:
        response = await http.get(
            OVERVIEW_URL,
            params={"function": "OVERVIEW", "symbol": symbol, "apikey": api_key},
        )
    except httpx.HTTPError as exc:
        # The API key is a query parameter; do not let request URLs leak into job details.
        raise AlphaVantageError(f"Alpha Vantage request failed ({type(exc).__name__})") from exc
    if response.status_code != 200:
        raise AlphaVantageError(f"Alpha Vantage returned HTTP {response.status_code}")
    try:
        data: dict[str, Any] = response.json()
    except ValueError as exc:
        raise AlphaVantageError("Alpha Vantage sent no JSON") from exc
    if data.get("Error Message"):
        raise AlphaVantageError(str(data["Error Message"])[:300])
    if data.get("Note") or data.get("Information"):
        raise AlphaVantageError(str(data.get("Note") or data.get("Information"))[:300])
    return data
