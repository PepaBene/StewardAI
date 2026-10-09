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
from steward.disclosure.conditional import ConditionalFetch
from steward.disclosure.deal_job import collect_deals
from steward.disclosure.esef_facts import read_pending_esef_versions
from steward.disclosure.estimate_store import collect_consensus
from steward.disclosure.fetch import DisclosureFetcher, FetchFailure, Validators
from steward.disclosure.foreign_job import collect_esef, held_lei_issuers
from steward.disclosure.identifier_job import (
    gleif_lei_lookup,
    openfigi_figi_lookup,
    resolve_identifiers,
)
from steward.disclosure.ingest import attempts_done_sql, record_attempt
from steward.disclosure.ingest_job import (
    CURRENT_REPORT_FORMS,
    index_filing_items,
    ingest_filings,
    mirror_stored_facts,
    require_source,
)
from steward.disclosure.note_job import extract_notes_for_document
from steward.disclosure.parse import PARSER_VERSION, parse_html
from steward.disclosure.probe import run_probes
from steward.disclosure.registry import (
    SPEC_BY_KEY,
    SourceNotEnabled,
    load_cursor,
    record_access_test,
    save_cursor,
    seed_sources,
    set_enabled,
)
from steward.disclosure.sec_discovery import (
    DiscoveredFiling,
    discover_filings,
    incremental,
    next_cursor,
)
from steward.jobs.runner import JobContext
from steward.notify.channels import ChannelError, EmailChannel
from steward.settings import Settings, get_settings
from steward.sources.gleif import GleifClient
from steward.storage import EvidenceStore

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SUBMISSIONS_SOURCE = "sec.submissions"
FORMS = {"10-K", "10-Q", "20-F", "40-F"}
FILINGS_PER_ISSUER = 3
MAX_MAILED = 100
type Pool = asyncpg.Pool


def new_filings(filings: list[DiscoveredFiling], cursor: dict[str, str]) -> list[DiscoveredFiling]:
    """The newest `FILINGS_PER_ISSUER` filings after the issuer's stored cursor."""
    return incremental(filings, cursor)[-FILINGS_PER_ISSUER:]


def advance_cursor(
    filings: list[DiscoveredFiling], cursor: dict[str, str], validators: Validators
) -> dict[str, str]:
    """The issuer's cursor after a clean run: the newest filing read, and the listing's
    validators so an unchanged listing next time costs a 304 and nothing else."""
    out = next_cursor(filings, cursor)
    for name, value in (("etag", validators.etag), ("last_modified", validators.last_modified)):
        if value:
            out[name] = value
        else:
            out.pop(name, None)
    return out


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

            totals = {
                "ingested": 0,
                "unchanged": 0,
                "failed": 0,
                "listings_unchanged": 0,
                "release_items": 0,
            }
            errors: list[str] = []
            for issuer in await held_issuers(ctx.pool):
                cik = str(int(issuer["cik"])).zfill(10)
                try:
                    cursor = await load_cursor(ctx.pool, SUBMISSIONS_SOURCE, cik)
                    listing = await fetcher.fetch(
                        SUBMISSIONS_SOURCE,
                        SUBMISSIONS_URL.format(cik=cik),
                        validators=Validators(cursor.get("etag"), cursor.get("last_modified")),
                    )
                    if not listing.modified:
                        totals["listings_unchanged"] += 1
                        continue
                    submissions = json.loads(listing.content or b"{}")
                    totals["release_items"] += await index_filing_items(
                        ctx.pool,
                        issuer["id"],
                        discover_filings(cik, submissions, forms=CURRENT_REPORT_FORMS),
                    )
                    filings = new_filings(discover_filings(cik, submissions, forms=FORMS), cursor)
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
                if outcome.failed == 0:  # a failed filing is read again on the next run
                    await save_cursor(
                        ctx.pool,
                        SUBMISSIONS_SOURCE,
                        cik,
                        advance_cursor(filings, cursor, listing.validators),
                    )
            step.detail.update(totals)
            step.detail["errors"] = errors
        async with ctx.step("backfill") as step:
            if store is None:
                step.detail["skipped"] = "no evidence store configured"
            else:
                step.detail.update(await backfill_stored(ctx.pool, store))


BACKFILL_PER_RUN = 5
BACKFILL_WINDOW = dt.timedelta(days=30)
BACKFILL_STEP = "sec_backfill"


