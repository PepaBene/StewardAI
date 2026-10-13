"""The AI steps of the exposure builder job: HS mapping of product lines, and currency and
commodity inputs and customer / supplier disclosures from the annual report. Each filing is read
once (`filing_extraction`); a failure on one holding never stops the others."""

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

import asyncpg
import pydantic

from steward.ai import Usage
from steward.budget_guard import BudgetExceeded
from steward.config.holding_profiles import HoldingProfile, HoldingProfilesConfig
from steward.config.jobs import ExposureBuildSettings
from steward.config.prompts import PromptsConfig
from steward.config.store import ConfigConflictError, load_active, save_config
from steward.exposures.archived import archived_text
from steward.exposures.filing_text import FilingText, filing_text, latest_annual_filing
from steward.exposures.hs import HS_SYSTEM, ask_hs, hs_weights, product_lines, write_hs
from steward.exposures.inputs import (
    INPUTS_SYSTEM,
    accept,
    added_keys,
    extract_inputs,
    merge_exposures,
    tombstone_keys,
)
from steward.graph.dependencies import (
    APPROVE_SYSTEM,
    DEPENDENCY_SYSTEM,
    check_findings,
    corroboration_notes,
    extract_dependencies,
    judge_dependencies,
    store_and_approve,
    store_reported,
)
from steward.graph.jobs import load_graph_holdings
from steward.jobs.runner import JobContext
from steward.llm.routing import RoutingClient, batch_collector
from steward.llm.types import LLMError
from steward.sources.edgar import EdgarClient, EdgarError
from steward.storage import EvidenceStore

# What one holding's AI call can raise without the others being affected.
AI_ERRORS = (LLMError, pydantic.ValidationError, ValueError)
RETRY_EMPTY_AFTER_DAYS = 30


async def _extracted(pool: asyncpg.Pool, ticker: str, accession: str, kind: str) -> bool:
    """Was this document read already? A read that found no readable sections is retried after
    a month (the layout may have been misparsed), any other read is final."""
    return bool(
        await pool.fetchval(
            "select exists(select 1 from public.filing_extraction"  # noqa: S608
            " where ticker = $1 and accession = $2 and kind = $3"
            " and not (coalesce(detail->>'no_sections', '') = 'true'"
            f"          and extracted_at < now() - interval '{RETRY_EMPTY_AFTER_DAYS} days'))",
            ticker,
            accession,
            kind,
        )
    )


async def _record(
    pool: asyncpg.Pool, ticker: str, accession: str, kind: str, url: str | None, detail: Any
) -> None:
    await pool.execute(
        "insert into public.filing_extraction (ticker, accession, kind, source_url, detail)"
        " values ($1, $2, $3, $4, $5::jsonb) on conflict (ticker, accession, kind)"
        " do update set detail = excluded.detail, extracted_at = now()",
        ticker,
        accession,
        kind,
        url,
        json.dumps(detail, default=str),
    )


Memo = dict[tuple[str, str], "FilingText | str | None"]


async def prefetch_batch(
    client: RoutingClient,
    wait_minutes: float,
    asks: Sequence[Callable[[RoutingClient], Awaitable[Any]]],
) -> int:
    """Cost lever E2/G2: send the model reads of a run to the provider's batch endpoint together
    (half price). Each answer lands in the client's result cache, so the step that follows,
    unchanged, finds it there and asks nothing; a read the batch does not answer is asked
    directly by the collector. An error here is left for that step to meet in its own way.
    Without a result cache or with one read there is nothing to gain: returns 0."""
    if client.cache is None or len(asks) < 2:
        return 0
    batch_client = RoutingClient(client.llm, client.cache)

    async def one(ask: Callable[[RoutingClient], Awaitable[Any]]) -> None:
        try:
            await ask(batch_client)
        except BudgetExceeded:
            raise
        except Exception:  # noqa: S110 - the step that follows meets the same error itself
            pass

    with batch_collector(batch_client, client.llm, wait_minutes) as collector:
        results = await collector.run([one(ask) for ask in asks])
    for result in results:
        if isinstance(result, BudgetExceeded):
            raise result
    return len(asks)


