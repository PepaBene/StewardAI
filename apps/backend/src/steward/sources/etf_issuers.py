"""ETF holdings from the fund issuers' own (undocumented) data endpoints.

- iShares / BlackRock product-data API
  (`www.blackrock.com/varnish-api/<site>-product-data/product-data/api/v2/get-product-data`):
  `component=holdings` returns column-oriented arrays (ticker, issueName, isin, sectorName,
  countryOfRisk, marketCurrencyCode, holdingPercent, ...) under
  `componentsByNameMap.holdings.containersByNameMap.all.dataPointsByNameMap`, as of the
  previous business day; `component=exposureBreakdowns` the official sector table. An
  unknown component answers HTTP 400. Fallback: the full-fund download
  (`api/v1/get-fund-document?component=fundDownloadV2`), SpreadsheetML 2003 XML despite its
  `.xls` name, whose Holdings sheet has no country or ISIN column.
- Vanguard Europe GraphQL (`/gpx/graphql`, header `X-Consumer-ID`, HTTP 400 without it):
  `borHoldings(portIds){holdings(limit, lastItemKey)}` pages every line item (ISIN, SEDOL,
  GICS sector, Bloomberg ISO country = the LISTING country, weight; no currency field), and
  `funds(portIds){marketAllocation, sectorDiversification}` the fund's country, region and
  ICB sector tables, each row with the benchmark's weight next to the fund's. Month-end
  data.

Both are undocumented and have changed shape before (the classic iShares `.ajax` CSV link
is dead). Parsing is defensive: a missing container, a column of the wrong length, GraphQL
errors or an HTTP 400 raise `IssuerSchemaError` / `IssuerHttpError` with what was missing,
which the look-through job records as a gap; nothing here guesses a value.
"""

import asyncio
import datetime as dt
import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import httpx

from steward.lookthrough.classify import country_code, currency_of, normalize_sector
from steward.sources.xml_guard import safe_fromstring

Provider = Literal["ishares", "vanguard"]
SourceName = Literal["ishares_json", "ishares_spreadsheet", "vanguard_graphql"]
AssetClass = Literal["equity", "cash", "derivative", "other"]
CountryBasis = Literal["country_of_risk", "listing_country"]

ISHARES_API = "https://www.blackrock.com/varnish-api/uk-retail01-product-data/product-data"
VANGUARD_GRAPHQL = "https://www.ie.vanguard/gpx/graphql"

RETRY_STATUSES = {429, 500, 502, 503, 504}


class IssuerError(Exception):
    """A fetch that produced no usable snapshot (recorded as a gap, never a crash)."""


class IssuerHttpError(IssuerError):
    def __init__(self, source: str, status: int, body: str) -> None:
        self.status = status
        super().__init__(f"{source} returned HTTP {status}: {body.strip()[:200]}")


class IssuerSchemaError(IssuerError):
    """The payload no longer has the shape this adapter reads: the endpoint changed."""


@dataclass(frozen=True)
class HoldingRow:
    name: str | None
    isin: str | None
    ticker: str | None
    sedol: str | None
    asset_class: AssetClass
    security_type: str | None
    weight: Decimal
    sector_raw: str | None
    sector: str | None
    country_raw: str | None
    country: str | None
    currency: str | None
    currency_derived: bool
    exchange: str | None
    market_value: Decimal | None


@dataclass(frozen=True)
class BreakdownRow:
    dimension: Literal["sector", "country", "region"]
    scheme: str
    code: str | None
    label: str
    fund_weight: Decimal | None
    benchmark_weight: Decimal | None
    as_of: dt.date | None


@dataclass
class FundSnapshot:
    provider: Provider
    portfolio_id: str
    source: SourceName
    as_of: dt.date
    fund_name: str | None
    fund_currency: str | None
    country_basis: CountryBasis | None
    currency_basis: Literal["issuer", "derived"]
    rows: list[HoldingRow]
    breakdowns: list[BreakdownRow] = field(default_factory=list)
    # Original payloads: JSON documents by component (raw) or the downloaded text.
    raw: dict[str, Any] | None = None
    raw_text: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def equity_weight(self) -> Decimal:
        return sum((r.weight for r in self.rows if r.asset_class == "equity"), Decimal(0))


