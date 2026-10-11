"""The "what changed?" feed (plan M7.2): the same facts as the app knew them at two dates, side by
side, sorted by how much of the portfolio they touch.

Each area is read twice: once as known at the end of the earlier day and once as known now,
using the `known_at` stamp every stored observation carries. An item points at the exact old and
new records, so the change can be checked. A value that did not change, or that has no earlier
record, is never shown as a change of zero: a first-time record is labelled "new", not "revised".
Areas the stored data cannot compare are listed, not left out."""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Self

import asyncpg
from pydantic import BaseModel, Field, model_validator

Kind = Literal["new", "revised", "moved", "added", "removed", "dropped"]
Area = Literal[
    "fundamentals", "estimates", "price", "fund_holdings", "graph", "macro", "ownership",
    "insiders", "patents",
]  # fmt: skip

WEIGHT_FULL = 0.10
AREA_IMPORTANCE = {
    "fundamentals": 1.0, "estimates": 0.8, "graph": 0.8, "insiders": 0.7, "ownership": 0.6,
    "fund_holdings": 0.6, "price": 0.6, "patents": 0.5, "macro": 0.4,
}  # fmt: skip
KIND_IMPORTANCE = {
    "revised": 1.0, "removed": 0.9, "moved": 0.8, "added": 0.8, "dropped": 0.8, "new": 0.7,
}  # fmt: skip
REVISION_TOLERANCE = 1e-9
PRICE_MOVE = 0.03
FUND_WEIGHT_MOVE = 0.01

