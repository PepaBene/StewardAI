"""Parsers and endpoint builders for the phase-2 public macro providers.

Transport remains injectable so fixtures can exercise normalization without network access.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote


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


def _date(value: str) -> dt.date:
    if len(value) == 4:
        return dt.date(int(value), 1, 1)
    if "-Q" in value:
        year, quarter = value.split("-Q")
        return dt.date(int(year), (int(quarter) - 1) * 3 + 1, 1)
    if len(value) == 7:
        return dt.date.fromisoformat(value + "-01")
    return dt.date.fromisoformat(value[:10])


def _decimal(value: Any) -> Decimal | None:
    try:
        return None if value in (None, "", "..") else Decimal(str(value))
    except InvalidOperation:
        return None


def world_bank_url(country: str, indicators: list[str], start: int, end: int) -> str:
    ids = ";".join(indicators)
    return f"https://api.worldbank.org/v2/country/{quote(country)}/indicator/{quote(ids)}?format=json&per_page=20000&date={start}:{end}"


def parse_world_bank(body: list[Any], source_url: str) -> list[MacroPoint]:
    rows = body[1] if len(body) > 1 and isinstance(body[1], list) else []
    out: list[MacroPoint] = []
    for row in rows:
        indicator = row.get("indicator") or {}
        # Preserve the requested country code (normally ISO alpha-2) so observations
        # reconcile with portfolio and exposure mappings; retain ISO-3 as metadata.
        country = (row.get("country") or {}).get("id") or row.get("countryiso3code")
        if not indicator.get("id") or not country or not row.get("date"):
            continue
        out.append(
            MacroPoint(
                indicator["id"],
                "world_bank",
                country,
                _date(row["date"]),
                _decimal(row.get("value")),
                row.get("unit") or "provider_unit",
                "annual",
                None,
                str(row.get("lastupdated") or "latest"),
                source_url,
                {
                    "indicator_name": indicator.get("value"),
                    "obs_status": row.get("obs_status"),
                    "country_iso3": row.get("countryiso3code"),
                },
            )
        )
    return out


def sdmx_url(base: str, flow: str, key: str, start: str | None = None) -> str:
    url = f"{base.rstrip('/')}/data/{quote(flow, safe=',')}/{quote(key, safe='.+')}"
    return url + (f"?startPeriod={quote(start)}" if start else "")


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
) -> list[MacroPoint]:
    """Parse SDMX-JSON 2.x observations using the response's time dimension."""
    data = body.get("data") or body
    structures = data.get("structures") or []
    datasets = data.get("dataSets") or []
    if not structures or not datasets:
        return []
    dimensions = structures[0].get("dimensions", {}).get("observation", [])
    time_dim = next((d for d in dimensions if d.get("id") in ("TIME_PERIOD", "TIME")), None)
    if time_dim is None:
        return []
    periods = [v.get("id") for v in time_dim.get("values", [])]
    observations = datasets[0].get("observations", {})
    out = []
    for key, raw in observations.items():
        index = int(str(key).split(":")[-1])
        if index >= len(periods):
            continue
        value = raw[0] if isinstance(raw, list) else raw
        out.append(
            MacroPoint(
                series_id,
                source,
                geography,
                _date(periods[index]),
                _decimal(value),
                unit,
                frequency,
                None,
                vintage,
                source_url,
                {},
            )
        )
    return out


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


def parse_comtrade(body: dict[str, Any]) -> list[TradePoint]:
    out = []
    for row in body.get("data") or []:
        flow_text = str(row.get("flowDesc") or "").lower()
        flow = "export" if "export" in flow_text else "import" if "import" in flow_text else None
        if flow is None:
            continue
        period = str(row.get("period") or row.get("refPeriodId") or "")
        if len(period) == 6:
            period = period[:4] + "-" + period[4:]
        out.append(
            TradePoint(
                str(row.get("reporterCode")),
                str(row.get("partnerCode")),
                flow,
                str(row.get("classificationCode") or row.get("typeCode") or "HS"),
                str(row.get("cmdCode")),
                _date(period),
                _decimal(row.get("primaryValue")),
                _decimal(row.get("netWgt") or row.get("qty")),
                row.get("qtyUnitAbbr"),
            )
        )
    return out


