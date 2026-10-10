"""Did the thesis process add value? (quant plan Q6.5, with M7.1.)

A thesis is closed when the journal holds a `sell` for the ticker after at least one `buy` or
`add` (the first of those opens it). For each closed thesis the holding-period price return,
from the prices written in the two entries, is set against the benchmark over the same dates.
Both are price returns (no dividends); the stock's is in its own currency, so a currency move is
inside it, not removed. A thesis whose two entries have different currencies, or no recorded
price, is listed without a return.

Hit rate = share of closed theses that beat the benchmark, by tier (the strategy's `risk`
classification at the time of reading). A section with fewer than `MIN_CLOSED` theses says so
instead of showing a percentage as if it meant something.

Risk-review triggers: of the closed theses that lost money, how many had a portfolio finding
(`portfolio_breach`) opened on the ticker during the holding period, i.e. the system had flagged
the position before the loss was realised.
"""

import datetime as dt
import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import asyncpg
from pydantic import BaseModel, Field

from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.config.strategy import StrategyConfig
from steward.journal.entries import JournalEntry, list_entries
from steward.performance.benchmark import series_return
from steward.reference.read import index_series
from steward.sources.market import quote_unit_of

log = logging.getLogger(__name__)

MIN_CLOSED = 5
OPENING = ("buy", "add")
UNCLASSIFIED = "Unclassified"


class ClosedThesis(BaseModel):
    ticker: str
    tier: str
    opened_on: dt.date
    closed_on: dt.date
    holding_days: int
    price_return: float | None
    benchmark_return: float | None
    excess_return: float | None
    beat_benchmark: bool | None
    trigger_before_close: bool | None = Field(
        description="A finding opened on the ticker during the holding period; None when the "
        "thesis did not lose money."
    )
    reason: str | None = Field(description="Why there is no return, when there is none.")


class TierHitRate(BaseModel):
    tier: str
    closed: int
    with_return: int
    beat_benchmark: int
    hit_rate: float | None
    enough: bool
    median_excess: float | None


class TriggerSummary(BaseModel):
    losing_theses: int
    flagged_first: int
    share: float | None
    enough: bool


class ProcessReport(BaseModel):
    available: bool
    reason: str | None
    theses: list[ClosedThesis] = Field(default_factory=list)
    by_tier: list[TierHitRate] = Field(default_factory=list)
    triggers: TriggerSummary | None = None
    notes: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class Pair:
    opening: JournalEntry
    closing: JournalEntry


def closed_pairs(entries: Sequence[JournalEntry]) -> list[Pair]:
    """Each `sell` paired with the first opening entry for its ticker that has not been paired
    already and was decided before it. A later buy after a sell starts a new thesis."""
    by_ticker: dict[str, list[JournalEntry]] = defaultdict(list)
    for e in sorted(entries, key=lambda e: (e.decided_on, e.id)):
        if e.ticker and e.scope == "security":
            by_ticker[e.ticker].append(e)
    pairs: list[Pair] = []
    for rows in by_ticker.values():
        opened: JournalEntry | None = None
        for e in rows:
            if e.decision in OPENING and opened is None:
                opened = e
            elif e.decision == "sell" and opened is not None:
                pairs.append(Pair(opened, e))
                opened = None
    return pairs


def build_theses(
    pairs: Sequence[Pair],
    tiers: Mapping[str, str],
    benchmark: Sequence[tuple[dt.date, float]],
    triggers: Mapping[str, Sequence[dt.datetime]],
) -> list[ClosedThesis]:
    from decimal import Decimal

    bench = [(d, Decimal(str(v))) for d, v in benchmark]
    out: list[ClosedThesis] = []
    for p in pairs:
        o, c = p.opening, p.closing
        ticker = o.ticker or ""
        ret: float | None = None
        reason: str | None = None
        if not o.price_at_decision or not c.price_at_decision:
            reason = "an entry has no recorded price"
        elif (
            o.price_currency
            and c.price_currency
            and (quote_unit_of(o.price_currency) != quote_unit_of(c.price_currency))
        ):
            reason = "the two entries record prices in different currencies"
        else:
            ret = c.price_at_decision / o.price_at_decision - 1.0
        b = series_return(bench, o.decided_on, c.decided_on) if bench else None
        bench_ret = float(b) if b is not None else None
        excess = ret - bench_ret if ret is not None and bench_ret is not None else None
        flagged: bool | None = None
        if ret is not None and ret < 0:
            flagged = any(
                o.decided_on <= t.date() <= c.decided_on for t in triggers.get(ticker, ())
            )
        out.append(
            ClosedThesis(
                ticker=ticker, tier=tiers.get(ticker, UNCLASSIFIED), opened_on=o.decided_on,
                closed_on=c.decided_on, holding_days=(c.decided_on - o.decided_on).days,
                price_return=ret, benchmark_return=bench_ret, excess_return=excess,
                beat_benchmark=None if excess is None else excess > 0,
                trigger_before_close=flagged,
                reason=reason or (None if bench_ret is not None else "no benchmark prices"),
            )
        )  # fmt: skip
    return out