METHOD = (
    "Each area is compared as the app knew it at the end of the earlier day and as it knows it "
    "now. Items are sorted by a 0-100 relevance score: 60 points for the share of the portfolio "
    "in the company or fund they concern (full at 10%) and 40 for how much that kind of change "
    "in that area usually matters (fixed factors, not learned). "
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
    relevance: int = Field(default=0, description="0-100 score; see the method for its parts.")
    relevance_reason: str = ""

    @model_validator(mode="after")
    def _score(self) -> Self:
        if self.relevance == 0 and not self.relevance_reason:
            self.relevance, self.relevance_reason = relevance(self.area, self.kind, self.weight)
        return self


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


def relevance(area: str, kind: str, weight: float | None) -> tuple[int, str]:
    """0-100: 60 points for the portfolio weight (full at 10%), 40 for how much the kind of
    change in this area usually matters. The parts are stated, not learned."""
    w = min(max(weight or 0.0, 0.0) / WEIGHT_FULL, 1.0)
    a = AREA_IMPORTANCE.get(area, 0.5)
    k = KIND_IMPORTANCE.get(kind, 0.7)
    score = round(100 * (0.6 * w + 0.4 * a * k))
    return score, f"weight {w:.0%} of the {WEIGHT_FULL:.0%} cap; {area} {a:g} x {kind} {k:g}"


def rank_items(items: list[ChangeItem]) -> list[ChangeItem]:
    """Highest relevance first, then largest portfolio weight; items outside the portfolio
    sort by relevance alone."""
    return sorted(
        items,
        key=lambda i: (
            -i.relevance,
            -(i.weight if i.weight is not None else -1.0),
            i.area,
            i.subject,
            i.what,
        ),
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


def _latest_as_of(
    rows: list[asyncpg.Record], cuts: tuple[dt.datetime, ...], identity: tuple[str, ...]
) -> list[list[asyncpg.Record]]:
    """Per cut-off, the newest row of each identity known by then. `rows` are ordered by the
    identity columns and then `known_at desc, id desc`, so one pass over a single read gives
    every state; reading once per cut-off scanned the same rows again for each."""
    seen: list[set[tuple[Any, ...]]] = [set() for _ in cuts]
    picked: list[list[asyncpg.Record]] = [[] for _ in cuts]
    for r in rows:
        key = tuple(r[c] for c in identity)
        for i, cut in enumerate(cuts):
            if key not in seen[i] and r["known_at"] <= cut:
                seen[i].add(key)
                picked[i].append(r)
    return picked


async def _fundamentals(
    pool: asyncpg.Pool, tickers: list[str], cuts: tuple[dt.datetime, ...]
) -> list[dict[tuple[Any, ...], Record]]:
    rows = await pool.fetch(
        "select id, ticker, concept, period_start, period_end, unit, value, known_at, source_url"
        " from public.financial_statement_fact"
        " where ticker = any($1::text[]) and value is not null and known_at <= $2"
        " and concept ~* $3"
        " and jsonb_array_length(coalesce(dimensions->'members', '[]'::jsonb)) = 0"
        " order by ticker, concept, period_start, period_end, unit, known_at desc, id desc",
        tickers, max(cuts), _CONCEPTS,
    )  # fmt: skip
    identity = ("ticker", "concept", "period_start", "period_end", "unit")
    return [
        {
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
            for r in picked
        }
        for picked in _latest_as_of(rows, cuts, identity)
    ]


async def _estimates(
    pool: asyncpg.Pool, tickers: list[str], cuts: tuple[dt.datetime, ...]
) -> list[dict[tuple[Any, ...], Record]]:
    rows = await pool.fetch(
        "select id, ticker, kind, provider, metric, basis, fiscal_period, value, known_at,"
        " source_url from public.forward_observation"
        " where ticker = any($1::text[]) and value is not null and known_at <= $2"
        " order by ticker, kind, provider, metric, basis, fiscal_period, known_at desc, id desc",
        tickers, max(cuts),
    )  # fmt: skip
    identity = ("ticker", "kind", "provider", "metric", "basis", "fiscal_period")
    return [
        {
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
            for r in picked
        }
        for picked in _latest_as_of(rows, cuts, identity)
    ]


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
    pool: asyncpg.Pool,
    weights: dict[str, float],
    since_cut: dt.datetime,
    days: tuple[dt.date, dt.date],
) -> tuple[list[ChangeItem], int]:
    """Holdings changes of the funds the owner holds (directly, on either comparison day):
    the latest stored snapshot against the latest one fetched on or before the cutoff."""
    funds = await pool.fetch(
        "select distinct on (i.isin) i.isin as fund_isin, i.t212_ticker from public.instrument i"
        " join public.position_snapshot p on p.ticker = i.t212_ticker"
        " and p.snapshot_date = any($1::date[])"
        " where i.isin is not null and exists (select 1 from public.etf_snapshot s"
        "   where s.fund_isin = i.isin and s.fetched_at is not null)"
        " order by i.isin, i.t212_ticker",
        list(days),
    )
    out: list[ChangeItem] = []
    compared = 0
    for f in funds:
        new_s = await pool.fetchrow(
            "select id, as_of, fetched_at from public.etf_snapshot"
            " where fund_isin = $1 and fetched_at is not null order by as_of desc, id desc limit 1",
            f["fund_isin"],
        )
        old_s = await pool.fetchrow(
            "select id, as_of, fetched_at from public.etf_snapshot"
            " where fund_isin = $1 and fetched_at is not null and fetched_at <= $2"
            " order by as_of desc, id desc limit 1",
            f["fund_isin"],
            since_cut,
        )
        if new_s is None or old_s is None or new_s["id"] == old_s["id"]:
            continue
        compared += 1
        rows = await pool.fetch(
            "select snapshot_id, coalesce(isin, name) k, max(name) n, sum(weight) w"
            " from public.etf_holding where snapshot_id = any($1::bigint[]) and weight is not null"
            " group by snapshot_id, coalesce(isin, name)",
            [new_s["id"], old_s["id"]],
        )
        by: dict[int, dict[str, float]] = {new_s["id"]: {}, old_s["id"]: {}}
        for r in rows:
            by[r["snapshot_id"]][r["n"] or r["k"]] = float(r["w"])
        ticker = f["t212_ticker"]
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
        "select r.id, r.ticker, r.type, r.object_name, r.status, r.created_at, r.updated_at,"
        " p.id as prev_id, p.to_status as prev_status, p.changed_at as prev_at"
        " from public.entity_relationship r left join lateral ("
        "   select l.id, l.to_status, l.changed_at from public.graph_status_log l"
        "   where l.table_name = 'entity_relationship' and l.row_id = r.id"
        "   and l.changed_at <= $1 order by l.changed_at desc, l.id desc limit 1) p on true"
        " where r.updated_at > $1 or r.created_at > $1"
        " order by r.updated_at desc limit 300",
        since_cut,
    )
    return [graph_item(r, since_cut, weights) for r in rows], len(rows)


