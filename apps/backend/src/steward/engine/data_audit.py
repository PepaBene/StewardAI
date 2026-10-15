"""A second look at data nobody else checks (autonomous engine plan, L2): prices, fund holdings
and the written digests. Each audit is a function `(pool, now) -> findings` listed in `AUDITS`, so
a new data source gets a check by adding one entry; `run` turns the findings into one notice per
subject and resolves the notices of subjects that are fine again. Audits only read.

- prices: two sources that stored a different close for the same day, and a one-day move so large
  it is more likely a missing split than a real move.
- fund holdings: a fund's latest holdings that do not add up to the whole fund, or one holding
  that is half the fund.
- digests: a recent digest in which the claim verifier could not support many of the sentences.
- macro: values dated before their release, revisions out of order, two sources that disagree.
- portfolio: the latest sync's positions, cash and total do not add up, a position without a
  quantity or price, a sync that is too old.
- stale prices: a held stock whose newest stored close is old.
- statements: a balance sheet where assets differ from liabilities plus equity, a figure restated
  by more than 5%.
- graph edges: a confirmed link of a company to itself, or the same pair both partner and
  competitor.
"""

import datetime as dt
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import asyncpg

from steward.autopilot import ledger
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


SNAPSHOT_TOLERANCE = 0.01  # the sums may differ from the broker's totals by 1%
SNAPSHOT_MAX_AGE_DAYS = 3
PRICE_MAX_AGE_DAYS = 6


def portfolio_problems(
    day: dt.date,
    positions_sum: float,
    invested: float,
    cash: float,
    total: float,
    bad_rows: int,
    now: dt.date,
) -> list[Finding]:
    """Invariants of the latest sync: the positions add up to what the broker says is invested,
    cash and invested add up to the total, nothing has a zero quantity or price, and the sync is
    recent."""
    found: list[Finding] = []
    if relative_gap(positions_sum, invested) > SNAPSHOT_TOLERANCE:
        found.append(
            Finding(
                f"portfolio:positions:{day.isoformat()}",
                f"The positions of {day:%d %b} do not add up to the invested value",
                f"They sum to {positions_sum:,.0f}; the broker reports {invested:,.0f} invested. "
                "A position is missing, doubled or priced wrongly in the sync.",
            )
        )
    if relative_gap(invested + cash, total) > SNAPSHOT_TOLERANCE:
        found.append(
            Finding(
                f"portfolio:total:{day.isoformat()}",
                f"Cash and invested value of {day:%d %b} do not add up to the account total",
                f"{invested:,.0f} invested plus {cash:,.0f} cash is not the reported {total:,.0f}.",
            )
        )
    if bad_rows:
        found.append(
            Finding(
                f"portfolio:rows:{day.isoformat()}",
                f"{bad_rows} position(s) of {day:%d %b} have no quantity or no price",
                "The sync stored a position with a zero or negative quantity or price.",
            )
        )
    if (now - day).days > SNAPSHOT_MAX_AGE_DAYS:
        found.append(
            Finding(
                "portfolio:stale",
                f"The last portfolio sync is from {day:%d %b}",
                f"It is {(now - day).days} days old. A sync was missed or is failing.",
            )
        )
    return found


async def portfolio(pool: asyncpg.Pool, now: dt.datetime) -> list[Finding]:
    row = await pool.fetchrow(
        "select a.snapshot_date, a.invested_value, a.cash_available, a.total_value,"
        " (select coalesce(sum(p.value_account), 0) from public.position_snapshot p"
        "  where p.snapshot_date = a.snapshot_date) as positions,"
        " (select count(*) from public.position_snapshot p where p.snapshot_date = a.snapshot_date"
        "  and (p.quantity <= 0 or p.current_price <= 0)) as bad"
        " from public.account_snapshot a order by a.snapshot_date desc limit 1"
    )
    if row is None:
        return []
    return portfolio_problems(
        row["snapshot_date"],
        float(row["positions"]),
        float(row["invested_value"]),
        float(row["cash_available"]),
        float(row["total_value"]),
        int(row["bad"]),
        now.date(),
    )