def hit_rates(theses: Sequence[ClosedThesis]) -> list[TierHitRate]:
    groups: dict[str, list[ClosedThesis]] = defaultdict(list)
    for t in theses:
        groups[t.tier].append(t)
    rows: list[TierHitRate] = []
    for tier, items in sorted(groups.items()):
        scored = [t for t in items if t.beat_benchmark is not None]
        beat = sum(bool(t.beat_benchmark) for t in scored)
        excess = sorted(t.excess_return for t in scored if t.excess_return is not None)
        mid = len(excess) // 2
        median = (
            None
            if not excess
            else excess[mid]
            if len(excess) % 2
            else (excess[mid - 1] + excess[mid]) / 2
        )
        enough = len(scored) >= MIN_CLOSED
        rows.append(
            TierHitRate(
                tier=tier, closed=len(items), with_return=len(scored), beat_benchmark=beat,
                hit_rate=beat / len(scored) if scored and enough else None, enough=enough,
                median_excess=median if enough else None,
            )
        )  # fmt: skip
    return rows


def trigger_summary(theses: Sequence[ClosedThesis]) -> TriggerSummary:
    losing = [t for t in theses if t.trigger_before_close is not None]
    flagged = sum(bool(t.trigger_before_close) for t in losing)
    enough = len(losing) >= MIN_CLOSED
    return TriggerSummary(
        losing_theses=len(losing), flagged_first=flagged,
        share=flagged / len(losing) if losing and enough else None, enough=enough,
    )  # fmt: skip


async def process_report(pool: asyncpg.Pool, owner_id: str) -> ProcessReport:
    entries = await list_entries(pool, owner_id, limit=2000)
    pairs = closed_pairs(entries)
    if not pairs:
        return ProcessReport(
            available=False,
            reason="no thesis is closed yet: the journal needs a buy or add followed by a sell "
            "for the same security",
        )
    stored = await load_active(pool, "strategy", StrategyConfig)
    strategy = stored.body if stored else None
    tiers = (
        {
            t: strategy.classifications.get(t, {}).get("risk") or UNCLASSIFIED
            for t in {p.opening.ticker or "" for p in pairs}
        }
        if strategy
        else {}
    )
    jobs_cfg = await load_active(pool, "jobs", JobsConfig)
    jobs = jobs_cfg.body if jobs_cfg else JobsConfig()
    notes: list[str] = []
    bench: list[tuple[dt.date, float]] = []
    if jobs.prices.benchmark_symbols:
        since = min(p.opening.decided_on for p in pairs) - dt.timedelta(days=10)
        _ref, bars = await index_series(pool, jobs.prices, jobs.prices.benchmark_symbols[0], since)
        bench = [(d, float(c)) for d, c in bars]
    else:
        notes.append("No benchmark is configured, so there is no return to compare with.")
    rows = await pool.fetch(
        "select ticker, first_seen from public.portfolio_breach where ticker is not null"
    )
    triggers: dict[str, list[dt.datetime]] = defaultdict(list)
    for r in rows:
        triggers[r["ticker"]].append(r["first_seen"])
    theses = build_theses(pairs, tiers, bench, triggers)
    notes.append(
        "Price returns without dividends; the stock's in its own currency, the benchmark's in "
        "its quote currency. Findings are counted from the day the system started storing them."
    )
    return ProcessReport(
        available=True, reason=None, theses=theses, by_tier=hit_rates(theses),
        triggers=trigger_summary(theses), notes=notes,
    )  # fmt: skip
