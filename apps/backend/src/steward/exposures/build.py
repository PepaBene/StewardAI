"""Exposure builder: where a holding's revenue is earned, computed from what it reported.

Geography comes from the revenue-by-country facts in the latest annual SEC XBRL filing
(`segment_revenue`). Only labels that name a single country are used: regions such as "Europe"
or "Asia Pacific" are not countries, so the share they carry stays unmapped rather than guessed.
Rows are written as basis `reported` with `origin = 'built'` and a provenance record. The
owner's own rows always win: an issuer with a hand-entered geography mapping is left alone, and
a built row the owner deleted is never rebuilt (`exposure_build_log`).
"""

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import asyncpg
import httpx

from steward import __version__
from steward.ai import AINotConfigured, Usage, create_client
from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.jobs import JobsConfig
from steward.config.prompts import load_prompts
from steward.config.store import load_active
from steward.exposures.ai_steps import run_dependencies, run_inputs, run_products
from steward.jobs.runner import JobContext
from steward.settings import get_settings
from steward.sources.edgar import EdgarClient
from steward.storage import SupabaseStorage

ANNUAL_FORMS = ("10-K", "20-F", "40-F")
TOLERANCE = Decimal("1.005")

# Single-country labels as filers write them, lower-cased. Combined or regional labels
# ("China (including Hong Kong)", "US & Canada", "Europe") are deliberately absent.
COUNTRIES: dict[str, str] = {
    "united states": "US",
    "united states of america": "US",
    "u.s.": "US",
    "us": "US",
    "china": "CN",
    "mainland china": "CN",
    "taiwan": "TW",
    "japan": "JP",
    "singapore": "SG",
    "united kingdom": "GB",
    "u.k.": "GB",
    "uk": "GB",
    "canada": "CA",
    "germany": "DE",
    "france": "FR",
    "italy": "IT",
    "spain": "ES",
    "sweden": "SE",
    "netherlands": "NL",
    "the netherlands": "NL",
    "australia": "AU",
    "south korea": "KR",
    "korea": "KR",
    "india": "IN",
    "ireland": "IE",
    "switzerland": "CH",
    "israel": "IL",
    "brazil": "BR",
    "mexico": "MX",
    "malaysia": "MY",
    "hong kong": "HK",
}


@dataclass(frozen=True)
class GeoShare:
    geography: str
    weight: Decimal
    label: str
    value: Decimal


def country_iso(label: str) -> str | None:
    return COUNTRIES.get(" ".join(label.lower().split()))


def plan_geography(rows: list[tuple[str, Decimal]], total: Decimal) -> list[GeoShare] | str:
    """Country shares of `total`, or the reason nothing can be built. Two labels for one
    country must agree (within half a percent), else that country is dropped as ambiguous."""
    if total <= 0:
        return "no positive total revenue"
    seen: dict[str, GeoShare] = {}
    ambiguous: set[str] = set()
    for label, value in rows:
        iso = country_iso(label)
        if iso is None or value < 0:
            continue
        share = GeoShare(iso, (value / total).quantize(Decimal("0.000001")), label, value)
        prior = seen.get(iso)
        if prior is None:
            seen[iso] = share
        elif max(prior.value, value) > min(prior.value, value) * TOLERANCE:
            ambiguous.add(iso)
    shares = [s for iso, s in seen.items() if iso not in ambiguous]
    if not shares:
        return "no single-country revenue labels"
    if sum((s.weight for s in shares), Decimal(0)) > TOLERANCE:
        return "country revenue exceeds total revenue"
    return sorted(shares, key=lambda s: -s.weight)


