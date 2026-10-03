"""Measured figures for the free-data plan's release targets (price, reach, provenance, identity).

Every number is computed from stored rows (`price_bar`, `fundamentals`, `financial_statement_fact`,
`coverage_status`, `instrument`, `position_snapshot`, the identity decision tables...). Nothing is
extrapolated: a sample is never turned into a global percentage, a missing value is counted as
missing, and the denominator is returned beside every share.
"""

import datetime as dt
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.reference.calendar import expected_sessions
from steward.reference.price_series import SeriesRef, holding_symbols, series_mic
from steward.sources.esma import MIC_COUNTRY
from steward.sources.market import major_currency, unit_factor

PRICE_TARGET = 0.95
PROVENANCE_TARGET = 1.0
IDENTITY_TARGET = 1.0
DEFAULT_MAX_STALE_SESSIONS = 3

# ---------------------------------------------------------------------------------------
# (1) Value-weighted price coverage
# ---------------------------------------------------------------------------------------
PriceState = Literal["usable", "stale", "no_price", "basis_unknown"]


class HoldingPriceCoverage(BaseModel):
    ticker: str
    name: str | None
    value_base: float = Field(description="Position value in the base currency (snapshot).")
    weight: float | None = Field(description="Snapshot weight (fraction, account weight basis).")
    share_of_held_value: float | None = Field(
        description="value_base / denominator_base (fraction); null when the denominator is 0."
    )
    state: PriceState = Field(
        description="usable: a stored adjusted close within the staleness limit with an explicit "
        "currency; stale: older than the limit; no_price: no stored usable close; "
        "basis_unknown: a close but no currency recorded."
    )
    symbol: str | None = Field(description="Provider symbol of the chosen price series.")
    source: str | None
    last_close_date: dt.date | None
    last_close: float | None = Field(description="In the quote unit (pence for GBX), not major.")
    currency: str | None = Field(description="Quote code as stored; GBX/GBp are pence.")
    quote_unit: str | None
    currency_major: str | None = Field(description="ISO currency behind the quote code (GBX->GBP).")
    unit_factor: float | None = Field(description="Major units per quoted unit (0.01 for pence).")
    adjustment_basis: str | None = Field(description="Stored bar type: adjusted or raw.")
    stale_sessions: int | None = Field(
        description="Exchange sessions after the last close up to today (calendar-aware)."
    )
    calendar_basis: str | None
    reason: str


class PriceCoverage(BaseModel):
    snapshot_date: dt.date | None = Field(description="Latest position snapshot; null if none.")
    as_of: dt.date = Field(description="Date staleness is judged against.")
    base_currency: str | None
    denominator_base: float = Field(
        description="Sum of held market value in the base currency (the denominator)."
    )
    usable_value_base: float
    unknown_value_base: float = Field(
        description="Value with no usable close (stale, no price or basis unknown). A separate "
        "bucket: never blended into a return."
    )
    usable_share: float | None = Field(
        description="usable_value_base / denominator_base, a fraction; null when empty."
    )
    unknown_share: float | None
    target: float = Field(description="The plan's release target, a fraction (0.95).")
    meets_target: bool
    max_stale_sessions: int = Field(description="A close older than this many sessions is stale.")
    holdings_total: int
    holdings_usable: int
    holdings_stale: int
    holdings_no_price: int
    holdings_basis_unknown: int
    holdings: list[HoldingPriceCoverage]


