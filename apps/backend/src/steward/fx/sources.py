"""Collectors for the two free reference-rate sources.

ČNB: the daily fixing text file. Asking for a date returns the fixing valid on that date
(the previous business day's on weekends and holidays); the first line carries the actual
date. Rates are CZK per `Amount` units of the currency.

ECB: the Data Portal's EXR dataflow as CSV. Rates are units of the currency per 1 EUR,
so they are inverted to EUR per unit here; the published value is kept alongside.
"""

import csv
import datetime as dt
import io
from decimal import Decimal, InvalidOperation, localcontext

import httpx

from steward.fx.convert import FxRate, FxSourceName

CNB_DAILY_URL = (
    "https://www.cnb.cz/en/financial-markets/foreign-exchange-market/"
    "central-bank-exchange-rate-fixing/central-bank-exchange-rate-fixing/daily.txt"
)
ECB_EXR_URL = "https://data-api.ecb.europa.eu/service/data/EXR/D..EUR.SP00.A"

# How far back to look for the latest ECB rate (covers long holiday closures).
ECB_LOOKBACK_DAYS = 10


class FxSourceError(Exception):
    pass


def parse_cnb(text: str) -> list[FxRate]:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if len(lines) < 3:
        raise FxSourceError("ČNB file is too short")
    try:
        rate_date = dt.datetime.strptime(lines[0].split("#")[0].strip(), "%d %b %Y").date()
    except ValueError as exc:
        raise FxSourceError(f"ČNB file has an unexpected first line: {lines[0]!r}") from exc
    header = [h.strip().lower() for h in lines[1].split("|")]
    try:
        amount_i, code_i, rate_i = (
            header.index("amount"),
            header.index("code"),
            header.index("rate"),
        )
    except ValueError as exc:
        raise FxSourceError(f"ČNB file has an unexpected header: {lines[1]!r}") from exc

    rates = []
    for line in lines[2:]:
        cells = line.split("|")
        try:
            amount = Decimal(cells[amount_i].strip())
            # The Czech version uses a decimal comma; accept both.
            published = Decimal(cells[rate_i].strip().replace(",", "."))
            code = cells[code_i].strip().upper()
        except (IndexError, InvalidOperation) as exc:
            raise FxSourceError(f"ČNB file has an unreadable row: {line!r}") from exc
        if amount <= 0 or published <= 0 or len(code) != 3:
            raise FxSourceError(f"ČNB file has an invalid row: {line!r}")
        rates.append(
            FxRate(
                source="cnb",
                rate_date=rate_date,
                currency=code,
                rate=published / amount,
                published_amount=amount,
                published_rate=published,
            )
        )
    return rates


def parse_ecb(text: str) -> list[FxRate]:
    reader = csv.DictReader(io.StringIO(text))
    required = {"CURRENCY", "CURRENCY_DENOM", "TIME_PERIOD", "OBS_VALUE"}
    if reader.fieldnames is None or not required <= set(reader.fieldnames):
        raise FxSourceError(f"ECB CSV lacks columns {sorted(required)}")
    rates = []
    for row in reader:
        if row["CURRENCY_DENOM"] != "EUR" or not row["OBS_VALUE"]:
            continue
        try:
            published = Decimal(row["OBS_VALUE"])
            rate_date = dt.date.fromisoformat(row["TIME_PERIOD"])
        except (InvalidOperation, ValueError) as exc:
            raise FxSourceError(f"ECB CSV has an unreadable row: {row}") from exc
        if published <= 0:
            raise FxSourceError(f"ECB CSV has a non-positive rate: {row}")
        with localcontext() as ctx:
            ctx.prec = 28
            rate = Decimal(1) / published
        rates.append(
            FxRate(
                source="ecb",
                rate_date=rate_date,
                currency=row["CURRENCY"].upper(),
                rate=rate,
                published_amount=Decimal(1),
                published_rate=published,
            )
        )
    return rates


async def fetch_cnb(http: httpx.AsyncClient, date: dt.date) -> list[FxRate]:
    response = await http.get(CNB_DAILY_URL, params={"date": date.strftime("%d.%m.%Y")})
    if response.status_code != 200:
        raise FxSourceError(f"ČNB returned HTTP {response.status_code}")
    return parse_cnb(response.text)


async def fetch_ecb(http: httpx.AsyncClient, date: dt.date) -> list[FxRate]:
    params = {
        "startPeriod": (date - dt.timedelta(days=ECB_LOOKBACK_DAYS)).isoformat(),
        "endPeriod": date.isoformat(),
        "format": "csvdata",
    }
    response = await http.get(ECB_EXR_URL, params=params)
    if response.status_code != 200:
        raise FxSourceError(f"ECB returned HTTP {response.status_code}")
    return parse_ecb(response.text)


async def fetch_rates(http: httpx.AsyncClient, source: FxSourceName, date: dt.date) -> list[FxRate]:
    if source == "cnb":
        return await fetch_cnb(http, date)
    return await fetch_ecb(http, date)
