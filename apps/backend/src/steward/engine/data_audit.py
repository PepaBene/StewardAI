"""A second look at data nobody else checks (autonomous engine plan, L2): prices, fund holdings
and the written digests. Each audit is a function `(pool, now) -> findings` listed in `AUDITS`, so
a new data source gets a check by adding one entry; `run` turns the findings into one notice per
subject and resolves the notices of subjects that are fine again. Audits only read.

- prices: two sources that stored a different close for the same day, and a one-day move so large
  it is more likely a missing split than a real move.
- fund holdings: a fund's latest holdings that do not add up to the whole fund, or one holding
  that is half the fund.
- digests: a recent digest in which the claim verifier could not support many of the sentences.
"""

import datetime as dt
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import asyncpg

from steward.ops import tasks

KIND = "data_audit"
PRICE_DISAGREE = 0.02  # two sources differ by more than 2%
PRICE_JUMP = 0.4  # a one-day move of 40% or more
PRICE_DAYS = 10
WEIGHT_LOW = 0.9
WEIGHT_HIGH = 1.1
HOLDING_MAX = 0.5
DIGEST_DAYS = 14
DIGEST_BAD_SHARE = 0.25
DIGEST_MIN_CLAIMS = 4


@dataclass(frozen=True)
class Finding:
    subject: str
    title: str
    detail: str


def relative_gap(a: float, b: float) -> float:
    return abs(a - b) / max(abs(a), abs(b)) if a and b else 0.0


def price_disagreement(symbol: str, day: dt.date, closes: dict[str, float]) -> Finding | None:
    """`closes` is source -> close for one symbol and day."""
    if len(closes) < 2:
        return None
    low, high = min(closes.values()), max(closes.values())
    gap = relative_gap(low, high)
    if gap <= PRICE_DISAGREE:
        return None
    sources = ", ".join(f"{s} {c:g}" for s, c in sorted(closes.items()))
    return Finding(
        f"price:{symbol}:{day.isoformat()}",
        f"{symbol} has two different closes for {day:%d %b}",
        f"{sources}: they differ by {gap:.1%}. One of the sources is wrong.",
    )


def price_jump(symbol: str, day: dt.date, before: float, after: float) -> Finding | None:
    move = after / before - 1 if before else 0.0
    if abs(move) < PRICE_JUMP:
        return None
    return Finding(
        f"jump:{symbol}:{day.isoformat()}",
        f"{symbol} moved {move:+.0%} in one day ({day:%d %b})",
        f"The close went from {before:g} to {after:g}. That is more often a missing split or a "
        "bad bar than a real move; check it before relying on returns that include this day.",
    )


def weights_problem(fund: str, total: float, largest: float) -> Finding | None:
    if total < WEIGHT_LOW or total > WEIGHT_HIGH:
        return Finding(
            f"fund:{fund}",
            f"The holdings of {fund} add up to {total:.0%} of the fund",
            "They should be close to 100%. The look-through numbers built on them are off.",
        )
    if largest > HOLDING_MAX:
        return Finding(
            f"fund:{fund}",
            f"One holding is {largest:.0%} of {fund}",
            "That is far more than a normal fund; the file may have been read wrongly.",
        )
    return None


def digest_problem(
    report_id: int, title: str, supported: int, unsupported: int, unchecked: int
) -> Finding | None:
    total = supported + unsupported + unchecked
    if total < DIGEST_MIN_CLAIMS:
        return None
    bad = (unsupported + unchecked) / total
    if bad < DIGEST_BAD_SHARE:
        return None
    return Finding(
        f"digest:{report_id}",
        f"{title}: {unsupported + unchecked} of {total} sentences are not backed by a source",
        f"{unsupported} could not be supported and {unchecked} were not checked.",
    )


async def prices(pool: asyncpg.Pool, now: dt.datetime) -> list[Finding]:
    since = now.date() - dt.timedelta(days=PRICE_DAYS)
    found: list[Finding] = []
    # Raw and adjusted bars differ by design, so only bars of the same type are compared.
    grouped: dict[tuple[str, dt.date, str], dict[str, float]] = {}
    for r in await pool.fetch(
        "select symbol, date, source, bar_type, close from public.price_bar where date >= $1",
        since,
    ):
        key = (r["symbol"], r["date"], r["bar_type"])
        grouped.setdefault(key, {})[r["source"]] = float(r["close"])
    for (symbol, day, _bar_type), closes in sorted(grouped.items()):
        f = price_disagreement(symbol, day, closes)
        if f:
            found.append(f)
    for r in await pool.fetch(
        "select symbol, date, close, lag(close) over (partition by symbol, source, bar_type"
        " order by date)"
        " as prev from public.price_bar where date >= $1::date - 5"
        " order by symbol, date",
        since,
    ):
        if r["prev"] is not None and r["date"] >= since:
            f = price_jump(r["symbol"], r["date"], float(r["prev"]), float(r["close"]))
            if f:
                found.append(f)
    return found


