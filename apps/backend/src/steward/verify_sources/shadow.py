"""30-day shadow-collection recorder and sign-off report (plan D8.2).

`record_shadow_snapshot` measures one UTC day per provider from the tables the collectors already
write (source_observation, macro_observation, provider_fetch_audit, provider_quota_usage,
source_conflict, identity decisions, coverage_status, job) and stores it in
`source_shadow_snapshot` / `source_shadow_job_snapshot`.  `generate_shadow_report` turns up to 30
days of snapshots into a markdown + JSON sign-off report with a dormant/low-value adapter
recommendation list.

Nothing is estimated: a provider with no snapshot rows is reported as "not measured", a ratio with a
zero denominator is null, and `sign_off_ready` stays false until the full window of daily
snapshots exists.

    python -m steward.verify_sources.shadow --days 30 --out ./shadow-report
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import asyncpg

WINDOW_DAYS = 30
MIN_DAYS_FOR_RECOMMENDATION = 7
LOW_VALUE_UNIQUE_RATIO = 0.05  # adds fewer than 5 % observations nobody else provides
UNRELIABLE_FAILURE_RATE = 0.5
MIN_ATTEMPTS_FOR_RELIABILITY = 10
HIGH_DISAGREEMENT_RATE = 0.1
QUOTA_PRESSURE = 0.8  # utilisation of the free daily quota on the busiest day


def _day_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime.combine(day, dt.time(), dt.UTC)
    return start, start + dt.timedelta(days=1)


def _ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


@dataclass
class _Provider:
    observations_new: int = 0
    metrics: set[str] = field(default_factory=set)
    geographies: set[str] = field(default_factory=set)
    entities: int = 0
    unique_observations: int = 0
    conflicts_opened: int = 0
    false_matches: int = 0
    identity_decisions: int = 0
    fetch_attempts: int = 0
    fetch_failures: int = 0
    denials: int = 0
    credits_used: float = 0.0
    quota_limit: float | None = None
    quota_period: str | None = None
    newest_known_at: dt.datetime | None = None
    coverage_available: int = 0
    coverage_total: int = 0
    endpoints: dict[str, dict[str, Any]] = field(default_factory=dict)


async def record_shadow_snapshot(pool: asyncpg.Pool, day: dt.date) -> int:
    """Measure `day` (UTC) and upsert one row per provider; returns the providers recorded.

    Re-running a day replaces that day's rows, so a late-arriving observation is picked up
    by running the recorder again."""
    start, end = _day_bounds(day)
    providers: dict[str, _Provider] = defaultdict(_Provider)

    for r in await pool.fetch(
        """select source provider, count(*) n, array_agg(distinct metric) metrics,
                  coalesce(array_agg(distinct geography) filter (where geography is not null),
                           '{}') geos,
                  count(distinct coalesce(security_id, issuer_id))
                    filter (where coalesce(security_id, issuer_id) is not null) entities
             from public.source_observation
            where known_at >= $1 and known_at < $2 group by source""",
        start,
        end,
    ):
        p = providers[r["provider"]]
        p.observations_new += r["n"]
        p.metrics |= set(r["metrics"])
        p.geographies |= set(r["geos"])
        p.entities += r["entities"]
    for r in await pool.fetch(
        """select source provider, count(*) n, array_agg(distinct series_id) metrics,
                  array_agg(distinct geography) geos
             from public.macro_observation
            where known_at >= $1 and known_at < $2 group by source""",
        start,
        end,
    ):
        p = providers[r["provider"]]
        p.observations_new += r["n"]
        p.metrics |= set(r["metrics"])
        p.geographies |= set(r["geos"])

    # Value added: today's observation has no comparable value from any other source known by
    # the end of the day.  Measured at ingestion, so it is a lower bound for later arrivals.
    for r in await pool.fetch(
        """select o.source provider, count(*) n from public.source_observation o
            where o.known_at >= $1 and o.known_at < $2 and o.value is not null
              and not exists (
                select 1 from public.source_observation x
                 where x.id <> o.id and x.source <> o.source and x.value is not null
                   and x.known_at < $2 and x.domain = o.domain and x.metric = o.metric
                   and x.period_end = o.period_end
                   and x.geography is not distinct from o.geography
                   and coalesce(x.security_id, 0) = coalesce(o.security_id, 0)
                   and coalesce(x.issuer_id, 0) = coalesce(o.issuer_id, 0))
            group by o.source""",
        start,
        end,
    ):
        providers[r["provider"]].unique_observations += r["n"]
    for r in await pool.fetch(
        """select m.source provider, count(*) n from public.macro_observation m
            where m.known_at >= $1 and m.known_at < $2 and m.value is not null
              and not exists (
                select 1 from public.macro_observation x
                 where x.id <> m.id and x.source <> m.source and x.value is not null
                   and x.known_at < $2 and x.series_id = m.series_id
                   and x.geography = m.geography and x.period = m.period)
            group by m.source""",
        start,
        end,
    ):
        providers[r["provider"]].unique_observations += r["n"]

    for r in await pool.fetch(
        """select s.source provider, count(distinct c.id) n
             from public.source_conflict c
             join public.source_observation pref on pref.id = c.preferred_observation_id
             join public.source_observation alt on alt.id = c.alternative_observation_id
            cross join lateral (values (pref.source), (alt.source)) s(source)
            where c.created_at >= $1 and c.created_at < $2 group by s.source""",
        start,
        end,
    ):
        providers[r["provider"]].conflicts_opened += r["n"]

    for r in await pool.fetch(
        """select provider, count(*) filter (where matched is false) n
             from public.provider_benchmark_result
            where tested_at >= $1 and tested_at < $2 group by provider""",
        start,
        end,
    ):
        providers[r["provider"]].false_matches += r["n"]
    for r in await pool.fetch(
        """select pi.provider, count(*) decisions,
                  count(*) filter (where d.decision = 'rejected') rejected
             from public.identity_match_decision d
             join public.provider_identifier pi on pi.id = d.provider_identifier_id
            where d.decided_at >= $1 and d.decided_at < $2 group by pi.provider""",
        start,
        end,
    ):
        p = providers[r["provider"]]
        p.identity_decisions += r["decisions"]
        p.false_matches += r["rejected"]

    for r in await pool.fetch(
        """select provider, count(*) attempts,
                  count(*) filter (where state = 'temporarily_failed') failures,
                  count(*) filter (where state in
                    ('paid_only','access_unverified','quota_exhausted')) denials,
                  coalesce(sum(credits), 0) credits
             from public.provider_fetch_audit
            where attempted_at >= $1 and attempted_at < $2 group by provider""",
        start,
        end,
    ):
        p = providers[r["provider"]]
        p.fetch_attempts = r["attempts"]
        p.fetch_failures = r["failures"]
        p.denials = r["denials"]

    for r in await pool.fetch(
        """select a.provider, a.endpoint, a.quota, a.quota_period,
                  coalesce(u.credits, 0) credits
             from public.provider_access a
             left join public.provider_quota_usage u
               on u.provider = a.provider and u.endpoint = a.endpoint
              and u.window_kind = 'day' and u.window_start >= $1 and u.window_start < $2""",
        start,
        end,
    ):
        p = providers[r["provider"]]
        credits = float(r["credits"])
        p.credits_used += credits
        if r["quota"] is not None and r["quota_period"] == "day":
            p.quota_limit = (p.quota_limit or 0.0) + float(r["quota"])
            p.quota_period = "day"
        if credits or r["quota"] is not None:
            p.endpoints[r["endpoint"]] = {
                "credits": credits,
                "quota": None if r["quota"] is None else float(r["quota"]),
                "period": r["quota_period"],
            }

    for r in await pool.fetch(
        """select provider, count(*) total, count(*) filter (where state = 'available') avail
             from public.coverage_status group by provider"""
    ):
        p = providers[r["provider"]]
        p.coverage_total, p.coverage_available = r["total"], r["avail"]

    for r in await pool.fetch(
        """select source provider, max(known_at) newest from public.source_observation
            where known_at < $1 group by source""",
        end,
    ):
        providers[r["provider"]].newest_known_at = r["newest"]
    for r in await pool.fetch(
        """select source provider, max(known_at) newest from public.macro_observation
            where known_at < $1 group by source""",
        end,
    ):
        p = providers[r["provider"]]
        if p.newest_known_at is None or r["newest"] > p.newest_known_at:
            p.newest_known_at = r["newest"]

    reference_now = min(dt.datetime.now(dt.UTC), end)
    async with pool.acquire() as conn, conn.transaction():
        for name, p in sorted(providers.items()):
            freshness = (
                None
                if p.newest_known_at is None
                else max(0.0, (reference_now - p.newest_known_at).total_seconds() / 3600)
            )
            await conn.execute(
                """insert into public.source_shadow_snapshot
                   (snapshot_date,provider,observations_new,metrics_covered,entities_covered,
                    unique_observations,conflicts_opened,false_matches,identity_decisions,
                    fetch_attempts,fetch_failures,denials,credits_used,quota_limit,quota_period,
                    newest_known_at,freshness_hours,coverage_available,coverage_total,detail)
                   values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,
                          $20::jsonb)
                   on conflict (snapshot_date,provider) do update set
                     observations_new=excluded.observations_new,
                     metrics_covered=excluded.metrics_covered,
                     entities_covered=excluded.entities_covered,
                     unique_observations=excluded.unique_observations,
                     conflicts_opened=excluded.conflicts_opened,
                     false_matches=excluded.false_matches,
                     identity_decisions=excluded.identity_decisions,
                     fetch_attempts=excluded.fetch_attempts,
                     fetch_failures=excluded.fetch_failures, denials=excluded.denials,
                     credits_used=excluded.credits_used, quota_limit=excluded.quota_limit,
                     quota_period=excluded.quota_period,
                     newest_known_at=excluded.newest_known_at,
                     freshness_hours=excluded.freshness_hours,
                     coverage_available=excluded.coverage_available,
                     coverage_total=excluded.coverage_total, detail=excluded.detail,
                     recorded_at=now()""",
                day,
                name,
                p.observations_new,
                len(p.metrics),
                p.entities,
                p.unique_observations,
                p.conflicts_opened,
                p.false_matches,
                p.identity_decisions,
                p.fetch_attempts,
                p.fetch_failures,
                p.denials,
                p.credits_used,
                p.quota_limit,
                p.quota_period,
                p.newest_known_at,
                freshness,
                p.coverage_available,
                p.coverage_total,
                json.dumps(
                    {
                        "metrics": sorted(p.metrics)[:500],
                        "geographies": sorted(p.geographies)[:250],
                        "endpoints": p.endpoints,
                    }
                ),
            )
        jobs = await conn.fetch(
            """select job_type, count(*) runs, count(*) filter (where status = 'failed') failed
                 from public.job where created_at >= $1 and created_at < $2 group by job_type""",
            start,
            end,
        )
        for j in jobs:
            await conn.execute(
                """insert into public.source_shadow_job_snapshot
                   (snapshot_date,job_type,runs,failed) values($1,$2,$3,$4)
                   on conflict (snapshot_date,job_type) do update
                     set runs=excluded.runs, failed=excluded.failed, recorded_at=now()""",
                day,
                j["job_type"],
                j["runs"],
                j["failed"],
            )
    return len(providers)


async def backfill_shadow_snapshots(pool: asyncpg.Pool, end: dt.date, days: int) -> int:
    """Record `days` days ending at `end` (inclusive); used to seed or repair a window."""
    total = 0
    for offset in range(days):
        total += await record_shadow_snapshot(pool, end - dt.timedelta(days=offset))
    return total


# ---------------------------------------------------------------------------------- report
@dataclass(frozen=True)
class Recommendation:
    provider: str
    classification: (
        str  # keep | dormant | low_value | unreliable | disabled_by_access | not_measured
    )
    reasons: tuple[str, ...]


def classify_provider(summary: dict[str, Any], days_in_window: int) -> Recommendation:
    """Dormant / low-value recommendation from a provider's measured summary.

    The plan keeps low-value adapters as documented gap-fill options instead of spending their
    free quota routinely: `dormant` and `low_value` mean exactly that."""
    name = summary["provider"]
    days = summary["days_with_data"]
    if days == 0:
        return Recommendation(name, "not_measured", ("no snapshot rows in the window",))
    reasons: list[str] = []
    obs = summary["observations"]
    attempts = summary["fetch_attempts"]
    if summary["denials"] and summary["denials"] >= max(1, attempts // 2) and not obs:
        return Recommendation(
            name,
            "disabled_by_access",
            (f"{summary['denials']} of {attempts} attempts were denials and no data arrived",),
        )
    if days < MIN_DAYS_FOR_RECOMMENDATION:
        return Recommendation(
            name,
            "not_measured",
            (f"only {days} days measured; {MIN_DAYS_FOR_RECOMMENDATION} needed to recommend",),
        )
    if attempts >= MIN_ATTEMPTS_FOR_RELIABILITY and summary["failure_rate"] is not None:
        if summary["failure_rate"] > UNRELIABLE_FAILURE_RATE:
            reasons.append(
                f"{summary['failure_rate']:.0%} of {attempts} attempts failed or were denied"
            )
            return Recommendation(name, "unreliable", tuple(reasons))
    if obs == 0:
        return Recommendation(
            name,
            "dormant",
            ("produced no observations in the window; keep as a documented gap-fill option",),
        )
    ratio = summary["value_added_ratio"]
    if ratio is not None and ratio < LOW_VALUE_UNIQUE_RATIO:
        return Recommendation(
            name,
            "low_value",
            (
                f"only {ratio:.1%} of its observations are not already provided by another source",
                "keep dormant: use for gap-fill, do not spend its free quota routinely",
            ),
        )
    if (
        summary["disagreement_rate"] is not None
        and summary["disagreement_rate"] > HIGH_DISAGREEMENT_RATE
    ):
        reasons.append(
            f"{summary['disagreement_rate']:.0%} of observations conflict with another source"
        )
    if summary["quota_pressure"] is not None and summary["quota_pressure"] >= QUOTA_PRESSURE:
        reasons.append(f"busiest day used {summary['quota_pressure']:.0%} of the free daily quota")
    return Recommendation(name, "keep", tuple(reasons))


async def _scope(pool: asyncpg.Pool) -> dict[str, Any]:
    held = await pool.fetch(
        """select distinct ticker from public.position_snapshot
            where snapshot_date = (select max(snapshot_date) from public.position_snapshot)"""
    )
    watch = await pool.fetch("select distinct ticker from public.watchlist_item")
    tickers = sorted({r["ticker"] for r in held} | {r["ticker"] for r in watch})
    securities = (
        await pool.fetch(
            """select distinct security_id from public.instrument_security_link
                where ticker = any($1::text[]) and status = 'confirmed'
                  and security_id is not null""",
            tickers,
        )
        if tickers
        else []
    )
    return {
        "held_tickers": len({r["ticker"] for r in held}),
        "watchlist_tickers": len({r["ticker"] for r in watch}),
        "scope_tickers": len(tickers),
        "scope_securities_resolved": len(securities),
        "security_ids": [r["security_id"] for r in securities],
    }


async def generate_shadow_report(
    pool: asyncpg.Pool,
    window_end: dt.date,
    *,
    window_days: int = WINDOW_DAYS,
    persist: bool = True,
) -> dict[str, Any]:
    """Build (and optionally store) the sign-off report for the window ending `window_end`."""
    window_start = window_end - dt.timedelta(days=window_days - 1)
    rows = await pool.fetch(
        """select * from public.source_shadow_snapshot
            where snapshot_date between $1 and $2 order by provider, snapshot_date""",
        window_start,
        window_end,
    )
    job_rows = await pool.fetch(
        """select job_type, sum(runs) runs, sum(failed) failed, count(*) days
             from public.source_shadow_job_snapshot
            where snapshot_date between $1 and $2 group by job_type order by job_type""",
        window_start,
        window_end,
    )
    scope = await _scope(pool)
    scope_ids = scope.pop("security_ids")
    scope_coverage: dict[str, int] = {}
    if scope_ids:
        for r in await pool.fetch(
            """select source provider, count(distinct security_id) n
                 from public.source_observation
                where security_id = any($1::bigint[]) and known_at >= $2 and known_at < $3
                group by source""",
            scope_ids,
            _day_bounds(window_start)[0],
            _day_bounds(window_end)[1],
        ):
            scope_coverage[r["provider"]] = r["n"]

    by_provider: dict[str, list[asyncpg.Record]] = defaultdict(list)
    for r in rows:
        by_provider[r["provider"]].append(r)
    snapshot_days = sorted({r["snapshot_date"] for r in rows})

    providers: list[dict[str, Any]] = []
    for name, items in sorted(by_provider.items()):
        obs = sum(i["observations_new"] for i in items)
        attempts = sum(i["fetch_attempts"] for i in items)
        bad = sum(i["fetch_failures"] + i["denials"] for i in items)
        metrics: set[str] = set()
        geos: set[str] = set()
        pressure: list[float] = []
        for i in items:
            detail = i["detail"] if isinstance(i["detail"], dict) else json.loads(i["detail"])
            metrics |= set(detail.get("metrics", []))
            geos |= set(detail.get("geographies", []))
            for ep in (detail.get("endpoints") or {}).values():
                if ep.get("quota") and ep.get("period") == "day":
                    pressure.append(ep["credits"] / ep["quota"])
        freshness = [float(i["freshness_hours"]) for i in items if i["freshness_hours"] is not None]
        last = items[-1]
        decisions = sum(i["identity_decisions"] for i in items)
        summary: dict[str, Any] = {
            "provider": name,
            "days_with_data": len(items),
            "observations": obs,
            "metrics_covered": len(metrics),
            "metrics": sorted(metrics),
            "geographies": sorted(geos),
            "unique_observations": sum(i["unique_observations"] for i in items),
            "value_added_ratio": _ratio(sum(i["unique_observations"] for i in items), obs),
            "conflicts": sum(i["conflicts_opened"] for i in items),
            "disagreement_rate": _ratio(sum(i["conflicts_opened"] for i in items), obs),
            "false_matches": sum(i["false_matches"] for i in items),
            "identity_decisions": decisions,
            "false_match_rate": _ratio(sum(i["false_matches"] for i in items), decisions),
            "fetch_attempts": attempts,
            "fetch_failures": sum(i["fetch_failures"] for i in items),
            "denials": sum(i["denials"] for i in items),
            "failure_rate": _ratio(bad, attempts),
            "credits_used": float(sum(i["credits_used"] for i in items)),
            "quota_pressure": round(max(pressure), 4) if pressure else None,
            "median_freshness_hours": round(statistics.median(freshness), 2) if freshness else None,
            "latest_freshness_hours": None
            if last["freshness_hours"] is None
            else round(float(last["freshness_hours"]), 2),
            "coverage_available": last["coverage_available"],
            "coverage_total": last["coverage_total"],
            "coverage_ratio": _ratio(last["coverage_available"], last["coverage_total"]),
            "scope_securities_with_data": scope_coverage.get(name, 0),
        }
        rec = classify_provider(summary, window_days)
        summary["recommendation"] = rec.classification
        summary["recommendation_reasons"] = list(rec.reasons)
        providers.append(summary)

    jobs = [
        {
            "job_type": r["job_type"],
            "runs": int(r["runs"]),
            "failed": int(r["failed"]),
            "failure_rate": _ratio(int(r["failed"]), int(r["runs"])),
        }
        for r in job_rows
    ]
    blockers: list[str] = []
    if not snapshot_days:
        blockers.append("no daily snapshots are recorded: the shadow collection has not run")
    elif len(snapshot_days) < window_days:
        blockers.append(
            f"{len(snapshot_days)} of {window_days} daily snapshots recorded "
            f"({window_start}..{window_end}); sign-off needs the full window"
        )
    if scope["scope_tickers"] == 0:
        blockers.append("no held or watchlist securities to measure against")
    elif scope["scope_securities_resolved"] < scope["scope_tickers"]:
        blockers.append(
            f"{scope['scope_tickers'] - scope['scope_securities_resolved']} of "
            f"{scope['scope_tickers']} held/watchlist tickers have no confirmed security link"
        )
    if not providers:
        blockers.append("no provider produced any measurement")
    not_measured = [p["provider"] for p in providers if p["recommendation"] == "not_measured"]
    if not_measured:
        blockers.append(f"too few days measured to recommend: {', '.join(not_measured)}")

    report: dict[str, Any] = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "window_days": window_days,
        "days_observed": len(snapshot_days),
        "sign_off_ready": not blockers,
        "blockers": blockers,
        "scope": scope,
        "providers": providers,
        "jobs": jobs,
        "recommendations": {
            "keep": [p["provider"] for p in providers if p["recommendation"] == "keep"],
            "dormant": [
                {"provider": p["provider"], "reasons": p["recommendation_reasons"]}
                for p in providers
                if p["recommendation"]
                in {"dormant", "low_value", "unreliable", "disabled_by_access"}
            ],
            "not_measured": not_measured,
        },
        "notes": [
            "Counts are measured from the app's own tables; unknown stays null.",
            "value_added_ratio is the share of a provider's observations with no comparable "
            "value from another source at ingestion time (a lower bound if other sources "
            "arrive later).",
            "A provider absent from this report produced no snapshot rows and is not measured.",
        ],
    }
    markdown = render_markdown(report)
    report["markdown_chars"] = len(markdown)
    if persist:
        await pool.execute(
            """insert into public.source_shadow_report
               (window_start,window_end,days_observed,sign_off_ready,report,markdown)
               values($1,$2,$3,$4,$5::jsonb,$6)""",
            window_start,
            window_end,
            len(snapshot_days),
            report["sign_off_ready"],
            json.dumps(report, default=str),
            markdown,
        )
    return report


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Free data sources - 30-day shadow collection sign-off",
        "",
        f"Window: {report['window_start']} to {report['window_end']} "
        f"({report['days_observed']} of {report['window_days']} daily snapshots recorded)",
        "",
        f"**Sign-off ready: {'yes' if report['sign_off_ready'] else 'no'}**",
        "",
    ]
    if report["blockers"]:
        lines += ["Blockers:", *[f"- {b}" for b in report["blockers"]], ""]
    scope = report["scope"]
    lines += [
        "## Scope",
        f"- Held tickers: {scope['held_tickers']}; "
        f"watchlist tickers: {scope['watchlist_tickers']}; "
        f"securities with a confirmed link: {scope['scope_securities_resolved']}",
        "",
    ]
    if not report["providers"]:
        lines += ["No provider data was recorded in this window; nothing can be signed off.", ""]
    else:
        lines += [
            "## Providers",
            "",
            "| Provider | Days | Observations | Metrics | Value added | Disagreement | "
            "False matches | Failure rate | Freshness (median h) | Quota peak | Verdict |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for p in report["providers"]:
            fresh = p["median_freshness_hours"]
            lines.append(
                f"| {p['provider']} | {p['days_with_data']} | {p['observations']} | "
                f"{p['metrics_covered']} | {_pct(p['value_added_ratio'])} | "
                f"{_pct(p['disagreement_rate'])} | {p['false_matches']} | "
                f"{_pct(p['failure_rate'])} | {'n/a' if fresh is None else fresh} | "
                f"{_pct(p['quota_pressure'])} | {p['recommendation']} |"
            )
        lines.append("")
    dormant = report["recommendations"]["dormant"]
    lines += ["## Dormant or low-value adapters (documented gap-fill options only)", ""]
    if dormant:
        for d in dormant:
            lines.append(f"- **{d['provider']}**: " + "; ".join(d["reasons"]))
    else:
        lines.append("- none identified in this window")
    if report["recommendations"]["not_measured"]:
        lines += [
            "",
            "Not enough data to recommend: " + ", ".join(report["recommendations"]["not_measured"]),
        ]
    lines += ["", "## Failed jobs", ""]
    if report["jobs"]:
        lines += ["| Job | Runs | Failed | Failure rate |", "|---|---|---|---|"]
        lines += [
            f"| {j['job_type']} | {j['runs']} | {j['failed']} | {_pct(j['failure_rate'])} |"
            for j in report["jobs"]
        ]
    else:
        lines.append("No job runs recorded in the window.")
    lines += ["", "## Notes", *[f"- {n}" for n in report["notes"]], ""]
    return "\n".join(lines)


def _write_outputs(out: Path, markdown: str, report: dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "shadow_report.md").write_text(markdown)
    (out / "shadow_report.json").write_text(json.dumps(report, indent=2, default=str))
    print(f"wrote {out / 'shadow_report.md'} and shadow_report.json")


async def _main(days: int, out: Path | None, record_today: bool) -> int:
    from steward.db import create_pool
    from steward.settings import get_settings

    pool = await create_pool(get_settings())
    try:
        today = dt.datetime.now(dt.UTC).date()
        if record_today:
            await record_shadow_snapshot(pool, today - dt.timedelta(days=1))
        report = await generate_shadow_report(pool, today - dt.timedelta(days=1), window_days=days)
    finally:
        await pool.close()
    markdown = render_markdown(report)
    if out is None:
        print(markdown)
    else:
        _write_outputs(out, markdown, report)
    return 0 if report["sign_off_ready"] else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Free-source shadow-collection sign-off report")
    parser.add_argument("--days", type=int, default=WINDOW_DAYS)
    parser.add_argument("--out", type=Path, default=None, help="directory for markdown + JSON")
    parser.add_argument("--record", action="store_true", help="record yesterday's snapshot first")
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args.days, args.out, args.record)))


if __name__ == "__main__":
    main()
