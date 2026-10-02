"""Parsers and endpoint builders for the phase-2 public macro providers.

Transport remains injectable so fixtures can exercise normalization without network access.
Every numeric parser rejects NaN and infinities (a provider placeholder is "missing", never a
number), and every period parser accepts the SDMX/Eurostat/ECB forms `2026`, `2026-07`,
`2026-Q2`, `2026-S1`, `2026-W05` and dates.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

from steward.macro.geo import ISO3_TO_ISO2, is_world, iso2_for_m49
from steward.macro.hs import normalize_hs_revision

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MacroPoint:
    series_id: str
    source: str
    geography: str
    period: dt.date
    value: Decimal | None
    unit: str
    frequency: str
    release_at: dt.datetime | None
    vintage: str
    source_url: str
    metadata: dict[str, Any]
    # D2.1 attributes: populated from SDMX attributes / config, never guessed.
    unit_multiplier: Decimal = Decimal(1)
    seasonal_adjustment: str | None = None
    transformation: str | None = None
    release_calendar: str | None = None
    geography_definition: str | None = None


# ---------------------------------------------------------------------------------------
# Periods and numbers
# ---------------------------------------------------------------------------------------

_PERIOD_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(\d{4})"), "year"),
    (re.compile(r"(\d{4})-?Q([1-4])", re.I), "quarter"),
    (re.compile(r"(\d{4})-?[SH]([12])", re.I), "half"),
    (re.compile(r"(\d{4})-?W(\d{2})", re.I), "week"),
    (re.compile(r"(\d{4})-(\d{2})"), "month"),
    (re.compile(r"(\d{4})(\d{2})"), "month"),
    (re.compile(r"(\d{4})(\d{2})(\d{2})"), "day8"),
]


def parse_period(text: str) -> dt.date:
    """First day of a period label: '2026' -> 2026-01-01, '2026-08' (or '202608') ->
    2026-08-01, '2026-Q2' -> 2026-04-01, '2026-S2' -> 2026-07-01, '2026-W05' -> the Monday of
    ISO week 5, a date or datetime -> its date. Anything else raises ValueError."""
    label = str(text).strip()
    for pattern, kind in _PERIOD_PATTERNS:
        match = pattern.fullmatch(label)
        if match is None:
            continue
        year = int(match.group(1))
        try:
            if kind == "year":
                return dt.date(year, 1, 1)
            if kind == "quarter":
                return dt.date(year, 3 * int(match.group(2)) - 2, 1)
            if kind == "half":
                return dt.date(year, 6 * int(match.group(2)) - 5, 1)
            if kind == "week":
                return dt.date.fromisocalendar(year, int(match.group(2)), 1)
            if kind == "month":
                return dt.date(year, int(match.group(2)), 1)
            return dt.date(year, int(match.group(2)), int(match.group(3)))
        except ValueError as exc:
            raise ValueError(f"invalid period {label!r}") from exc
    try:
        return dt.date.fromisoformat(label[:10])
    except ValueError as exc:
        raise ValueError(f"unrecognised period {label!r}") from exc


def frequency_of(label: str) -> str:
    """Frequency implied by a period label."""
    text = str(label).strip()
    if re.fullmatch(r"\d{4}", text):
        return "annual"
    if re.search(r"-?Q[1-4]$", text, re.I):
        return "quarterly"
    if re.search(r"-?[SH][12]$", text, re.I):
        return "semiannual"
    if re.search(r"-?W\d{2}$", text, re.I):
        return "weekly"
    if re.fullmatch(r"\d{4}-\d{2}|\d{6}", text):
        return "monthly"
    return "daily"


def finite_decimal(value: Any) -> Decimal | None:
    """A finite Decimal, or None for missing markers, NaN, infinities and non-numbers."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    text = str(value).strip()
    if text in ("", ".", "..", "-", "NaN", "nan"):
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


_decimal = finite_decimal


def _date(value: str) -> dt.date:
    return parse_period(value)


# ---------------------------------------------------------------------------------------
# World Bank
# ---------------------------------------------------------------------------------------

