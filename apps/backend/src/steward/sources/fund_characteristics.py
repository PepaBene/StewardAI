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

Parser-drift audit (plan D6.9). Each result also carries
- `shape` / `fingerprint`: the structure of the fields the parser reads (key sets and value
  types of the tracked data points, never their values). A changed fingerprint means the
  page/GraphQL shape moved - parser drift - whatever the numbers say;
- `rules`: the issuer's stated exclusion/capping rules parsed from its methodology text
  (negative earnings excluded, P/E capped at 60, P/B capped at 25, ...), so a rule change is
  recognised as a methodology change rather than a value change;
- `basis` ('fund' - the fund's own holdings; the index figures are separate fields with their
  own `index_as_of`) and the effective dates.
`compare_definitions` decides whether these figures may be compared with a constituent-
weighted metric the app computes itself: only when the definitions align.
"""

import datetime as dt
import hashlib
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
    # Parser-drift audit (D6.9)
    basis: str = "fund"
    index_as_of: dt.date | None = None
    rules: dict[str, Any] = field(default_factory=dict, compare=False)
    shape: dict[str, Any] = field(default_factory=dict, compare=False)
    fingerprint: str = field(default="", compare=False)


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


def fingerprint_of(shape: dict[str, Any]) -> str:
    """Stable hash of a shape description (key sets and type names, no values)."""
    canonical = json.dumps(shape, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _shape_of_point(point: Any) -> Any:
    """Key set and value types of one data point: its structure, not its numbers. A null and
    a number are the same shape (a metric may be empty on a given day)."""
    if not isinstance(point, dict):
        return type(point).__name__
    return {
        k: ("scalar" if v is None or isinstance(v, int | float | str) else type(v).__name__)
        for k, v in sorted(point.items())
        if k in ISHARES_SHAPE_KEYS
    }


# The data point keys the iShares parser relies on (others may come and go freely).
ISHARES_SHAPE_KEYS = ("value", "asOfDate", "infoBubble", "formattedValue", "name")
ISHARES_TRACKED_POINTS = ("priceEarnings", "priceBook", "numHoldings")

_CAP = re.compile(
    r"(?P<what>p/e|price to earnings|price to book|p/b)[^.]*?\bover\s+(?P<over>\d+(?:\.\d+)?)"
    r"\s+are\s+set\s+to\s+(?P<to>\d+(?:\.\d+)?)",
    re.I,
)


def parse_rules(methodology: str) -> dict[str, Any]:
    """The issuer's stated exclusion / capping rules from its methodology text. A rule the
    text does not state is absent (not assumed)."""
    text = html.unescape(methodology)
    rules: dict[str, Any] = {}
    if re.search(r"negative earnings are excluded", text, re.I):
        rules["negative_earnings_excluded"] = True
    if re.search(r"negative book values? (are|is) excluded", text, re.I):
        rules["negative_book_excluded"] = True
    if re.search(r"extraordinary items are excluded", text, re.I):
        rules["extraordinary_items_excluded"] = True
    for sentence in re.split(r"(?<=[.])\s+", text):
        for match in _CAP.finditer(sentence):
            key = "pb_cap" if match["what"].lower() in ("price to book", "p/b") else "pe_cap"
            if match["over"] == match["to"]:
                rules[key] = float(match["over"])
    if re.search(r"weighted average", text, re.I):
        rules["aggregation"] = "weighted_average"
    return rules


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
    methodology = " ".join(html.unescape(m) for m in methods)
    shape = {
        "container_count": len(props.get("containersByNameMap") or {}),
        "points": {k: _shape_of_point(points.get(k)) for k in ISHARES_TRACKED_POINTS},
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
        methodology=methodology,
        source_url=url,
        raw={"fundName": props.get("fundName"), "dataPoints": raw_points},
        basis="fund",
        index_as_of=None,
        rules=parse_rules(methodology),
        shape=shape,
        fingerprint=fingerprint_of(shape),
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
    index_dates = [d for d in (index_pe.get("effectiveDate"), index_pb.get("effectiveDate")) if d]
    try:
        index_as_of = (
            max(dt.date.fromisoformat(str(d)[:10]) for d in index_dates) if index_dates else None
        )
    except ValueError:
        index_as_of = None
    shape = {
        "history": type(history[0]).__name__,
        "monthly": sorted((history[0].get("monthly") or {}).keys()),
        "analytics": sorted(analytics.keys()),
        "points": {
            name: _shape_of_point(point)
            for name, point in (
                ("fund.PERATIO", pe),
                ("fund.PBRATIO", pb),
                ("benchmark.PERATIOBMK", index_pe),
                ("benchmark.PBRATIOBMK", index_pb),
                ("yield.FRCDIVYLD", dividend),
            )
        },
    }
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
        basis="fund",
        index_as_of=index_as_of,
        rules=parse_rules(VANGUARD_METHOD),
        shape=shape,
        fingerprint=fingerprint_of(shape),
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


# ---------------------------------------------------------------------------
# Comparison with constituent-weighted metrics (D6.9)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricDefinition:
    """How a P/E or P/B figure was built, for alignment checks."""

    metric: str  # 'pe' | 'pb'
    aggregation: str  # 'weighted_average' | 'weighted_harmonic' | ...
    basis: str  # 'fund' | 'index'
    negatives_excluded: bool
    cap: float | None
    as_of: dt.date


@dataclass(frozen=True)
class Alignment:
    comparable: bool
    reasons: list[str]


def issuer_definition(
    figures: FundFigures, metric: str, *, index: bool = False
) -> MetricDefinition:
    """The issuer's definition of its P/E or P/B, from the rules it states. Aggregation is
    'weighted_average' only when the methodology says so, otherwise 'unstated'."""
    rules = figures.rules
    negatives = bool(
        rules.get("negative_earnings_excluded" if metric == "pe" else "negative_book_excluded")
    )
    cap = rules.get("pe_cap" if metric == "pe" else "pb_cap")
    return MetricDefinition(
        metric=metric,
        aggregation=str(rules.get("aggregation", "unstated")),
        basis="index" if index else figures.basis,
        negatives_excluded=negatives,
        cap=float(cap) if cap is not None else None,
        as_of=(figures.index_as_of if index and figures.index_as_of else figures.as_of),
    )


def compare_definitions(
    issuer: MetricDefinition, own: MetricDefinition, *, max_days_apart: int = 7
) -> Alignment:
    """May the issuer figure be compared with our constituent-weighted one? Only when metric,
    basis, aggregation, exclusion and capping rules and the effective date line up."""
    reasons: list[str] = []
    if issuer.metric != own.metric:
        reasons.append(f"different metrics ({issuer.metric} vs {own.metric})")
    if issuer.basis != own.basis:
        reasons.append(f"different basis ({issuer.basis} vs {own.basis})")
    if issuer.aggregation == "unstated":
        reasons.append("issuer does not state its aggregation")
    elif issuer.aggregation != own.aggregation:
        reasons.append(f"different aggregation ({issuer.aggregation} vs {own.aggregation})")
    if issuer.negatives_excluded != own.negatives_excluded:
        reasons.append("negative earnings/book treatment differs")
    if issuer.cap != own.cap:
        reasons.append(f"different capping rule ({issuer.cap} vs {own.cap})")
    if abs((issuer.as_of - own.as_of).days) > max_days_apart:
        reasons.append(f"effective dates differ ({issuer.as_of} vs {own.as_of})")
    return Alignment(comparable=not reasons, reasons=reasons)
