"""Journal gaps (C4): trades the broker sync saw that the decision journal and the switch log
do not explain yet.

The journal and the switch log hold the reasons the owner wrote at the time, and the scorecard
grades those reasons, so nothing here writes to them. A detected trade only becomes a notice in
"Needs you" ("you sold X on Tuesday; write down why"), and a sell and a buy of different stocks
within a few days become one notice offering the switch log. The notice clears the moment the
entry exists, or after `LOOKBACK_DAYS`, so an old trade never nags.
"""

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any

import asyncpg

from steward.journal import entries as journal_entries
from steward.journal import switches as journal_switches
from steward.ops import tasks

logger = logging.getLogger("steward.ops")

LOOKBACK_DAYS = 14
# A journal entry or switch within this many days of a fill explains it.
WINDOW_DAYS = 3
KIND = "journal_gap"


@dataclass(frozen=True)
class Fill:
    ticker: str
    side: str  # "BUY" or "SELL"
    day: dt.date
    amount: float  # absolute value, in the transaction's currency
    currency: str | None


@dataclass(frozen=True)
class Gap:
    subject: str
    title: str
    detail: str
    href: str
    weight: float
    fills: tuple[Fill, ...] = ()
    # "switch" when the fills are a sell and a buy of different stocks, else "trade".
    kind: str = "trade"


def _near(a: dt.date, b: dt.date) -> bool:
    return abs((a - b).days) <= WINDOW_DAYS


def find_gaps(
    fills: list[Fill],
    journal: list[tuple[str, dt.date]],
    switches: list[tuple[str, str, dt.date]],
) -> list[Gap]:
    """The notices to show. `journal` is (ticker, decided_on) and `switches` is
    (sold, bought, switched_on), both as stored."""
    explained = [f for f in fills if any(t == f.ticker and _near(d, f.day) for t, d in journal)]
    open_fills = [f for f in fills if f not in explained]
    gaps: list[Gap] = []
    used: set[Fill] = set()

    # A sell and a buy of different stocks a few days apart look like a switch.
    for sell in (f for f in open_fills if f.side == "SELL"):
        buy = next(
            (
                b
                for b in open_fills
                if b.side == "BUY"
                and b.ticker != sell.ticker
                and b not in used
                and _near(b.day, sell.day)
            ),
            None,
        )
        if buy is None or sell in used:
            continue
        used.update((sell, buy))
        if any(s == sell.ticker and b == buy.ticker and _near(d, sell.day) for s, b, d in switches):
            continue
        gaps.append(
            Gap(
                subject=f"switch:{sell.ticker}>{buy.ticker}:{sell.day}",
                title=f"Did you switch {sell.ticker} into {buy.ticker}?",
                detail=(
                    f"You sold {sell.ticker} on {sell.day} and bought {buy.ticker} on "
                    f"{buy.day}. If it was one decision, add it to the switch log so the "
                    "scorecard can follow it; otherwise write a journal entry for each trade."
                ),
                href="/journal",
                weight=0.6,
                fills=(sell, buy),
                kind="switch",
            )
        )
    for f in open_fills:
        if f in used:
            continue
        verb = "bought" if f.side == "BUY" else "sold"
        gaps.append(
            Gap(
                subject=f"trade:{f.ticker}:{f.side}:{f.day}",
                title=f"Write down why you {verb} {f.ticker}",
                detail=(
                    f"The broker sync saw you {verb} {f.ticker} on {f.day} for about "
                    f"{f.amount:,.0f} {f.currency or ''}".rstrip()
                    + ". The journal is empty for it; the reasons you write now are what the "
                    "scorecard later grades."
                ),
                href="/journal",
                weight=0.5,
                fills=(f,),
            )
        )
    return gaps