async def _source(
    ctx: JobContext,
    edgar: EdgarClient | None,
    storage: EvidenceStore | None,
    ticker: str,
    profile: HoldingProfile | None,
    kind: str,
    memo: Memo | None = None,
) -> FilingText | str | None:
    """`_read_source`, remembering the answer in `memo` so a batched run fetches each filing
    once for its prefetch and its step."""
    if memo is not None and (ticker, kind) in memo:
        return memo[(ticker, kind)]
    found = await _read_source(ctx, edgar, storage, ticker, profile, kind)
    if memo is not None:
        memo[(ticker, kind)] = found
    return found


async def _read_source(
    ctx: JobContext,
    edgar: EdgarClient | None,
    storage: EvidenceStore | None,
    ticker: str,
    profile: HoldingProfile | None,
    kind: str,
) -> FilingText | str | None:
    """The text still to be read for this holding and extraction kind: its newest annual report,
    or for an issuer with none on EDGAR its newest archived results document. A string is the
    reason it cannot be read; None means nothing is left to read."""
    if edgar is not None:
        try:
            annual = await latest_annual_filing(ctx.pool, edgar, ticker, profile)
            if annual is not None:
                if await _extracted(ctx.pool, ticker, annual.accession, kind):
                    return None
                found = await filing_text(edgar, ticker, annual)
                return found or "annual report text too short to read"
        except EdgarError as exc:
            return f"filing not readable: {str(exc)[:80]}"
    if storage is None:
        return None
    archived = await archived_text(ctx.pool, storage, ticker)
    if isinstance(archived, FilingText) and await _extracted(
        ctx.pool, ticker, archived.accession, kind
    ):
        return None
    return archived


async def run_products(
    ctx: JobContext,
    client: RoutingClient,
    cfg: ExposureBuildSettings,
    holdings: Sequence[Mapping[str, Any]],
    usage: Usage,
    prompts: PromptsConfig,
) -> dict[str, Any]:
    """HS rows (estimated) from each holding's newest annual product lines."""
    results: dict[str, Any] = {}
    done = 0
    system = prompts.text("exposure_hs_mapping", HS_SYSTEM)
    if cfg.use_batch:
        asks: list[Callable[[RoutingClient], Awaitable[Any]]] = []
        for row in holdings:
            if len(asks) >= cfg.filings_per_run:
                break
            if row["issuer_id"] is None:
                continue
            todo = await product_lines(ctx.pool, row["ticker"])
            if todo is None or await _extracted(
                ctx.pool, row["ticker"], todo[0]["accession"], "hs_mapping"
            ):
                continue
            name = await ctx.pool.fetchval(
                "select name from public.instrument where t212_ticker = $1", row["ticker"]
            )
            labels = [label for label, _ in todo[1]]

            def ask_products(
                c: RoutingClient, n: str = str(name or row["ticker"]), ls: list[str] = labels
            ) -> Awaitable[Any]:
                return ask_hs(c, cfg.model, cfg.effort, n, ls, usage, system)

            asks.append(ask_products)
        await prefetch_batch(client, cfg.batch_wait_minutes, asks)
    for row in holdings:
        if done >= cfg.filings_per_run:
            break
        ticker = row["ticker"]
        if row["issuer_id"] is None:
            continue
        found = await product_lines(ctx.pool, ticker)
        if found is None:
            continue
        facts, lines, total = found
        if await _extracted(ctx.pool, ticker, facts["accession"], "hs_mapping"):
            continue
        name = await ctx.pool.fetchval(
            "select name from public.instrument where t212_ticker = $1", ticker
        )
        try:
            mapping = await ask_hs(
                client,
                cfg.model,
                cfg.effort,
                str(name or ticker),
                [label for label, _ in lines],
                usage,
                system,
            )
        except BudgetExceeded:
            results["stopped"] = "the monthly AI budget is reached"
            break
        except AI_ERRORS as exc:
            results[ticker] = f"AI step failed: {type(exc).__name__}"
            continue
        weights = hs_weights(lines, mapping, total)
        if isinstance(weights, str):
            results[ticker] = weights
            detail: dict[str, Any] = {"result": weights, "mapping": mapping}
        else:
            try:
                counts = await write_hs(ctx.pool, row["issuer_id"], weights, facts, cfg.model)
            except asyncpg.PostgresError as exc:
                results[ticker] = f"rows refused by the database: {str(exc)[:100]}"
                continue
            results[ticker] = {**counts, "headings": sorted(weights)}
            detail = {"result": counts, "mapping": mapping}
        await _record(
            ctx.pool, ticker, facts["accession"], "hs_mapping", facts["instance_url"], detail
        )
        done += 1
    return results