def _dec(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation:
        return None
    return out if out.is_finite() else None


def _pct(value: Any) -> Decimal | None:
    number = _dec(value)
    return None if number is None else number / 100


def _weight_warning(names: list[str]) -> str:
    """Rows dropped for a weight that is missing or not a number: they are weight the
    snapshot does not account for, so the fund's rows may not reconcile to 100 %."""
    shown = ", ".join(names[:5]) + (", ..." if len(names) > 5 else "")
    return f"{len(names)} row(s) skipped: weight missing or unparseable ({shown})"


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _ymd(value: Any) -> dt.date | None:
    """20260925 (int or string) or 2026-09-25."""
    text = _text(value)
    if text is None:
        return None
    try:
        if re.fullmatch(r"\d{8}", text):
            return dt.datetime.strptime(text, "%Y%m%d").date()
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
    )
}


def _dmy_text(value: str) -> dt.date | None:
    """ "25/Sept/2026" or "25/Sep/2026" (iShares' display dates)."""
    match = re.search(r"(\d{1,2})/([A-Za-z]{3,9})/(\d{4})", value)
    if not match:
        return None
    month = _MONTHS.get(match.group(2)[:3].lower())
    if month is None:
        return None
    try:
        return dt.date(int(match.group(3)), month, int(match.group(1)))
    except ValueError:
        return None


def _path(body: Any, keys: Sequence[str], what: str) -> Any:
    node = body
    for i, key in enumerate(keys):
        if not isinstance(node, dict) or key not in node or node[key] is None:
            raise IssuerSchemaError(f"{what}: missing {'.'.join(keys[: i + 1])}")
        node = node[key]
    return node


# ---------------------------------------------------------------------------
# iShares product-data JSON
# ---------------------------------------------------------------------------

_ISHARES_ASSET_CLASS: dict[str, AssetClass] = {
    "equity": "equity",
    "cash": "cash",
    "cash collateral and margins": "cash",
    "money market": "cash",
    "futures": "derivative",
    "fx": "derivative",
    "forwards": "derivative",
    "swaps": "derivative",
}


def _ishares_asset_class(value: str | None) -> AssetClass:
    return _ISHARES_ASSET_CLASS.get((value or "").strip().lower(), "other")


def parse_ishares_holdings(body: Any, portfolio_id: str) -> FundSnapshot:
    """The `component=holdings` document as a snapshot (raw kept as-is)."""
    what = "iShares holdings"
    points = _path(
        body,
        [
            "componentsByNameMap",
            "holdings",
            "containersByNameMap",
            "all",
            "dataPointsByNameMap",
        ],
        what,
    )
    if not isinstance(points, dict):
        raise IssuerSchemaError(f"{what}: dataPointsByNameMap is not an object")

    def column(name: str, required: bool = False) -> list[Any] | None:
        entry = points.get(name)
        value = entry.get("value") if isinstance(entry, dict) else None
        if value is None:
            if required:
                raise IssuerSchemaError(f"{what}: missing column {name!r}")
            return None
        if not isinstance(value, list):
            raise IssuerSchemaError(f"{what}: column {name!r} is not a list")
        return value

    as_of_entry = points.get("asOfDate")
    as_of = _ymd(as_of_entry.get("value") if isinstance(as_of_entry, dict) else None)
    if as_of is None:
        raise IssuerSchemaError(f"{what}: missing or unreadable asOfDate")
    weights = column("holdingPercent", required=True)
    names = column("issueName", required=True)
    assert weights is not None and names is not None
    n = len(weights)
    if n == 0:
        raise IssuerSchemaError(f"{what}: no holdings rows")
    optional = {
        k: column(k)
        for k in (
            "ticker",
            "isin",
            "sectorName",
            "assetClass",
            "countryOfRisk",
            "exchange",
            "marketCurrencyCode",
            "marketValue",
        )
    }
    for name, col in [("issueName", names), *optional.items()]:
        if col is not None and len(col) != n:
            raise IssuerSchemaError(
                f"{what}: column {name!r} has {len(col)} rows, holdingPercent {n}"
            )
    warnings = [f"column {k!r} missing" for k, v in optional.items() if v is None]

    def cell(name: str, i: int) -> Any:
        col = optional.get(name)
        return col[i] if col is not None else None

    rows: list[HoldingRow] = []
    unknown_countries: Counter[str] = Counter()
    unparseable: list[str] = []
    for i in range(n):
        weight = _pct(weights[i])
        if weight is None:
            unparseable.append(_text(names[i]) or f"row {i}")
            continue
        country_raw = _text(cell("countryOfRisk", i))
        country = country_code(country_raw)
        asset_class = _ishares_asset_class(_text(cell("assetClass", i)))
        if country_raw and country is None and asset_class == "equity":
            unknown_countries[country_raw] += 1
        sector_raw = _text(cell("sectorName", i))
        currency = _text(cell("marketCurrencyCode", i))
        rows.append(
            HoldingRow(
                name=_text(names[i]),
                isin=_text(cell("isin", i)),
                ticker=_text(cell("ticker", i)),
                sedol=None,
                asset_class=asset_class,
                security_type=_text(cell("assetClass", i)),
                weight=weight,
                sector_raw=sector_raw,
                sector=normalize_sector(sector_raw) if asset_class == "equity" else None,
                country_raw=country_raw,
                country=country,
                currency=currency.upper() if currency and len(currency) == 3 else None,
                currency_derived=False,
                exchange=_text(cell("exchange", i)),
                market_value=_dec(cell("marketValue", i)),
            )
        )
    if unknown_countries:
        warnings.append(f"unmapped countries: {dict(unknown_countries)}")
    if unparseable:
        warnings.append(_weight_warning(unparseable))
    return FundSnapshot(
        provider="ishares",
        portfolio_id=portfolio_id,
        source="ishares_json",
        as_of=as_of,
        fund_name=_text(body.get("fundName")),
        fund_currency=_text(body.get("currencyCode")),
        country_basis="country_of_risk",
        currency_basis="issuer",
        rows=rows,
        raw={"holdings": body},
        warnings=warnings,
    )