WORLD_BANK_GEOGRAPHY = (
    "World Bank country id: ISO 3166-1 alpha-2 for countries, World Bank codes for aggregates"
)
WORLD_BANK_CALENDAR = (
    "World Bank WDI: annual data, revised in periodic dataset updates (lastupdated)"
)


def world_bank_url(country: str, indicators: list[str], start: int, end: int) -> str:
    ids = ";".join(indicators)
    return f"https://api.worldbank.org/v2/country/{quote(country)}/indicator/{quote(ids)}?format=json&per_page=20000&date={start}:{end}"


def world_bank_header(body: Any) -> dict[str, Any]:
    """The paging/metadata object of a World Bank response (`pages`, `total`, `lastupdated`)."""
    if isinstance(body, list) and body and isinstance(body[0], dict):
        return body[0]
    return {}


def parse_world_bank(body: list[Any], source_url: str) -> list[MacroPoint]:
    rows = body[1] if len(body) > 1 and isinstance(body[1], list) else []
    # In the real payload `lastupdated` is in the page header (body[0]), not on each row.
    header_updated = world_bank_header(body).get("lastupdated")
    out: list[MacroPoint] = []
    for row in rows:
        indicator = row.get("indicator") or {}
        # Preserve the requested country code (normally ISO alpha-2) so observations
        # reconcile with portfolio and exposure mappings; retain ISO-3 as metadata.
        country = (row.get("country") or {}).get("id") or row.get("countryiso3code")
        if not indicator.get("id") or not country or not row.get("date"):
            continue
        try:
            period = parse_period(row["date"])
        except ValueError:
            continue
        updated = str(row.get("lastupdated") or header_updated or "latest")
        out.append(
            MacroPoint(
                indicator["id"],
                "world_bank",
                country,
                period,
                finite_decimal(row.get("value")),
                row.get("unit") or "provider_unit",
                "annual",
                None,
                updated,
                source_url,
                {
                    "indicator_name": indicator.get("value"),
                    "obs_status": row.get("obs_status"),
                    "country_iso3": row.get("countryiso3code"),
                    "lastupdated": updated,
                },
                release_calendar=WORLD_BANK_CALENDAR,
                geography_definition=WORLD_BANK_GEOGRAPHY,
            )
        )
    return out


# ---------------------------------------------------------------------------------------
# SDMX-JSON (OECD, BIS)
# ---------------------------------------------------------------------------------------


def sdmx_url(base: str, flow: str, key: str, start: str | None = None) -> str:
    url = f"{base.rstrip('/')}/data/{quote(flow, safe=',')}/{quote(key, safe='.+')}"
    return url + (f"?startPeriod={quote(start)}" if start else "")


class SdmxAmbiguousError(ValueError):
    """A response holds several distinct series for one geography and period; the request key
    does not pin every dimension, so a value cannot be attributed to one series."""


GEO_DIMENSIONS = ("REF_AREA", "BORROWERS_CTY", "COUNTRY", "LOCATION", "JURISDICTION")
TIME_DIMENSIONS = ("TIME_PERIOD", "TIME")
FREQ_CODES = {
    "A": "annual",
    "Q": "quarterly",
    "M": "monthly",
    "D": "daily",
    "W": "weekly",
    "S": "semiannual",
    "H": "semiannual",
}
ADJUSTMENT_IDS = ("SEASONAL_ADJUSTMENT", "ADJUSTMENT", "ADJUSTMENT_INDICATOR", "SEASONAL_ADJUST")
TRANSFORMATION_IDS = ("TRANSFORMATION",)
SDMX_CALENDAR = {
    "oecd": "OECD.Stat release calendar (per dataflow)",
    "bis": "BIS statistical release calendar (quarterly data, per dataset)",
}


def _code(values: list[Any], index: Any) -> str | None:
    try:
        entry = values[int(index)]
    except (TypeError, ValueError, IndexError):
        return None
    return str(entry.get("id")) if isinstance(entry, dict) and entry.get("id") is not None else None