async def stale_prices(pool: asyncpg.Pool, now: dt.datetime) -> list[Finding]:
    """A held stock whose newest stored close is old, which would quietly freeze its numbers."""
    held = list(await ledger.holding_weights(pool))
    if not held:
        return []
    rows = await pool.fetch(
        "select i.t212_ticker as ticker, max(b.date) as last_bar from public.instrument i"
        " left join public.price_bar b on b.symbol = i.price_symbol"
        " where i.t212_ticker = any($1::text[]) and i.price_symbol is not null"
        " group by i.t212_ticker",
        held,
    )
    found = []
    for r in rows:
        age = (now.date() - r["last_bar"]).days if r["last_bar"] else None
        if age is None or age > PRICE_MAX_AGE_DAYS:
            found.append(
                Finding(
                    f"stale:{r['ticker']}",
                    f"{r['ticker']} has no recent price",
                    "No price has ever been stored."
                    if age is None
                    else f"The newest stored close is {age} days old.",
                )
            )
    return found


ALERT_DAYS = 7
ALERT_STUCK_HOURS = 6


def undelivered_problem(channel: str, failed: int, stuck: int) -> Finding | None:
    if not failed and not stuck:
        return None
    parts = []
    if failed:
        parts.append(f"{failed} failed to send in the last {ALERT_DAYS} days")
    if stuck:
        parts.append(
            f"{stuck} are still waiting more than {ALERT_STUCK_HOURS} hours after they were due"
        )
    return Finding(
        f"alerts:{channel}",
        f"Alerts by {channel} are not getting through",
        "; ".join(parts) + ". You may have missed something; check the channel's settings.",
    )


async def undelivered_alerts(pool: asyncpg.Pool, now: dt.datetime) -> list[Finding]:
    """Alerts that failed or never left: a dead letter would otherwise be silent."""
    rows = await pool.fetch(
        "select channel, count(*) filter (where status = 'failed') as failed,"
        " count(*) filter (where status = 'pending' and send_after < $2) as stuck"
        " from public.alert where created_at >= $1 group by channel",
        now - dt.timedelta(days=ALERT_DAYS),
        now - dt.timedelta(hours=ALERT_STUCK_HOURS),
    )
    return [
        f
        for r in rows
        if (f := undelivered_problem(r["channel"], r["failed"], r["stuck"])) is not None
    ]


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


STATEMENT_DAYS = 30
STATEMENT_TOLERANCE = 0.005  # assets and liabilities plus equity differ by more than 0.5%
RESTATED_BY = 0.05  # a restated figure moved by more than 5%
ASSETS = ("us-gaap:Assets", "ifrs-full:Assets")
LIABILITIES_AND_EQUITY = (
    "us-gaap:LiabilitiesAndStockholdersEquity",
    "ifrs-full:EquityAndLiabilities",
)


def statement_problem(ticker: str, period: dt.date, assets: float, claims: float) -> Finding | None:
    """Assets must equal liabilities plus equity; anything else is a misread or a missing line."""
    if relative_gap(assets, claims) <= STATEMENT_TOLERANCE:
        return None
    return Finding(
        f"statement:balance:{ticker}:{period.isoformat()}",
        f"{ticker}: the balance sheet of {period:%d %b %Y} does not balance",
        f"Assets are {assets:,.0f} but liabilities and equity are {claims:,.0f} "
        f"({relative_gap(assets, claims):.1%} apart). A line was misread, scaled wrongly or "
        "is missing.",
    )


def restatement_problem(
    ticker: str, concept: str, period: dt.date, before: float, after: float
) -> Finding | None:
    if relative_gap(before, after) <= RESTATED_BY:
        return None
    return Finding(
        f"statement:restated:{ticker}:{concept}:{period.isoformat()}",
        f"{ticker}: {concept.split(':')[-1]} for {period:%d %b %Y} was restated",
        f"It was {before:,.0f} and is now {after:,.0f} ({relative_gap(before, after):.1%}). "
        "Check that anything built on the earlier figure (a verdict, a ratio) is still right.",
    )


async def statements(pool: asyncpg.Pool, now: dt.datetime) -> list[Finding]:
    """Cross-statement identities on filings stored in the last month, and restated figures."""
    since = now - dt.timedelta(days=STATEMENT_DAYS)
    found: list[Finding] = []
    for r in await pool.fetch(
        "select a.ticker, a.period_end, a.value as assets, l.value as claims"
        " from public.financial_statement_fact a join public.financial_statement_fact l"
        " on l.ticker = a.ticker and l.period_end = a.period_end and l.accession = a.accession"
        " and l.statement = 'balance_sheet' and l.concept = any($3)"
        " where a.statement = 'balance_sheet' and a.concept = any($2) and a.known_at >= $1"
        " and a.dimensions = '{}'::jsonb and l.dimensions = '{}'::jsonb"
        " and a.value is not null and l.value is not null and a.ticker is not null"
        " order by a.ticker, a.period_end limit 100",
        since,
        list(ASSETS),
        list(LIABILITIES_AND_EQUITY),
    ):
        if f := statement_problem(r["ticker"], r["period_end"], r["assets"], r["claims"]):
            found.append(f)
    for r in await pool.fetch(
        "select n.ticker, n.concept, n.period_end, o.value as before, n.value as after"
        " from public.financial_statement_fact n join public.financial_statement_fact o"
        " on o.id = n.amends_fact_id where n.known_at >= $1 and n.value is not null"
        " and o.value is not null and n.ticker is not null and n.dimensions = '{}'::jsonb"
        " order by n.ticker, n.period_end limit 100",
        since,
    ):
        if f := restatement_problem(
            r["ticker"], r["concept"], r["period_end"], float(r["before"]), float(r["after"])
        ):
            found.append(f)
    return found


