# ruff: noqa: E501
"""Aggregate, cache and refresh the company checks (insight plan I3.9).

The result is computed from the stored read models, never by an LLM. Every computation is
cached in `company_check_snapshot` by the hash of its inputs, the rules and the method version;
the screener, the portfolio scorecard and the dividend tiers read the latest cached row, so they
show the same result the company page shows.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import uuid
from decimal import Decimal
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel

from steward.analytics.contract import input_hash
from steward.analytics.versions import version
from steward.checks.contract import SCORED_AXES, AxisScore, Check, axis_score, score_text
from steward.checks.dividend import dividend_checks
from steward.checks.future import future_checks
from steward.checks.health import health_checks
from steward.checks.inputs import CheckInputs
from steward.checks.past import past_checks
from steward.checks.risk import risk_checks
from steward.checks.sentences import SENTENCES_VERSION
from steward.checks.value import value_checks
from steward.config.checks import ChecksConfig, load_checks
from steward.jobs.runner import JobContext

METHOD = "checks"


class CompanyChecks(BaseModel):
    ticker: str
    name: str | None
    status: Literal["complete", "partial", "unavailable"]
    computed_at: dt.datetime
    method_version: str
    sentences_version: str
    rules_version: int | None
    input_hash: str
    axes: list[AxisScore]
    checks: list[Check]
    risks: list[Check]
    verdict: str
    rewards: list[Check]
    risk_highlights: list[Check]
    coverage: Decimal | None
    reason: str | None = None


def evaluate(inp: CheckInputs, rules: ChecksConfig) -> tuple[list[Check], list[Check]]:
    lim = rules.limits
    checks = [
        *value_checks(inp, lim),
        *future_checks(inp, lim),
        *past_checks(inp, lim),
        *health_checks(inp, lim),
        *dividend_checks(inp, lim),
    ]
    return checks, risk_checks(inp, lim)


def verdict(axes: list[AxisScore]) -> str:
    scored = [a for a in axes if a.score is not None]
    if not scored:
        return "Not enough data to score any axis yet."
    best = max(scored, key=lambda a: (a.score or 0, a.applicable))
    worst = min(scored, key=lambda a: (a.score or 0, -a.applicable))
    if len(scored) == 1:
        return f"Only {best.label} can be scored: {score_text(best)}."
    if best.score == worst.score:
        return f"Level across the {len(scored)} scored axes: each at {best.score:.1f} of 6."
    return f"Strongest on {best.label} ({score_text(best)}); weakest on {worst.label} ({score_text(worst)})."


def assemble(
    inp: CheckInputs, rules: ChecksConfig, rules_version: int | None, hash_: str, now: dt.datetime
) -> CompanyChecks:
    checks, risks = evaluate(inp, rules)
    axes = [axis_score(a, checks) for a in SCORED_AXES]
    relevant = sum(a.applicable + a.unknown for a in axes)
    applicable = sum(a.applicable for a in axes)
    coverage = Decimal(applicable) / relevant if relevant else None
    status: Literal["complete", "partial", "unavailable"] = (
        "unavailable"
        if applicable == 0
        else "complete"
        if all(a.unknown == 0 for a in axes)
        else "partial"
    )
    return CompanyChecks(
        ticker=inp.ticker, name=inp.name, status=status, computed_at=now, method_version=version(METHOD),
        sentences_version=SENTENCES_VERSION, rules_version=rules_version, input_hash=hash_, axes=axes, checks=checks,
        risks=risks, verdict=verdict(axes), rewards=[c for c in checks if c.state == "pass"][:3],
        risk_highlights=[c for c in risks if c.state == "fail" and not c.informational][:3],
        coverage=coverage.quantize(Decimal("0.0001")) if coverage is not None else None,
    )  # fmt: skip


def inputs_hash(inp: CheckInputs, rules: ChecksConfig) -> str:
    body = dataclasses.asdict(inp)
    body.pop("today", None)
    return input_hash({"inputs": json.loads(json.dumps(body, default=str)), "rules": rules.model_dump(mode="json"),
                       "method": version(METHOD), "sentences": SENTENCES_VERSION, "day": inp.today.isoformat()})  # fmt: skip


def scores_of(result: CompanyChecks) -> dict[str, Any]:
    out: dict[str, Any] = {
        a.axis: {
            "passes": a.passes,
            "applicable": a.applicable,
            "unknown": a.unknown,
            "na": a.na,
            "score": str(a.score) if a.score is not None else None,
        }
        for a in result.axes
    }
    out["risk"] = {
        "flags": sum(1 for c in result.risks if c.state == "fail" and not c.informational),
        "unknown": sum(1 for c in result.risks if c.state == "unknown"),
    }
    out["checks"] = {c.id: c.state for c in [*result.checks, *result.risks]}
    return out


async def company_checks(pool: asyncpg.Pool, owner_id: Any, ticker: str, *, div_ctx: Any = None, theses: dict[str, Any] | None = None,
                         today: dt.date | None = None) -> CompanyChecks | None:  # fmt: skip
    """Computed from today's inputs; the stored row with the same input hash is reused."""
    from steward.checks.load import load_inputs

    inp = await load_inputs(pool, owner_id, ticker, div_ctx=div_ctx, theses=theses, today=today)
    if inp is None:
        return None
    rules, rules_version = await load_checks(pool)
    h = inputs_hash(inp, rules)
    # Reusing a stored row makes it the newest: the holdings rows and drawer read the newest row,
    # and inputs that went back to an earlier state must not leave them on a later stale one.
    row = await pool.fetchrow(
        "update public.company_check_snapshot set computed_at = greatest(computed_at, now())"
        " where owner_id = $1 and ticker = $2 and input_hash = $3 returning result",
        owner_id, ticker, h,
    )  # fmt: skip
    if row is not None:
        stored = row["result"]
        return CompanyChecks.model_validate(
            json.loads(stored) if isinstance(stored, str) else stored
        )
    result = assemble(inp, rules, rules_version, h, dt.datetime.now(dt.UTC))
    await pool.execute(
        """insert into public.company_check_snapshot (owner_id, ticker, method_version, input_hash, scores, result)
           values ($1, $2, $3, $4, $5::jsonb, $6::jsonb) on conflict (owner_id, ticker, input_hash) do nothing""",
        owner_id, ticker, result.method_version, h, json.dumps(scores_of(result)), result.model_dump_json(),
    )  # fmt: skip
    # Keep the latest few per ticker: older rows are superseded results, not history.
    await pool.execute(
        """delete from public.company_check_snapshot where owner_id = $1 and ticker = $2 and id not in (
             select id from public.company_check_snapshot where owner_id = $1 and ticker = $2 order by computed_at desc limit 5)""",
        owner_id, ticker,
    )  # fmt: skip
    return result