async def check_journal_gaps(
    pool: asyncpg.Pool, today: dt.date, owner_id: str | None = None
) -> dict[str, Any]:
    since = today - dt.timedelta(days=LOOKBACK_DAYS)
    rows = await pool.fetch(
        "select ticker, upper(subtype) as side, (occurred_at at time zone 'UTC')::date as day,"
        " abs(amount) as amount, currency from public.transaction"
        " where type = 'order' and upper(subtype) in ('BUY', 'SELL') and ticker is not null"
        " and occurred_at >= $1::date - $2::int order by occurred_at",
        since,
        WINDOW_DAYS,
    )
    # Fills of one order arrive as several rows; one notice per stock, side and day.
    merged: dict[tuple[str, str, dt.date], Fill] = {}
    for r in rows:
        if r["day"] < since:
            continue
        key = (r["ticker"], r["side"], r["day"])
        prev = merged.get(key)
        total = float(r["amount"]) + (prev.amount if prev else 0.0)
        merged[key] = Fill(r["ticker"], r["side"], r["day"], total, r["currency"])
    journal = [
        (r["ticker"], r["decided_on"])
        for r in await pool.fetch(
            "select ticker, decided_on from public.decision_journal"
            " where ticker is not null and decided_on >= $1::date - $2::int"
            " and coalesce(snapshot->>'origin', 'owner') = 'owner'",
            since,
            WINDOW_DAYS,
        )
    ]
    switches = [
        (r["sold_ticker"], r["bought_ticker"], r["switched_on"])
        for r in await pool.fetch(
            "select sold_ticker, bought_ticker, switched_on from public.switch_log"
            " where switched_on >= $1::date - $2::int"
            " and coalesce(snapshot->>'origin', 'owner') = 'owner'",
            since,
            WINDOW_DAYS,
        )
    ]
    gaps = find_gaps(list(merged.values()), journal, switches)
    for g in gaps:
        await tasks.see(
            pool,
            KIND,
            g.subject,
            title=g.title,
            detail=g.detail,
            href=g.href,
            weight=g.weight,
            status="needs_owner",
        )
    logged = await autolog(pool, str(owner_id), gaps) if owner_id else None
    return {
        "gaps": [g.subject for g in gaps],
        "resolved": await tasks.settle(pool, KIND, {g.subject for g in gaps}),
        "logged": logged,
    }


AUTO_HYPOTHESIS = (
    "Logged automatically from your broker sync. The reasons for this trade have not been "
    "written yet; add a review entry to this one with yours."
)


async def autolog(pool: asyncpg.Pool, owner_id: str, gaps: list[Gap]) -> dict[str, int]:
    """Put each unexplained trade on the timeline: a journal entry for a trade, a switch-log row
    for a sell and buy pair. They are marked `origin: auto`, hold no reasons, and are left out
    of the scorecard and the process review, so nothing here is graded as a decision you made.
    Written once per trade."""
    entries = switches = 0
    for g in gaps:
        if g.kind == "switch":
            sell, buy = g.fills
            known = await pool.fetchval(
                "select exists (select 1 from public.switch_log where owner_id = $1"
                " and sold_ticker = $2 and bought_ticker = $3 and switched_on = $4)",
                owner_id,
                sell.ticker,
                buy.ticker,
                sell.day,
            )
            if not known:
                await journal_switches.record(
                    pool,
                    owner_id,
                    journal_switches.SwitchIn(
                        sold_ticker=sell.ticker,
                        bought_ticker=buy.ticker,
                        switched_on=sell.day,
                        sold_value_base=None,
                        note="Detected from your trades; add the reasons in the note.",
                    ),
                    origin="auto",
                )
                switches += 1
            continue
        (fill,) = g.fills
        decision = "buy" if fill.side == "BUY" else "sell"
        known = await pool.fetchval(
            "select exists (select 1 from public.decision_journal where owner_id = $1"
            " and ticker = $2 and decision = $3 and decided_on = $4"
            " and snapshot->>'origin' = 'auto')",
            owner_id,
            fill.ticker,
            decision,
            fill.day,
        )
        if not known:
            await journal_entries.record(
                pool,
                owner_id,
                journal_entries.JournalEntryIn.model_validate(
                    {
                        "scope": "security",
                        "ticker": fill.ticker,
                        "decision": decision,
                        "decided_on": fill.day,
                        "hypothesis": AUTO_HYPOTHESIS,
                    }
                ),
                origin="auto",
            )
            entries += 1
    return {"entries": entries, "switches": switches}