async def price_coverage(
    pool: asyncpg.Pool,
    today: dt.date | None = None,
    max_stale_sessions: int = DEFAULT_MAX_STALE_SESSIONS,
) -> PriceCoverage:
    today = today or dt.datetime.now(dt.UTC).date()
    snap = await pool.fetchval("select max(snapshot_date) from public.position_snapshot")
    positions = (
        await pool.fetch(
            "select p.ticker, i.name, i.home_mic, p.value_base, p.weight, p.base_currency"
            " from public.position_snapshot p left join public.instrument i"
            " on i.t212_ticker = p.ticker where p.snapshot_date = $1"
            " order by p.value_base desc, p.ticker",
            snap,
        )
        if snap
        else []
    )
    jobs = await load_active(pool, "jobs", JobsConfig)
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    calendar = (jobs.body if jobs else JobsConfig()).calendar
    tickers = [p["ticker"] for p in positions]
    symbols = await holding_symbols(
        pool, tickers, profiles.body if profiles else HoldingProfilesConfig()
    )
    refs = sorted(
        {r for s in symbols.values() for r in s.refs()}, key=lambda r: (r.symbol, r.source)
    )
    latest = await _latest_bars(pool, refs)
    denominator = sum((Decimal(p["value_base"]) for p in positions), Decimal(0))
    rows: list[HoldingPriceCoverage] = []
    exact_values: list[Decimal] = []  # exact per-row values: the buckets are summed in Decimal
    for p in positions:
        value = Decimal(p["value_base"])
        exact_values.append(value)
        best: tuple[SeriesRef, asyncpg.Record] | None = None
        for ref in symbols[p["ticker"]].refs():
            bar = latest.get((ref.symbol, ref.source))
            # Newest close wins; the source order breaks ties (refs() is in that order).
            if bar is not None and (best is None or bar["day"] > best[1]["day"]):
                best = (ref, bar)
        row = HoldingPriceCoverage(
            ticker=p["ticker"],
            name=p["name"],
            value_base=float(value),
            weight=float(p["weight"]) if p["weight"] is not None else None,
            share_of_held_value=float(value / denominator) if denominator else None,
            state="no_price",
            symbol=None,
            source=None,
            last_close_date=None,
            last_close=None,
            currency=None,
            quote_unit=None,
            currency_major=None,
            unit_factor=None,
            adjustment_basis=None,
            stale_sessions=None,
            calendar_basis=None,
            reason="no stored adjusted close for any of the holding's price symbols",
        )
        if best is not None:
            ref, bar = best
            mic = bar["mic"] or p["home_mic"] or series_mic(ref.symbol, ref.source)
            after = await expected_sessions(
                pool, calendar, mic, bar["day"] + dt.timedelta(days=1), today
            )
            row.symbol, row.source = ref.symbol, ref.source
            row.last_close_date, row.last_close = bar["day"], float(bar["close"])
            row.currency, row.quote_unit = bar["currency"], bar["quote_unit"]
            row.currency_major = major_currency(bar["currency"])
            row.unit_factor = float(unit_factor(bar["currency"])) if bar["currency"] else None
            row.adjustment_basis = bar["bar_type"]
            row.stale_sessions, row.calendar_basis = after.count, after.basis
            if not bar["currency"]:
                row.state = "basis_unknown"
                row.reason = "a close is stored but its currency is not recorded"
            elif after.count > max_stale_sessions:
                row.state = "stale"
                row.reason = (
                    f"last close is {after.count} sessions old (limit {max_stale_sessions})"
                )
            else:
                row.state = "usable"
                row.reason = (
                    f"{bar['bar_type']} close in {bar['currency']}, {after.count} sessions old"
                )
        rows.append(row)
    # Exact Decimal buckets: the unknown value is the sum of the non-usable rows, so it can never
    # be float residue (-8.9e-14) when everything is usable.
    usable = sum(
        (v for v, r in zip(exact_values, rows, strict=True) if r.state == "usable"), Decimal(0)
    )
    unknown = sum(
        (v for v, r in zip(exact_values, rows, strict=True) if r.state != "usable"), Decimal(0)
    )
    share = float(usable / denominator) if denominator else None
    counts = Counter(r.state for r in rows)
    return PriceCoverage(
        snapshot_date=snap,
        as_of=today,
        base_currency=positions[0]["base_currency"] if positions else None,
        denominator_base=float(denominator),
        usable_value_base=float(usable),
        unknown_value_base=float(unknown),
        usable_share=share,
        unknown_share=float(unknown / denominator) if denominator else None,
        target=PRICE_TARGET,
        meets_target=share is not None and share >= PRICE_TARGET,
        max_stale_sessions=max_stale_sessions,
        holdings_total=len(rows),
        holdings_usable=counts["usable"],
        holdings_stale=counts["stale"],
        holdings_no_price=counts["no_price"],
        holdings_basis_unknown=counts["basis_unknown"],
        holdings=rows,
    )


