"""`pdmr_uk`: parse UK PDMR (MAR Article 19) announcements from the RNS stream and store them.

Input is the RNS news items the pipeline already stores (`news_item`, source `rns*`): the ones
whose headline or event type is a PDMR notification (`is_pdmr_announcement`) and whose page has
not been parsed yet. Each attempt, successful or not, is recorded in `pdmr_notification` with
its honest status (`parsed`, `no_template`, `access_unverified`, `paid_only`, `quota_exhausted`,
`temporarily_failed`), so the UK channel's standing status is derived from what really
happened. A quota denial stops the run; failures are retried later, a bounded number of times.
"""

import datetime as dt
from dataclasses import dataclass
from typing import Any

import asyncpg
import httpx

from steward import __version__
from steward.jobs.runner import JobContext
from steward.sources.base import AccessState
from steward.sources.pdmr_uk import (
    PdmrFetchResult,
    PdmrNotification,
    fetch_pdmr,
    is_pdmr_announcement,
)

MAX_PER_RUN = 25
MAX_ATTEMPTS = 3
RETRY_AFTER_DAYS = 7
TRANSIENT_RETRY_AFTER_DAYS = 1
LOOKBACK_DAYS = 180


@dataclass(frozen=True)
class Candidate:
    news_item_id: int
    url: str
    headline: str
    published_at: dt.datetime
    ticker: str | None


def status_of(result: PdmrFetchResult) -> str:
    if result.notification is not None:
        return "parsed"
    if result.no_template:
        return "no_template"
    return (
        result.access.state.value
        if result.access.state.value
        in {
            "access_unverified",
            "paid_only",
            "quota_exhausted",
            "temporarily_failed",
        }
        else "access_unverified"
    )


async def candidates(pool: asyncpg.Pool, *, limit: int = MAX_PER_RUN) -> list[Candidate]:
    rows = await pool.fetch(
        """select ni.id, ni.url, ni.headline, ni.published_at, ni.event_type,
                  coalesce(ni.affected_ticker, ni.tickers[1]) as ticker
           from public.news_item ni
           where ni.source_id like 'rns%'
             and ni.published_at > now() - ($1::int * interval '1 day')
             and not exists (
               select 1 from public.pdmr_notification p
               where p.source_url = ni.url
                 and (p.status = 'parsed' or p.attempts >= $2::int
                      or p.last_attempt_at > now() - (
                           case when p.status in ('quota_exhausted', 'temporarily_failed')
                                then $3::int else $4::int end * interval '1 day')))
           order by ni.published_at desc limit $5::int""",
        LOOKBACK_DAYS,
        MAX_ATTEMPTS,
        TRANSIENT_RETRY_AFTER_DAYS,
        RETRY_AFTER_DAYS,
        limit * 4,
    )
    out = [
        Candidate(r["id"], r["url"], r["headline"], r["published_at"], r["ticker"])
        for r in rows
        if is_pdmr_announcement(r["headline"], r["event_type"])
    ]
    return out[:limit]


async def store_result(pool: asyncpg.Pool, item: Candidate, result: PdmrFetchResult) -> int:
    status = status_of(result)
    note: PdmrNotification | None = result.notification
    async with pool.acquire() as conn, conn.transaction():
        notification_id: int = await conn.fetchval(
            """insert into public.pdmr_notification
                 (source_url, news_item_id, ticker, headline, announced_at, status, detail,
                  person_name, position, is_amendment, issuer_name, lei, parsed_at)
               values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,
                       case when $6 = 'parsed' then now() end)
               on conflict (source_url) do update set
                 news_item_id = excluded.news_item_id, ticker = excluded.ticker,
                 headline = excluded.headline, announced_at = excluded.announced_at,
                 status = excluded.status, detail = excluded.detail,
                 person_name = excluded.person_name, position = excluded.position,
                 is_amendment = excluded.is_amendment, issuer_name = excluded.issuer_name,
                 lei = excluded.lei,
                 parsed_at = case when excluded.status = 'parsed' then now()
                                  else public.pdmr_notification.parsed_at end,
                 attempts = public.pdmr_notification.attempts + 1, last_attempt_at = now()
               returning id""",
            item.url,
            item.news_item_id,
            item.ticker,
            item.headline,
            item.published_at,
            status,
            (
                result.access.detail
                or ("" if status == "parsed" else f"retrieval ended as {status}")
            )[:500]
            or None,
            note.person_name if note else None,
            note.position if note else None,
            note.is_amendment if note else None,
            note.issuer_name if note else None,
            note.lei if note else None,
        )
        await conn.execute(
            "delete from public.pdmr_transaction where notification_id = $1", notification_id
        )
        if note is not None:
            await conn.executemany(
                """insert into public.pdmr_transaction
                     (notification_id,row_index,instrument,isin,nature,price,currency,volume,
                      transaction_date,venue,economic_class,classification_basis)
                   values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)""",
                [
                    (
                        notification_id,
                        i,
                        t.instrument,
                        t.isin,
                        t.nature,
                        t.price,
                        t.currency,
                        t.volume,
                        t.transaction_date,
                        t.venue,
                        t.economic_class,
                        t.classification_basis,
                    )
                    for i, t in enumerate(note.transactions)
                ],
            )
    return notification_id


async def run_pdmr(ctx: JobContext, http: httpx.AsyncClient, *, limit: int = MAX_PER_RUN) -> None:
    async with ctx.step("pdmr-announcements") as step:
        items = await candidates(ctx.pool, limit=limit)
        counts: dict[str, Any] = {"candidates": len(items)}
        for item in items:
            try:
                result = await fetch_pdmr(http, item.url)
            except (httpx.HTTPError, ValueError) as exc:  # a parser or transport surprise
                from steward.sources.base import AccessResult

                result = PdmrFetchResult(
                    AccessResult(AccessState.TEMPORARILY_FAILED, f"{type(exc).__name__}: {exc}")
                )
            status = status_of(result)
            await store_result(ctx.pool, item, result)
            counts[status] = counts.get(status, 0) + 1
            if status == "quota_exhausted":
                counts["stopped"] = "provider quota: remaining announcements wait for the next run"
                break
        step.detail.update(counts)


async def pdmr_uk_handler(ctx: JobContext) -> None:
    headers = {"User-Agent": f"StewardAI/{__version__} (personal portfolio tool)"}
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=15.0), headers=headers
    ) as http:
        await run_pdmr(ctx, http)
