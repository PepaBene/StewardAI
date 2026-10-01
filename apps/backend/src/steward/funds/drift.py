"""Parser-drift audit of the issuers' fund-level P/E and P/B (plan D6.9).

The iShares product page and Vanguard's GraphQL endpoint are undocumented, so a changed page
and a changed market look alike from the numbers alone. Each observation is stored with the
payload *shape fingerprint* and the issuer's stated exclusion/capping rules
(`fund_characteristic_audit`) and compared with the previous observation of the same fund:

- first_observation   nothing to compare with
- unchanged           same shape, rules and figures
- normal_update       a newer as-of date with ordinary movement (no alert)
- value_change        a real change: a restated figure for the same date, a move beyond the
                      threshold, a figure that disappeared, or an as-of date going backwards
- methodology_change  the issuer's stated rules (caps, exclusions) changed
- parser_drift        the payload shape changed: the parser, not the market, is suspect; the
                      figures are not compared and raise no value alert (needs_parser_review)

`alert` is true only for real changes (value_change, methodology_change).

Growth: the job runs daily, so an `unchanged` verdict is not stored every time. A row is
written when something changed, and as a heartbeat when the newest stored row is at least
`HEARTBEAT_DAYS` old (so "checked and still the same" stays visible, one row a week).
"""

import datetime as dt
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

import asyncpg
import httpx

from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.jobs.runner import JobContext
from steward.sources.fund_characteristics import FundDataError, FundFigures

Verdict = Literal[
    "first_observation",
    "unchanged",
    "normal_update",
    "value_change",
    "methodology_change",
    "parser_drift",
]
ALERT_VERDICTS: set[str] = {"value_change", "methodology_change"}
DEFAULT_THRESHOLD = Decimal("0.15")
HEARTBEAT_DAYS = 7

METRICS = ("pe", "pb", "index_pe", "index_pb")


@dataclass(frozen=True)
class Prior:
    fingerprint: str
    rules: dict[str, Any]
    as_of: dt.date
    values: dict[str, Decimal | None]
    observed_at: dt.datetime | None = None


@dataclass
class Classification:
    verdict: Verdict
    alert: bool
    detail: dict[str, Any] = field(default_factory=dict)
    recorded: bool = True  # False when an unchanged observation was not stored


def _values(figures: FundFigures) -> dict[str, Decimal | None]:
    return {m: getattr(figures, m) for m in METRICS}


def classify_observation(
    prior: Prior | None, figures: FundFigures, *, threshold: Decimal = DEFAULT_THRESHOLD
) -> Classification:
    """Real value change versus parser drift (see the module docstring)."""
    if prior is None:
        return Classification("first_observation", False)
    if prior.fingerprint != figures.fingerprint:
        return Classification(
            "parser_drift",
            False,
            {
                "needs_parser_review": True,
                "previous_fingerprint": prior.fingerprint,
                "fingerprint": figures.fingerprint,
                "note": "payload shape changed: figures not compared",
            },
        )
    if prior.rules != figures.rules:
        return Classification(
            "methodology_change",
            True,
            {"previous_rules": prior.rules, "rules": figures.rules},
        )
    new = _values(figures)
    changes: dict[str, Any] = {}
    big = False
    for metric in METRICS:
        old, cur = prior.values.get(metric), new[metric]
        if old == cur:
            continue
        entry: dict[str, Any] = {"from": old, "to": cur}
        if old is not None and cur is None:
            entry["removed"] = True
            big = True
        elif old is not None and cur is not None:
            rel = abs(cur - old) / old
            entry["relative"] = float(rel)
            big = big or rel > threshold
        changes[metric] = entry
    if figures.as_of < prior.as_of:
        return Classification(
            "value_change", True, {"as_of_moved_backwards": True, "changes": changes}
        )
    if figures.as_of == prior.as_of:
        if not changes:
            return Classification("unchanged", False)
        return Classification(
            "value_change", True, {"restated_for_same_as_of": True, "changes": changes}
        )
    if big:
        return Classification("value_change", True, {"changes": changes})
    return Classification("normal_update", False, {"changes": changes})


