"""Scheduled disclosure jobs: filing ingestion, identifier suggestions, alert scan and e-mail.

* `disclosure_ingest` reads the newest annual/quarterly filings of held issuers. It does nothing
  (and says so in the step detail) unless the SEC sources are enabled with a passing access test.
* `disclosure_identifiers` stores missing LEI / FIGI matches as suggestions only.
* `disclosure_alerts` scans for new alerts and e-mails the ones not yet mailed, grouped by
  ticker. An alert is marked mailed only after the provider accepted the message.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import asyncpg
import httpx

from steward import __version__
from steward.disclosure import alerts
from steward.disclosure.fetch import DisclosureFetcher, FetchFailure
from steward.disclosure.identifier_job import (
    gleif_lei_lookup,
    openfigi_figi_lookup,
    resolve_identifiers,
)
from steward.disclosure.ingest_job import ingest_filings
from steward.disclosure.registry import SourceNotEnabled
from steward.disclosure.sec_discovery import discover_filings
from steward.jobs.runner import JobContext
from steward.notify.channels import ChannelError, EmailChannel
from steward.settings import Settings, get_settings
from steward.sources.gleif import GleifClient
from steward.storage import EvidenceStore

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
FORMS = {"10-K", "10-Q", "20-F", "40-F"}
FILINGS_PER_ISSUER = 3
MAX_MAILED = 100
type Pool = asyncpg.Pool


async def held_issuers(pool: Pool) -> list[asyncpg.Record]:
    """Issuers with a CIK whose instrument is in the latest position snapshot."""
    rows = await pool.fetch(
        """select distinct i.id, i.cik,
                  (select l.ticker from public.security_listing l
                     join public.security s on s.id = l.security_id
                    where s.issuer_id = i.id and l.status = 'active'
                    order by l.id limit 1) as ticker
             from public.issuer i
             join public.instrument n on ltrim(n.cik, '0') = ltrim(i.cik, '0')
            where i.cik is not null and n.t212_ticker in (
                  select ticker from public.position_snapshot
                   where snapshot_date = (select max(snapshot_date) from public.account_snapshot))
            order by i.id"""
    )
    return list(rows)


def evidence_store(settings: Settings, http: httpx.AsyncClient) -> EvidenceStore | None:
    if settings.supabase_url is None or settings.supabase_secret_key is None:
        return None
    from steward.storage import SupabaseStorage

    return SupabaseStorage(
        http, settings.supabase_url, settings.supabase_secret_key.get_secret_value()
    )


async def disclosure_ingest_handler(ctx: JobContext) -> None:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=120.0)) as http:
        async with ctx.step("ingest") as step:
            if not settings.sec_user_agent:
                step.detail["skipped"] = "SEC_USER_AGENT is not set"
                return
            store = evidence_store(settings, http)
            fetcher = DisclosureFetcher(
                ctx.pool, http, user_agent=settings.sec_user_agent, store=store
            )

            async def fetch(url: str) -> Any:
                return await fetcher.fetch("sec.archive", url)

            totals = {"ingested": 0, "unchanged": 0, "failed": 0}
            errors: list[str] = []
            for issuer in await held_issuers(ctx.pool):
                cik = str(int(issuer["cik"])).zfill(10)
                try:
                    listing = await fetcher.fetch(
                        "sec.submissions", SUBMISSIONS_URL.format(cik=cik)
                    )
                    filings = discover_filings(
                        cik, json.loads(listing.content or b"{}"), forms=FORMS
                    )[-FILINGS_PER_ISSUER:]
                    outcome = await ingest_filings(
                        ctx.pool,
                        issuer_id=issuer["id"],
                        ticker=issuer["ticker"],
                        filings=filings,
                        fetch=fetch,
                        store=store,
                    )
                except SourceNotEnabled as exc:
                    step.detail["skipped"] = str(exc)
                    return
                except (FetchFailure, httpx.HTTPError, ValueError) as exc:
                    errors.append(f"issuer {issuer['id']}: {type(exc).__name__}")
                    continue
                totals["ingested"] += outcome.ingested
                totals["unchanged"] += outcome.unchanged
                totals["failed"] += outcome.failed
            step.detail.update(totals)
            step.detail["errors"] = errors


async def disclosure_identifiers_handler(ctx: JobContext) -> None:
    settings = get_settings()
    key = settings.openfigi_api_key.get_secret_value() if settings.openfigi_api_key else None
    async with httpx.AsyncClient(
        timeout=30, headers={"User-Agent": f"StewardAI/{__version__}"}
    ) as http:
        async with ctx.step("resolve") as step:
            result = await resolve_identifiers(
                ctx.pool,
                lei_lookup=gleif_lei_lookup(GleifClient(http)),
                figi_lookup=openfigi_figi_lookup(http, key),
            )
            step.detail["issuers_checked"] = result.issuers_checked
            step.detail["suggested"] = sum(1 for s in result.suggestions if s.stored)
            step.detail["failed"] = list(result.failed)[:20]


def digest_text(groups: list[dict[str, Any]], base_url: str | None = None) -> str:
    """Plain text: one block per ticker with the alerts' own headlines, nothing reworded."""
    total = sum(g["count"] for g in groups)
    lines = [f"{total} new disclosure alert{'s' if total != 1 else ''}", ""]
    for g in groups:
        lines.append(f"{g['ticker']} ({g['count']})")
        lines.extend(f"  - {h}" for h in g["headlines"])
        lines.append("")
    if base_url:
        lines.append(f"Open: {base_url.rstrip('/')}/disclosures/alerts")
    return "\n".join(lines).rstrip() + "\n"