async def _previous_detail(pool: asyncpg.Pool, ticker: str) -> Any:
    return await pool.fetchval(
        "select detail from public.filing_extraction where ticker = $1 and kind = 'exposure'"
        " and not coalesce(detail->>'no_sections', '') = 'true'"
        " order by extracted_at desc limit 1",
        ticker,
    )


async def run_inputs(
    ctx: JobContext,
    client: RoutingClient,
    cfg: ExposureBuildSettings,
    edgar: EdgarClient | None,
    holdings: Sequence[Mapping[str, Any]],
    profiles_id: int,
    profiles: HoldingProfilesConfig,
    usage: Usage,
    prompts: PromptsConfig,
    storage: EvidenceStore | None = None,
) -> dict[str, Any]:
    """Currency and commodity rows from each holding's newest annual report (or archived
    results document)."""
    results: dict[str, Any] = {}
    done = 0
    memo: Memo = {}
    system = prompts.text("exposure_inputs", INPUTS_SYSTEM)
    if cfg.use_batch:
        asks: list[Callable[[RoutingClient], Awaitable[Any]]] = []
        for row in holdings:
            if len(asks) >= cfg.filings_per_run:
                break
            profile = profiles.for_ticker(row["ticker"])
            if profile is None:
                continue
            text = await _source(ctx, edgar, storage, row["ticker"], profile, "exposure", memo)
            if isinstance(text, FilingText):

                def ask_inputs(c: RoutingClient, f: FilingText = text) -> Awaitable[Any]:
                    return extract_inputs(c, cfg.model, cfg.effort, f, usage, system)

                asks.append(ask_inputs)
        await prefetch_batch(client, cfg.batch_wait_minutes, asks)
    for row in holdings:
        if done >= cfg.filings_per_run:
            break
        ticker = row["ticker"]
        profile = profiles.for_ticker(ticker)
        if profile is None:
            continue
        filing = await _source(ctx, edgar, storage, ticker, profile, "exposure", memo)
        if isinstance(filing, str):
            results[ticker] = filing
            continue
        if filing is None:
            continue
        try:
            findings, sections = await extract_inputs(
                client,
                cfg.model,
                cfg.effort,
                filing,
                usage,
                system,
            )
        except BudgetExceeded:
            results["stopped"] = "the monthly AI budget is reached"
            break
        except AI_ERRORS as exc:
            results[ticker] = f"AI step failed: {type(exc).__name__}"
            continue
        if not sections:
            results[ticker] = "no readable exposure sections (retried in a month)"
            await _record(
                ctx.pool,
                ticker,
                filing.accession,
                "exposure",
                filing.source_url,
                {"no_sections": True},
            )
            continue
        accepted = accept(findings, filing)
        previous = await _previous_detail(ctx.pool, ticker)
        info: dict[str, Any] = {}
        stopped = False
        for attempt in (1, 2):
            merged, info = merge_exposures(
                profile, accepted.exposures, added_keys(previous), tombstone_keys(previous)
            )
            if not info["changed"]:
                break
            updated = profiles.model_copy(
                update={
                    "profiles": [merged if p.ticker == ticker else p for p in profiles.profiles]
                }
            )
            try:
                async with ctx.pool.acquire() as conn:
                    saved = await save_config(
                        conn,
                        "holding_profiles",
                        updated,
                        created_by=None,
                        note=f"Currency and input exposures for {ticker} from {filing.form}",
                        expected_active_id=profiles_id,
                    )
            except ConfigConflictError:
                # The owner saved the profiles meanwhile: take theirs and merge again once.
                latest = await load_active(ctx.pool, "holding_profiles", HoldingProfilesConfig)
                fresh = latest.body.for_ticker(ticker) if latest else None
                if latest is None or fresh is None or attempt == 2:
                    stopped = True
                    break
                profiles_id, profiles, profile = latest.id, latest.body, fresh
                continue
            profiles_id, profiles = saved.id, saved.body
            break
        if stopped:
            results["stopped"] = "the profiles changed; the next run continues"
            break
        results[ticker] = {**info, "rejected": len(accepted.rejected)}
        await _record(
            ctx.pool,
            ticker,
            filing.accession,
            "exposure",
            filing.source_url,
            {**info, "rejected": accepted.rejected[:20]},
        )
        done += 1
    return results