class CachedScore(BaseModel):
    ticker: str
    computed_at: dt.datetime
    axes: dict[str, AxisScore]
    risk_flags: int


async def latest_scores(
    pool: asyncpg.Pool, owner_id: Any, tickers: list[str] | None = None
) -> dict[str, CachedScore]:
    """The latest cached axis scores per ticker (no recomputation)."""
    rows = await pool.fetch(
        """select distinct on (ticker) ticker, computed_at, result from public.company_check_snapshot
            where owner_id = $1 and ($2::text[] is null or ticker = any($2::text[]))
            order by ticker, computed_at desc""",
        owner_id, tickers,
    )  # fmt: skip
    out: dict[str, CachedScore] = {}
    for r in rows:
        res = r["result"]
        res = json.loads(res) if isinstance(res, str) else res
        axes = {a["axis"]: AxisScore.model_validate(a) for a in res.get("axes", [])}
        flags = sum(
            1
            for c in res.get("risks", [])
            if c.get("state") == "fail" and not c.get("informational")
        )
        out[r["ticker"]] = CachedScore(
            ticker=r["ticker"], computed_at=r["computed_at"], axes=axes, risk_flags=flags
        )
    return out


async def refresh(pool: asyncpg.Pool, owner_id: Any, tickers: list[str]) -> dict[str, Any]:
    from steward.dividends.service import load_context
    from steward.valuation.fair_value import load_theses

    ctx = await load_context(pool)
    theses = await load_theses(pool)
    out: dict[str, Any] = {}
    for t in tickers:
        try:
            res = await company_checks(pool, owner_id, t, div_ctx=ctx, theses=theses)
        except (ValueError, ArithmeticError) as exc:
            out[t] = f"failed: {type(exc).__name__}: {exc}"[:200]
            continue
        out[t] = None if res is None else {a.axis: score_text(a) for a in res.axes}
    return out


async def checks_refresh_handler(ctx: JobContext) -> None:
    """Daily: recompute the checks of every holding and watchlist name, so the scorecard, the
    dividend tiers and the screener read today's results."""
    from steward.settings import get_settings
    from steward.valuation.fair_value import _tickers

    owner = get_settings().owner_user_id
    async with ctx.step("checks_refresh") as step:
        if not owner:
            step.detail["skipped"] = "OWNER_USER_ID is not set"
            return
        oid = uuid.UUID(str(owner))
        step.detail["tickers"] = await refresh(ctx.pool, oid, await _tickers(ctx.pool, oid))