async def latest_annual_geography(
    pool: asyncpg.Pool, ticker: str
) -> tuple[dict[str, Any], list[tuple[str, Decimal]], Decimal] | None:
    """The newest annual filing period that has country rows and a total, as
    (filing facts, [(label, value)], total)."""
    rows = await pool.fetch(
        """select f.accession, f.form, f.filed, f.instance_url, f.id as filing_id,
                  sr.label, sr.value, sr.period_start, sr.period_end
           from public.segment_revenue sr join public.xbrl_filing f on f.id = sr.filing_id
           where f.ticker = $1 and sr.kind = 'geographic' and not sr.subtotal
             and f.form = any($2::text[]) and sr.period_end - sr.period_start between 350 and 380
           order by sr.period_end desc, f.filed desc""",
        ticker,
        list(ANNUAL_FORMS),
    )
    for period_end in dict.fromkeys(r["period_end"] for r in rows):
        group = [r for r in rows if r["period_end"] == period_end]
        filing = group[0]["filing_id"]
        group = [r for r in group if r["filing_id"] == filing]
        total = await pool.fetchval(
            "select value from public.segment_revenue where filing_id = $1 and kind = 'total'"
            " and period_start = $2 and period_end = $3 order by value desc limit 1",
            filing,
            group[0]["period_start"],
            period_end,
        )
        if total is None:
            continue
        facts = {
            k: group[0][k]
            for k in ("accession", "form", "filed", "instance_url", "period_start", "period_end")
        }
        return facts, [(r["label"], r["value"]) for r in group], total
    return None


async def write_geography(
    pool: asyncpg.Pool,
    issuer_id: int,
    shares: list[GeoShare],
    facts: dict[str, Any],
    total: Decimal,
) -> dict[str, int]:
    """Replace the issuer's built geography rows with `shares`, effective from the period end.
    Returns counts of inserted, closed and skipped rows."""
    counts = {"inserted": 0, "closed": 0, "unchanged": 0, "deleted_by_owner": 0}
    effective: dt.date = facts["period_end"]
    async with pool.acquire() as conn, conn.transaction():
        owned = await conn.fetchval(
            "select count(*) from public.exposure_mapping where issuer_id = $1"
            " and origin = 'user' and geography is not null and hs_code is null",
            issuer_id,
        )
        if owned:
            return {**counts, "owner_entries": int(owned)}
        wanted = {s.geography for s in shares}
        open_rows = await conn.fetch(
            "select id, geography, valid_from from public.exposure_mapping where issuer_id = $1"
            " and origin = 'built' and valid_to is null and hs_code is null",
            issuer_id,
        )
        current = {r["geography"]: r for r in open_rows}
        for geography, row in current.items():
            if row["valid_from"] > effective:
                return {**counts, "newer_exists": 1}
            if geography not in wanted:
                await conn.execute(
                    "update public.exposure_mapping set valid_to = $2 where id = $1",
                    row["id"],
                    effective - dt.timedelta(days=1),
                )
                counts["closed"] += 1
        for share in shares:
            row = current.get(share.geography)
            if row is not None and row["valid_from"] == effective:
                counts["unchanged"] += 1
                continue
            ever_built = await conn.fetchval(
                "select exists(select 1 from public.exposure_build_log"
                " where issuer_id = $1 and geography = $2)",
                issuer_id,
                share.geography,
            )
            still_there = await conn.fetchval(
                "select exists(select 1 from public.exposure_mapping where issuer_id = $1"
                " and geography = $2 and origin = 'built' and hs_code is null)",
                issuer_id,
                share.geography,
            )
            if ever_built and not still_there:
                counts["deleted_by_owner"] += 1
                continue
            if row is not None:
                await conn.execute(
                    "update public.exposure_mapping set valid_to = $2 where id = $1",
                    row["id"],
                    effective - dt.timedelta(days=1),
                )
                counts["closed"] += 1
            provenance = {
                "accession": facts["accession"],
                "form": facts["form"],
                "instance_url": facts["instance_url"],
                "label": share.label,
                "value": str(share.value),
                "total_revenue": str(total),
                "period_start": facts["period_start"].isoformat(),
                "period_end": effective.isoformat(),
            }
            await conn.execute(
                """insert into public.exposure_mapping
                   (issuer_id, segment, geography, basis, weight, valid_from, origin, provenance)
                   values ($1, $2, $3, 'reported', $4, $5, 'built', $6::jsonb)""",
                issuer_id,
                f"Revenue: {share.label}",
                share.geography,
                share.weight,
                effective,
                json.dumps(provenance),
            )
            await conn.execute(
                "insert into public.exposure_build_log (issuer_id, geography) values ($1, $2)"
                " on conflict do nothing",
                issuer_id,
                share.geography,
            )
            counts["inserted"] += 1
    return counts