async def latest_prior(pool: asyncpg.Pool, fund_key: str) -> Prior | None:
    row = await pool.fetchrow(
        "select fingerprint, rules, as_of, pe, pb, index_pe, index_pb, observed_at"
        " from public.fund_characteristic_audit where fund_key = $1"
        " order by observed_at desc, id desc limit 1",
        fund_key,
    )
    if row is None:
        return None
    rules = json.loads(row["rules"]) if isinstance(row["rules"], str) else row["rules"]
    return Prior(
        fingerprint=row["fingerprint"],
        rules=dict(rules or {}),
        as_of=row["as_of"],
        values={m: (Decimal(row[m]) if row[m] is not None else None) for m in METRICS},
        observed_at=row["observed_at"],
    )


async def record_observation(
    pool: asyncpg.Pool,
    fund_key: str,
    figures: FundFigures,
    *,
    threshold: Decimal = DEFAULT_THRESHOLD,
) -> Classification:
    prior = await latest_prior(pool, fund_key)
    result = classify_observation(prior, figures, threshold=threshold)
    if (
        result.verdict == "unchanged"
        and prior is not None
        and prior.observed_at is not None
        and dt.datetime.now(dt.UTC) - prior.observed_at < dt.timedelta(days=HEARTBEAT_DAYS)
    ):
        result.recorded = False
        return result
    await pool.execute(
        "insert into public.fund_characteristic_audit (fund_key, provider, as_of, fingerprint,"
        " shape, rules, basis, pe, pb, index_pe, index_pb, verdict, alert, detail)"
        " values ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8, $9, $10, $11, $12, $13,"
        " $14::jsonb)",
        fund_key,
        figures.provider,
        figures.as_of,
        figures.fingerprint,
        json.dumps(figures.shape),
        json.dumps(figures.rules),
        figures.basis,
        figures.pe,
        figures.pb,
        figures.index_pe,
        figures.index_pb,
        result.verdict,
        result.alert,
        json.dumps(result.detail, default=str),
    )
    return result


async def read_audit(
    pool: asyncpg.Pool, fund_key: str | None = None, *, limit: int = 50, alerts_only: bool = False
) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        "select fund_key, provider, as_of, fingerprint, basis, rules, pe, pb, index_pe, index_pb,"
        " verdict, alert, detail, observed_at from public.fund_characteristic_audit"
        " where ($1::text is null or fund_key = $1) and (not $2 or alert)"
        " order by observed_at desc, id desc limit $3",
        fund_key,
        alerts_only,
        limit,
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        for k in ("rules", "detail"):
            if isinstance(d[k], str):
                d[k] = json.loads(d[k])
        out.append(d)
    return out


async def fund_characteristics_audit_handler(ctx: JobContext) -> None:
    """Fetch each configured valuation fund's characteristics and audit them. Does not
    replace `valuation_refresh`, which stores the figures the valuation uses."""
    from steward.valuation.collect import fetch_fund  # heavy import, only for the job

    stored = await load_active(ctx.pool, "jobs", JobsConfig)
    await ctx.record_config_version(stored.id if stored else None)
    config = stored.body if stored else JobsConfig()
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as http:
        for fund in config.valuation.funds:
            async with ctx.step(f"audit:{fund.key}") as step:
                try:
                    figures = await fetch_fund(http, fund)
                except (FundDataError, httpx.HTTPError) as exc:
                    # A parser that finds nothing where it used to find figures is drift
                    # evidence in itself; no figures, so no value is compared.
                    step.detail.update(outcome="fetch_failed", error=str(exc)[:300])
                    continue
                result = await record_observation(ctx.pool, fund.key, figures)
                step.detail.update(
                    outcome=result.verdict,
                    alert=result.alert,
                    as_of=figures.as_of.isoformat(),
                    recorded=result.recorded,
                )
