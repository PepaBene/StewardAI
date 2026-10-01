"""SEC Form N-PORT (NPORT-P) XML parser.

N-PORT reports what a registered fund *owns* on a report date; it is a fund position, not
beneficial ownership of the issuer, and it is public only about 60 days after quarter end.
The parser keeps the filing's own identifiers and values as filed: a missing or ``N/A``
identifier is ``None`` (never zero or an empty string), and nothing is converted or summed.
"""

from __future__ import annotations

import datetime as dt
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from steward.sources.ciks import ten_digit_cik
from steward.sources.xml_guard import safe_fromstring

# Public availability: N-PORT is filed within 60 days after the fiscal quarter end and made
# public then (SEC Form N-PORT general instructions); counted from the report date.
PUBLIC_LAG_DAYS = 60

NULL_TOKENS = {"", "N/A", "NA", "NONE", "000000000", "0"}
DERIVATIVE_ELEMENTS = {
    "fwdDeriv": "FWD",
    "futrDeriv": "FUT",
    "optionSwaptionWarrantDeriv": "OPT",
    "swapDeriv": "SWP",
    "othDeriv": "OTH",
}


class NportError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class NportHolding:
    row_index: int
    issuer_name: str | None
    issuer_lei: str | None
    title: str | None
    cusip: str | None
    isin: str | None
    other_id: str | None
    balance: Decimal | None
    units: str | None
    currency: str | None
    value_usd: Decimal | None
    pct_value: Decimal | None
    payoff_profile: str | None
    asset_cat: str | None
    issuer_cat: str | None
    country: str | None
    fair_value_level: str | None
    is_restricted: bool | None
    is_derivative: bool
    derivative_cat: str | None


@dataclass(frozen=True, slots=True)
class NportReport:
    submission_type: str | None
    registrant_cik: str | None
    registrant_name: str | None
    registrant_lei: str | None
    series_id: str
    series_name: str | None
    series_lei: str | None
    class_ids: tuple[str, ...]
    fiscal_year_end: dt.date | None
    report_period_end: dt.date
    is_final: bool
    total_assets: Decimal | None
    total_liabilities: Decimal | None
    net_assets: Decimal | None
    risk_metrics: dict[str, Any] = field(default_factory=dict)
    class_returns: list[dict[str, Any]] = field(default_factory=list)
    flows: dict[str, Any] = field(default_factory=dict)
    holdings: list[NportHolding] = field(default_factory=list)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(node: ET.Element | None, name: str) -> ET.Element | None:
    if node is None:
        return None
    return next((c for c in node if _local(c.tag) == name), None)


def _children(node: ET.Element | None, name: str) -> list[ET.Element]:
    return [] if node is None else [c for c in node if _local(c.tag) == name]


def _path(node: ET.Element | None, *names: str) -> ET.Element | None:
    for name in names:
        node = _child(node, name)
    return node


def _text(node: ET.Element | None, *names: str) -> str | None:
    found = _path(node, *names)
    value = found.text.strip() if found is not None and found.text else ""
    return None if value.upper() in ("", "N/A") else value


def _number(raw: str | None) -> Decimal | None:
    if raw is None:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _num(node: ET.Element | None, *names: str) -> Decimal | None:
    return _number(_text(node, *names))