async def backfill_stored(pool: Pool, store: EvidenceStore) -> dict[str, Any]:
    """Finish the work on recently archived filings that have no linked facts yet: link the
    stored statement facts to their place in the document and read the note tables. Both are
    safe to repeat. A filing this parser already read without result, or failed on three times,
    is skipped, so it cannot hold a slot every run; a new parser version tries it again."""
    rows = await pool.fetch(
        f"""select v.id version_id, v.archive_path, v.known_at, d.issuer_id, d.external_id,
                  d.form, d.stable_url,
                  (select l.ticker from public.security_listing l
                     join public.security s on s.id = l.security_id
                    where s.issuer_id = d.issuer_id and l.status = 'active'
                    order by l.id limit 1) ticker
             from public.source_document_version v
             join public.source_document d on d.id = v.document_id
            where v.archive_path is not null and not v.quarantined and d.issuer_id is not null
              and d.regulator = 'SEC' and v.first_seen_at > now() - $1::interval
              and not exists (select 1 from public.disclosure_observation o
                               where o.document_version_id = v.id)
              and not {attempts_done_sql("v.id", BACKFILL_STEP, "$3")}
            order by v.id desc limit $2""",  # noqa: S608 - fixed fragments, no input
        BACKFILL_WINDOW,
        BACKFILL_PER_RUN,
        PARSER_VERSION,
    )
    done: dict[str, Any] = {"checked": len(rows), "mirrored": 0, "note_rows": 0, "errors": []}
    errors: list[str] = []
    for row in rows:
        version_id = int(row["version_id"])
        try:
            doc = parse_html(await store.get(row["archive_path"]))
            mirrored = await mirror_stored_facts(
                pool, row["issuer_id"], row["external_id"], row["version_id"], doc
            )
            done["mirrored"] += mirrored
            notes = await extract_notes_for_document(
                pool,
                issuer_id=row["issuer_id"],
                ticker=row["ticker"],
                accession=row["external_id"],
                form=row["form"],
                known_at=row["known_at"],
                source_url=row["stable_url"],
                doc=doc,
            )
            done["note_rows"] += notes.stored
            if mirrored == 0:
                await record_attempt(
                    pool,
                    version_id,
                    BACKFILL_STEP,
                    PARSER_VERSION,
                    "empty",
                    f"{notes.stored} note rows, no observations",
                )
        except Exception as exc:  # one filing must not stop the rest
            errors.append(f"version {row['version_id']}: {type(exc).__name__}")
            await record_attempt(
                pool, version_id, BACKFILL_STEP, PARSER_VERSION, "failed", type(exc).__name__
            )
    done["errors"] = errors
    return done


# Sources a job in this module reads. They are switched on after a recorded passing access test
# and off again, by `record_access_test`, the day a test fails.
AUTO_ENABLED = ("sec.submissions", "sec.archive")


async def disclosure_sources_handler(ctx: JobContext) -> None:
    """Re-test every disclosure source and store the verdicts. A source that stops passing is
    switched off; the SEC sources the other jobs need are switched on when they pass."""
    settings = get_settings()
    async with ctx.step("seed") as step:
        step.detail["new_sources"] = await seed_sources(ctx.pool)
    async with ctx.step("probe") as step:
        results = await run_probes(settings)
        verdicts: dict[str, str] = {}
        for r in results:
            if r.source_key not in SPEC_BY_KEY or r.source_key == "issuer_ir.consensus":
                continue
            await record_access_test(ctx.pool, r.source_key, r.verdict, r.detail[:500])
            verdicts[r.source_key] = r.verdict.value
        for key in AUTO_ENABLED:
            if verdicts.get(key) == "available":
                await set_enabled(ctx.pool, key, True)
        step.detail["verdicts"] = verdicts


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


DEAL_LOOKBACK = dt.timedelta(days=45)


async def disclosure_deals_handler(ctx: JobContext) -> None:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=120.0)) as http:
        async with ctx.step("deals") as step:
            if not settings.sec_user_agent:
                step.detail["skipped"] = "SEC_USER_AGENT is not set"
                return
            fetcher = DisclosureFetcher(
                ctx.pool,
                http,
                user_agent=settings.sec_user_agent,
                store=evidence_store(settings, http),
            )

            listing = ConditionalFetch(ctx.pool, fetcher, SUBMISSIONS_SOURCE, "deals")

            async def fetch(url: str) -> Any:
                return await fetcher.fetch("sec.archive", url)

            try:
                await require_source(ctx.pool, "sec.archive")
                totals = await collect_deals(
                    ctx.pool,
                    await held_issuers(ctx.pool),
                    listing,
                    fetch,
                    submissions_url=SUBMISSIONS_URL,
                    since=(dt.datetime.now(dt.UTC) - DEAL_LOOKBACK).date(),
                    on_done=listing.done,
                )
            except SourceNotEnabled as exc:
                step.detail["skipped"] = str(exc)
                return
            step.detail.update(totals.as_detail())


async def disclosure_esef_handler(ctx: JobContext) -> None:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=120.0)) as http:
        async with ctx.step("esef") as step:
            fetcher = DisclosureFetcher(
                ctx.pool,
                http,
                user_agent=settings.sec_user_agent or "",
                store=evidence_store(settings, http),
            )

            async def fetch(url: str) -> Any:
                return await fetcher.fetch("esef.filings_xbrl_org", url)

            try:
                await require_source(ctx.pool, "esef.filings_xbrl_org")
                index = ConditionalFetch(ctx.pool, fetcher, "esef.filings_xbrl_org", "esef_index")
                totals = await collect_esef(
                    ctx.pool, await held_lei_issuers(ctx.pool), index, fetch, on_done=index.done
                )
            except SourceNotEnabled as exc:
                step.detail["skipped"] = str(exc)
            else:
                step.detail.update(totals.as_detail())
        # Reading stored reports needs no fetch, so it runs even when the source is off.
        async with ctx.step("facts") as step:
            step.detail.update(
                await read_pending_esef_versions(ctx.pool, evidence_store(settings, http))
            )


async def disclosure_estimates_handler(ctx: JobContext) -> None:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=120.0)) as http:
        async with ctx.step("consensus") as step:
            fetcher = DisclosureFetcher(
                ctx.pool,
                http,
                user_agent=settings.sec_user_agent or "",
                store=evidence_store(settings, http),
            )

            pages = ConditionalFetch(ctx.pool, fetcher, "issuer_ir.consensus", "consensus")
            try:
                step.detail.update(await collect_consensus(ctx.pool, pages, on_done=pages.done))
            except SourceNotEnabled as exc:
                step.detail["skipped"] = str(exc)


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
