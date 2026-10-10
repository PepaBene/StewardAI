"""P3.6, P4.6, P5.8: alerts raised from stored disclosures, once each.

Three kinds, all written to `disclosure_alert` with a `dedupe_key` so a job that runs twice, or an
amendment that repeats a trade, raises one alert:

  * insider_trade       a material, verified open-market buy or sell (never a plan trade, a bundled
                        sale, a grant, a gift, an unknown, or anything from a failed collection);
  * major_holder        a new 13D/G filing on a held share class, or an amendment whose stake moved
                        by at least a stated number of points (never a 13F quarter-to-quarter move,
                        which is not trading intent);
  * relationship_change a confirmed material customer/supplier relationship that appeared or ended.

P8.5 adds four more, each stored with `why_fired` (rule name, threshold, inputs):

  * controlling_stake       a verified 13D/G stake that crossed a stated control threshold;
  * insider_buy_cluster     discretionary open-market buys by several different insiders in a
                            window;
  * estimate_revision       a comparable published-consensus revision past a stated size, and only
                            once the revision review gate is met;
  * debt_maturity_change    the near-term share of a debt maturity schedule moved between two
                            filings.

An alert states what was filed and which rule fired, never a motive. Anything unverified (a
suggested edge, a non-comparable or ungated estimate revision) becomes a review task through
`review.enqueue`, never an alert. A collection that failed stored nothing, so it cannot alert; a
scan that errors is counted in `failed`. Each (ticker, kind) raises at most `THROTTLE_PER_DAY`
alerts a day.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from functools import partial
from typing import Any

import asyncpg

from steward.disclosure import dependency_read, estimate_read, insider_analysis, review
from steward.disclosure import estimates as est
from steward.disclosure.note_store import CONCEPT_PREFIX, NOTE_SOURCE

log = logging.getLogger(__name__)

type Pool = asyncpg.Pool | asyncpg.Connection

THROTTLE_PER_DAY = 5
CONTROL_THRESHOLD = Decimal(25)
CLUSTER_MIN_INSIDERS = 3
REVISION_MIN_PCT = Decimal(5)
DEBT_SHIFT_MIN_POINTS = Decimal(10)
LOOKBACK_DAYS = 30


def why(rule: str, threshold: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """The explanation stored with an alert: which rule, its threshold, the values it saw."""
    return {"rule": rule, "threshold": threshold, "inputs": inputs}


async def record(
    pool: Pool,
    *,
    kind: str,
    ticker: str,
    dedupe_key: str,
    headline: str,
    detail: dict[str, Any],
    known_at: dt.datetime,
    source_url: str | None = None,
    why_fired: dict[str, Any] | None = None,
    throttle: int | None = THROTTLE_PER_DAY,
) -> bool:
    """Store one alert; False when it was already raised or the (ticker, kind) throttle is full.
    A throttled alert stores nothing, so a later scan can still raise it once the day has passed."""
    if throttle is not None:
        recent = await pool.fetchval(
            """select count(*) from public.disclosure_alert
                where ticker = $1 and kind = $2 and created_at > now() - interval '1 day'""",
            ticker,
            kind,
        )
        if recent is not None and int(recent) >= throttle:
            return False
    new_id = await pool.fetchval(
        """insert into public.disclosure_alert
             (kind, ticker, dedupe_key, headline, detail, source_url, known_at, why_fired)
           values ($1,$2,$3,$4,$5::jsonb,$6,$7,$8::jsonb)
           on conflict (dedupe_key) do nothing returning id""",
        kind,
        ticker,
        dedupe_key,
        headline,
        json.dumps(detail, default=str),
        source_url,
        known_at,
        None if why_fired is None else json.dumps(why_fired, default=str),
    )
    return new_id is not None


# ----------------------------------------------------------------------------- pure rules
def crossed_control(prior: Decimal | None, current: Decimal | None, threshold: Decimal) -> bool:
    """A reported stake at or above `threshold` that was not already there. An unstated percentage
    never crosses; a first filing (no prior) at or above the threshold does."""
    if current is None or current < threshold:
        return False
    return prior is None or prior < threshold


@dataclass(frozen=True)
class RevisionDecision:
    action: str  # "alert" | "review" | "skip"
    reason: str
    review_reason: str | None = None


def revision_decision(
    pair: est.Pair, *, gate_met: bool, series_ok: bool, min_pct: Decimal = REVISION_MIN_PCT
) -> RevisionDecision:
    """A revision alerts only when it is a published-consensus pair, comparable, large enough and
    the owner's 20-pair review gate is met. A non-comparable pair or an ungated rule goes to
    review."""
    if not series_ok:
        return RevisionDecision("skip", "only published consensus revisions are alertable")
    if not pair.comparable:
        return RevisionDecision("review", "not comparable", "format_change")
    if pair.change_pct is None:
        return RevisionDecision("skip", "no percent change is defined")
    if abs(pair.change_pct) < min_pct:
        return RevisionDecision("skip", "below the revision threshold")
    if not gate_met:
        return RevisionDecision("review", "revision review gate not met", "low_confidence")
    return RevisionDecision("alert", "comparable revision past threshold, gate met")


def near_term_share(buckets: dict[str, Decimal]) -> Decimal | None:
    """Percent of the scheduled principal due in year 1. Uses year_N and thereafter buckets only,
    never the stated total; None when year 1 or the denominator is missing or not positive."""
    parts = {k: v for k, v in buckets.items() if k.startswith("year_") or k == "thereafter"}
    total = sum(parts.values(), Decimal(0))
    if "year_1" not in parts or total <= 0:
        return None
    return parts["year_1"] / total * 100


def maturity_shift(
    prior: dict[str, Decimal], current: dict[str, Decimal], min_points: Decimal
) -> tuple[Decimal, Decimal, Decimal] | None:
    """(prior share, current share, move in points) when the near-term share moved by at least
    `min_points`; None when either schedule cannot state a share or the move is small."""
    before, after = near_term_share(prior), near_term_share(current)
    if before is None or after is None:
        return None
    move = after - before
    return (before, after, move) if abs(move) >= min_points else None


def _money(value: Decimal) -> str:
    return f"${value:,.0f}"


async def scan_insiders(
    pool: Pool, ticker: str, now: dt.datetime, *, min_value: Decimal = Decimal(100_000)
) -> int:
    raised = 0
    for a in await insider_analysis.pending_alerts(pool, ticker, min_value, now):
        t = a.tx
        lines = a.rows or (t,)
        accessions = sorted({r.accession for r in lines})
        # The key names the person, day and direction, never split-restated share counts, so a
        # later split cannot make an old trade look new. An alert stored under the earlier key
        # format (shares and price in the key) for one of these filings already covers it.
        already = await pool.fetchval(
            """select exists (select 1 from public.disclosure_alert
                               where kind = 'insider_trade' and ticker = $1
                                 and detail ->> 'insider' = $2
                                 and detail ->> 'accession' = any($3::text[]))""",
            ticker,
            t.insider,
            accessions,
        )
        if already:
            continue
        who = t.name or t.insider
        total = a.shares
        shares = f"{total:,.0f}" if total is not None else "an unknown number of"
        url = await pool.fetchval(
            "select source_url from public.insider_filing where accession = $1", t.accession
        )
        raised += await record(
            pool,
            kind="insider_trade",
            ticker=ticker,
            dedupe_key=f"insider:{ticker}:{t.insider}:{t.tx_date}:{a.reason}",
            headline=f"{who} reported an {a.reason} of {shares} shares ({_money(a.value)})",
            detail={
                "accession": t.accession,
                "accessions": accessions,
                "lines": len(lines),
                "row_index": t.row_index,
                "insider": t.insider,
                "role": t.role,
                "transaction_date": t.tx_date,
                "filed": t.filed,
                "value": str(a.value),
                "plan": "unknown" if t.plan is None else str(t.plan).lower(),
            },
            known_at=t.known_at,
            source_url=url,
            why_fired=why(
                "material_insider_trade",
                f"verified open-market trade of at least {_money(min_value)}",
                {"value": str(a.value), "reason": a.reason, "plan_trade": False},
            ),
        )
    return raised


async def scan_major_holders(
    pool: Pool,
    ticker: str,
    since: dt.datetime,
    now: dt.datetime,
    *,
    min_points: Decimal = Decimal(1),
) -> int:
    """New 13D/G filings and material stake amendments on a share class matched by CUSIP."""
    rows = await pool.fetch(
        """select f.accession, f.schedule, f.is_amendment, f.stake_percent, f.stake_shares,
                  f.filed, f.known_at, f.source_url, f.filer_cik, f.prior_accession,
                  p.stake_percent as prior_percent,
                  (select h.person_name from public.control_stake_holder h
                    where h.accession = f.accession
                    order by h.aggregate_shares desc nulls last, h.row_index limit 1) as holder
             from public.control_stake_filing f
             join public.control_stake_issuer_link l on l.accession = f.accession
             left join public.control_stake_filing p on p.accession = f.prior_accession
            where l.ticker = $1 and l.class_status = 'cusip_match'
              and (f.known_at > $2 or (f.loaded_at > $2 and f.loaded_at <= $3))
              and f.known_at <= $3
            order by f.known_at, f.accession""",
        ticker,
        since,
        now,
    )
    raised = 0
    for r in rows:
        who = r["holder"] or r["filer_cik"] or "an unnamed filer"
        pct = r["stake_percent"]
        if r["is_amendment"] and r["prior_percent"] is not None and pct is not None:
            move = Decimal(pct) - Decimal(r["prior_percent"])
            if abs(move) < min_points:
                continue
            headline = (
                f"{who} amended its Schedule {r['schedule']}: reported stake "
                f"{r['prior_percent']}% to {pct}% ({move:+.2f} points)"
            )
        elif r["is_amendment"]:
            continue  # an amendment with no comparable figure is not a measured change
        else:
            shown = "no stated percentage" if pct is None else f"{pct}%"
            headline = f"{who} filed a Schedule {r['schedule']} reporting {shown} of the class"
        raised += await record(
            pool,
            kind="major_holder",
            ticker=ticker,
            dedupe_key=f"holder:{r['accession']}",
            headline=headline,
            detail={
                "accession": r["accession"],
                "schedule": r["schedule"],
                "amendment": r["is_amendment"],
                "stake_percent": None if pct is None else str(pct),
                "prior_percent": None if r["prior_percent"] is None else str(r["prior_percent"]),
                "filed": r["filed"],
            },
            known_at=r["known_at"],
            source_url=r["source_url"],
            why_fired=why(
                "major_holder_filing",
                f"new 13D/G, or an amendment moving the stake by at least {min_points} points",
                {
                    "stake_percent": None if pct is None else str(pct),
                    "prior_percent": None
                    if r["prior_percent"] is None
                    else str(r["prior_percent"]),
                    "amendment": r["is_amendment"],
                },
            ),
        )
    return raised


async def scan_controlling_stakes(
    pool: Pool,
    ticker: str,
    since: dt.datetime,
    now: dt.datetime,
    *,
    threshold: Decimal = CONTROL_THRESHOLD,
) -> int:
    """A 13D/G stake on a class matched by CUSIP (the only check a 13D/G row carries) that reached
    `threshold` percent and was below it before. The prior stake is the linked prior filing, else
    the same filer's latest earlier filing on that class. An amendment with no earlier filing
    found is not a crossing: the stake may have been above the threshold all along. A stake with
    no stated percentage never alerts."""
    rows = await pool.fetch(
        """select f.accession, f.schedule, f.stake_percent, f.filed, f.known_at, f.source_url,
                  f.filer_cik, f.is_amendment, prior.stake_percent as prior_percent,
                  prior.accession as prior_found,
                  (select h.person_name from public.control_stake_holder h
                    where h.accession = f.accession
                    order by h.aggregate_shares desc nulls last, h.row_index limit 1) as holder
             from public.control_stake_filing f
             join public.control_stake_issuer_link l on l.accession = f.accession
             left join lateral (
               select q.accession, q.stake_percent
                 from public.control_stake_filing q
                 join public.control_stake_issuer_link lq on lq.accession = q.accession
                where (q.accession = f.prior_accession
                       or (f.prior_accession is null and q.filer_cik = f.filer_cik
                           and lq.ticker = $1 and q.known_at < f.known_at))
                order by (q.accession = f.prior_accession) desc, q.known_at desc
                limit 1) prior on true
            where l.ticker = $1 and l.class_status = 'cusip_match'
              and (f.known_at > $2 or (f.loaded_at > $2 and f.loaded_at <= $3))
              and f.known_at <= $3
            order by f.known_at, f.accession""",
        ticker,
        since,
        now,
    )
    raised = 0
    for r in rows:
        pct = None if r["stake_percent"] is None else Decimal(r["stake_percent"])
        prior = None if r["prior_percent"] is None else Decimal(r["prior_percent"])
        if r["is_amendment"] and r["prior_found"] is None:
            continue
        if r["prior_found"] is not None and prior is None:
            continue  # an earlier filing exists but states no percentage: no crossing is shown
        if not crossed_control(prior, pct, threshold):
            continue
        who = r["holder"] or r["filer_cik"] or "an unnamed filer"
        raised += await record(
            pool,
            kind="controlling_stake",
            ticker=ticker,
            dedupe_key=f"control:{r['accession']}",
            headline=f"{who} reports {pct}% of {ticker} in a Schedule {r['schedule']}",
            detail={"accession": r["accession"], "schedule": r["schedule"], "filed": r["filed"]},
            known_at=r["known_at"],
            source_url=r["source_url"],
            why_fired=why(
                "controlling_stake_crossed",
                f"reported stake at or above {threshold}% and not previously",
                {"stake_percent": str(pct), "prior_percent": None if prior is None else str(prior)},
            ),
        )
    return raised


async def scan_insider_clusters(
    pool: Pool, ticker: str, now: dt.datetime, *, since: dt.datetime
) -> int:
    """One alert per cluster of discretionary open-market buys by several different insiders,
    keyed by the cluster's first date so a cluster that grows is not alerted twice. Only clusters
    that ended inside the window are raised."""
    view = await insider_analysis.insider_activity(pool, ticker, now)
    raised = 0
    for c in view["clusters"]:
        if c["end"] is None or c["end"] < since.date() or c["insiders"] < CLUSTER_MIN_INSIDERS:
            continue
        raised += await record(
            pool,
            kind="insider_buy_cluster",
            ticker=ticker,
            dedupe_key=f"cluster:{ticker}:{c['start']}",
            headline=(
                f"{c['insiders']} insiders bought {ticker} in the open market between "
                f"{c['start']} and {c['end']} ({_money(Decimal(c['value']))})"
            ),
            detail={"start": c["start"], "end": c["end"], "shares": c["shares"]},
            known_at=now,
            why_fired=why(
                "insider_buy_cluster",
                f"{CLUSTER_MIN_INSIDERS} or more different insiders, discretionary buys, 30 days",
                {"insiders": c["insiders"], "value": c["value"], "shares": c["shares"]},
            ),
        )
    return raised


async def scan_estimate_revisions(
    pool: Pool, ticker: str, since: dt.datetime, now: dt.datetime
) -> tuple[int, int]:
    """(alerts, review tasks). A published-consensus revision is alerted only when comparable,
    at least `REVISION_MIN_PCT` percent and the revision review gate is met. A non-comparable pair,
    or any large pair while the gate is unmet, opens a review task on the later estimate value."""
    issuer = await estimate_read.issuer_for_ticker(pool, ticker)
    if issuer is None:
        return 0, 0
    rows = await pool.fetch(estimate_read.VALUE_SQL, int(issuer["id"]), now)
    # Only what the estimates page shows: a file replaced by a correction known by now is gone,
    # so a superseded file and its correction never form a "revision".
    values = est.visible([estimate_read.to_value(r) for r in rows], now)
    g = await estimate_read.gate(pool)
    alerted = reviewed = 0
    for p in est.revision_pairs(v for v in values if not est.problems(v)):
        if not (since < p.later_known_at <= now):
            continue
        d = revision_decision(
            p, gate_met=g.met, series_ok=p.series == "published_analyst_consensus"
        )
        if d.action == "review" and d.review_reason is not None:
            opened = await review.enqueue(
                pool,
                "estimate_value",
                p.later_id,
                d.review_reason,  # type: ignore[arg-type]
                {
                    "ticker": ticker,
                    "metric": p.metric,
                    "fiscal_period": p.fiscal_period,
                    "series": p.series,
                    "why": d.reason,
                    "break_reasons": list(p.reasons),
                    "change_pct": None if p.change_pct is None else str(p.change_pct),
                },
            )
            reviewed += opened is not None
        elif d.action == "alert" and p.change_pct is not None:
            alerted += await record(
                pool,
                kind="estimate_revision",
                ticker=ticker,
                dedupe_key=f"estimate:{p.earlier_id}:{p.later_id}",
                headline=(
                    f"{ticker} consensus {p.metric} for {p.fiscal_period} moved "
                    f"{p.change_pct:+.1f}% ({p.earlier_value} to {p.later_value})"
                ),
                detail={"earlier_value_id": p.earlier_id, "later_value_id": p.later_id},
                known_at=p.later_known_at,
                why_fired=why(
                    "consensus_revision",
                    f"comparable published-consensus revision of at least {REVISION_MIN_PCT}% "
                    "and review gate met",
                    {
                        "change_pct": str(p.change_pct),
                        "earlier_value": str(p.earlier_value),
                        "later_value": str(p.later_value),
                        "gate": f"{g.correct}/{g.reviewed} reviewed correct",
                    },
                ),
            )
    return alerted, reviewed


async def scan_suggested_edges(
    pool: Pool, ticker: str, since: dt.datetime, now: dt.datetime
) -> int:
    """Machine-suggested relationship claims about a holding become review tasks, never alerts."""
    rows = await pool.fetch(
        """select c.id, c.claim_type, c.object_label, c.confidence, c.known_at
             from public.extracted_claim c
             join public.instrument n on n.t212_ticker = $1
             join public.issuer i on i.cik = n.cik
            where (c.subject_issuer_id = i.id or c.object_issuer_id = i.id)
              and c.claim_type like 'relationship%' and c.review_state = 'machine_suggested'
              and c.known_at > $2 and c.known_at <= $3""",
        ticker,
        since,
        now,
    )
    opened = 0
    for r in rows:
        made = await review.enqueue(
            pool,
            "claim",
            int(r["id"]),
            "low_confidence",
            {
                "ticker": ticker,
                "claim_type": r["claim_type"],
                "counterparty": r["object_label"],
                "confidence": None if r["confidence"] is None else str(r["confidence"]),
                "why": "suggested edge: a person must confirm it before it can alert",
            },
        )
        opened += made is not None
    return opened


async def scan_debt_maturity(
    pool: Pool, ticker: str, now: dt.datetime, *, since: dt.datetime | None = None
) -> int:
    """Compare the near-term share of the two latest filings' debt maturity schedules. With
    `since`, only a schedule that became known after it can alert, so an old shift found late (a
    backfill) is not raised as news."""
    rows = await pool.fetch(
        """select accession, known_at, source_url, value,
                  dimensions #>> '{note,bucket}' as bucket, dimensions #>> '{note,status}' as status
             from public.financial_statement_fact
            where ticker = $1 and statement = 'note' and source = $2 and concept = $3
              and known_at <= $4 and value is not null""",
        ticker,
        NOTE_SOURCE,
        CONCEPT_PREFIX + "debt_maturity",
        now,
    )
    by_filing: dict[str, dict[str, Any]] = {}
    # A schedule with a bucket awaiting review is incomplete: the near-term share of what is left
    # could read "due within a year" for a schedule whose long maturities are the missing part.
    incomplete = {r["accession"] for r in rows if r["status"] == "review" or r["bucket"] is None}
    for r in rows:
        if r["accession"] in incomplete:
            continue
        f = by_filing.setdefault(
            r["accession"],
            {"known_at": r["known_at"], "url": r["source_url"], "buckets": {}},
        )
        f["buckets"][r["bucket"]] = Decimal(r["value"])
    ordered = sorted(by_filing.items(), key=lambda kv: (kv[1]["known_at"], kv[0]))
    if len(ordered) < 2:
        return 0
    (_, prior), (acc, cur) = ordered[-2], ordered[-1]
    if since is not None and cur["known_at"] <= since:
        return 0
    shift = maturity_shift(prior["buckets"], cur["buckets"], DEBT_SHIFT_MIN_POINTS)
    if shift is None:
        return 0
    before, after, move = shift
    return await record(
        pool,
        kind="debt_maturity_change",
        ticker=ticker,
        dedupe_key=f"debt:{acc}",
        headline=(
            f"{ticker} debt due within a year went from {before:.0f}% to {after:.0f}% of the "
            "scheduled principal"
        ),
        detail={"accession": acc},
        known_at=cur["known_at"],
        source_url=cur["url"],
        why_fired=why(
            "debt_maturity_shift",
            f"year-1 share of scheduled principal moved by at least {DEBT_SHIFT_MIN_POINTS} points",
            {
                "prior_share": f"{before:.1f}",
                "current_share": f"{after:.1f}",
                "move_points": f"{move:+.1f}",
            },
        ),
    )


async def scan_relationships(
    pool: Pool,
    ticker: str,
    since: dt.datetime,
    now: dt.datetime,
    *,
    min_share: Decimal = Decimal("0.1"),
) -> int:
    raised = 0
    for c in await dependency_read.relationship_changes(
        pool, ticker, since, now, min_share=min_share
    ):
        share = "" if c["share"] is None else f" ({Decimal(c['share']) * 100:.0f}% reported)"
        verb = "now discloses" if c["kind"] == "new" else "no longer lists"
        raised += await record(
            pool,
            kind="relationship_change",
            ticker=ticker,
            dedupe_key=f"relationship:{c['kind']}:{c['claim_id']}",
            headline=f"{ticker} {verb} {c['counterparty']} as a {c['relationship']}{share}",
            detail={
                "claim_id": c["claim_id"],
                "change": c["kind"],
                "passage": c["passage"][:400],
            },
            known_at=c["known_at"],
            source_url=c["source_url"],
            why_fired=why(
                "confirmed_relationship_change",
                f"confirmed relationship worth at least {min_share * 100:.0f}% when stated",
                {"change": c["kind"], "share": None if c["share"] is None else str(c["share"])},
            ),
        )
    return raised


async def scan_deals(pool: Pool, ticker: str, since: dt.datetime, now: dt.datetime) -> int:
    """One alert per documented state transition that became known in the window. A rumoured
    state is unverified and never alerts; a filing that repeats the state the deal was already in
    is not a change; the alert states the new state and its source, no more."""
    rows = await pool.fetch(
        """select t.id, t.state, t.known_at, e.title, e.deal_type, d.stable_url as source_url
             from public.deal_state_transition t
             join public.deal_event e on e.id = t.deal_id
             left join public.source_document_version v on v.id = t.document_version_id
             left join public.source_document d on d.id = v.document_id
            where e.ticker = $1 and t.state <> 'rumored'
              and (t.known_at > $2 or (t.created_at > $2 and t.created_at <= $3))
              and t.known_at <= $3
              and t.state is distinct from (
                select p.state from public.deal_state_transition p
                 where p.deal_id = t.deal_id and (p.known_at, p.id) < (t.known_at, t.id)
                 order by p.known_at desc, p.id desc limit 1)
            order by t.known_at, t.id""",
        ticker,
        since,
        now,
    )
    raised = 0
    for r in rows:
        raised += await record(
            pool,
            kind="deal_state",
            ticker=ticker,
            dedupe_key=f"deal:{r['id']}",
            headline=f"{ticker}: {r['title']} is now {r['state']}",
            detail={"transition_id": r["id"], "state": r["state"], "deal_type": r["deal_type"]},
            known_at=r["known_at"],
            source_url=r["source_url"],
            why_fired=why(
                "deal_state_transition",
                "any documented (not rumoured) state change",
                {"state": r["state"], "deal_type": r["deal_type"]},
            ),
        )
    return raised


async def held_tickers(pool: Pool) -> list[str]:
    rows = await pool.fetch(
        """select distinct p.ticker from public.position_snapshot p
            where p.snapshot_date = (select max(snapshot_date) from public.account_snapshot)
            order by p.ticker"""
    )
    return [r["ticker"] for r in rows]


async def scan_all(
    pool: Pool, now: dt.datetime | None = None, *, lookback_days: int = LOOKBACK_DAYS
) -> dict[str, int]:
    """Run every scan for every held ticker. A failure in one ticker is counted, never fatal.
    Holder and deal scans window on either the time a filing became known (SEC acceptance) or the
    time we stored it, so a filing ingested late still alerts; the lookback is long enough that
    an alert held back by the daily throttle gets raised later too. `record` dedupes, so a wide
    window repeats nothing."""
    end = now or dt.datetime.now(dt.UTC)
    start = end - dt.timedelta(days=lookback_days)
    totals = {
        "insider_trade": 0,
        "major_holder": 0,
        "relationship_change": 0,
        "deal_state": 0,
        "controlling_stake": 0,
        "insider_buy_cluster": 0,
        "estimate_revision": 0,
        "debt_maturity_change": 0,
        "review_tasks": 0,
        "failed": 0,
    }
    for ticker in await held_tickers(pool):
        scans: list[tuple[str, Callable[[], Awaitable[int | tuple[int, int]]]]] = [
            ("insider_trade", partial(scan_insiders, pool, ticker, end)),
            ("major_holder", partial(scan_major_holders, pool, ticker, start, end)),
            ("relationship_change", partial(scan_relationships, pool, ticker, start, end)),
            ("deal_state", partial(scan_deals, pool, ticker, start, end)),
            ("controlling_stake", partial(scan_controlling_stakes, pool, ticker, start, end)),
            ("insider_buy_cluster", partial(scan_insider_clusters, pool, ticker, end, since=start)),
            ("estimate_revision", partial(scan_estimate_revisions, pool, ticker, start, end)),
            ("review_tasks", partial(scan_suggested_edges, pool, ticker, start, end)),
            ("debt_maturity_change", partial(scan_debt_maturity, pool, ticker, end, since=start)),
        ]
        # Each scan stands alone: bad data in one (an unparsable number, a missing field) is
        # counted and the rest still run, for this ticker and the next.
        for kind, run in scans:
            try:
                got = await run()
            except (asyncpg.PostgresError, ValueError, ArithmeticError, TypeError, KeyError):
                log.exception("disclosure alert scan %s failed for %s", kind, ticker)
                totals["failed"] += 1
                continue
            if isinstance(got, tuple):
                totals[kind] += got[0]
                totals["review_tasks"] += got[1]
            else:
                totals[kind] += got
    return totals


async def list_alerts(
    pool: Pool, *, ticker: str | None = None, include_acknowledged: bool = False, limit: int = 100
) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """select id, kind, ticker, headline, detail, why_fired, source_url, known_at, created_at,
                  acknowledged_at
             from public.disclosure_alert
            where ($1::text is null or ticker = $1)
              and ($2 or acknowledged_at is null)
            order by created_at desc, id desc limit $3""",
        ticker,
        include_acknowledged,
        limit,
    )
    out = []
    for r in rows:
        d = dict(r)
        raw = r["detail"]
        d["detail"] = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        w = r["why_fired"]
        d["why_fired"] = json.loads(w) if isinstance(w, str) else (dict(w) if w else None)
        out.append(d)
    return out


def digest(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Open alerts grouped by ticker, most alerts first, each group newest first. Counts are of
    the alerts given; nothing is summarised beyond the headlines already stated."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in sorted(rows, key=lambda r: (r["created_at"], r["id"]), reverse=True):
        groups.setdefault(row["ticker"], []).append(row)
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    return [
        {
            "ticker": ticker,
            "count": len(items),
            "kinds": sorted({i["kind"] for i in items}),
            "headlines": [i["headline"] for i in items],
            "latest_known_at": max(i["known_at"] for i in items),
        }
        for ticker, items in ordered
    ]


async def acknowledge(pool: Pool, alert_id: int, by: str) -> bool:
    updated = await pool.fetchval(
        """update public.disclosure_alert set acknowledged_at = now(), acknowledged_by = $2
            where id = $1 and acknowledged_at is null returning id""",
        alert_id,
        by,
    )
    return updated is not None