async def _latest_bars(
    pool: asyncpg.Pool, refs: list[SeriesRef]
) -> dict[tuple[str, str], asyncpg.Record]:
    if not refs:
        return {}
    rows = await pool.fetch(
        """select distinct on (symbol, source) symbol, source, coalesce(session_date, date) as day,
                  close, currency, quote_unit, bar_type, mic
           from public.price_bar
           where (symbol, source) in (select * from unnest($1::text[], $2::text[]))
             and bar_type = 'adjusted' and access_state = 'available'
           order by symbol, source, coalesce(session_date, date) desc, fetched_at desc""",
        [r.symbol for r in refs],
        [r.source for r in refs],
    )
    return {(r["symbol"], r["source"]): r for r in rows}


# ---------------------------------------------------------------------------------------
# (2) Global reach
# ---------------------------------------------------------------------------------------
AssetClass = Literal["security", "etf", "fund", "index", "unknown"]
UNIVERSE_NOTE = (
    "The universe is this app's held, watchlist and tested symbols only (symbols with at least "
    "one stored price, fundamentals, statement-fact or coverage-ledger row). Every figure counts "
    "stored rows; none is a share of a global or exchange-wide universe, and no sample is "
    "extrapolated."
)


class ReachRow(BaseModel):
    exchange_mic: str | None = Field(description="Null: not part of this grouping, or unknown.")
    country: str | None
    asset_class: AssetClass | None = Field(description="Null when not part of this grouping.")
    metric: str = Field(
        description="price | fundamentals | statement_facts | coverage:<dataset> (ledger)."
    )
    tested_symbols: int = Field(description="Distinct symbols/tickers with a stored row.")
    valid_records: int = Field(
        description="Stored rows usable as data: available price bars, fundamentals without a "
        "fetch error, statement facts with a value, ledger rows in state available."
    )
    history_first: dt.date | None
    history_last: dt.date | None
    history_count: int = Field(description="Records spanning the history (periods or bars).")
    freshest_known_at: dt.datetime | None = Field(
        description="Newest known_at (price_bar/fundamentals: fetched_at; ledger: checked_at)."
    )
    coverage_states: dict[str, int] = Field(
        description="Symbols by state: price = access_state of the newest bar; ledger = state of "
        "each entry; fundamentals and statement facts have no state column (empty)."
    )


class DataReach(BaseModel):
    universe_note: str
    as_of: dt.datetime
    held_tickers: int
    watchlist_tickers: int
    tested_symbols: int = Field(description="Distinct symbols with any stored row.")
    cells: list[ReachRow] = Field(description="Exchange x country x asset class x metric.")
    by_exchange: list[ReachRow]
    by_country: list[ReachRow]
    by_asset_class: list[ReachRow]


@dataclass
class _Acc:
    symbols: set[str] = field(default_factory=set)
    valid: int = 0
    first: dt.date | None = None
    last: dt.date | None = None
    count: int = 0
    newest: dt.datetime | None = None
    states: Counter[str] = field(default_factory=Counter)

    def add(
        self,
        symbol: str,
        *,
        valid: int,
        count: int,
        first: dt.date | None,
        last: dt.date | None,
        newest: dt.datetime | None,
        states: dict[str, int],
    ) -> None:
        self.symbols.add(symbol)
        self.valid += valid
        self.count += count
        if first is not None and (self.first is None or first < self.first):
            self.first = first
        if last is not None and (self.last is None or last > self.last):
            self.last = last
        if newest is not None and (self.newest is None or newest > self.newest):
            self.newest = newest
        self.states.update(states)

    def row(
        self, metric: str, mic: str | None, country: str | None, cls: AssetClass | None
    ) -> ReachRow:
        return ReachRow(
            exchange_mic=mic,
            country=country,
            asset_class=cls,
            metric=metric,
            tested_symbols=len(self.symbols),
            valid_records=self.valid,
            history_first=self.first,
            history_last=self.last,
            history_count=self.count,
            freshest_known_at=self.newest,
            coverage_states=dict(sorted(self.states.items())),
        )