def parse_ishares_exposure(body: Any) -> list[BreakdownRow]:
    """The `component=exposureBreakdowns` sector table (this fund publishes only sector)."""
    what = "iShares exposureBreakdowns"
    containers = _path(
        body, ["componentsByNameMap", "exposureBreakdowns", "containersByNameMap"], what
    )
    sector = containers.get("sector") if isinstance(containers, dict) else None
    if not isinstance(sector, dict):
        raise IssuerSchemaError(f"{what}: missing sector container")
    points = _path(sector, ["dataPointsByNameMap"], what)
    labels = (points.get("type") or {}).get("value")
    fund = (points.get("fund") or {}).get("value")
    if not isinstance(labels, list) or not isinstance(fund, list) or len(labels) != len(fund):
        raise IssuerSchemaError(f"{what}: sector type/fund columns missing or misaligned")
    as_of = _ymd((points.get("asOf") or {}).get("value"))
    return [
        BreakdownRow(
            dimension="sector",
            scheme="ishares_sector",
            code=None,
            label=str(label),
            fund_weight=_pct(value),
            benchmark_weight=None,
            as_of=as_of,
        )
        for label, value in zip(labels, fund, strict=True)
        if _text(label)
    ]


def _ishares_params(portfolio_id: str, component: str, site: str, locale: str) -> dict[str, str]:
    return {
        "appType": "PRODUCT_PAGE",
        "appSubType": "ISHARES",
        "targetSite": site,
        "locale": locale,
        "portfolioId": portfolio_id,
        "userType": "individual",
        "component": component,
    }


async def _send(
    http: httpx.AsyncClient,
    source: str,
    request: httpx.Request,
    retries: int,
    retry_delay: float,
) -> httpx.Response:
    """One request with a bounded retry on transport errors and 429/5xx."""
    attempt = 0
    while True:
        try:
            response = await http.send(request)
        except httpx.TransportError as exc:
            if attempt >= retries:
                raise IssuerError(f"{source}: {type(exc).__name__}: {exc}") from exc
        else:
            if response.status_code not in RETRY_STATUSES or attempt >= retries:
                return response
        attempt += 1
        await asyncio.sleep(retry_delay * attempt)


async def _get_json(
    http: httpx.AsyncClient,
    source: str,
    url: str,
    params: dict[str, str],
    retries: int,
    retry_delay: float,
) -> Any:
    response = await _send(
        http, source, http.build_request("GET", url, params=params), retries, retry_delay
    )
    if response.status_code != 200:
        raise IssuerHttpError(source, response.status_code, response.text)
    try:
        return response.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise IssuerSchemaError(f"{source}: response is not JSON") from exc


