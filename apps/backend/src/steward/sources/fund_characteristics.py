"""Fund-level valuation (P/E, P/B) published by ETF providers, no key needed.

- iShares: the product page (e.g. CSPX, product 253743) embeds the "Portfolio
  Characteristics" component as HTML-escaped JSON in a `componentprops` attribute; its data
  points `priceEarnings` and `priceBook` carry the value, the as-of date and the
  methodology ("negative earnings are excluded ... P/E ratios over 60 are set to 60",
  "holding price to book ratios over 25 are set to 25"). Updated daily. Tier 1 (the
  issuer's own page), undocumented format.
- Vanguard: the product pages load their figures from a GraphQL endpoint (`/gpx/graphql`,
  header `x-consumer-id`); the query below is the one the UK product page issues for its
  "Portfolio characteristics" panel (`polarisAnalyticsHistory.monthly.analytics`): the
  fund's and the index's P/E (`PERATIO`) and P/B (`PBRATIO`) as of the last month end, and
  the holdings' forecast dividend yield (`FRCDIVYLD`). Tier 1 data, undocumented endpoint.

Both fail loudly (FundDataError) when the expected structure is missing, so a page redesign
shows up as a failed step rather than as silently empty figures.
"""

import datetime as dt
import html
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36",
    "Accept-Language": "en-GB,en;q=0.9",
}

VANGUARD_QUERY = """query PortfolioCharacteristicsQuery($portIds: [String!], $getLatest: Boolean) {
  funds(portIds: $portIds) { profile { fundFullName shareClassName } }
  polarisAnalyticsHistory(portIds: $portIds) {
    portId
    monthly {
      analytics {
        portfolioStatistics(getLatest: $getLatest) {
          items { codes { CSTOCK { analyticValue: value effectiveDate } } }
        }
        fund(getLatest: $getLatest) {
          items { codes {
            PERATIO { analyticValue effectiveDate }
            PBRATIO { analyticValue effectiveDate }
          } }
        }
        benchmark(getLatest: $getLatest, benchmarkTypes: [FBPC]) {
          items { codes {
            PERATIOBMK: PERATIO { analyticValue effectiveDate }
            PBRATIOBMK: PBRATIO { analyticValue effectiveDate }
          } }
        }
      }
      yields {
        fund(getLatest: true) {
          items { codes { FRCDIVYLD { asOfDate: effectiveDate statValue: percent } } }
        }
      }
    }
  }
}"""

VANGUARD_METHOD = (
    "Vanguard: for a portfolio, the weighted average P/E (and P/B) ratio of the stocks it "
    "holds, as of the last month end."
)


class FundDataError(Exception):
    pass


@dataclass(frozen=True)
class FundFigures:
    provider: str
    as_of: dt.date
    pe: Decimal | None
    pb: Decimal | None
    index_pe: Decimal | None
    index_pb: Decimal | None
    dividend_yield: Decimal | None  # fraction
    dividend_yield_kind: str | None  # 'trailing' | 'forecast'
    methodology: str
    source_url: str
    raw: dict[str, Any] = field(repr=False, compare=False)


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        out = Decimal(str(value))
    except InvalidOperation:
        return None
    return out if out.is_finite() else None


def _positive(value: Decimal | None) -> Decimal | None:
    return value if value is not None and value > 0 else None


# ---------------------------------------------------------------------------
# iShares
# ---------------------------------------------------------------------------

_COMPONENT = re.compile(
    r'data-componentname="fundamentalsAndRisk"\s*>\s*<walrus-render-on-client[^>]*?'
    r'componentprops="([^"]*)"',
    re.S,
)


def _ishares_date(value: Any) -> dt.date | None:
    text = str(value or "")
    if re.fullmatch(r"\d{8}", text):
        return dt.date(int(text[:4]), int(text[4:6]), int(text[6:]))
    return None