def asset_class(security_type: str | None, instrument_type: str | None, symbol: str) -> AssetClass:
    kind = (security_type or "").upper()
    if symbol.startswith("^") or "INDEX" in kind:
        return "index"
    if any(k in kind for k in ("ETP", "ETF", "ETN")):
        return "etf"
    if "FUND" in kind or "MUTUAL" in kind:
        return "fund"
    if kind:
        return "security"
    itype = (instrument_type or "").upper()
    if itype == "ETF":
        return "etf"
    if itype in ("STOCK", "EQUITY"):
        return "security"
    return "unknown"


Attribution = tuple[str | None, str | None, AssetClass]


async def _attribution_index(
    pool: asyncpg.Pool,
    canonical: dict[str, str],
) -> tuple[dict[str, Attribution], dict[str, Attribution]]:
    """Attribution (mic, country, class) of a ticker, and of every provider price symbol."""
    rows = await pool.fetch(
        "select t212_ticker, security_type, instrument_type, home_mic, home_country"
        " from public.instrument"
    )
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    tickers = [r["t212_ticker"] for r in rows]
    symbols = await holding_symbols(
        pool, tickers, profiles.body if profiles else HoldingProfilesConfig()
    )
    by_ticker: dict[str, Attribution] = {}
    by_symbol: dict[str, Attribution] = {}
    canonical.clear()
    for r in rows:
        mic = r["home_mic"]
        country = r["home_country"] or MIC_COUNTRY.get(mic or "")
        attr: Attribution = (
            mic,
            country,
            asset_class(r["security_type"], r["instrument_type"], r["t212_ticker"]),
        )
        by_ticker[r["t212_ticker"]] = attr
        for ref in symbols[r["t212_ticker"]].refs():
            by_symbol.setdefault(ref.symbol.lower(), attr)
            canonical.setdefault(ref.symbol.lower(), r["t212_ticker"])
    return by_ticker, by_symbol