async def exposure_build_handler(ctx: JobContext) -> None:
    """Build the geography exposure of every holding that has an issuer and annual XBRL."""
    only = ctx.params.get("ticker")
    rows = await ctx.pool.fetch(
        """select l.ticker, s.issuer_id from public.instrument_security_link l
           join public.security s on s.id = l.security_id
           where l.status = 'confirmed' and s.issuer_id is not null order by l.ticker"""
    )
    if isinstance(only, str) and only:
        rows = [r for r in rows if r["ticker"] == only]
    results: dict[str, Any] = {}
    async with ctx.step("geography") as step:
        for row in rows:
            ticker = row["ticker"]
            found = await latest_annual_geography(ctx.pool, ticker)
            if found is None:
                results[ticker] = "no annual country revenue in XBRL"
                continue
            facts, labelled, total = found
            plan = plan_geography(labelled, total)
            if isinstance(plan, str):
                results[ticker] = plan
                continue
            counts = await write_geography(ctx.pool, row["issuer_id"], plan, facts, total)
            mapped = sum((s.weight for s in plan), Decimal(0))
            results[ticker] = {**counts, "countries": len(plan), "mapped": float(mapped)}
        step.detail["holdings"] = len(rows)
        step.detail["results"] = results
    await _ai_steps(ctx, list(rows))


async def _ai_steps(ctx: JobContext, rows: list[asyncpg.Record]) -> None:
    """HS mapping and currency / input extraction; skipped when AI or EDGAR is not set up."""
    settings = get_settings()
    stored = await load_active(ctx.pool, "jobs", JobsConfig)
    await ctx.record_config_version(stored.id if stored else None)
    cfg = (stored.body if stored else JobsConfig()).exposure_build_settings
    if not (cfg.map_products or cfg.extract_inputs or cfg.extract_dependencies):
        return
    try:
        client = create_client(settings)
    except AINotConfigured as exc:
        async with ctx.step("ai") as step:
            step.detail["skipped"] = str(exc)
        return
    usage = Usage(model=cfg.model)
    prompts = await load_prompts(ctx.pool)
    if cfg.map_products:
        async with ctx.step("products") as step:
            step.detail["results"] = await run_products(ctx, client, cfg, rows, usage, prompts)
            step.usage = usage.step_usage()
    profiles_stored = await load_active(ctx.pool, "holding_profiles", HoldingProfilesConfig)
    if not (cfg.extract_inputs or cfg.extract_dependencies) or profiles_stored is None:
        return
    async with ctx.step("filings") as step:
        if not settings.sec_user_agent:
            step.detail["skipped"] = "SEC_USER_AGENT is not set"
            return
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, read=120.0),
            headers={"User-Agent": f"StewardAI/{__version__}"},
            follow_redirects=True,
        ) as http:
            edgar = EdgarClient(http, settings.sec_user_agent)
            storage = (
                SupabaseStorage(
                    http, settings.supabase_url, settings.supabase_secret_key.get_secret_value()
                )
                if settings.supabase_url and settings.supabase_secret_key
                else None
            )
            if cfg.extract_inputs:
                step.detail["inputs"] = await run_inputs(
                    ctx,
                    client,
                    cfg,
                    edgar,
                    rows,
                    profiles_stored.id,
                    profiles_stored.body,
                    usage,
                    prompts,
                    storage,
                )
            if cfg.extract_dependencies:
                step.detail["dependencies"] = await run_dependencies(
                    ctx, client, cfg, edgar, rows, profiles_stored.body, usage, prompts, storage
                )
        step.usage = usage.step_usage()