async def fund_holdings(pool: asyncpg.Pool, now: dt.datetime) -> list[Finding]:
    rows = await pool.fetch(
        "select s.fund_isin, coalesce(max(s.fund_name), s.fund_isin) as name,"
        " sum(h.weight) as total, max(h.weight) as largest"
        " from public.etf_snapshot s join public.etf_holding h on h.snapshot_id = s.id"
        " where s.id = (select id from public.etf_snapshot x where x.fund_isin = s.fund_isin"
        "  order by as_of desc, id desc limit 1) group by s.fund_isin"
    )
    return [
        f
        for r in rows
        if (f := weights_problem(r["name"], float(r["total"]), float(r["largest"]))) is not None
    ]


async def digests(pool: asyncpg.Pool, now: dt.datetime) -> list[Finding]:
    rows = await pool.fetch(
        "select id, title, body->'claims' as claims from public.report where type = 'digest'"
        " and created_at >= $1 and body->'claims' is not null and body->>'claims' <> 'null'",
        now - dt.timedelta(days=DIGEST_DAYS),
    )
    found: list[Finding] = []
    for r in rows:
        c = json.loads(r["claims"]) if isinstance(r["claims"], str) else r["claims"]
        f = digest_problem(
            r["id"],
            r["title"],
            int(c.get("supported", 0)),
            int(c.get("unsupported", 0)),
            int(c.get("unchecked", 0)),
        )
        if f:
            found.append(f)
    return found


MACRO_DAYS = 30
MACRO_DISAGREE = 0.02


def macro_problem(kind: str, series: str, period: dt.date, detail: str) -> Finding:
    titles = {
        "early": f"{series} for {period:%b %Y} was stored as known before it was published",
        "backwards": f"{series} for {period:%b %Y} has a later revision known earlier",
        "sources": f"{series} for {period:%b %Y} differs between two sources",
    }
    return Finding(f"macro:{kind}:{series}:{period.isoformat()}", titles[kind], detail)


async def macro(pool: asyncpg.Pool, now: dt.datetime) -> list[Finding]:
    """Time and value checks on macro data stored in the last month: a value dated before it was
    released (it would leak the future into an as-of read), a revision that is known before the
    one it revises, and two sources giving different values for the same series and period."""
    since = now - dt.timedelta(days=MACRO_DAYS)
    found: list[Finding] = []
    for r in await pool.fetch(
        "select series_id, period, known_at, release_at from public.macro_observation"
        " where fetched_at >= $1 and release_at is not null and known_at < release_at"
        " order by series_id, period limit 50",
        since,
    ):
        found.append(
            macro_problem(
                "early",
                r["series_id"],
                r["period"],
                f"It is dated {r['known_at']:%d %b %Y} but was released "
                f"{r['release_at']:%d %b %Y}. A read as of the earlier date would see a "
                "number nobody had yet.",
            )
        )
    for r in await pool.fetch(
        "select a.series_id, a.period, a.revision as low, b.revision as high"
        " from public.macro_observation a join public.macro_observation b"
        " on b.series_id = a.series_id and b.source = a.source and b.geography = a.geography"
        " and b.period = a.period and b.revision > a.revision and b.known_at < a.known_at"
        " where a.fetched_at >= $1 order by a.series_id, a.period limit 50",
        since,
    ):
        found.append(
            macro_problem(
                "backwards",
                r["series_id"],
                r["period"],
                f"Revision {r['high']} is stored as known before revision {r['low']}, so an "
                "as-of read picks the wrong one.",
            )
        )
    latest: dict[tuple[str, str, dt.date], dict[str, float]] = {}
    for r in await pool.fetch(
        "select distinct on (series_id, geography, period, source)"
        " series_id, geography, period, source, value * unit_multiplier as value"
        " from public.macro_observation where fetched_at >= $1 and value is not null"
        " order by series_id, geography, period, source, known_at desc, revision desc",
        since,
    ):
        key = (r["series_id"], r["geography"], r["period"])
        latest.setdefault(key, {})[r["source"]] = float(r["value"])
    for (series, _geo, period), values in sorted(latest.items()):
        if len(values) < 2:
            continue
        gap = relative_gap(min(values.values()), max(values.values()))
        if gap > MACRO_DISAGREE:
            sources = ", ".join(f"{s} {v:g}" for s, v in sorted(values.items()))
            found.append(macro_problem("sources", series, period, f"{sources}: {gap:.1%} apart."))
    return found


Audit = Callable[[asyncpg.Pool, dt.datetime], Awaitable[list[Finding]]]
AUDITS: dict[str, Audit] = {
    "prices": prices,
    "fund_holdings": fund_holdings,
    "digests": digests,
    "macro": macro,
}


async def run(pool: asyncpg.Pool, now: dt.datetime) -> dict[str, int]:
    """Run every audit; one notice per finding, resolved when the finding is gone."""
    counts: dict[str, int] = {}
    present: set[str] = set()
    for name, audit in AUDITS.items():
        findings = await audit(pool, now)
        counts[name] = len(findings)
        for f in findings:
            present.add(f.subject)
            await tasks.see(
                pool,
                KIND,
                f.subject,
                title=f.title,
                detail=f.detail,
                href="/quality",
                weight=0.3,
                status="needs_owner",
            )
    await tasks.settle(pool, KIND, present)
    return counts