async def fetch_ishares_component(
    http: httpx.AsyncClient,
    portfolio_id: str,
    component: str,
    *,
    api_url: str = ISHARES_API,
    site: str = "ishares-uk",
    locale: str = "en_GB",
    retries: int = 1,
    retry_delay: float = 2.0,
) -> Any:
    """One product-data component as the issuer sends it (e.g. `keyFundFacts`, `fundHeader`)."""
    return await _get_json(
        http,
        "iShares product-data API",
        f"{api_url.rstrip('/')}/api/v2/get-product-data",
        _ishares_params(portfolio_id, component, site, locale),
        retries,
        retry_delay,
    )


async def fetch_ishares(
    http: httpx.AsyncClient,
    portfolio_id: str,
    *,
    api_url: str = ISHARES_API,
    site: str = "ishares-uk",
    locale: str = "en_GB",
    retries: int = 1,
    retry_delay: float = 2.0,
) -> FundSnapshot:
    """Holdings JSON (required) plus the sector table (best effort: a failure there is a
    warning on the snapshot, not a lost snapshot)."""
    url = f"{api_url.rstrip('/')}/api/v2/get-product-data"
    body = await _get_json(
        http,
        "iShares product-data API",
        url,
        _ishares_params(portfolio_id, "holdings", site, locale),
        retries,
        retry_delay,
    )
    snapshot = parse_ishares_holdings(body, portfolio_id)
    try:
        exposure = await _get_json(
            http,
            "iShares product-data API",
            url,
            _ishares_params(portfolio_id, "exposureBreakdowns", site, locale),
            retries,
            retry_delay,
        )
        snapshot.breakdowns = parse_ishares_exposure(exposure)
        assert snapshot.raw is not None
        snapshot.raw["exposureBreakdowns"] = exposure
    except IssuerError as exc:
        snapshot.warnings.append(f"sector table unavailable: {exc}")
    return snapshot


# ---------------------------------------------------------------------------
# iShares full-fund SpreadsheetML download (fallback: no country, no ISIN)
# ---------------------------------------------------------------------------

_SS = "{urn:schemas-microsoft-com:office:spreadsheet}"


def _sheet_rows(text: str, sheet: str) -> list[list[str]]:
    try:
        root = safe_fromstring(text, IssuerSchemaError)
    except IssuerSchemaError as exc:
        raise IssuerSchemaError(f"iShares fund download: {exc}") from exc
    for ws in root.iter(f"{_SS}Worksheet"):
        if ws.get(f"{_SS}Name") != sheet:
            continue
        rows: list[list[str]] = []
        for row in ws.iter(f"{_SS}Row"):
            cells = []
            for cell in row.iter(f"{_SS}Cell"):
                data = cell.find(f"{_SS}Data")
                cells.append((data.text or "").strip() if data is not None else "")
            rows.append(cells)
        return rows
    raise IssuerSchemaError(f"iShares fund download: no {sheet!r} worksheet")


