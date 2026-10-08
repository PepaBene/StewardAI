"""The "what changed?" feed (plan M7.2): the same facts as the app knew them at two dates, side by
side, sorted by how much of the portfolio they touch.

Each area is read twice: once as known at the end of the earlier day and once as known now,
using the `known_at` stamp every stored observation carries. An item points at the exact old and
new records, so the change can be checked. A value that did not change, or that has no earlier
record, is never shown as a change of zero: a first-time record is labelled "new", not "revised".
Areas the stored data cannot compare are listed, not left out."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

Kind = Literal["new", "revised", "moved", "added", "removed", "dropped"]
Area = Literal[
    "fundamentals", "estimates", "price", "fund_holdings", "graph", "macro", "ownership",
    "insiders", "patents",
]  # fmt: skip

REVISION_TOLERANCE = 1e-9
PRICE_MOVE = 0.03
FUND_WEIGHT_MOVE = 0.01

METHOD = (
    "Each area is compared as the app knew it at the end of the earlier day and as it knows it "
    "now. Items are sorted by the share of the portfolio in the company or fund they concern. "
    "A first-time record is 'new'; only a changed value for the same period is 'revised'. "
    "Prices compare the two portfolio snapshots and show moves of 3% or more."
)

NOT_COVERED = [
    "Ownership and insiders are compared by period (the two latest 13F quarters, the last 90 "
    "days of insider trades against the 90 before), not by what the app knew on a date.",
    "Patents: only new families are listed; a change inside a family is not tracked.",
    "Trade exposures: trade series are not compared between dates here.",
]


class Ref(BaseModel):
    table: str
    id: str
    known_at: dt.datetime | None
    url: str | None = None


class ChangeItem(BaseModel):
    area: Area
    kind: Kind
    subject: str = Field(description="Ticker, fund or series.")
    what: str
    before: str | None
    after: str | None
    before_ref: Ref | None
    after_ref: Ref | None
    weight: float | None = Field(description="Share of the portfolio concerned; null if none.")


class AreaCount(BaseModel):
    area: Area
    items: int
    compared: int = Field(description="Records looked at in the area.")
    note: str | None = None


class ChangeFeed(BaseModel):
    since: dt.date
    compared_with: dt.date
    items: list[ChangeItem]
    areas: list[AreaCount]
    not_covered: list[str] = NOT_COVERED
    method: str = METHOD


@dataclass(frozen=True)
class Record:
    value: Decimal | float | str | None
    ref: Ref
    label: str = ""


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float, Decimal)) and isinstance(b, (int, float, Decimal)):
        return abs(float(a) - float(b)) <= REVISION_TOLERANCE * max(1.0, abs(float(a)))
    return bool(a == b)


def compare(
    old: dict[tuple[Any, ...], Record], new: dict[tuple[Any, ...], Record]
) -> list[tuple[tuple[Any, ...], Kind, Record | None, Record | None]]:
    """Keys new now, changed in value, or gone. Equal values produce nothing."""
    out: list[tuple[tuple[Any, ...], Kind, Record | None, Record | None]] = []
    for key, after in new.items():
        before = old.get(key)
        if before is None:
            out.append((key, "new", None, after))
        elif not _same(before.value, after.value):
            out.append((key, "revised", before, after))
    for key, before in old.items():
        if key not in new:
            out.append((key, "removed", before, None))
    return out


def weight_of(subject: str, weights: dict[str, float]) -> float | None:
    """Weight by broker ticker or by bare symbol (AAPL matches AAPL_US_EQ)."""
    if subject in weights:
        return weights[subject]
    bare = {t.split("_")[0]: w for t, w in weights.items()}
    return bare.get(subject.split("_")[0])


def rank_items(items: list[ChangeItem]) -> list[ChangeItem]:
    """Largest portfolio weight first; items outside the portfolio last."""
    return sorted(
        items,
        key=lambda i: (-(i.weight if i.weight is not None else -1.0), i.area, i.subject, i.what),
    )


def price_moves(
    old: dict[str, tuple[float, str]],
    new: dict[str, tuple[float, str]],
    weights: dict[str, float],
    threshold: float = PRICE_MOVE,
) -> list[ChangeItem]:
    out: list[ChangeItem] = []
    for ticker, (price, ref) in new.items():
        prev = old.get(ticker)
        if prev is None or prev[0] <= 0:
            continue
        move = price / prev[0] - 1
        if abs(move) < threshold:
            continue
        out.append(
            ChangeItem(
                area="price", kind="moved", subject=ticker, what="Price", before=f"{prev[0]:,.2f}",
                after=f"{price:,.2f} ({move:+.1%})",
                before_ref=Ref(table="position_snapshot", id=prev[1], known_at=None),
                after_ref=Ref(table="position_snapshot", id=ref, known_at=None),
                weight=weight_of(ticker, weights),
            )
        )  # fmt: skip
    return out


def holding_changes(
    fund: str,
    old: dict[str, float],
    new: dict[str, float],
    refs: tuple[Ref, Ref],
    fund_weight: float | None,
    threshold: float = FUND_WEIGHT_MOVE,
) -> list[ChangeItem]:
    """Constituents added, dropped or moved by a percentage point or more between two snapshots."""
    out: list[ChangeItem] = []

    def item(kind: Kind, name: str, b: float | None, a: float | None) -> ChangeItem:
        return ChangeItem(
            area="fund_holdings", kind=kind, subject=fund, what=name,
            before=None if b is None else f"{b:.2%}", after=None if a is None else f"{a:.2%}",
            before_ref=refs[0], after_ref=refs[1], weight=fund_weight,
        )  # fmt: skip

    for name, a in new.items():
        b = old.get(name)
        if b is None and a >= threshold:
            out.append(item("added", name, None, a))
        elif b is not None and abs(a - b) >= threshold:
            out.append(item("moved", name, b, a))
    out += [item("dropped", n, b, None) for n, b in old.items() if n not in new and b >= threshold]
    return out


# ---------------------------------------------------------------------------- stored data

_CONCEPTS = (
    "(RevenueFromContractWithCustomer[A-Za-z]*|:Revenues|OperatingIncomeLoss|NetIncomeLoss"
    "|GrossProfit|:Assets|StockholdersEquity)$"
)


def _cutoff(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min, tzinfo=dt.UTC)


async def snapshot_days(pool: asyncpg.Pool) -> list[dt.date]:
    rows = await pool.fetch(
        "select distinct snapshot_date from public.position_snapshot order by 1 desc limit 30"
    )
    return [r["snapshot_date"] for r in rows]


async def holding_weights(pool: asyncpg.Pool, day: dt.date) -> dict[str, float]:
    rows = await pool.fetch(
        "select ticker, value_base from public.position_snapshot where snapshot_date = $1", day
    )
    total = sum(float(r["value_base"]) for r in rows)
    return {r["ticker"]: float(r["value_base"]) / total for r in rows} if total > 0 else {}


async def _fundamentals(
    pool: asyncpg.Pool, tickers: list[str], at: dt.datetime
) -> dict[tuple[Any, ...], Record]:
    rows = await pool.fetch(
        "select distinct on (ticker, concept, period_start, period_end, unit)"
        " id, ticker, concept, period_start, period_end, unit, value, known_at, source_url"
        " from public.financial_statement_fact"
        " where ticker = any($1::text[]) and value is not null and known_at <= $2"
        " and concept ~* $3"
        " and jsonb_array_length(coalesce(dimensions->'members', '[]'::jsonb)) = 0"
        " order by ticker, concept, period_start, period_end, unit, known_at desc, id desc",
        tickers, at, _CONCEPTS,
    )  # fmt: skip
    return {
        (
            r["ticker"].split("_")[0],
            r["concept"],
            r["period_start"],
            r["period_end"],
            r["unit"],
        ): Record(
            r["value"],
            Ref(
                table="financial_statement_fact",
                id=str(r["id"]),
                known_at=r["known_at"],
                url=r["source_url"],
            ),
        )
        for r in rows
    }


async def _estimates(
    pool: asyncpg.Pool, tickers: list[str], at: dt.datetime
) -> dict[tuple[Any, ...], Record]:
    rows = await pool.fetch(
        "select distinct on (ticker, kind, provider, metric, basis, fiscal_period)"
        " id, ticker, kind, provider, metric, basis, fiscal_period, value, known_at, source_url"
        " from public.forward_observation"
        " where ticker = any($1::text[]) and value is not null and known_at <= $2"
        " order by ticker, kind, provider, metric, basis, fiscal_period, known_at desc, id desc",
        tickers, at,
    )  # fmt: skip
    return {
        (
            r["ticker"].split("_")[0],
            r["kind"],
            r["provider"],
            r["metric"],
            r["basis"],
            r["fiscal_period"],
        ): Record(
            r["value"],
            Ref(
                table="forward_observation",
                id=str(r["id"]),
                known_at=r["known_at"],
                url=r["source_url"],
            ),
        )
        for r in rows
    }


def _label_fact(key: tuple[Any, ...]) -> str:
    concept = str(key[1]).split(":")[-1]
    return f"{concept} for the period ending {key[3]}"


def _label_estimate(key: tuple[Any, ...]) -> str:
    return f"{key[2]} {key[3]} estimate for {key[5]}"


def _fmt(value: Any) -> str:
    return f"{float(value):,.4g}" if isinstance(value, (int, float, Decimal)) else str(value)


def _items_from(
    area: Area,
    diffs: list[tuple[tuple[Any, ...], Kind, Record | None, Record | None]],
    label: Any,
    weights: dict[str, float],
) -> list[ChangeItem]:
    return [
        ChangeItem(
            area=area, kind=kind, subject=str(key[0]), what=label(key),
            before=None if b is None else _fmt(b.value), after=None if a is None else _fmt(a.value),
            before_ref=b.ref if b else None, after_ref=a.ref if a else None,
            weight=weight_of(str(key[0]), weights),
        )
        for key, kind, b, a in diffs
        if kind != "removed"
    ]  # fmt: skip


async def _fund_changes(
    pool: asyncpg.Pool, weights: dict[str, float], since_cut: dt.datetime
) -> tuple[list[ChangeItem], int]:
    funds = await pool.fetch(
        "select distinct fund_isin from public.etf_snapshot where fetched_at is not null"
    )
    out: list[ChangeItem] = []
    compared = 0
    for f in funds:
        snaps = await pool.fetch(
            "select id, as_of, fetched_at from public.etf_snapshot where fund_isin = $1"
            " order by as_of desc, id desc limit 2",
            f["fund_isin"],
        )
        if len(snaps) < 2:
            continue
        compared += 1
        new_s, old_s = snaps[0], snaps[1]
        rows = await pool.fetch(
            "select snapshot_id, coalesce(isin, name) k, max(name) n, sum(weight) w"
            " from public.etf_holding where snapshot_id = any($1::bigint[]) and weight is not null"
            " group by snapshot_id, coalesce(isin, name)",
            [new_s["id"], old_s["id"]],
        )
        by: dict[int, dict[str, float]] = {new_s["id"]: {}, old_s["id"]: {}}
        for r in rows:
            by[r["snapshot_id"]][r["n"] or r["k"]] = float(r["w"])
        ticker = await pool.fetchval(
            "select t212_ticker from public.instrument where isin = $1 limit 1", f["fund_isin"]
        )
        refs = (
            Ref(table="etf_snapshot", id=str(old_s["id"]), known_at=old_s["fetched_at"]),
            Ref(table="etf_snapshot", id=str(new_s["id"]), known_at=new_s["fetched_at"]),
        )
        out += holding_changes(
            f"{f['fund_isin']} ({old_s['as_of']} to {new_s['as_of']})",
            by[old_s["id"]], by[new_s["id"]], refs,
            weights.get(ticker) if ticker else None,
        )  # fmt: skip
    return out, compared


async def _graph(
    pool: asyncpg.Pool, since_cut: dt.datetime, weights: dict[str, float]
) -> tuple[list[ChangeItem], int]:
    rows = await pool.fetch(
        "select id, ticker, type, object_name, status, created_at, updated_at"
        " from public.entity_relationship where updated_at > $1 or created_at > $1"
        " order by updated_at desc limit 300",
        since_cut,
    )
    out = [
        ChangeItem(
            area="graph",
            kind="new" if r["created_at"] > since_cut else "revised",
            subject=r["ticker"] or "",
            what=f"{r['type']}: {r['object_name']}",
            before=None if r["created_at"] > since_cut else "earlier status",
            after=str(r["status"]),
            before_ref=None,
            after_ref=Ref(table="entity_relationship", id=str(r["id"]), known_at=r["updated_at"]),
            weight=weight_of(r["ticker"], weights) if r["ticker"] else None,
        )
        for r in rows
    ]
    return out, len(rows)


async def _macro(pool: asyncpg.Pool, since_cut: dt.datetime) -> tuple[list[ChangeItem], int]:
    rows = await pool.fetch(
        "select n.id nid, n.series_id, n.period, n.value nv, n.known_at nk, n.source_url,"
        " o.id oid, o.value ov, o.known_at ok"
        " from public.macro_observation n join lateral ("
        "   select id, value, known_at from public.macro_observation o"
        "   where o.series_id = n.series_id and o.period = n.period and o.known_at < n.known_at"
        "   order by o.known_at desc, o.id desc limit 1) o on true"
        " where n.known_at > $1 and n.value is distinct from o.value"
        " order by n.known_at desc limit 200",
        since_cut,
    )
    out = [
        ChangeItem(
            area="macro", kind="revised", subject=r["series_id"], what=f"Period {r['period']}",
            before=_fmt(r["ov"]), after=_fmt(r["nv"]),
            before_ref=Ref(table="macro_observation", id=str(r["oid"]), known_at=r["ok"]),
            after_ref=Ref(
                table="macro_observation", id=str(r["nid"]), known_at=r["nk"], url=r["source_url"]
            ),
            weight=None,
        )
        for r in rows
    ]  # fmt: skip
    return out, len(rows)


COMPLETE_QUARTER_MANAGERS = 1000
INSIDER_WINDOW_DAYS = 90
INSIDER_MOVE_SHARES = 1


def ownership_item(
    ticker: str,
    prev: tuple[dt.date, int, float] | None,
    now: tuple[dt.date, int, float] | None,
    weights: dict[str, float],
) -> ChangeItem | None:
    """Holders and shares in the latest 13F quarter against the one before. A holding with no
    record in the earlier quarter is 'new'; unchanged counts give no item."""
    if now is None or (prev is not None and prev[1:] == now[1:]):
        return None
    after = f"{now[1]:,} managers, {now[2]:,.0f} shares ({now[0]})"
    before = f"{prev[1]:,} managers, {prev[2]:,.0f} shares ({prev[0]})" if prev else None
    return ChangeItem(
        area="ownership", kind="moved" if prev else "new", subject=ticker,
        what="Institutional holders in Form 13F filings", before=before, after=after,
        before_ref=None, after_ref=Ref(table="institutional_holding", id=f"{ticker}:{now[0]}",
                                       known_at=None),
        weight=weight_of(ticker, weights),
    )  # fmt: skip


async def _ownership(
    pool: asyncpg.Pool, tickers: list[str], weights: dict[str, float]
) -> tuple[list[ChangeItem], int, str | None]:
    rows = await pool.fetch(
        "with p as (select period from public.form13f_filing group by period"
        "  having count(distinct cik) > $2 order by period desc limit 2)"
        " select f.period, h.ticker, count(distinct f.cik) managers, sum(h.shares)::float8 shares"
        " from public.institutional_holding h"
        " join public.form13f_filing f on f.accession = h.accession"
        " where f.period in (select period from p) and h.ticker = any($1::text[])"
        " and h.put_call is null group by 1, 2",
        tickers,
        COMPLETE_QUARTER_MANAGERS,
    )
    periods = sorted({r["period"] for r in rows})
    by: dict[str, dict[dt.date, tuple[dt.date, int, float]]] = {}
    for r in rows:
        by.setdefault(r["ticker"], {})[r["period"]] = (r["period"], r["managers"], r["shares"])
    if len(periods) < 2:
        return [], len(by), "Fewer than two complete 13F quarters are stored."
    out = []
    for ticker, per in sorted(by.items()):
        item = ownership_item(ticker, per.get(periods[0]), per.get(periods[1]), weights)
        if item:
            out.append(item)
    return (
        out,
        len(by),
        "Compares the two latest quarters; filings arrive up to 45 days late. Share totals add "
        "up every filed line, so amendments and managers' sub-accounts may repeat; the manager "
        "count is the steadier figure.",
    )


def insider_item(
    ticker: str, recent: float, prior: float, weights: dict[str, float]
) -> ChangeItem | None:
    """Net open-market shares (purchases minus sales) in the last 90 days against the 90 before."""
    if abs(recent - prior) < INSIDER_MOVE_SHARES:
        return None
    return ChangeItem(
        area="insiders", kind="moved", subject=ticker,
        what=f"Net open-market insider shares, {INSIDER_WINDOW_DAYS} days",
        before=f"{prior:+,.0f}", after=f"{recent:+,.0f}", before_ref=None,
        after_ref=Ref(table="insider_transaction", id=ticker, known_at=None),
        weight=weight_of(ticker, weights),
    )  # fmt: skip


async def _insiders(
    pool: asyncpg.Pool, tickers: list[str], weights: dict[str, float], now: dt.datetime
) -> tuple[list[ChangeItem], int]:
    today = now.date()
    cut = today - dt.timedelta(days=INSIDER_WINDOW_DAYS)
    start = today - dt.timedelta(days=2 * INSIDER_WINDOW_DAYS)
    rows = await pool.fetch(
        "select f.ticker, (t.transaction_date >= $3) as recent,"
        " sum(case when t.transaction_code = 'P' then t.shares else -t.shares end)::float8 net"
        " from public.insider_transaction t"
        " join public.insider_filing f on f.accession = t.accession"
        " where not t.derivative and t.transaction_code in ('P', 'S')"
        " and f.ticker = any($1::text[]) and t.transaction_date >= $2"
        " and t.transaction_date <= $4 and f.superseded_by_accession is null"
        " group by 1, 2",
        tickers, start, cut, today,
    )  # fmt: skip
    by: dict[str, dict[bool, float]] = {}
    for r in rows:
        by.setdefault(r["ticker"], {})[r["recent"]] = r["net"] or 0.0
    out = [
        item
        for ticker, v in sorted(by.items())
        if (item := insider_item(ticker, v.get(True, 0.0), v.get(False, 0.0), weights))
    ]
    return out, len(by)


async def _patents(
    pool: asyncpg.Pool, weights: dict[str, float], since_cut: dt.datetime
) -> tuple[list[ChangeItem], int]:
    rows = await pool.fetch(
        "select f.id, f.family_title, f.source_url, f.known_at, o.ticker"
        " from public.patent_family f join public.patent_family_owner o on o.family_id = f.id"
        " where o.valid_to is null order by f.known_at desc limit 500"
    )
    out = [
        ChangeItem(
            area="patents", kind="new", subject=r["ticker"],
            what=f"New patent family: {r['family_title'] or 'untitled'}", before=None,
            after="stored", before_ref=None,
            after_ref=Ref(
                table="patent_family", id=str(r["id"]), known_at=r["known_at"], url=r["source_url"]
            ),
            weight=weight_of(r["ticker"], weights) if r["ticker"] else None,
        )
        for r in rows
        if r["known_at"] > since_cut and r["ticker"]
    ]  # fmt: skip
    return out, len(rows)


async def build_feed(pool: asyncpg.Pool, since: dt.date | None = None) -> ChangeFeed | None:
    days = await snapshot_days(pool)
    if not days:
        return None
    now_day = days[0]
    old_day = since or (days[1] if len(days) > 1 else now_day)
    weights = await holding_weights(pool, now_day)
    tickers = sorted(set(weights) | {t.split("_")[0] for t in weights})
    old_cut, now_cut = _cutoff(old_day), dt.datetime.now(dt.UTC)

    items: list[ChangeItem] = []
    areas: list[AreaCount] = []

    old_f, new_f = (
        await _fundamentals(pool, tickers, old_cut),
        await _fundamentals(pool, tickers, now_cut),
    )
    d = compare(old_f, new_f)
    got = _items_from("fundamentals", d, _label_fact, weights)
    items += got
    areas.append(AreaCount(area="fundamentals", items=len(got), compared=len(new_f)))

    old_e, new_e = (
        await _estimates(pool, tickers, old_cut),
        await _estimates(pool, tickers, now_cut),
    )
    got = _items_from("estimates", compare(old_e, new_e), _label_estimate, weights)
    items += got
    areas.append(AreaCount(area="estimates", items=len(got), compared=len(new_e)))

    rows = await pool.fetch(
        "select snapshot_date, ticker, current_price from public.position_snapshot"
        " where snapshot_date in ($1, $2) and current_price is not null",
        old_day, now_day,
    )  # fmt: skip
    px: dict[dt.date, dict[str, tuple[float, str]]] = {old_day: {}, now_day: {}}
    for r in rows:
        px[r["snapshot_date"]][r["ticker"]] = (float(r["current_price"]), f"{r['snapshot_date']}")
    got = price_moves(px[old_day], px[now_day], weights) if old_day != now_day else []
    items += got
    areas.append(AreaCount(area="price", items=len(got), compared=len(px[now_day])))

    got, n = await _fund_changes(pool, weights, old_cut)
    items += got
    areas.append(
        AreaCount(
            area="fund_holdings",
            items=len(got),
            compared=n,
            note=None if n else "Fewer than two stored holdings snapshots for any held fund.",
        )
    )
    got, n = await _graph(pool, old_cut, weights)
    items += got
    areas.append(AreaCount(area="graph", items=len(got), compared=n))
    got, n = await _macro(pool, old_cut)
    items += got
    areas.append(AreaCount(area="macro", items=len(got), compared=n))

    got, n, note = await _ownership(pool, tickers, weights)
    items += got
    areas.append(AreaCount(area="ownership", items=len(got), compared=n, note=note))
    got, n = await _insiders(pool, tickers, weights, now_cut)
    items += got
    areas.append(AreaCount(area="insiders", items=len(got), compared=n))
    got, n = await _patents(pool, weights, old_cut)
    items += got
    areas.append(
        AreaCount(
            area="patents",
            items=len(got),
            compared=n,
            note=None if n else "No patent families are stored yet (EPO OPS credentials unset).",
        )
    )

    return ChangeFeed(since=old_day, compared_with=now_day, items=rank_items(items), areas=areas)