def graph_item(
    r: Mapping[str, Any], since_cut: dt.datetime, weights: dict[str, float]
) -> ChangeItem:
    """A link changed since the cutoff. It is 'revised' only when an earlier status record
    exists (the logged status at the cutoff), which then is the item's before record; with
    none, the owner has nothing earlier to compare against, so it is 'new'."""
    has_before = r["created_at"] <= since_cut and r["prev_id"] is not None
    return ChangeItem(
        area="graph",
        kind="revised" if has_before else "new",
        subject=r["ticker"] or "",
        what=f"{r['type']}: {r['object_name']}",
        before=str(r["prev_status"]) if has_before else None,
        after=str(r["status"]),
        before_ref=(
            Ref(table="graph_status_log", id=str(r["prev_id"]), known_at=r["prev_at"])
            if has_before
            else None
        ),
        after_ref=Ref(table="entity_relationship", id=str(r["id"]), known_at=r["updated_at"]),
        weight=weight_of(r["ticker"], weights) if r["ticker"] else None,
    )


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

    # The areas read different tables and do not depend on each other, so they run together.
    async with asyncio.TaskGroup() as group:
        t_fundamentals = group.create_task(_fundamentals(pool, tickers, (old_cut, now_cut)))
        t_estimates = group.create_task(_estimates(pool, tickers, (old_cut, now_cut)))
        t_funds = group.create_task(_fund_changes(pool, weights, old_cut, (old_day, now_day)))
        t_graph = group.create_task(_graph(pool, old_cut, weights))
        t_macro = group.create_task(_macro(pool, old_cut))
        t_ownership = group.create_task(_ownership(pool, tickers, weights))
        t_insiders = group.create_task(_insiders(pool, tickers, weights, now_cut))
        t_patents = group.create_task(_patents(pool, weights, old_cut))
    old_f, new_f = t_fundamentals.result()
    old_e, new_e = t_estimates.result()
    fund_result = t_funds.result()
    graph_result = t_graph.result()
    macro_result = t_macro.result()
    ownership_result = t_ownership.result()
    insider_result = t_insiders.result()
    patent_result = t_patents.result()
    d = compare(old_f, new_f)
    got = _items_from("fundamentals", d, _label_fact, weights)
    items += got
    areas.append(AreaCount(area="fundamentals", items=len(got), compared=len(new_f)))

    got = _items_from("estimates", compare(old_e, new_e), _label_estimate, weights)
    items += got
    areas.append(AreaCount(area="estimates", items=len(got), compared=len(new_e)))

    # `since` need not be a snapshot day: prices are compared with the last snapshot on or
    # before it, and the area says which day that was and how many holdings were compared.
    price_day = await pool.fetchval(
        "select max(snapshot_date) from public.position_snapshot where snapshot_date <= $1",
        old_day,
    )
    rows = await pool.fetch(
        "select snapshot_date, ticker, current_price from public.position_snapshot"
        " where snapshot_date in ($1, $2) and current_price is not null",
        price_day or old_day, now_day,
    )  # fmt: skip
    px: dict[dt.date, dict[str, tuple[float, str]]] = {now_day: {}}
    if price_day is not None:
        px[price_day] = {}
    for r in rows:
        px[r["snapshot_date"]][r["ticker"]] = (float(r["current_price"]), f"{r['snapshot_date']}")
    price_note = None
    if price_day is None:
        price_note = f"No position snapshot on or before {old_day}; prices were not compared."
        got, compared = [], 0
    else:
        both = px[price_day].keys() & px[now_day].keys() if price_day != now_day else set()
        got = price_moves(px[price_day], px[now_day], weights) if price_day != now_day else []
        compared = len(both)
        if price_day != old_day:
            price_note = f"{old_day} has no snapshot; compared with the snapshot of {price_day}."
    items += got
    areas.append(AreaCount(area="price", items=len(got), compared=compared, note=price_note))

    got, n = fund_result
    items += got
    areas.append(
        AreaCount(
            area="fund_holdings",
            items=len(got),
            compared=n,
            note=None if n else "Fewer than two stored holdings snapshots for any held fund.",
        )
    )
    got, n = graph_result
    items += got
    areas.append(AreaCount(area="graph", items=len(got), compared=n))
    got, n = macro_result
    items += got
    areas.append(AreaCount(area="macro", items=len(got), compared=n))

    got, n, note = ownership_result
    items += got
    areas.append(AreaCount(area="ownership", items=len(got), compared=n, note=note))
    got, n = insider_result
    items += got
    areas.append(AreaCount(area="insiders", items=len(got), compared=n))
    got, n = patent_result
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