async def run_dependencies(
    ctx: JobContext,
    client: RoutingClient,
    cfg: ExposureBuildSettings,
    edgar: EdgarClient | None,
    holdings: Sequence[Mapping[str, Any]],
    profiles: HoldingProfilesConfig,
    usage: Usage,
    prompts: PromptsConfig,
    storage: EvidenceStore | None = None,
) -> dict[str, Any]:
    """Customer and supplier disclosures from each holding's newest annual report (or archived
    results document): stored as suggested, confirmed when the reviewer approves, never over an
    owner decision."""
    results: dict[str, Any] = {}
    done = 0
    graph_holdings = await load_graph_holdings(ctx.pool)
    for row in holdings:  # reported XBRL concentration needs no AI and no filing download
        reported = await store_reported(ctx.pool, row["ticker"])
        if reported["stored"]:
            results[f"{row['ticker']} (xbrl)"] = reported
    memo: Memo = {}
    system = prompts.text("dependency_extract", DEPENDENCY_SYSTEM)
    if cfg.use_batch:
        # Only the long read is batched; the short approval that follows it depends on the
        # read's checked findings and is asked directly.
        asks: list[Callable[[RoutingClient], Awaitable[Any]]] = []
        for row in holdings:
            if len(asks) >= cfg.filings_per_run:
                break
            text = await _source(
                ctx,
                edgar,
                storage,
                row["ticker"],
                profiles.for_ticker(row["ticker"]),
                "dependency",
                memo,
            )
            if isinstance(text, FilingText):

                def ask_dependencies(c: RoutingClient, f: FilingText = text) -> Awaitable[Any]:
                    return extract_dependencies(c, cfg.model, cfg.effort, f, usage, system)

                asks.append(ask_dependencies)
        await prefetch_batch(client, cfg.batch_wait_minutes, asks)
    for row in holdings:
        if done >= cfg.filings_per_run:
            break
        ticker = row["ticker"]
        profile = profiles.for_ticker(ticker)
        filing = await _source(ctx, edgar, storage, ticker, profile, "dependency", memo)
        if isinstance(filing, str):
            results[ticker] = filing
            continue
        if filing is None:
            continue
        name = await ctx.pool.fetchval(
            "select name from public.instrument where t212_ticker = $1", ticker
        )
        own_names = tuple(
            next((h["names"] for h in graph_holdings if h["ticker"] == ticker), [str(name)])
        )
        try:
            found, sections = await extract_dependencies(
                client,
                cfg.model,
                cfg.effort,
                filing,
                usage,
                system,
            )
            if not sections:
                results[ticker] = "no readable customer or supplier sections (retried in a month)"
                await _record(
                    ctx.pool,
                    ticker,
                    filing.accession,
                    "dependency",
                    filing.source_url,
                    {"no_sections": True},
                )
                continue
            checked = check_findings(found, filing, own_names)
            notes = await corroboration_notes(
                ctx.pool, ticker, own_names, checked.kept, graph_holdings
            )
            verdicts = await judge_dependencies(
                client,
                cfg.model,
                cfg.effort,
                filing,
                checked.kept,
                usage,
                prompts.text("dependency_approve", APPROVE_SYSTEM),
                notes,
            )
        except BudgetExceeded:
            results["stopped"] = "the monthly AI budget is reached"
            break
        except AI_ERRORS as exc:
            results[ticker] = f"AI step failed: {type(exc).__name__}"
            continue
        counts = await store_and_approve(ctx.pool, filing, checked.kept, verdicts, cfg.model)
        results[ticker] = {**counts, "rejected": len(checked.rejected)}
        await _record(
            ctx.pool,
            ticker,
            filing.accession,
            "dependency",
            filing.source_url,
            {**counts, "rejected": checked.rejected[:20]},
        )
        done += 1
    return results