def _date(raw: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def _flag(raw: str | None) -> bool | None:
    return None if raw is None else raw.upper() in ("Y", "TRUE", "1")


def _identifier(raw: str | None) -> str | None:
    """CUSIP/LEI as filed; the all-zero and N/A placeholders mean 'none', not a real code."""
    if raw is None or raw.strip().upper() in NULL_TOKENS:
        return None
    return raw.strip().upper()


def _dict_numbers(node: ET.Element | None) -> dict[str, str]:
    """Child elements and attributes as strings (Decimals are kept as text for JSON)."""
    if node is None:
        return {}
    out = {_local(k): v for k, v in node.attrib.items()}
    for child in node:
        if child.text and child.text.strip():
            out[_local(child.tag)] = child.text.strip()
    return out


def valid_isin(value: str | None) -> bool:
    if not value or not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", value):
        return False
    digits = "".join(str(int(c, 36)) for c in value)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            d = d - 9 if d > 9 else d
        total += d
    return total % 10 == 0


def _holding(index: int, node: ET.Element) -> NportHolding:
    isin_node = _path(node, "identifiers", "isin")
    isin = _identifier(isin_node.get("value") if isin_node is not None else None)
    if isin is not None and not valid_isin(isin):
        isin = None
    other = _path(node, "identifiers", "other")
    derivative = _child(node, "derivativeInfo")
    derivative_cat = None
    if derivative is not None:
        for child in derivative:
            if _local(child.tag) in DERIVATIVE_ELEMENTS:
                derivative_cat = child.get("derivCat") or DERIVATIVE_ELEMENTS[_local(child.tag)]
                break
    asset_cat = _text(node, "assetCat")
    return NportHolding(
        row_index=index,
        issuer_name=_text(node, "name"),
        issuer_lei=_identifier(_text(node, "lei")),
        title=_text(node, "title"),
        cusip=_identifier(_text(node, "cusip")),
        isin=isin,
        other_id=other.get("value") if other is not None else None,
        balance=_num(node, "balance"),
        units=_text(node, "units"),
        currency=_text(node, "curCd"),
        value_usd=_num(node, "valUSD"),
        pct_value=_num(node, "pctVal"),
        payoff_profile=_text(node, "payoffProfile"),
        asset_cat=asset_cat,
        issuer_cat=_text(node, "issuerCat"),
        country=_text(node, "invCountry"),
        fair_value_level=_text(node, "fairValLevel"),
        is_restricted=_flag(_text(node, "isRestrictedSec")),
        is_derivative=derivative is not None or (asset_cat or "").startswith("D"),
        derivative_cat=derivative_cat,
    )


def parse_nport(xml: bytes) -> NportReport:
    """Parse one NPORT-P / NPORT-P/A primary document."""
    root = safe_fromstring(xml, NportError)
    if _local(root.tag) != "edgarSubmission":
        raise NportError("not an EDGAR N-PORT submission")
    header = _child(root, "headerData")
    form = _child(root, "formData")
    gen = _child(form, "genInfo")
    series_id = _text(gen, "seriesId")
    period = _date(_text(gen, "repPdDate"))
    if series_id is None or period is None:
        raise NportError("N-PORT lacks a series id or report period date")
    fund = _child(form, "fundInfo")

    metrics: dict[str, Any] = {}
    for metric in _children(_child(fund, "curMetrics"), "curMetric"):
        currency = _text(metric, "curCd")
        if currency:
            metrics.setdefault("interest_rate_dv01", {})[currency] = _dict_numbers(
                _child(metric, "intrstRtRiskdv01")
            )
    for tag, key in (
        ("creditSprdRiskInvstGrade", "credit_spread_investment_grade"),
        ("creditSprdRiskNonInvstGrade", "credit_spread_non_investment_grade"),
    ):
        node = _child(fund, tag)
        if node is not None:
            metrics[key] = _dict_numbers(node)

    returns = [
        {k: v for k, v in _dict_numbers(r).items()}
        for r in _children(_path(fund, "returnInfo", "monthlyTotReturns"), "monthlyTotReturn")
    ]
    flows = {
        name: _dict_numbers(node)
        for name in ("mon1Flow", "mon2Flow", "mon3Flow")
        if (node := _child(fund, name)) is not None
    }
    class_ids = tuple(
        c.text.strip()
        for c in _children(_path(header, "filerInfo", "seriesClassInfo"), "classId")
        if c.text and c.text.strip()
    )
    holdings = [
        _holding(i, n) for i, n in enumerate(_children(_child(form, "invstOrSecs"), "invstOrSec"))
    ]
    cik = _text(_path(header, "filerInfo", "filer", "issuerCredentials"), "cik") or _text(
        gen, "regCik"
    )
    return NportReport(
        submission_type=_text(header, "submissionType"),
        registrant_cik=ten_digit_cik(cik),
        registrant_name=_text(gen, "regName"),
        registrant_lei=_identifier(_text(gen, "regLei")),
        series_id=series_id,
        series_name=_text(gen, "seriesName"),
        series_lei=_identifier(_text(gen, "seriesLei")),
        class_ids=class_ids,
        fiscal_year_end=_date(_text(gen, "repPdEnd")),
        report_period_end=period,
        is_final=bool(_flag(_text(gen, "isFinalFiling"))),
        total_assets=_num(fund, "totAssets"),
        total_liabilities=_num(fund, "totLiabs"),
        net_assets=_num(fund, "netAssets"),
        risk_metrics=metrics,
        class_returns=returns,
        flows=flows,
        holdings=holdings,
    )


def public_after(report_period_end: dt.date) -> dt.date:
    """Earliest date a report is expected to be public: report date plus 60 days.

    N-PORT is filed within 60 days after the end of the fund's *fiscal* quarter and made
    public from then on. The report date is a month end of the fund's own fiscal calendar, so
    adding the lag to it never assumes calendar quarters (the old quarter-end rounding
    overstated the date by up to two months for a fund with a non-calendar fiscal year)."""
    return report_period_end + dt.timedelta(days=PUBLIC_LAG_DAYS)


def fiscal_quarter_end_months(fiscal_year_end: dt.date | None) -> frozenset[int]:
    """Calendar months in which the fund's fiscal quarters end, from its fiscal year end
    (`repPdEnd`). Without it the calendar quarters are the only thing that can be said."""
    anchor = fiscal_year_end.month if fiscal_year_end is not None else 12
    return frozenset(((anchor - 1 - 3 * k) % 12) + 1 for k in range(4))
