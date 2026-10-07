"""P3.6, P4.6, P5.8: alerts raised from stored disclosures, once each.

Three kinds, all written to `disclosure_alert` with a `dedupe_key` so a job that runs twice, or an
amendment that repeats a trade, raises one alert:

  * insider_trade       a material, verified open-market buy or sell (never a plan trade, a bundled
                        sale, a grant, a gift, an unknown, or anything from a failed collection);
  * major_holder        a new 13D/G filing on a held share class, or an amendment whose stake moved
                        by at least a stated number of points (never a 13F quarter-to-quarter move,
                        which is not trading intent);
  * relationship_change a confirmed material customer/supplier relationship that appeared or ended.

An alert states what was filed. It never says why.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any

import asyncpg

from steward.disclosure import dependency_read, insider_analysis

type Pool = asyncpg.Pool | asyncpg.Connection


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
) -> bool:
    """Store one alert; False when it was already raised."""
    new_id = await pool.fetchval(
        """insert into public.disclosure_alert
             (kind, ticker, dedupe_key, headline, detail, source_url, known_at)
           values ($1,$2,$3,$4,$5::jsonb,$6,$7)
           on conflict (dedupe_key) do nothing returning id""",
        kind,
        ticker,
        dedupe_key,
        headline,
        json.dumps(detail, default=str),
        source_url,
        known_at,
    )
    return new_id is not None


def _money(value: Decimal) -> str:
    return f"${value:,.0f}"


async def scan_insiders(
    pool: Pool, ticker: str, now: dt.datetime, *, min_value: Decimal = Decimal(100_000)
) -> int:
    raised = 0
    for a in await insider_analysis.pending_alerts(pool, ticker, min_value, now):
        t = a.tx
        who = t.name or t.insider
        shares = f"{t.shares:,.0f}" if t.shares is not None else "an unknown number of"
        url = await pool.fetchval(
            "select source_url from public.insider_filing where accession = $1", t.accession
        )
        raised += await record(
            pool,
            kind="insider_trade",
            ticker=ticker,
            dedupe_key=f"insider:{t.insider}:{t.tx_date}:{a.reason}:{t.shares}:{t.price}",
            headline=f"{who} reported an {a.reason} of {shares} shares ({_money(a.value)})",
            detail={
                "accession": t.accession,
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
              and f.known_at > $2 and f.known_at <= $3
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
        )
    return raised


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
    pool: Pool, now: dt.datetime | None = None, *, lookback_days: int = 7
) -> dict[str, int]:
    """Run every scan for every held ticker. A failure in one ticker is counted, never fatal."""
    end = now or dt.datetime.now(dt.UTC)
    start = end - dt.timedelta(days=lookback_days)
    totals = {"insider_trade": 0, "major_holder": 0, "relationship_change": 0, "failed": 0}
    for ticker in await held_tickers(pool):
        try:
            totals["insider_trade"] += await scan_insiders(pool, ticker, end)
            totals["major_holder"] += await scan_major_holders(pool, ticker, start, end)
            totals["relationship_change"] += await scan_relationships(pool, ticker, start, end)
        except (asyncpg.PostgresError, ValueError):
            totals["failed"] += 1
    return totals


async def list_alerts(
    pool: Pool, *, ticker: str | None = None, include_acknowledged: bool = False, limit: int = 100
) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """select id, kind, ticker, headline, detail, source_url, known_at, created_at,
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