async def data_reach(pool: asyncpg.Pool, now: dt.datetime | None = None) -> DataReach:
    now = now or dt.datetime.now(dt.UTC)
    canonical: dict[str, str] = {}  # provider symbol (lower) -> instrument ticker
    by_ticker, by_symbol = await _attribution_index(pool, canonical)

    def resolve(key: str, mic_hint: str | None = None) -> Attribution:
        attr = by_ticker.get(key) or by_symbol.get(key.lower())
        if attr is not None:
            mic = attr[0] or mic_hint
            return (mic, attr[1] or MIC_COUNTRY.get(mic or ""), attr[2])
        mic = mic_hint
        kind: AssetClass = "index" if key.startswith("^") else "unknown"
        return (mic, MIC_COUNTRY.get(mic or ""), kind)

    cells: dict[tuple[str | None, str | None, AssetClass, str], _Acc] = {}

    def put(metric: str, key: str, mic_hint: str | None, **kw: object) -> None:
        mic, country, cls = resolve(key, mic_hint)
        # One instrument is one tested symbol, whatever provider symbol a row is stored under.
        entity = key if key in by_ticker else canonical.get(key.lower(), key)
        cells.setdefault((mic, country, cls, metric), _Acc()).add(entity, **kw)  # type: ignore[arg-type]

    for r in await pool.fetch(
        """select symbol, count(*) as total,
                  count(*) filter (where access_state = 'available') as valid,
                  min(coalesce(session_date, date))
                    filter (where access_state = 'available') as first,
                  max(coalesce(session_date, date))
                    filter (where access_state = 'available') as last,
                  max(fetched_at) as newest,
                  (array_agg(access_state order by coalesce(session_date, date) desc,
                             fetched_at desc))[1] as state,
                  (array_agg(mic order by coalesce(session_date, date) desc)
                     filter (where mic is not null))[1] as mic
           from public.price_bar group by symbol"""
    ):
        put(
            "price",
            r["symbol"],
            r["mic"] or series_mic(r["symbol"], "yahoo"),
            valid=r["valid"],
            count=r["valid"],
            first=r["first"],
            last=r["last"],
            newest=r["newest"],
            states={r["state"]: 1},
        )
    for r in await pool.fetch(
        """select ticker, count(*) filter (where last_error is null) as valid,
                  min(period_end) as first, max(period_end) as last, max(fetched_at) as newest,
                  count(*) as total
           from public.fundamentals group by ticker"""
    ):
        put(
            "fundamentals",
            r["ticker"],
            None,
            valid=r["valid"],
            count=r["total"],
            first=r["first"],
            last=r["last"],
            newest=r["newest"],
            states={},
        )
    for r in await pool.fetch(
        """select coalesce(ticker, 'cik:' || cik) as key,
                  count(*) filter (where value is not null or value_text is not null) as valid,
                  count(distinct period_end) as periods, min(period_end) as first,
                  max(period_end) as last, max(known_at) as newest
           from public.financial_statement_fact
           where coalesce(ticker, cik) is not null group by 1"""
    ):
        put(
            "statement_facts",
            r["key"],
            None,
            valid=r["valid"],
            count=r["periods"],
            first=r["first"],
            last=r["last"],
            newest=r["newest"],
            states={},
        )
    for r in await pool.fetch(
        """select dataset, entity_key, state, available_periods, earliest_period, latest_period,
                  checked_at from public.coverage_status"""
    ):
        put(
            f"coverage:{r['dataset']}",
            r["entity_key"],
            None,
            valid=1 if r["state"] == "available" else 0,
            count=r["available_periods"] or 0,
            first=r["earliest_period"],
            last=r["latest_period"],
            newest=r["checked_at"],
            states={r["state"]: 1},
        )

    def rollup(pick: int) -> list[ReachRow]:
        merged: dict[tuple[object, str], _Acc] = {}
        for key, acc in cells.items():
            target = merged.setdefault((key[pick], key[3]), _Acc())
            target.symbols |= acc.symbols
            target.add(
                "",
                valid=acc.valid,
                count=acc.count,
                first=acc.first,
                last=acc.last,
                newest=acc.newest,
                states=dict(acc.states),
            )
            target.symbols.discard("")
        out = []
        for (value, metric), acc in merged.items():
            out.append(
                acc.row(
                    metric,
                    value if pick == 0 else None,  # type: ignore[arg-type]
                    value if pick == 1 else None,  # type: ignore[arg-type]
                    value if pick == 2 else None,  # type: ignore[arg-type]
                )
            )
        return sorted(
            out,
            key=lambda x: (x.exchange_mic or "~", x.country or "~", x.asset_class or "~", x.metric),
        )

    all_symbols: set[str] = set()
    for acc in cells.values():
        all_symbols |= acc.symbols
    ordered = sorted(cells.items(), key=lambda kv: tuple("~" if v is None else v for v in kv[0]))
    return DataReach(
        universe_note=UNIVERSE_NOTE,
        as_of=now,
        held_tickers=await pool.fetchval(
            "select count(*) from public.position_snapshot where snapshot_date ="
            " (select max(snapshot_date) from public.position_snapshot)"
        ),
        watchlist_tickers=await pool.fetchval(
            "select count(distinct ticker) from public.watchlist_item"
        ),
        tested_symbols=len(all_symbols),
        cells=[acc.row(k[3], k[0], k[1], k[2]) for k, acc in ordered],
        by_exchange=rollup(0),
        by_country=rollup(1),
        by_asset_class=rollup(2),
    )