def parse_ishares_spreadsheet(text: str, portfolio_id: str) -> FundSnapshot:
    """The Holdings sheet of the fund download. It has no country or ISIN column, so the
    snapshot's rows carry neither (shown as unclassified, never inferred)."""
    rows = _sheet_rows(text, "Holdings")
    as_of: dt.date | None = None
    header_at = None
    for i, cells in enumerate(rows):
        if cells and cells[0].lower() == "as of" and len(cells) > 1:
            as_of = _dmy_text(cells[1])
        if "Weight (%)" in cells:
            header_at = i
            break
    if header_at is None:
        raise IssuerSchemaError("iShares fund download: Holdings header row not found")
    if as_of is None:
        raise IssuerSchemaError("iShares fund download: Holdings 'as of' date not found")
    header = rows[header_at]
    need = ("Name", "Weight (%)")
    for name in need:
        if name not in header:
            raise IssuerSchemaError(f"iShares fund download: missing column {name!r}")
    col = {name: header.index(name) for name in header if name}

    def get(cells: list[str], name: str) -> str | None:
        i = col.get(name)
        return _text(cells[i]) if i is not None and i < len(cells) else None

    out: list[HoldingRow] = []
    unparseable: list[str] = []
    for cells in rows[header_at + 1 :]:
        if not any(cells):
            break
        raw_weight = get(cells, "Weight (%)")
        weight = _pct(raw_weight)
        if weight is None:
            # A footer line has no weight cell at all; a weight cell that is present but not a
            # number is a lost row.
            if raw_weight is not None and raw_weight != "-":
                unparseable.append(get(cells, "Name") or raw_weight)
            continue
        asset = _text(get(cells, "Asset Class"))
        asset_class = _ishares_asset_class(asset)
        sector_raw = get(cells, "Sector")
        currency = get(cells, "Market Currency")
        market_value = get(cells, "Market Value")
        out.append(
            HoldingRow(
                name=get(cells, "Name"),
                isin=None,
                ticker=get(cells, "Issuer Ticker"),
                sedol=None,
                asset_class=asset_class,
                security_type=asset,
                weight=weight,
                sector_raw=sector_raw,
                sector=normalize_sector(sector_raw) if asset_class == "equity" else None,
                country_raw=None,
                country=None,
                currency=currency.upper() if currency and len(currency) == 3 else None,
                currency_derived=False,
                exchange=None,
                market_value=_dec(re.sub(r"^[A-Z]{3}\s+", "", market_value or "")),
            )
        )
    if not out:
        raise IssuerSchemaError("iShares fund download: Holdings sheet has no rows")
    sheet_warnings = ["fund download has no country or ISIN column (weights rounded to 0.01%)"]
    if unparseable:
        sheet_warnings.append(_weight_warning(unparseable))
    return FundSnapshot(
        provider="ishares",
        portfolio_id=portfolio_id,
        source="ishares_spreadsheet",
        as_of=as_of,
        fund_name=None,
        fund_currency=None,
        country_basis=None,
        currency_basis="issuer",
        rows=out,
        raw_text=text,
        warnings=sheet_warnings,
    )


async def fetch_ishares_spreadsheet(
    http: httpx.AsyncClient,
    portfolio_id: str,
    *,
    api_url: str = ISHARES_API,
    site: str = "ishares-uk",
    locale: str = "en_GB",
    retries: int = 1,
    retry_delay: float = 2.0,
) -> FundSnapshot:
    params = _ishares_params(portfolio_id, "fundDownloadV2", site, locale)
    request = http.build_request(
        "GET", f"{api_url.rstrip('/')}/api/v1/get-fund-document", params=params
    )
    response = await _send(http, "iShares fund download", request, retries, retry_delay)
    if response.status_code != 200:
        raise IssuerHttpError("iShares fund download", response.status_code, response.text)
    return parse_ishares_spreadsheet(response.text, portfolio_id)


# ---------------------------------------------------------------------------
# Vanguard GraphQL
# ---------------------------------------------------------------------------

VANGUARD_FUND_QUERY = (
    "query LookThroughFund($portIds: [String!]) { funds(portIds: $portIds) {"
    " profile { fundFullName fundCurrency primarySectorEquityClassification }"
    " marketAllocation { date countryName countryCode holdingStatCode regionCode regionName"
    " fundMktPercent benchmarkMktPercent }"
    " sectorDiversification { date sectorName fundPercent benchmarkPercent } } }"
)
VANGUARD_HOLDINGS_QUERY = (
    "query LookThroughHoldings($portIds: [String!], $lastItemKey: String) {"
    " borHoldings(portIds: $portIds) { holdings(limit: LIMIT, lastItemKey: $lastItemKey) {"
    " items { issuerName securityLongDescription isin sedol1 ticker gicsSectorDescription"
    " icbIndustryDescription marketValuePercentage securityType effectiveDate"
    " bloombergIsoCountry marketValueBaseCurrency } totalHoldings lastItemKey } } }"
)

# marketAllocation's holdingStatCode: which classification a row belongs to.
VANGUARD_ALLOCATION_SCHEMES: dict[str, tuple[Literal["country", "region"], str]] = {
    "FTCTYATPCS": ("country", "ftse_country"),
    "MSCTYATPCS": ("country", "msci_country"),
    "SASTTYPPC": ("region", "ftse_region"),
}


def _vanguard_asset_class(security_type: str | None) -> AssetClass:
    t = (security_type or "").upper()
    if t.startswith("EQ."):
        return "equity"
    if t in ("CRNY", "CASH") or t.startswith("MM"):
        return "cash"
    if t.startswith(("CT.", "DE.", "FX")):
        return "derivative"
    return "other"


