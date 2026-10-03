"""OpenFIGI (`api.openfigi.com/v3`): an ISIN to its share class and every listing.

An ISIN identifies a security, not where it trades. OpenFIGI maps it to one FIGI per listing
(exchange), grouped by a composite FIGI per country and one share-class FIGI across all of
them, so a company listed on several exchanges is one holding. No key is needed; an API key
(OPENFIGI_API_KEY) raises the rate limit.
"""

import asyncio
from collections import Counter
from dataclasses import dataclass
from typing import Any

import httpx

MAPPING_URL = "https://api.openfigi.com/v3/mapping"
# Rate limits (https://www.openfigi.com/api/documentation): 25 mapping requests a minute
# without a key; with a key the live response says `ratelimit-policy: 250;w=60` (250 a minute,
# checked 2026-10-03) with `ratelimit-limit`, `ratelimit-remaining` and `ratelimit-reset`
# (seconds to the window's end) headers on every 200. Each request here is one job (the cap is
# 10 jobs per request without a key, 100 with one) and a mapping job returns all its FIGIs, with no
# pagination. A 429 is waited out (the `ratelimit-reset` header, in seconds) and retried,
# so a first run over many holdings resolves them all rather than failing past the 25th.
RATE_LIMIT_RETRIES = 3
MAX_RESET_WAIT = 65.0
_sleep = asyncio.sleep


class OpenFigiError(Exception):
    pass


@dataclass(frozen=True)
class FigiListing:
    figi: str
    composite_figi: str | None
    share_class_figi: str | None
    ticker: str | None
    exch_code: str | None
    name: str | None
    security_type: str | None
    security_type2: str | None
    market_sector: str | None
    security_description: str | None
    raw: dict[str, Any]

    @property
    def is_composite(self) -> bool:
        return self.figi == self.composite_figi


def parse_mapping(entry: dict[str, Any]) -> list[FigiListing]:
    """One entry of a mapping response: its listings, or [] for "No identifier found"."""
    if "error" in entry and "data" not in entry:
        if "No identifier found" in str(entry["error"]):
            return []
        raise OpenFigiError(str(entry["error"]))
    return [
        FigiListing(
            figi=r["figi"],
            composite_figi=r.get("compositeFIGI"),
            share_class_figi=r.get("shareClassFIGI"),
            ticker=r.get("ticker"),
            exch_code=r.get("exchCode"),
            name=r.get("name"),
            security_type=r.get("securityType"),
            security_type2=r.get("securityType2"),
            market_sector=r.get("marketSector"),
            security_description=r.get("securityDescription"),
            raw=r,
        )
        for r in entry.get("data", [])
        if r.get("figi")
    ]


async def map_isin(
    http: httpx.AsyncClient, isin: str, api_key: str | None = None
) -> list[FigiListing]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-OPENFIGI-APIKEY"] = api_key
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        response = await http.post(
            MAPPING_URL, json=[{"idType": "ID_ISIN", "idValue": isin}], headers=headers
        )
        if response.status_code != 429 or attempt == RATE_LIMIT_RETRIES:
            break
        await _sleep(_reset_wait(response, api_key))
    if response.status_code == 429:
        raise OpenFigiError("OpenFIGI rate limit reached; retried on the next run")
    if response.status_code != 200:
        raise OpenFigiError(f"OpenFIGI returned HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise OpenFigiError("OpenFIGI returned a body that is not JSON") from exc
    if not isinstance(body, list) or not body:
        raise OpenFigiError("OpenFIGI returned an unexpected body")
    return parse_mapping(body[0])


def _reset_wait(response: httpx.Response, api_key: str | None) -> float:
    try:
        wait = float(response.headers.get("ratelimit-reset") or "")
    except ValueError:
        wait = 6.0 if api_key else 60.0
    return min(max(wait, 1.0), MAX_RESET_WAIT)


# The composite (country-level) exchange code of an ISIN's home country.
HOME_COMPOSITE: dict[str, str] = {
    "US": "US", "GB": "LN", "DE": "GR", "FR": "FP", "NL": "NA", "CH": "SW", "DK": "DC",
    "SE": "SS", "NO": "NO", "FI": "FH", "IT": "IM", "ES": "SM", "BE": "BB", "IE": "ID",
    "AT": "AV", "PT": "PL", "CZ": "CP", "PL": "PW", "JP": "JP", "CA": "CN", "AU": "AU",
}  # fmt: skip

# Composite exchange code -> Stooq market suffix (Stooq covers a subset of markets).
# Amsterdam (NA), Copenhagen (DC) and Prague (CP) use the same country-suffix scheme
# (`asml.nl`, `cez.cz`, as the repo's fixtures do). They could not be checked against Stooq
# itself (it resets connections from cloud hosts): a wrong one shows as "Stooq has no data",
# Yahoo fills in, and the holding profile's `price_symbol` corrects it.
STOOQ_SUFFIX: dict[str, str] = {
    "US": "us", "LN": "uk", "GR": "de", "JP": "jp", "PW": "pl", "NA": "nl", "DC": "dk", "CP": "cz",
}  # fmt: skip


@dataclass(frozen=True)
class Resolution:
    share_class_figi: str | None
    home: FigiListing | None
    composites: list[FigiListing]


def resolve(listings: list[FigiListing], isin: str) -> Resolution:
    """The share class (the one most listings share) and the home-country listing (by ISIN
    country, the composite one when present), else the first composite listing."""
    classes = Counter(x.share_class_figi for x in listings if x.share_class_figi)
    share_class = classes.most_common(1)[0][0] if classes else None
    composites = [x for x in listings if x.is_composite]
    if share_class:
        composites = [x for x in composites if x.share_class_figi == share_class] or composites
    wanted = HOME_COMPOSITE.get(isin[:2])
    home = next((x for x in composites if x.exch_code == wanted), None)
    if home is None and wanted:
        # The home-country composite is not always in the mapping (Vodafone's LN listing
        # comes back as a plain listing); any listing on the home exchange will do.
        same_class = [x for x in listings if not share_class or x.share_class_figi == share_class]
        home = next((x for x in same_class if x.exch_code == wanted), None)
    return Resolution(share_class, home or (composites[0] if composites else None), composites)


def stooq_symbol(listing: FigiListing | None) -> str | None:
    """ "AAPL" on US -> "aapl.us"; "VOD" on LN -> "vod.uk". None where Stooq has no market."""
    if listing is None or not listing.ticker or listing.exch_code not in STOOQ_SUFFIX:
        return None
    ticker = listing.ticker.lower().replace("/", "-").replace(" ", "-")
    return f"{ticker}.{STOOQ_SUFFIX[listing.exch_code]}"