# ---------------------------------------------------------------------------------------
# (3) Provenance completeness
# ---------------------------------------------------------------------------------------
# table -> {dimension: SQL expression over the stored columns, or None when the table has no
# column that could carry it}. A null or blank value counts as missing.
PROVENANCE_DIMENSIONS = ("source", "as_of", "known_at", "method", "access_state")
PROVENANCE_SPEC: dict[str, tuple[str, dict[str, str | None]]] = {
    "source_observation": (
        "public.source_observation",
        {
            "source": "source",
            "as_of": "period_end",
            "known_at": "known_at",
            "method": "coalesce(nullif(btrim(vintage), ''),"
            " nullif(btrim(transformation_version), ''))",
            "access_state": "access_state",
        },
    ),
    "fundamentals": (
        "public.fundamentals",
        {
            "source": "source",
            "as_of": "period_end",
            "known_at": "fetched_at",
            "method": "basis",
            "access_state": None,
        },
    ),
    "financial_statement_fact": (
        "public.financial_statement_fact",
        {
            "source": "source",
            "as_of": "period_end",
            "known_at": "known_at",
            "method": "basis",
            "access_state": None,
        },
    ),
    "forward_observation": (
        "public.forward_observation",
        {
            "source": "provider",
            "as_of": "coalesce(period_end::text, nullif(btrim(fiscal_period), ''))",
            "known_at": "known_at",
            "method": "vintage",
            "access_state": None,
        },
    ),
    "macro_observation": (
        "public.macro_observation",
        {
            "source": "source",
            "as_of": "period",
            "known_at": "known_at",
            "method": "coalesce(nullif(btrim(vintage), ''), nullif(btrim(transformation), ''))",
            "access_state": None,
        },
    ),
    "corporate_action": (
        "public.corporate_action",
        {
            "source": "source",
            "as_of": "ex_date",
            "known_at": "known_at",
            "method": None,
            "access_state": None,
        },
    ),
    "peer_set": (
        "public.peer_set",
        {
            "source": "source",
            "as_of": "coalesce(valid_from, known_at::date)",
            "known_at": "known_at",
            "method": "basis",
            "access_state": None,
        },
    ),
    "price_bar": (
        "public.price_bar",
        {
            "source": "source",
            "as_of": "coalesce(session_date, date)",
            "known_at": "fetched_at",
            "method": "bar_type",
            "access_state": "access_state",
        },
    ),
}


class ProvenanceDimension(BaseModel):
    dimension: Literal["source", "as_of", "known_at", "method", "access_state"]
    column: str | None = Field(
        description="Stored column(s) read; null when the table has no column for it (counts as "
        "not carried: the target cannot be met)."
    )
    present: int = Field(description="Rows where the column is non-null and non-blank.")
    missing: int
    share: float | None = Field(description="present / rows, a fraction; null for an empty table.")


class ProvenanceTable(BaseModel):
    table: str
    rows: int
    dimensions: list[ProvenanceDimension]
    complete_rows: int = Field(description="Rows carrying every dimension that has a column.")
    complete_share: float | None = Field(
        description="complete_rows / rows; null when empty or a dimension has no column."
    )
    target: float
    meets_target: bool = Field(
        description="True only for a non-empty table where every dimension has a column and "
        "every row carries it."
    )


class ProvenanceCompleteness(BaseModel):
    target: float = Field(description="The plan's target, a fraction (1.0).")
    meets_target: bool = Field(description="True when every non-empty table meets the target.")
    tables_total: int
    tables_meeting_target: int
    empty_tables: list[str]
    tables: list[ProvenanceTable]


async def provenance_completeness(pool: asyncpg.Pool) -> ProvenanceCompleteness:
    tables: list[ProvenanceTable] = []
    for name, (relation, spec) in PROVENANCE_SPEC.items():
        parts = ["count(*) as rows"]
        covered = [d for d in PROVENANCE_DIMENSIONS if spec[d]]
        for d in covered:
            parts.append(
                f"count(*) filter (where nullif(btrim(({spec[d]})::text), '') is not null) as {d}"
            )
        all_present = " and ".join(
            f"nullif(btrim(({spec[d]})::text), '') is not null" for d in covered
        )
        parts.append(f"count(*) filter (where {all_present}) as complete")
        row = await pool.fetchrow(f"select {', '.join(parts)} from {relation}")  # noqa: S608 - constant spec
        assert row is not None
        total = int(row["rows"])
        dims = []
        for d in PROVENANCE_DIMENSIONS:
            expr = spec[d]
            present = int(row[d]) if expr else 0
            dims.append(
                ProvenanceDimension(
                    dimension=d,
                    column=expr,
                    present=present,
                    missing=total - present,
                    share=(present / total) if total else None,
                )
            )
        complete = int(row["complete"])
        meets = total > 0 and len(covered) == len(PROVENANCE_DIMENSIONS) and complete == total
        tables.append(
            ProvenanceTable(
                table=name,
                rows=total,
                dimensions=dims,
                complete_rows=complete,
                complete_share=(complete / total) if total and len(covered) == 5 else None,
                target=PROVENANCE_TARGET,
                meets_target=meets,
            )
        )
    non_empty = [t for t in tables if t.rows]
    return ProvenanceCompleteness(
        target=PROVENANCE_TARGET,
        meets_target=bool(non_empty) and all(t.meets_target for t in non_empty),
        tables_total=len(tables),
        tables_meeting_target=sum(t.meets_target for t in tables),
        empty_tables=[t.table for t in tables if not t.rows],
        tables=tables,
    )


