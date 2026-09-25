"""ESMA registers: FIRDS (reference data per ISIN and trading venue) and FITRS (equity
transparency: liquidity, turnover, the most relevant market).

Both are read per ISIN from the registers' Solr API (no key). Tier 1 (the regulator).
"""

import datetime as dt
from dataclasses import dataclass
from typing import Any

import httpx

FIRDS_URL = "https://registers.esma.europa.eu/solr/esma_registers_firds/select"
FITRS_URL = "https://registers.esma.europa.eu/solr/esma_registers_fitrs_equities/select"


class EsmaError(Exception):
    pass


@dataclass(frozen=True)
class FirdsRecord:
    isin: str
    mic: str
    full_name: str | None
    short_name: str | None
    cfi: str | None
    currency: str | None
    issuer_lei: str | None
    admitted_at: dt.datetime | None
    first_trade_at: dt.datetime | None
    terminated_at: dt.datetime | None
    status: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class FitrsRecord:
    isin: str
    liquid: bool | None
    avg_daily_turnover: float | None
    turnover_currency: str | None
    avg_daily_trades: float | None
    most_relevant_market: str | None
    period_from: dt.date | None
    period_to: dt.date | None
    raw: dict[str, Any]


def _ts(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_firds(body: dict[str, Any]) -> list[FirdsRecord]:
    """Current records per venue (cancelled records dropped; the latest per MIC kept)."""
    latest: dict[str, dict[str, Any]] = {}
    for doc in body.get("response", {}).get("docs", []):
        if doc.get("status") == "CANC" or not doc.get("mic") or not doc.get("isin"):
            continue
        current = latest.get(doc["mic"])
        if current is None or str(doc.get("publication_date", "")) > str(
            current.get("publication_date", "")
        ):
            latest[doc["mic"]] = doc
    return [
        FirdsRecord(
            isin=d["isin"],
            mic=d["mic"],
            full_name=d.get("gnr_full_name"),
            short_name=d.get("gnr_short_name"),
            cfi=d.get("gnr_cfi_code"),
            currency=d.get("gnr_notional_curr_code"),
            issuer_lei=d.get("lei"),
            admitted_at=_ts(d.get("mrkt_issr_trdng_pprvl_date") or d.get("mrkt_trdng_rqst_date")),
            first_trade_at=_ts(d.get("mrkt_trdng_start_date")),
            terminated_at=_ts(d.get("mrkt_trdng_trmination_date")),
            status=d.get("status"),
            raw=d,
        )
        for d in sorted(latest.values(), key=lambda d: d["mic"])
    ]


def parse_fitrs(body: dict[str, Any]) -> FitrsRecord | None:
    """The latest calculation period's record."""
    docs = [d for d in body.get("response", {}).get("docs", []) if d.get("isin")]
    if not docs:
        return None
    doc = max(docs, key=lambda d: str(d.get("calculation_period_to", "")))
    flag = doc.get("liquidity_flag")
    period_from, period_to = (
        _ts(doc.get("calculation_period_from")),
        _ts(doc.get("calculation_period_to")),
    )
    return FitrsRecord(
        isin=doc["isin"],
        liquid=None if flag is None else str(flag).lower() in ("liquid", "true", "t"),
        avg_daily_turnover=doc.get("adt"),
        turnover_currency=doc.get("adt_curr_code"),
        avg_daily_trades=doc.get("adnte"),
        most_relevant_market=doc.get("mrmtl"),
        period_from=period_from.date() if period_from else None,
        period_to=period_to.date() if period_to else None,
        raw=doc,
    )


async def _solr(http: httpx.AsyncClient, url: str, isin: str, rows: int) -> dict[str, Any]:
    response = await http.get(url, params={"q": f"isin:{isin}", "wt": "json", "rows": rows})
    if response.status_code != 200:
        raise EsmaError(f"ESMA registers returned HTTP {response.status_code}")
    body: dict[str, Any] = response.json()
    return body


async def firds(http: httpx.AsyncClient, isin: str) -> list[FirdsRecord]:
    return parse_firds(await _solr(http, FIRDS_URL, isin, 500))


async def fitrs(http: httpx.AsyncClient, isin: str) -> FitrsRecord | None:
    return parse_fitrs(await _solr(http, FITRS_URL, isin, 50))


# Segment and operating MICs of common home markets -> country.
MIC_COUNTRY: dict[str, str] = {
    "XETR": "DE", "XETA": "DE", "XETB": "DE", "XETU": "DE", "XFRA": "DE", "FRAA": "DE",
    "XLON": "GB", "XLOM": "GB", "AIMX": "GB", "XNAS": "US", "XNYS": "US", "ARCX": "US",
    "XPAR": "FR", "XAMS": "NL", "XBRU": "BE", "XLIS": "PT", "XDUB": "IE", "XMIL": "IT",
    "MTAA": "IT", "XMAD": "ES", "XSTO": "SE", "XHEL": "FI", "XCSE": "DK", "XOSL": "NO",
    "XSWX": "CH", "XVTX": "CH", "XWBO": "AT", "XPRA": "CZ", "XWAR": "PL", "XBUD": "HU",
}  # fmt: skip


def home_country(most_relevant_market: str | None, isin: str) -> str:
    """Home market country: the FITRS most relevant market when known, else the ISIN's."""
    if most_relevant_market and most_relevant_market in MIC_COUNTRY:
        return MIC_COUNTRY[most_relevant_market]
    return isin[:2]
