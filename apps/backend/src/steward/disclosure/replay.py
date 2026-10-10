"""P8.7: decision replay. What did the owner hold, and what was publicly known, on a past date?

The result has two halves per holding and dataset. `known` is read through the same `as_of` reads
the live pages use, so nothing with `known_at` after the date is admitted. `since` is what became
known afterwards (read at `now`), kept apart so a later filing, estimate or restated value can never
be mistaken for what the owner could have seen. Holdings come from the last stored snapshot on or
before the date; no snapshot is a stated gap, not an empty portfolio.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable
from typing import Any

import asyncpg

from steward.disclosure import asof, deal_read, estimate_read, insider_analysis, ownership_read

type Pool = asyncpg.Pool | asyncpg.Connection


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


def item(
    label: str, known_at: dt.datetime | None, detail: str = "", ref: Any = None
) -> dict[str, Any]:
    return {
        "label": label,
        "known_at": known_at,
        "detail": detail,
        "ref": None if ref is None else str(ref),
    }


def new_since(
    known: Iterable[dict[str, Any]], current: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """What became known afterwards: items whose `ref` was not known (`change` "new"), and items
    whose ref was known but whose label or detail now reads differently (`change` "changed",
    with `was`), such as a deal that moved state or a holder whose stake was amended."""
    seen = {i["ref"]: i for i in known}
    out: list[dict[str, Any]] = []
    for i in current:
        before = seen.get(i["ref"])
        if before is None:
            out.append({**i, "change": "new"})
        elif (before["label"], before["detail"]) != (i["label"], i["detail"]):
            out.append({**i, "change": "changed", "was": before["detail"] or before["label"]})
    return out


def drop_future(
    items: Iterable[dict[str, Any]], as_of: dt.datetime
) -> tuple[list[dict[str, Any]], int]:
    """Defence in depth: an item stamped after `as_of` is removed from the known half and counted.
    An item with no `known_at` is kept; the source read already filtered it."""
    kept: list[dict[str, Any]] = []
    leaked = 0
    for i in items:
        k = i.get("known_at")
        if k is not None and _aware(k) > as_of:
            leaked += 1
        else:
            kept.append(i)
    return kept, leaked


async def holdings_at(
    pool: Pool, as_of: dt.datetime
) -> tuple[dt.date | None, list[dict[str, Any]]]:
    """Positions of the latest snapshot on or before `as_of`, and that snapshot's date."""
    day = await pool.fetchval(
        "select max(snapshot_date) from public.position_snapshot where snapshot_date <= $1",
        as_of.astimezone(dt.UTC).date(),  # the snapshot day is a UTC day, not the caller's
    )
    if day is None:
        return None, []
    rows = await pool.fetch(
        """select ticker, quantity, value, instrument_currency from public.position_snapshot
            where snapshot_date = $1 order by ticker""",
        day,
    )
    return day, [
        {
            "ticker": r["ticker"],
            "quantity": str(r["quantity"]),
            "value": str(r["value"]),
            "currency": r["instrument_currency"],
        }
        for r in rows
    ]


# ------------------------------------------------------------------------ dataset summaries
def _filings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item(
            f"{r['form'] or 'filing'} {r['external_id']}",
            r["known_at"],
            ("current version" if r["is_current"] else "a later version replaced it")
            + (f", period {r['fiscal_period']}" if r["fiscal_period"] else ""),
            r["version_id"],
        )
        for r in rows
    ]


def _estimates(view: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for series, lines in view["lanes"].items():
        for ln in lines:
            stat = ln["mean"] or ln["median"] or ln["high"] or ln["low"]
            out.append(
                item(
                    f"{series.replace('_', ' ')}: {ln['metric']} {ln['fiscal_period']}",
                    ln["known_at"],
                    f"{stat} {ln['unit']}" if stat is not None else "",
                    ln["id"],
                )
            )
    return out


def _deals(view: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for d in view["deals"]:
        ident = d.get("id") or d.get("deal_id") or d.get("title")
        out.append(
            item(
                str(d.get("title") or d.get("name") or f"deal {ident}"),
                d.get("known_at"),
                str(d.get("state") or ""),
                ident,
            )
        )
    return out


def _ownership(view: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item(
            f"{ln['title']}: {e['holder']}",
            e.get("known_at"),
            "; ".join(
                p
                for p in (
                    ln["basis"],
                    e["shares"] and f"{e['shares']} shares",
                    e["reported_percent"] and f"{e['reported_percent']}%",
                )
                if p
            ),
            f"{ln['key']}:{e['holder']}",
        )
        for ln in view["lanes"]
        for e in ln["entries"]
    ]


def _insiders(view: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item(
            f"{t['insider']} ({t.get('role') or 'role not stated'})",
            t.get("known_at"),
            "; ".join(
                str(p)
                for p in (t.get("tx_date"), t.get("code"), t.get("shares"), t.get("price"))
                if p is not None
            ),
            f"{t['accession']}:{t['row_index']}",
        )
        for t in view["transactions"]
    ]


DATASETS = ("filings", "estimates", "deals", "ownership", "insiders")


async def _read(
    pool: Pool, dataset: str, ticker: str, issuer_id: int | None, when: dt.datetime
) -> list[dict[str, Any]]:
    if dataset == "filings":
        if issuer_id is None:
            return []
        return _filings(await asof.filing_timeline(pool, issuer_id, when, limit=100))
    if dataset == "estimates":
        return _estimates(await estimate_read.estimate_view(pool, ticker, when))
    if dataset == "deals":
        return _deals(await deal_read.holding_deals(pool, ticker, when))
    if dataset == "ownership":
        return _ownership(await ownership_read.ownership(pool, ticker, when))
    return _insiders(await insider_analysis.insider_activity(pool, ticker, when))


async def replay(
    pool: Pool,
    as_of: dt.datetime,
    *,
    now: dt.datetime | None = None,
    reader: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    when = _aware(as_of).astimezone(dt.UTC)  # day comparisons below are UTC days
    today = _aware(now or dt.datetime.now(dt.UTC))
    read = reader or _read
    snapshot_day, positions = await holdings_at(pool, when)
    notes: list[str] = []
    if snapshot_day is None:
        notes.append(
            "No portfolio snapshot exists on or before this date, so no holdings are shown."
        )
    elif snapshot_day < when.date():
        notes.append(
            f"Holdings are from the last snapshot, {snapshot_day}; later trades are not reflected."
        )
    if when > today:
        notes.append("The date is in the future; everything known so far is shown.")
    holdings = []
    for pos in positions:
        ticker = pos["ticker"]
        issuer = await estimate_read.issuer_for_ticker(pool, ticker)
        issuer_id = int(issuer["id"]) if issuer else None
        datasets: dict[str, Any] = {}
        for name in DATASETS:
            try:
                known = await read(pool, name, ticker, issuer_id, when)
                current = await read(pool, name, ticker, issuer_id, today)
            except (asyncpg.PostgresError, ValueError) as exc:
                datasets[name] = {
                    "known": [],
                    "since": [],
                    "leaked_dropped": 0,
                    "error": f"Could not read {name}: {type(exc).__name__}.",
                }
                continue
            kept, leaked = drop_future(known, when)
            datasets[name] = {
                "known": kept,
                "since": new_since(kept, current),
                "leaked_dropped": leaked,
                "error": None,
            }
        holdings.append({**pos, "issuer_id": issuer_id, "datasets": datasets})
    return {
        "as_of": when,
        "read_at": today,
        "snapshot_date": snapshot_day,
        "holdings": holdings,
        "notes": notes,
    }