def parse_ishares(page: str, url: str) -> FundFigures:
    match = _COMPONENT.search(page)
    if match is None:
        raise FundDataError("iShares page has no Portfolio Characteristics component")
    try:
        props = json.loads(html.unescape(match.group(1)))
    except ValueError as exc:
        raise FundDataError(f"iShares characteristics are not JSON: {exc}") from exc
    points: dict[str, Any] = {}
    for container in (props.get("containersByNameMap") or {}).values():
        points.update(container.get("dataPointsByNameMap") or {})
    pe_point = points.get("priceEarnings") or {}
    pb_point = points.get("priceBook") or {}
    as_of = _ishares_date(pe_point.get("asOfDate")) or _ishares_date(pb_point.get("asOfDate"))
    if as_of is None:
        raise FundDataError("iShares characteristics carry no P/E or P/B as-of date")
    methods = [
        str(p.get("infoBubble")).strip() for p in (pe_point, pb_point) if p.get("infoBubble")
    ]
    raw_points = {
        k: points[k] for k in ("priceEarnings", "priceBook", "numHoldings") if k in points
    }
    return FundFigures(
        provider="ishares",
        as_of=as_of,
        pe=_positive(_dec(pe_point.get("value"))),
        pb=_positive(_dec(pb_point.get("value"))),
        index_pe=None,
        index_pb=None,
        dividend_yield=None,
        dividend_yield_kind=None,
        methodology=" ".join(html.unescape(m) for m in methods),
        source_url=url,
        raw={"fundName": props.get("fundName"), "dataPoints": raw_points},
    )


async def ishares_characteristics(http: httpx.AsyncClient, url: str) -> FundFigures:
    response = await http.get(url, headers=BROWSER_HEADERS, follow_redirects=True)
    if response.status_code != 200:
        raise FundDataError(f"iShares page returned HTTP {response.status_code}")
    return parse_ishares(response.text, url)


# ---------------------------------------------------------------------------
# Vanguard
# ---------------------------------------------------------------------------


def _code(items: Any, code: str) -> dict[str, Any]:
    if not isinstance(items, dict):
        return {}
    rows = items.get("items") or []
    if not rows:
        return {}
    value = (rows[0].get("codes") or {}).get(code)
    return value if isinstance(value, dict) else {}


def parse_vanguard(data: dict[str, Any], url: str) -> FundFigures:
    if data.get("errors"):
        raise FundDataError(f"Vanguard GraphQL errors: {str(data['errors'])[:300]}")
    history = (data.get("data") or {}).get("polarisAnalyticsHistory") or []
    if not history:
        raise FundDataError("Vanguard returned no analytics for the fund")
    monthly = history[0].get("monthly") or {}
    analytics = monthly.get("analytics") or {}
    pe = _code(analytics.get("fund"), "PERATIO")
    pb = _code(analytics.get("fund"), "PBRATIO")
    index_pe = _code(analytics.get("benchmark"), "PERATIOBMK")
    index_pb = _code(analytics.get("benchmark"), "PBRATIOBMK")
    dividend = _code((monthly.get("yields") or {}).get("fund"), "FRCDIVYLD")
    dates = [d for d in (pe.get("effectiveDate"), pb.get("effectiveDate")) if d]
    if not dates:
        raise FundDataError("Vanguard analytics carry no P/E or P/B")
    try:
        as_of = max(dt.date.fromisoformat(str(d)[:10]) for d in dates)
    except ValueError as exc:
        raise FundDataError(f"Vanguard as-of date unreadable: {exc}") from exc
    percent = _dec(dividend.get("statValue"))
    return FundFigures(
        provider="vanguard",
        as_of=as_of,
        pe=_positive(_dec(pe.get("analyticValue"))),
        pb=_positive(_dec(pb.get("analyticValue"))),
        index_pe=_positive(_dec(index_pe.get("analyticValue"))),
        index_pb=_positive(_dec(index_pb.get("analyticValue"))),
        dividend_yield=percent / 100 if percent is not None else None,
        dividend_yield_kind="forecast" if percent is not None else None,
        methodology=VANGUARD_METHOD,
        source_url=url,
        raw=data,
    )


async def vanguard_characteristics(
    http: httpx.AsyncClient, url: str, port_id: str, consumer_id: str
) -> FundFigures:
    response = await http.post(
        url,
        json={
            "operationName": "PortfolioCharacteristicsQuery",
            "query": VANGUARD_QUERY,
            "variables": {"portIds": [port_id], "getLatest": True},
        },
        headers={**BROWSER_HEADERS, "x-consumer-id": consumer_id},
    )
    if response.status_code != 200:
        raise FundDataError(f"Vanguard GraphQL returned HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError as exc:
        raise FundDataError(f"Vanguard sent no JSON: {exc}") from exc
    return parse_vanguard(data, url)