async def _vanguard_post(
    http: httpx.AsyncClient,
    url: str,
    consumer_id: str,
    query: str,
    variables: dict[str, Any],
    retries: int,
    retry_delay: float,
) -> dict[str, Any]:
    request = http.build_request(
        "POST",
        url,
        json={"query": query, "variables": variables},
        headers={"X-Consumer-ID": consumer_id, "Content-Type": "application/json"},
    )
    response = await _send(http, "Vanguard GraphQL", request, retries, retry_delay)
    if response.status_code != 200:
        # A 400 is how this endpoint reports a missing header or a field it no longer has.
        raise IssuerHttpError("Vanguard GraphQL", response.status_code, response.text)
    try:
        body = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise IssuerSchemaError("Vanguard GraphQL: response is not JSON") from exc
    if not isinstance(body, dict):
        raise IssuerSchemaError("Vanguard GraphQL: response is not an object")
    if body.get("errors"):
        messages = "; ".join(str(e.get("message", e))[:160] for e in body["errors"][:3])
        raise IssuerSchemaError(f"Vanguard GraphQL errors: {messages}")
    return body


def vanguard_as_of(fund_body: dict[str, Any]) -> dt.date | None:
    """The as-of date of the fund query (its country table's date)."""
    try:
        fund = fund_body["data"]["funds"][0]
    except (KeyError, IndexError, TypeError):
        return None
    dates = [
        _ymd(r.get("date"))
        for r in (fund.get("marketAllocation") or []) + (fund.get("sectorDiversification") or [])
        if isinstance(r, dict)
    ]
    known = [d for d in dates if d is not None]
    return max(known) if known else None


async def fetch_vanguard_fund(
    http: httpx.AsyncClient,
    port_id: str,
    *,
    url: str = VANGUARD_GRAPHQL,
    consumer_id: str = "ie0",
    retries: int = 1,
    retry_delay: float = 2.0,
) -> dict[str, Any]:
    """The small fund query (profile, country/region and sector tables; a few KB)."""
    return await _vanguard_post(
        http, url, consumer_id, VANGUARD_FUND_QUERY, {"portIds": [port_id]}, retries, retry_delay
    )


async def fetch_vanguard_holdings(
    http: httpx.AsyncClient,
    port_id: str,
    *,
    url: str = VANGUARD_GRAPHQL,
    consumer_id: str = "ie0",
    page_size: int = 1500,
    max_pages: int = 20,
    retries: int = 1,
    retry_delay: float = 2.0,
) -> list[dict[str, Any]]:
    """Every page of `borHoldings`, following `lastItemKey` until it is null."""
    pages: list[dict[str, Any]] = []
    key: str | None = None
    seen: set[str] = set()
    for _ in range(max_pages):
        body = await _vanguard_post(
            http,
            url,
            consumer_id,
            VANGUARD_HOLDINGS_QUERY.replace("LIMIT", str(int(page_size))),
            {"portIds": [port_id], "lastItemKey": key},
            retries,
            retry_delay,
        )
        pages.append(body)
        holdings = _path(body, ["data", "borHoldings"], "Vanguard borHoldings")
        if not isinstance(holdings, list) or not holdings:
            raise IssuerSchemaError("Vanguard borHoldings: empty result for the fund")
        page = _path(holdings[0], ["holdings"], "Vanguard borHoldings")
        key = _text(page.get("lastItemKey"))
        if key is None:
            return pages
        if key in seen:
            raise IssuerSchemaError("Vanguard borHoldings: paging cursor repeated")
        seen.add(key)
    raise IssuerSchemaError(f"Vanguard borHoldings: more than {max_pages} pages")