# ---------------------------------------------------------------------------------------
# (4) Identity review share
# ---------------------------------------------------------------------------------------
class UnresolvedHolding(BaseModel):
    ticker: str
    name: str | None
    link_status: str = Field(
        description="instrument_security_link.status, 'confirmed_without_path' (confirmed but "
        "no security or listing), or 'no_link'."
    )
    score: float | None = Field(description="Match score, 0..1.")
    last_decision: str | None = Field(description="Latest identity_match_decision.decision.")
    last_decision_at: dt.datetime | None
    identity_error: str | None


class IdentityReview(BaseModel):
    snapshot_date: dt.date | None
    held_positions: int = Field(description="Denominator: positions in the latest snapshot.")
    reviewed: int = Field(
        description="Held positions whose link is confirmed with a security or listing path."
    )
    owner_reviewed: int = Field(description="Of those, confirmed by the owner (decided_by set).")
    auto_confirmed: int = Field(description="Of those, confirmed by the matcher's evidence rules.")
    reviewed_share: float | None = Field(description="reviewed / held_positions, a fraction.")
    owner_reviewed_share: float | None
    unresolved_count: int
    unresolved: list[UnresolvedHolding]
    accounted_share: float | None = Field(
        description="(reviewed + visible unresolved exceptions) / held_positions: the plan's "
        "wording 'reviewed path or a visible unresolved exception'."
    )
    target: float
    meets_target: bool = Field(description="Strict: every held position has a reviewed path.")


async def identity_review(pool: asyncpg.Pool) -> IdentityReview:
    snap = await pool.fetchval("select max(snapshot_date) from public.position_snapshot")
    rows = (
        await pool.fetch(
            """select p.ticker, i.name, i.identity_error, l.status, l.score, l.security_id,
                      l.listing_id, l.decided_by,
                      d.decision as last_decision, d.decided_at as last_decision_at
               from public.position_snapshot p
               left join public.instrument i on i.t212_ticker = p.ticker
               left join public.instrument_security_link l on l.ticker = p.ticker
               left join lateral (select decision, decided_at from public.identity_match_decision m
                                   where m.instrument_ticker = p.ticker
                                   order by m.decided_at desc, m.id desc limit 1) d on true
               where p.snapshot_date = $1 order by p.ticker""",
            snap,
        )
        if snap
        else []
    )
    reviewed = owner = 0
    unresolved: list[UnresolvedHolding] = []
    for r in rows:
        has_path = r["security_id"] is not None or r["listing_id"] is not None
        if r["status"] == "confirmed" and has_path:
            reviewed += 1
            owner += r["decided_by"] is not None
            continue
        status = (
            "no_link" if r["status"] is None
            else "confirmed_without_path" if r["status"] == "confirmed"
            else r["status"]
        )  # fmt: skip
        unresolved.append(
            UnresolvedHolding(
                ticker=r["ticker"],
                name=r["name"],
                link_status=status,
                score=float(r["score"]) if r["score"] is not None else None,
                last_decision=r["last_decision"],
                last_decision_at=r["last_decision_at"],
                identity_error=r["identity_error"],
            )
        )
    total = len(rows)
    return IdentityReview(
        snapshot_date=snap,
        held_positions=total,
        reviewed=reviewed,
        owner_reviewed=owner,
        auto_confirmed=reviewed - owner,
        reviewed_share=(reviewed / total) if total else None,
        owner_reviewed_share=(owner / total) if total else None,
        unresolved_count=len(unresolved),
        unresolved=unresolved,
        accounted_share=((reviewed + len(unresolved)) / total) if total else None,
        target=IDENTITY_TARGET,
        meets_target=total > 0 and reviewed == total,
    )
