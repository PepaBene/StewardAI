"""Write macro and trade observations without re-inserting what is already stored (B10).

An observation is identified by its series, source, geography and period (trade: reporter,
partner, flow, HS revision and code, period). A fetch that returns the value already stored
inserts nothing, whatever the provider vintage label; a different value is a *revision*: a new
row with `revision` one higher, `known_at` = the fetch time and the provider's own vintage
label. ALFRED rows are different: each real-time window is a vintage the provider published,
so they are keyed by (period, vintage). Rows go in with one `executemany` per series.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import asyncpg

from steward.macro.providers import MacroPoint, TradePoint


@dataclass(frozen=True, slots=True)
class MacroRow:
    series_id: str
    source: str
    geography: str
    period: dt.date
    value: Decimal | None
    unit: str
    frequency: str
    release_at: dt.datetime | None
    known_at: dt.datetime
    vintage: str
    source_url: str
    metadata: dict[str, Any]
    unit_multiplier: Decimal = Decimal(1)
    seasonal_adjustment: str | None = None
    transformation: str | None = None

    @classmethod
    def from_point(
        cls, point: MacroPoint, known_at: dt.datetime, *, vintage: str | None = None
    ) -> MacroRow:
        return cls(
            point.series_id,
            point.source,
            point.geography,
            point.period,
            point.value,
            point.unit,
            point.frequency,
            point.release_at,
            known_at,
            vintage or point.vintage or known_at.date().isoformat(),
            point.source_url,
            point.metadata,
            point.unit_multiplier,
            point.seasonal_adjustment,
            point.transformation,
        )


@dataclass(frozen=True, slots=True)
class StoreResult:
    inserted: int = 0
    revised: int = 0
    unchanged: int = 0
    rows: tuple[MacroRow, ...] = ()  # the rows actually written (new periods and revisions)

    @property
    def written(self) -> int:
        return self.inserted + self.revised


MACRO_INSERT = """insert into public.macro_observation
   (series_id,source,geography,period,value,unit,unit_multiplier,frequency,seasonal_adjustment,
    release_at,known_at,vintage,revision,transformation,source_url,metadata)
   values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16::jsonb)
   on conflict do nothing"""


async def store_macro_rows(
    pool: asyncpg.Pool, rows: Sequence[MacroRow], *, vintage_keyed: bool = False
) -> StoreResult:
    """Insert new periods and value revisions; skip rows whose value is already stored.

    Rows without a value are not stored (a gap is reported in coverage, not as a null
    observation). `vintage_keyed` (ALFRED) keeps every provider vintage instead of comparing
    values: a (period, vintage) already stored is skipped."""
    if vintage_keyed:
        vintages: dict[tuple[str, str, str], list[MacroRow]] = {}
        for row in rows:
            if row.value is not None:
                vintages.setdefault((row.series_id, row.source, row.geography), []).append(row)
        return await _store_vintages(pool, vintages)
    groups: dict[tuple[str, str, str], dict[dt.date, MacroRow]] = {}
    for row in rows:
        if row.value is not None:
            # Within one batch the last row for a period wins.
            groups.setdefault((row.series_id, row.source, row.geography), {})[row.period] = row
    inserted = revised = unchanged = 0
    written: list[MacroRow] = []
    for (series_id, source, geography), by_period in groups.items():
        latest = {
            r["period"]: r
            for r in await pool.fetch(
                """select distinct on (period) period, value, revision
                   from public.macro_observation
                   where series_id=$1 and source=$2 and geography=$3 and period = any($4::date[])
                   order by period, revision desc, known_at desc""",
                series_id,
                source,
                geography,
                list(by_period),
            )
        }
        args: list[tuple[Any, ...]] = []
        for period, row in sorted(by_period.items()):
            prior = latest.get(period)
            if prior is not None and prior["value"] == row.value:
                unchanged += 1
                continue
            revision = 0 if prior is None else prior["revision"] + 1
            metadata = dict(row.metadata)
            if prior is not None:
                revised += 1
                metadata["revised_from"] = str(prior["value"])
            else:
                inserted += 1
            args.append(_args(row, revision, metadata))
            written.append(row)
        if args:
            await pool.executemany(MACRO_INSERT, args)
    return StoreResult(inserted, revised, unchanged, tuple(written))


async def _store_vintages(
    pool: asyncpg.Pool, groups: dict[tuple[str, str, str], list[MacroRow]]
) -> StoreResult:
    inserted = unchanged = 0
    written: list[MacroRow] = []
    for (series_id, source, geography), rows in groups.items():
        known = await pool.fetch(
            """select period, vintage, revision from public.macro_observation
               where series_id=$1 and source=$2 and geography=$3 and period = any($4::date[])""",
            series_id,
            source,
            geography,
            sorted({r.period for r in rows}),
        )
        seen = {(r["period"], r["vintage"]) for r in known}
        top: dict[dt.date, int] = {}
        for r in known:
            top[r["period"]] = max(top.get(r["period"], -1), r["revision"])
        args: list[tuple[Any, ...]] = []
        for row in sorted(rows, key=lambda r: (r.period, r.vintage)):
            if (row.period, row.vintage) in seen:
                unchanged += 1
                continue
            seen.add((row.period, row.vintage))
            top[row.period] = top.get(row.period, -1) + 1
            args.append(_args(row, top[row.period], row.metadata))
            written.append(row)
            inserted += 1
        if args:
            await pool.executemany(MACRO_INSERT, args)
    return StoreResult(inserted, 0, unchanged, tuple(written))


def _args(row: MacroRow, revision: int, metadata: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.series_id,
        row.source,
        row.geography,
        row.period,
        row.value,
        row.unit,
        row.unit_multiplier,
        row.frequency,
        row.seasonal_adjustment,
        row.release_at,
        row.known_at,
        row.vintage,
        revision,
        row.transformation,
        row.source_url,
        json.dumps(metadata, default=str),
    )


TRADE_INSERT = """insert into public.trade_observation
   (reporter,partner,flow,hs_revision,hs_code,period,trade_value,quantity,quantity_unit,
    net_weight,trade_currency,known_at,source_url,raw,reporter_iso,partner_iso,vintage,revision)
   values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'USD',$11,$12,$13::jsonb,$14,$15,$16,$17)
   on conflict do nothing"""


async def store_trade_points(
    pool: asyncpg.Pool, points: Sequence[TradePoint], known_at: dt.datetime, source_url: str
) -> StoreResult:
    """Trade rows with the same dedupe/revision rule as `store_macro_rows`: an unchanged value
    and quantity inserts nothing, a changed one is the next revision."""
    latest: dict[tuple[str, str, str, str, str], dict[dt.date, asyncpg.Record]] = {}
    keyed: dict[tuple[str, str, str, str, str], dict[dt.date, TradePoint]] = {}
    for point in points:
        if point.value is None and point.quantity is None and point.net_weight is None:
            continue
        key = (point.reporter, point.partner, point.flow, point.hs_revision, point.hs_code)
        keyed.setdefault(key, {})[point.period] = point
    inserted = revised = unchanged = 0
    args: list[tuple[Any, ...]] = []
    for key, by_period in keyed.items():
        rows = await pool.fetch(
            """select distinct on (period) period, trade_value, quantity, net_weight, revision
               from public.trade_observation
               where reporter=$1 and partner=$2 and flow=$3 and hs_revision=$4 and hs_code=$5
                 and period = any($6::date[])
               order by period, revision desc, known_at desc""",
            *key,
            list(by_period),
        )
        latest[key] = {r["period"]: r for r in rows}
        for period, point in sorted(by_period.items()):
            prior = latest[key].get(period)
            if (
                prior is not None
                and prior["trade_value"] == point.value
                and prior["quantity"] == point.quantity
                and prior["net_weight"] == point.net_weight
            ):
                unchanged += 1
                continue
            raw: dict[str, Any] = {"national_trade_not_issuer_contract": True}
            if prior is not None:
                revised += 1
                raw["revised_from"] = str(prior["trade_value"])
            else:
                inserted += 1
            args.append(
                (
                    point.reporter,
                    point.partner,
                    point.flow,
                    point.hs_revision,
                    point.hs_code,
                    point.period,
                    point.value,
                    point.quantity,
                    point.quantity_unit,
                    point.net_weight,
                    known_at,
                    source_url,
                    json.dumps(raw),
                    point.reporter_iso,
                    point.partner_iso,
                    point.vintage or known_at.date().isoformat(),
                    0 if prior is None else prior["revision"] + 1,
                )
            )
    if args:
        await pool.executemany(TRADE_INSERT, args)
    return StoreResult(inserted, revised, unchanged)


async def upsert_series_metadata(
    pool: asyncpg.Pool,
    *,
    source: str,
    series_id: str,
    label: str,
    definition: str,
    frequency: str,
    unit: str,
    consumer: str,
    unit_multiplier: Decimal = Decimal(1),
    seasonal_adjustment: str | None = None,
    transformation: str | None = None,
    geography_definition: str | None = None,
    release_calendar: str | None = None,
    code_lists: dict[str, Any] | None = None,
) -> None:
    """The D2.1 semantic record of a series; later non-null values replace earlier ones, a
    missing attribute never erases a known one."""
    await pool.execute(
        """insert into public.macro_series_metadata
           (source,series_id,label,definition,frequency,unit,unit_multiplier,seasonal_adjustment,
            transformation,geography_definition,release_calendar,code_lists,consumer)
           values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13)
           on conflict(source,series_id) do update set label=excluded.label,
             definition=excluded.definition, frequency=excluded.frequency, unit=excluded.unit,
             unit_multiplier=excluded.unit_multiplier,
             seasonal_adjustment=coalesce(excluded.seasonal_adjustment,
                                          macro_series_metadata.seasonal_adjustment),
             transformation=coalesce(excluded.transformation,
                                     macro_series_metadata.transformation),
             geography_definition=coalesce(excluded.geography_definition,
                                           macro_series_metadata.geography_definition),
             release_calendar=coalesce(excluded.release_calendar,
                                       macro_series_metadata.release_calendar),
             code_lists=case when excluded.code_lists = '{}'::jsonb
                             then macro_series_metadata.code_lists else excluded.code_lists end,
             consumer=excluded.consumer""",
        source,
        series_id,
        label,
        definition,
        frequency,
        unit,
        unit_multiplier,
        seasonal_adjustment,
        transformation,
        geography_definition,
        release_calendar,
        json.dumps(code_lists or {}),
        consumer,
    )