def _structure(body: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(datasets, structure) for SDMX-JSON 2.0 (`data.structures[0]`) and 1.0 (`structure`)."""
    nested = body.get("data")
    data: dict[str, Any] = nested if isinstance(nested, dict) else body
    datasets = data.get("dataSets") or []
    structures = data.get("structures")
    if isinstance(structures, list) and structures:
        structure = structures[0]
    else:
        structure = data.get("structure") or body.get("structure") or {}
    return (
        [d for d in datasets if isinstance(d, dict)],
        structure if isinstance(structure, dict) else {},
    )


def sdmx_unit_multiplier(raw: str | None) -> Decimal:
    """UNIT_MULT is a power of ten ('6' = millions)."""
    if raw in (None, ""):
        return Decimal(1)
    try:
        exponent = int(str(raw))
    except ValueError:
        return Decimal(1)
    return Decimal(10) ** exponent if -12 <= exponent <= 18 else Decimal(1)


def parse_sdmx_json(
    body: dict[str, Any],
    *,
    source: str,
    series_id: str,
    geography: str,
    unit: str,
    frequency: str,
    source_url: str,
    vintage: str,
    expect_geography: str | None = None,
    geography_map: Mapping[str, str] | None = None,
    skipped: list[str] | None = None,
) -> list[MacroPoint]:
    """Parse SDMX-JSON 1.0/2.0 observations, series-shaped (`dataSets[0].series`) or flat
    (`dataSets[0].observations`, as returned for `dimensionAtObservation=AllDimensions`).

    The geography of each point is read from the response's own geography dimension
    (REF_AREA, BORROWERS_CTY, ...), translated by `geography_map` (e.g. OECD alpha-3 to
    alpha-2); `geography` is only the fallback for a response without one. With
    `expect_geography` (the code the request was pinned to) other areas are dropped. If
    several non-geography series remain for one geography the key was not pinned and
    `SdmxAmbiguousError` is raised. Unparseable rows are appended to `skipped`."""
    datasets, structure = _structure(body)
    if not datasets:
        return []
    dims = structure.get("dimensions") or {}
    series_dims: list[dict[str, Any]] = dims.get("series") or []
    obs_dims: list[dict[str, Any]] = dims.get("observation") or []
    attrs = structure.get("attributes") or {}
    series_attrs: list[dict[str, Any]] = attrs.get("series") or []
    obs_attrs: list[dict[str, Any]] = attrs.get("observation") or []
    dataset_attrs: list[dict[str, Any]] = attrs.get("dataSet") or []
    time_in_obs = next((i for i, d in enumerate(obs_dims) if d.get("id") in TIME_DIMENSIONS), None)
    if time_in_obs is None:
        return []
    dataset = datasets[0]
    dataset_level = {
        str(a.get("id")): _code(a.get("values") or [], 0) for a in dataset_attrs if a.get("values")
    }

    # (dimension values, attribute values, period label, raw observation)
    records: list[tuple[dict[str, str], dict[str, str], str, Any]] = []

    def decode_obs(
        key: str, raw: Any, base_dims: dict[str, str], base_attrs: dict[str, str]
    ) -> None:
        parts = key.split(":")
        dim_values = dict(base_dims)
        for position, dimension in enumerate(obs_dims):
            if position < len(parts):
                code = _code(dimension.get("values") or [], parts[position])
                if code is not None:
                    dim_values[str(dimension.get("id"))] = code
        obs_attr_values = dict(base_attrs)
        if isinstance(raw, list):
            for position, attribute in enumerate(obs_attrs):
                if position + 1 < len(raw) and raw[position + 1] is not None:
                    code = _code(attribute.get("values") or [], raw[position + 1])
                    if code is not None:
                        obs_attr_values[str(attribute.get("id"))] = code
        label = next((dim_values.pop(t) for t in TIME_DIMENSIONS if t in dim_values), None)
        if label is None:
            if skipped is not None:
                skipped.append(f"observation {key}: no time period")
            return
        records.append((dim_values, obs_attr_values, label, raw))

    for skey, sdata in (dataset.get("series") or {}).items():
        series_values: dict[str, str] = {}
        for position, code_index in enumerate(str(skey).split(":")):
            if position < len(series_dims):
                code = _code(series_dims[position].get("values") or [], code_index)
                if code is not None:
                    series_values[str(series_dims[position].get("id"))] = code
        series_attr_values: dict[str, str] = {}
        for position, code_index in enumerate((sdata or {}).get("attributes") or []):
            if code_index is not None and position < len(series_attrs):
                code = _code(series_attrs[position].get("values") or [], code_index)
                if code is not None:
                    series_attr_values[str(series_attrs[position].get("id"))] = code
        for okey, raw in ((sdata or {}).get("observations") or {}).items():
            decode_obs(str(okey), raw, series_values, series_attr_values)
    for okey, raw in (dataset.get("observations") or {}).items():
        decode_obs(str(okey), raw, {}, {})

    out: list[MacroPoint] = []
    signatures: dict[tuple[str, dt.date], tuple[tuple[str, str], ...]] = {}
    for dim_values, attr_values, label, raw in records:
        geo_code = next((dim_values[g] for g in GEO_DIMENSIONS if g in dim_values), None)
        if expect_geography is not None and geo_code is not None and geo_code != expect_geography:
            if skipped is not None:
                skipped.append(f"area {geo_code} outside the pinned {expect_geography}")
            continue
        point_geo = (
            (geography_map or {}).get(geo_code, geo_code) if geo_code is not None else geography
        )
        try:
            period = parse_period(label)
        except ValueError:
            if skipped is not None:
                skipped.append(f"period {label!r}")
            continue
        value = finite_decimal(raw[0] if isinstance(raw, list) and raw else raw)
        if value is None and skipped is not None:
            skipped.append(f"{point_geo} {label}: no finite value")
        signature = tuple(sorted((k, v) for k, v in dim_values.items() if k not in GEO_DIMENSIONS))
        previous = signatures.setdefault((point_geo, period), signature)
        if previous != signature:
            raise SdmxAmbiguousError(
                f"{series_id}: several series for {point_geo} {label} "
                f"({dict(previous)} vs {dict(signature)}); pin every dimension in the key"
            )
        merged = {**dataset_level, **attr_values}
        lookup = {**dim_values, **merged}
        adjustment = next((lookup[i] for i in ADJUSTMENT_IDS if i in lookup), None)
        transformation = next((lookup[i] for i in TRANSFORMATION_IDS if i in lookup), None)
        freq_code = dim_values.get("FREQ")
        out.append(
            MacroPoint(
                series_id,
                source,
                point_geo,
                period,
                value,
                unit,
                FREQ_CODES.get(freq_code or "", frequency) if freq_code else frequency,
                None,
                vintage,
                source_url,
                {
                    "dimensions": dict(sorted(dim_values.items())),
                    "attributes": dict(sorted(merged.items())),
                    "provider_geography": geo_code,
                    "period_label": label,
                },
                unit_multiplier=sdmx_unit_multiplier(merged.get("UNIT_MULT")),
                seasonal_adjustment=adjustment,
                transformation=transformation,
                release_calendar=SDMX_CALENDAR.get(source),
                geography_definition=_geography_definition(source, dim_values, geo_code, point_geo),
            )
        )
    return out


def _geography_definition(
    source: str, dim_values: dict[str, str], geo_code: str | None, point_geo: str
) -> str | None:
    if geo_code is None:
        return None
    dimension = next((g for g in GEO_DIMENSIONS if g in dim_values), "area")
    suffix = f" (= {point_geo})" if point_geo != geo_code else ""
    return f"{source.upper()} {dimension} code {geo_code}{suffix}"


# ---------------------------------------------------------------------------------------
# UN Comtrade
# ---------------------------------------------------------------------------------------

COMTRADE_FLOWS = {"X": "export", "M": "import", "RX": "re_export", "RM": "re_import"}
BASE_FLOWS = ("export", "import")


@dataclass(frozen=True, slots=True)
class TradePoint:
    reporter: str
    partner: str
    flow: str
    hs_revision: str
    hs_code: str
    period: dt.date
    value: Decimal | None
    quantity: Decimal | None
    quantity_unit: str | None
    net_weight: Decimal | None = None  # kilograms, kept apart from `quantity`
    reporter_iso: str | None = None
    partner_iso: str | None = None
    vintage: str | None = None


def _comtrade_flow(row: dict[str, Any]) -> str | None:
    code = str(row.get("flowCode") or "").strip().upper()
    if code in COMTRADE_FLOWS:
        return COMTRADE_FLOWS[code]
    text = str(row.get("flowDesc") or "").lower().replace("-", " ").replace("_", " ")
    if "re export" in text or "reexport" in text:
        return "re_export"
    if "re import" in text or "reimport" in text:
        return "re_import"
    if "export" in text:
        return "export"
    if "import" in text:
        return "import"
    return None


def parse_comtrade(body: Any, skipped: list[str] | None = None) -> list[TradePoint]:
    rows = body.get("data") if isinstance(body, dict) else None
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        flow = _comtrade_flow(row)
        if flow is None:
            if skipped is not None:
                skipped.append(f"unknown flow {row.get('flowCode') or row.get('flowDesc')!r}")
            continue
        period_text = str(row.get("period") or row.get("refPeriodId") or "")
        try:
            period = parse_period(period_text)
        except ValueError:
            if skipped is not None:
                skipped.append(f"period {period_text!r}")
            continue
        unit = row.get("qtyUnitAbbr")
        quantity = finite_decimal(row.get("qty"))
        if quantity == 0 and str(unit or "N/A").upper() in ("N/A", "NA", ""):
            quantity = None  # a zero quantity with no unit is "not reported"
        reporter, partner = str(row.get("reporterCode")), str(row.get("partnerCode"))
        revision = normalize_hs_revision(row.get("classificationCode") or row.get("typeCode"))
        out.append(
            TradePoint(
                reporter,
                partner,
                flow,
                revision or "HS",
                str(row.get("cmdCode")),
                period,
                finite_decimal(row.get("primaryValue")),
                quantity,
                unit if unit and str(unit).upper() not in ("N/A", "NA") else None,
                net_weight=finite_decimal(row.get("netWgt")),
                reporter_iso=iso2_for_m49(reporter),
                partner_iso=iso2_for_m49(partner),
            )
        )
    return out


def _step_months(periods: set[dt.date], hint: int | None) -> int:
    if hint:
        return hint
    return 12 if periods and all(p.month == 1 and p.day == 1 for p in periods) else 1


def _shift(period: dt.date, months: int) -> dt.date:
    index = period.year * 12 + period.month - 1 - months
    return dt.date(index // 12, index % 12 + 1, 1)


def flow_totals(rows: list[TradePoint], flow: str) -> dict[dt.date, Decimal]:
    """Per period total of one flow: the World partner row when reported, otherwise the sum of
    the country partners. A missing (None) value is excluded, never counted as zero."""
    world: dict[dt.date, Decimal] = {}
    partners: dict[dt.date, Decimal] = {}
    for row in rows:
        if row.flow != flow or row.value is None:
            continue
        if is_world(row.partner, row.partner_iso):
            world[row.period] = world.get(row.period, Decimal(0)) + row.value
        else:
            partners[row.period] = partners.get(row.period, Decimal(0)) + row.value
    return {**partners, **world}


def _concentration(rows: list[TradePoint], flow: str) -> Decimal | None:
    """Herfindahl index over country partners (the World aggregate is not a partner) in the
    latest period that has any."""
    by_period: dict[dt.date, dict[str, Decimal]] = {}
    for row in rows:
        if row.flow != flow or row.value is None or is_world(row.partner, row.partner_iso):
            continue
        shares = by_period.setdefault(row.period, {})
        shares[row.partner] = shares.get(row.partner, Decimal(0)) + row.value
    if not by_period:
        return None
    partners = by_period[max(by_period)]
    total = sum(partners.values(), Decimal(0))
    if not total:
        return None
    return sum(((value / total) ** 2 for value in partners.values()), Decimal(0))


def _growth(totals: dict[dt.date, Decimal], months: int | None) -> Decimal | None:
    """Growth of the latest period against the *adjacent* one only."""
    if not totals:
        return None
    latest = max(totals)
    previous = totals.get(_shift(latest, _step_months(set(totals), months)))
    if previous is None or not previous:
        return None
    return totals[latest] / previous - 1


def _product_mix_change(rows: list[TradePoint], flow: str, months: int | None) -> Decimal | None:
    totals = flow_totals(rows, flow)
    if not totals:
        return None
    latest = max(totals)
    previous_period = _shift(latest, _step_months(set(totals), months))
    if previous_period not in totals:
        return None
    by_period: dict[dt.date, dict[str, Decimal]] = {latest: {}, previous_period: {}}
    has_partner_rows = {
        r.period for r in rows if r.flow == flow and not is_world(r.partner, r.partner_iso)
    }
    for row in rows:
        if row.flow != flow or row.value is None or row.period not in by_period:
            continue
        world = is_world(row.partner, row.partner_iso)
        if world and row.period in has_partner_rows:
            continue  # the partner rows already carry this product
        bucket = by_period[row.period]
        bucket[row.hs_code] = bucket.get(row.hs_code, Decimal(0)) + row.value
    current, before = by_period[latest], by_period[previous_period]
    current_total, before_total = (
        sum(current.values(), Decimal(0)),
        sum(before.values(), Decimal(0)),
    )
    if not current_total or not before_total:
        return None
    codes = current.keys() | before.keys()
    return (
        sum(
            (
                abs(
                    current.get(c, Decimal(0)) / current_total
                    - before.get(c, Decimal(0)) / before_total
                )
                for c in codes
            ),
            Decimal(0),
        )
        / 2
    )


def trade_metrics(
    rows: list[TradePoint], period_months: int | None = None
) -> dict[str, Decimal | None]:
    """National trade trends per flow; company supply dependence is not inferable from these.

    Exports and imports are never blended: growth and partner concentration (HHI over country
    partners, the World aggregate excluded) are reported per flow, growth compares the latest
    period only with the adjacent one, and a missing value is not zero. Re-exports and
    re-imports are not part of the base flows."""
    exports, imports = flow_totals(rows, "export"), flow_totals(rows, "import")
    common = sorted(set(exports) & set(imports))
    export_share = None
    if common:
        latest = common[-1]
        two_way = exports[latest] + imports[latest]
        export_share = exports[latest] / two_way if two_way else None
    return {
        "export_growth": _growth(exports, period_months),
        "import_growth": _growth(imports, period_months),
        "export_partner_concentration": _concentration(rows, "export"),
        "import_partner_concentration": _concentration(rows, "import"),
        # Revealed comparative advantage needs world totals and GDP to describe dependence.
        "revealed_trade_dependence": None,
        "export_share_of_two_way_trade": export_share,
        "product_mix_change": _product_mix_change(rows, "export", period_months),
    }


# ---------------------------------------------------------------------------------------
# ALFRED / FRED real-time vintages
# ---------------------------------------------------------------------------------------


def parse_fred_vintages(
    body: dict[str, Any],
    *,
    series_id: str,
    unit: str,
    source_url: str,
    frequency: str | None = None,
    skipped: list[str] | None = None,
) -> list[MacroPoint]:
    """Normalize ALFRED `output_type=1` rows (one row per value and real-time window) without
    treating fetch time as release time. The vintage is the window's start (the day that value
    became the published one); the window end moves every day for an open window and is kept
    in the metadata only, so re-fetching never creates a second vintage."""
    out = []
    for row in body.get("observations") or []:
        realtime_start = row.get("realtime_start")
        if not realtime_start or not row.get("date"):
            continue
        try:
            released = dt.datetime.combine(parse_period(realtime_start), dt.time(), dt.UTC)
            period = parse_period(row["date"])
        except ValueError:
            if skipped is not None:
                skipped.append(f"{row.get('date')} / {realtime_start}")
            continue
        out.append(
            MacroPoint(
                series_id,
                "alfred",
                "US",
                period,
                finite_decimal(row.get("value")),
                unit,
                frequency or str(body.get("frequency_short") or "provider_frequency"),
                released,
                str(realtime_start),
                source_url,
                {"realtime_end": row.get("realtime_end")},
            )
        )
    return out


__all__ = [
    "ISO3_TO_ISO2",
    "MacroPoint",
    "SdmxAmbiguousError",
    "TradePoint",
    "finite_decimal",
    "frequency_of",
    "parse_comtrade",
    "parse_fred_vintages",
    "parse_period",
    "parse_sdmx_json",
    "parse_world_bank",
    "sdmx_url",
    "trade_metrics",
    "world_bank_url",
]