async def unmailed_alerts(pool: Pool, limit: int = MAX_MAILED) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """select id, kind, ticker, headline, known_at, created_at
             from public.disclosure_alert
            where emailed_at is null and acknowledged_at is null
            order by created_at, id limit $1""",
        limit,
    )
    return [dict(r) for r in rows]


async def mail_alert_digest(
    pool: Pool, channel: EmailChannel, *, now: dt.datetime, base_url: str | None = None
) -> int:
    """E-mail the unmailed open alerts; mark them mailed only if the send was accepted.
    Returns how many were sent. A send failure raises ChannelError and marks nothing."""
    rows = await unmailed_alerts(pool)
    if not rows:
        return 0
    groups = alerts.digest(rows)
    ids = [r["id"] for r in rows]
    await channel.send(
        f"{len(rows)} new disclosure alert{'s' if len(rows) != 1 else ''}",
        digest_text(groups, base_url),
        idempotency_key=f"disclosure-alerts-{ids[0]}-{ids[-1]}-{len(ids)}",
    )
    await pool.execute(
        "update public.disclosure_alert set emailed_at=$2"
        " where id = any($1) and emailed_at is null",
        ids,
        now,
    )
    return len(ids)


async def disclosure_alerts_handler(ctx: JobContext) -> None:
    settings = get_settings()
    async with ctx.step("scan") as step:
        totals = await alerts.scan_all(ctx.pool)
        step.detail.update(totals)
    async with ctx.step("email") as step:
        if not (settings.resend_api_key and settings.alert_email_from and settings.alert_email_to):
            step.detail["skipped"] = "RESEND_API_KEY, ALERT_EMAIL_FROM and ALERT_EMAIL_TO not set"
            return
        async with httpx.AsyncClient(timeout=30) as http:
            channel = EmailChannel(
                http,
                settings.resend_api_key.get_secret_value(),
                settings.alert_email_from,
                settings.alert_email_to,
            )
            try:
                step.detail["mailed"] = await mail_alert_digest(
                    ctx.pool, channel, now=dt.datetime.now(dt.UTC)
                )
            except ChannelError as exc:
                step.detail["failed"] = str(exc)
                raise