def trade_metrics(rows: list[TradePoint]) -> dict[str, Decimal | None]:
    """National trade trends; company supply dependence is not inferable from these rows."""
    totals: dict[dt.date, Decimal] = {}
    latest_by_partner: dict[str, Decimal] = {}
    for row in rows:
        value = row.value or Decimal(0)
        totals[row.period] = totals.get(row.period, Decimal(0)) + value
    if not totals:
        return {
            "growth": None,
            "partner_concentration": None,
            "revealed_trade_dependence": None,
            "export_share_of_two_way_trade": None,
            "product_mix_change": None,
        }
    latest = max(totals)
    for row in rows:
        if row.period == latest:
            latest_by_partner[row.partner] = latest_by_partner.get(row.partner, Decimal(0)) + (
                row.value or Decimal(0)
            )
    total = sum(latest_by_partner.values(), Decimal(0))
    concentration = (
        sum(((value / total) ** 2 for value in latest_by_partner.values()), Decimal(0))
        if total
        else None
    )
    previous_dates = sorted((date for date in totals if date < latest), reverse=True)
    previous = totals[previous_dates[0]] if previous_dates else Decimal(0)
    growth = (totals[latest] / previous - 1) if previous else None
    latest_flows: dict[str, Decimal] = {}
    for row in rows:
        if row.period == latest:
            latest_flows[row.flow] = latest_flows.get(row.flow, Decimal(0)) + (
                row.value or Decimal(0)
            )
    flow_total = sum(latest_flows.values(), Decimal(0))
    export_share = latest_flows.get("export", Decimal(0)) / flow_total if flow_total else None

    product_mix_change = None
    if previous_dates and previous:
        previous_date = previous_dates[0]
        current_products: dict[str, Decimal] = {}
        previous_products: dict[str, Decimal] = {}
        for row in rows:
            target = current_products if row.period == latest else previous_products
            if row.period in (latest, previous_date):
                target[row.hs_code] = target.get(row.hs_code, Decimal(0)) + (
                    row.value or Decimal(0)
                )
        codes = current_products.keys() | previous_products.keys()
        product_mix_change = (
            sum(
                (
                    abs(
                        current_products.get(code, Decimal(0)) / totals[latest]
                        - previous_products.get(code, Decimal(0)) / previous
                    )
                    for code in codes
                ),
                Decimal(0),
            )
            / 2
        )
    return {
        "growth": growth,
        "partner_concentration": concentration,
        # Revealed comparative advantage needs world totals and GDP to describe dependence.
        "revealed_trade_dependence": None,
        "export_share_of_two_way_trade": export_share,
        "product_mix_change": product_mix_change,
    }


def parse_fred_vintages(
    body: dict[str, Any], *, series_id: str, unit: str, source_url: str
) -> list[MacroPoint]:
    """Normalize ALFRED/FRED output_type=4 rows without treating fetch time as release time."""
    out = []
    for row in body.get("observations") or []:
        realtime_start = row.get("realtime_start")
        if not realtime_start or not row.get("date"):
            continue
        released = dt.datetime.combine(_date(realtime_start), dt.time(), dt.UTC)
        out.append(
            MacroPoint(
                series_id,
                "alfred",
                "US",
                _date(row["date"]),
                _decimal(row.get("value")),
                unit,
                str(body.get("frequency_short") or "provider_frequency"),
                released,
                f"{realtime_start}/{row.get('realtime_end') or ''}",
                source_url,
                {"realtime_end": row.get("realtime_end")},
            )
        )
    return out
