"""The AI steps of the exposure builder job: HS mapping of product lines, and currency and
commodity inputs from the annual report. Each filing is read once (`filing_extraction`)."""

import json
from typing import Any

import anthropic
import asyncpg

from steward.ai import Usage
from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.jobs import ExposureBuildSettings
from steward.config.prompts import PromptsConfig
from steward.config.store import ConfigConflictError, save_config
from steward.exposures.filing_text import filing_text, latest_annual_filing
from steward.exposures.hs import HS_SYSTEM, ask_hs, hs_weights, product_lines, write_hs
from steward.exposures.inputs import (
    INPUTS_SYSTEM,
    accept,
    added_keys,
    extract_inputs,
    merge_exposures,
)
from steward.graph.dependencies import (
    APPROVE_SYSTEM,
    DEPENDENCY_SYSTEM,
    check_findings,
    extract_dependencies,
    judge_dependencies,
    store_and_approve,
)
from steward.jobs.runner import JobContext
from steward.sources.edgar import EdgarClient, EdgarError


async def _extracted(pool: asyncpg.Pool, ticker: str, accession: str, kind: str) -> bool:
    return bool(
        await pool.fetchval(
            "select exists(select 1 from public.filing_extraction"
            " where ticker = $1 and accession = $2 and kind = $3)",
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


async def run_products(
    ctx: JobContext,
    client: anthropic.AsyncAnthropic,
    cfg: ExposureBuildSettings,
    holdings: list[asyncpg.Record],
    usage: Usage,
    prompts: PromptsConfig,
) -> dict[str, Any]:
    """HS rows (estimated) from each holding's newest annual product lines."""
    results: dict[str, Any] = {}
    done = 0
    for row in holdings:
        if done >= cfg.filings_per_run:
            break
        ticker = row["ticker"]
        found = await product_lines(ctx.pool, ticker)
        if found is None:
            continue
        facts, lines, total = found
        if await _extracted(ctx.pool, ticker, facts["accession"], "hs_mapping"):
            continue
        name = await ctx.pool.fetchval(
            "select name from public.instrument where t212_ticker = $1", ticker
        )
        mapping = await ask_hs(
            client,
            cfg.model,
            cfg.effort,
            str(name or ticker),
            [label for label, _ in lines],
            usage,
            prompts.text("exposure_hs_mapping", HS_SYSTEM),
        )
        weights = hs_weights(lines, mapping, total)
        if isinstance(weights, str):
            results[ticker] = weights
            detail: dict[str, Any] = {"result": weights, "mapping": mapping}
        else:
            counts = await write_hs(ctx.pool, row["issuer_id"], weights, facts, cfg.model)
            results[ticker] = {**counts, "headings": sorted(weights)}
            detail = {"result": counts, "mapping": mapping}
        await _record(
            ctx.pool, ticker, facts["accession"], "hs_mapping", facts["instance_url"], detail
        )
        done += 1
    return results


async def run_inputs(
    ctx: JobContext,
    client: anthropic.AsyncAnthropic,
    cfg: ExposureBuildSettings,
    edgar: EdgarClient,
    holdings: list[asyncpg.Record],
    profiles_id: int,
    profiles: HoldingProfilesConfig,
    usage: Usage,
    prompts: PromptsConfig,
) -> dict[str, Any]:
    """Currency and commodity rows from each holding's newest annual report."""
    results: dict[str, Any] = {}
    done = 0
    for row in holdings:
        if done >= cfg.filings_per_run:
            break
        ticker = row["ticker"]
        profile = profiles.for_ticker(ticker)
        if profile is None:
            continue
        try:
            annual = await latest_annual_filing(ctx.pool, edgar, ticker, profile)
            if annual is None:
                continue
            if await _extracted(ctx.pool, ticker, annual.accession, "exposure"):
                continue
            filing = await filing_text(edgar, ticker, annual)
        except EdgarError as exc:
            results[ticker] = f"filing not readable: {str(exc)[:80]}"
            continue
        if filing is None:
            results[ticker] = "annual report text too short to read"
            continue
        findings, _sections = await extract_inputs(
            client,
            cfg.model,
            cfg.effort,
            filing,
            usage,
            prompts.text("exposure_inputs", INPUTS_SYSTEM),
        )
        accepted = accept(findings, filing)
        previous = await ctx.pool.fetchval(
            "select detail from public.filing_extraction where ticker = $1 and kind = 'exposure'"
            " order by extracted_at desc limit 1",
            ticker,
        )
        merged, info = merge_exposures(profile, accepted.exposures, added_keys(previous))
        if info["changed"]:
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
                results["stopped"] = "the profiles changed; the next run continues"
                break
            profiles_id, profiles = saved.id, saved.body
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
    client: anthropic.AsyncAnthropic,
    cfg: ExposureBuildSettings,
    edgar: EdgarClient,
    holdings: list[asyncpg.Record],
    profiles: HoldingProfilesConfig,
    usage: Usage,
    prompts: PromptsConfig,
) -> dict[str, Any]:
    """Customer and supplier disclosures from each holding's newest annual report: stored as
    suggested, confirmed when the reviewer approves, never over an owner decision."""
    results: dict[str, Any] = {}
    done = 0
    for row in holdings:
        if done >= cfg.filings_per_run:
            break
        ticker = row["ticker"]
        profile = profiles.for_ticker(ticker)
        try:
            annual = await latest_annual_filing(ctx.pool, edgar, ticker, profile)
            if annual is None or await _extracted(ctx.pool, ticker, annual.accession, "dependency"):
                continue
            filing = await filing_text(edgar, ticker, annual)
        except EdgarError as exc:
            results[ticker] = f"filing not readable: {str(exc)[:80]}"
            continue
        if filing is None:
            results[ticker] = "annual report text too short to read"
            continue
        name = await ctx.pool.fetchval(
            "select name from public.instrument where t212_ticker = $1", ticker
        )
        found = await extract_dependencies(
            client,
            cfg.model,
            cfg.effort,
            filing,
            usage,
            prompts.text("dependency_extract", DEPENDENCY_SYSTEM),
        )
        checked = check_findings(found, filing, (str(name),) if name else ())
        verdicts = await judge_dependencies(
            client,
            cfg.model,
            cfg.effort,
            filing,
            checked.kept,
            usage,
            prompts.text("dependency_approve", APPROVE_SYSTEM),
        )
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