# A confirmed link is only as good as its evidence: flag the ones that cannot be right.
OPPOSED = (("partner", "competitor"), ("customer", "competitor"), ("supplier", "competitor"))


async def graph_edges(pool: asyncpg.Pool, now: dt.datetime) -> list[Finding]:
    """Confirmed company links that are implausible: a company linked to itself, the same pair
    both partner and competitor. (Single-source links are not flagged: the owner confirming a
    link is itself a decision, and flagging all of them would bury the real problems.)"""
    found: list[Finding] = []
    for r in await pool.fetch(
        "select id, subject_key, object_key, edge_type from public.company_graph_edge"
        " where status = 'confirmed' and subject_key = object_key order by id limit 50"
    ):
        found.append(
            Finding(
                f"graph:self:{r['id']}",
                f"{r['subject_key']} is linked to itself as {r['edge_type']}",
                "A confirmed link whose two ends are the same company. It is probably a "
                "name-matching mistake.",
            )
        )
    for a, b in OPPOSED:
        for r in await pool.fetch(
            "select x.subject_key, x.object_key from public.company_graph_edge x"
            " join public.company_graph_edge y on y.subject_key = x.subject_key"
            " and y.object_key = x.object_key and y.status = 'confirmed'"
            " and y.edge_type = $2 where x.status = 'confirmed' and x.edge_type = $1"
            " and (x.valid_to is null or x.valid_to >= current_date)"
            " and (y.valid_to is null or y.valid_to >= current_date)"
            " group by x.subject_key, x.object_key limit 50",
            a,
            b,
        ):
            found.append(
                Finding(
                    f"graph:opposed:{r['subject_key']}:{r['object_key']}:{a}:{b}",
                    f"{r['subject_key']} and {r['object_key']} are both {a} and {b}",
                    "Both links are confirmed and neither has ended. One of them is probably "
                    "out of date or wrong.",
                )
            )
    return found


CHAT_DAYS = 30
CHAT_MIN_GAPS = 3


def chat_gap_problem(questions: list[str]) -> Finding | None:
    """One rolled-up notice: questions the chat could not back with evidence. A single one is
    noise; several are a missing tool or data source."""
    if len(questions) < CHAT_MIN_GAPS:
        return None
    shown = "; ".join(f"“{q[:80]}”" for q in questions[:5])
    return Finding(
        "chat:gaps",
        f"{len(questions)} chat questions could not be answered with evidence",
        f"In the last {CHAT_DAYS} days, for example: {shown}. Each is a missing tool, a missing "
        "data source or a question the app should say it cannot answer; add the ones that "
        "matter to the chat benchmark.",
    )


async def chat_gaps(pool: asyncpg.Pool, now: dt.datetime) -> list[Finding]:
    """The backlog of questions the chat answered with no checked, sourced claim."""
    rows = await pool.fetch(
        "select q.content from public.chat_message a"
        " join lateral (select content from public.chat_message u"
        "   where u.conversation_id = a.conversation_id and u.role = 'user' and u.id < a.id"
        "   order by u.id desc limit 1) q on true"
        " where a.role = 'assistant' and a.created_at >= $1"
        " and a.body->>'shape' = 'low_evidence' order by a.id desc limit 50",
        now - dt.timedelta(days=CHAT_DAYS),
    )
    found = chat_gap_problem(list(dict.fromkeys(r["content"] for r in rows)))
    return [found] if found else []


Audit = Callable[[asyncpg.Pool, dt.datetime], Awaitable[list[Finding]]]
AUDITS: dict[str, Audit] = {
    "chat_gaps": chat_gaps,
    "statements": statements,
    "graph_edges": graph_edges,
    "prices": prices,
    "fund_holdings": fund_holdings,
    "digests": digests,
    "macro": macro,
    "portfolio": portfolio,
    "stale_prices": stale_prices,
    "undelivered_alerts": undelivered_alerts,
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