def parse_vanguard(
    fund_body: dict[str, Any], pages: list[dict[str, Any]], port_id: str
) -> FundSnapshot:
    what = "Vanguard GraphQL"
    funds = _path(fund_body, ["data", "funds"], what)
    if not isinstance(funds, list) or not funds or not isinstance(funds[0], dict):
        raise IssuerSchemaError(f"{what}: no fund {port_id} in funds()")
    fund = funds[0]
    profile = fund.get("profile") or {}
    warnings: list[str] = []

    breakdowns: list[BreakdownRow] = []
    allocation = fund.get("marketAllocation")
    if not isinstance(allocation, list):
        warnings.append("marketAllocation missing")
        allocation = []
    for r in allocation:
        code = str(r.get("holdingStatCode") or "")
        dimension, scheme = VANGUARD_ALLOCATION_SCHEMES.get(
            code, ("country" if r.get("countryCode") else "region", f"vanguard_{code.lower()}")
        )
        label = _text(r.get("countryName") if dimension == "country" else r.get("regionName"))
        if label is None:
            continue
        breakdowns.append(
            BreakdownRow(
                dimension=dimension,
                scheme=scheme,
                code=_text(r.get("countryCode") if dimension == "country" else r.get("regionCode")),
                label=label,
                fund_weight=_pct(r.get("fundMktPercent")),
                benchmark_weight=_pct(r.get("benchmarkMktPercent")),
                as_of=_ymd(r.get("date")),
            )
        )
    sectors = fund.get("sectorDiversification")
    if not isinstance(sectors, list):
        warnings.append("sectorDiversification missing")
        sectors = []
    for r in sectors:
        label = _text(r.get("sectorName"))
        if label is None:
            continue
        breakdowns.append(
            BreakdownRow(
                dimension="sector",
                scheme="icb_industry",
                code=None,
                label=label,
                fund_weight=_pct(r.get("fundPercent")),
                benchmark_weight=_pct(r.get("benchmarkPercent")),
                as_of=_ymd(r.get("date")),
            )
        )

    rows: list[HoldingRow] = []
    dates: Counter[dt.date] = Counter()
    unparseable: list[str] = []
    for body in pages:
        holdings = _path(body, ["data", "borHoldings"], "Vanguard borHoldings")
        page = _path(holdings[0], ["holdings"], "Vanguard borHoldings")
        items = page.get("items")
        if not isinstance(items, list):
            raise IssuerSchemaError("Vanguard borHoldings: missing holdings.items")
        for item in items:
            if not isinstance(item, dict):
                continue
            weight = _pct(item.get("marketValuePercentage"))
            if weight is None:
                unparseable.append(
                    _text(item.get("issuerName")) or _text(item.get("isin")) or "unnamed row"
                )
                continue
            effective = _ymd(item.get("effectiveDate"))
            if effective:
                dates[effective] += 1
            security_type = _text(item.get("securityType"))
            asset_class = _vanguard_asset_class(security_type)
            listing = _text(item.get("bloombergIsoCountry"))
            country = country_code(listing)
            ticker = _text(item.get("ticker"))
            currency: str | None
            if asset_class == "cash" and ticker and re.fullmatch(r"[A-Z]{3}", ticker):
                currency, derived = ticker, False
            else:
                currency, derived = currency_of(country), True
            sector_raw = _text(item.get("gicsSectorDescription"))
            rows.append(
                HoldingRow(
                    name=_text(item.get("issuerName"))
                    or _text(item.get("securityLongDescription")),
                    isin=_text(item.get("isin")),
                    ticker=ticker,
                    sedol=_text(item.get("sedol1")),
                    asset_class=asset_class,
                    security_type=security_type,
                    weight=weight,
                    sector_raw=sector_raw,
                    sector=normalize_sector(sector_raw) if asset_class == "equity" else None,
                    country_raw=listing,
                    country=country,
                    currency=currency if asset_class != "derivative" else None,
                    currency_derived=derived and currency is not None,
                    exchange=None,
                    market_value=_dec(item.get("marketValueBaseCurrency")),
                )
            )
    if not rows:
        raise IssuerSchemaError("Vanguard borHoldings: no holdings rows")
    as_of = dates.most_common(1)[0][0] if dates else vanguard_as_of(fund_body)
    if as_of is None:
        raise IssuerSchemaError(f"{what}: no effectiveDate or allocation date")
    if unparseable:
        warnings.append(_weight_warning(unparseable))
    if len(dates) > 1:
        warnings.append(f"holdings carry several effective dates: {sorted(map(str, dates))}")
    table_as_of = vanguard_as_of(fund_body)
    if table_as_of is not None and table_as_of != as_of:
        warnings.append(f"fund tables are as of {table_as_of}, holdings as of {as_of}")
    return FundSnapshot(
        provider="vanguard",
        portfolio_id=port_id,
        source="vanguard_graphql",
        as_of=as_of,
        fund_name=_text(profile.get("fundFullName")),
        fund_currency=_text(profile.get("fundCurrency")),
        country_basis="listing_country",
        currency_basis="derived",
        rows=rows,
        breakdowns=breakdowns,
        raw={"fund": fund_body, "holdings_pages": pages},
        warnings=warnings,
    )
