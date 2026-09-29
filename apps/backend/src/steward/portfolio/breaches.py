"""Drift, hard-limit and strategy-rule checks after each sync.

Every sync evaluates the strategy against the new snapshot:
- a position, category or cash weight outside its target band is a `band` flag at the
  strategy's `band_breach_severity` (default 1: listed in the next digest, never alerted;
  2 or 3 alert like a breach);
- a position above its hard limit is a `hard_limit` breach at `hard_limit_severity`
  (default 3);
- a structured rule that does not hold (position or category weight, cash weight, position
  count) is a `rule` breach at the rule's `on_breach` severity.

A breach is a row in `portfolio_breach`, open while the condition holds. It alerts once,
when it opens (routed by the notification rules like any event), and is cleared, not
re-alerted, while it stays open; when the portfolio is back in band it is closed, and a
later breach opens a new row that alerts again.
"""

import datetime as dt
from dataclasses import dataclass

import asyncpg

from steward.config.notifications import NotificationsConfig
from steward.config.store import load_active
from steward.config.strategy import (
    CashWeightRule,
    CategoryWeightRule,
    PositionCountRule,
    PositionWeightRule,
    StrategyConfig,
)
from steward.notify.alerts import create_alerts
from steward.notify.route import AlertRequest
from steward.portfolio.drift import DriftReport


def _holds(value: float, operator: str, limit: float) -> bool:
    """Whether `value operator limit` holds (weights compared with a small tolerance)."""
    eps = 1e-9
    if operator == "<":
        return value < limit
    if operator == "<=":
        return value <= limit + eps
    if operator == ">":
        return value > limit
    if operator == ">=":
        return value >= limit - eps
    return abs(value - limit) <= eps


@dataclass(frozen=True)
class Finding:
    key: str
    kind: str  # band, hard_limit, rule
    ticker: str | None
    severity: int
    detail: str
    observed: float | None
    limit: float | None


def _pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def evaluate(report: DriftReport, strategy: StrategyConfig) -> list[Finding]:
    findings: list[Finding] = []
    hard_severity = int(strategy.hard_limit_severity[-1])
    band_severity = int(strategy.band_breach_severity[-1])
    for p in report.positions:
        if p.status == "above_hard_limit" and p.hard_limit is not None:
            findings.append(
                Finding(
                    f"hard_limit:{p.ticker}",
                    "hard_limit",
                    p.ticker,
                    hard_severity,
                    f"{p.ticker} weighs {_pct(p.weight)}, above its hard limit of "
                    f"{_pct(p.hard_limit)}",
                    p.weight,
                    p.hard_limit,
                )
            )
        if p.status in ("above_band", "below_band", "above_hard_limit") and p.upper is not None:
            findings.append(
                Finding(
                    f"band:position:{p.ticker}",
                    "band",
                    p.ticker,
                    band_severity,
                    f"{p.ticker} weighs {_pct(p.weight)}, outside its band "
                    f"{_pct(p.lower or 0)}-{_pct(p.upper)}",
                    p.weight,
                    p.target_weight,
                )
            )
    for dim in report.categories:
        for b in dim.buckets:
            if b.status in ("above_band", "below_band") and b.upper is not None:
                findings.append(
                    Finding(
                        f"band:category:{dim.dimension}:{b.bucket}",
                        "band",
                        None,
                        band_severity,
                        f"{dim.dimension} {b.bucket} weighs {_pct(b.weight)}, outside its band "
                        f"{_pct(b.lower or 0)}-{_pct(b.upper)}",
                        b.weight,
                        b.target_weight,
                    )
                )
    c = report.cash
    if c.status in ("above_band", "below_band") and c.weight is not None and c.upper is not None:
        findings.append(
            Finding(
                "band:cash",
                "band",
                None,
                band_severity,
                f"Cash weighs {_pct(c.weight)}, outside its band "
                f"{_pct(c.lower or 0)}-{_pct(c.upper)}",
                c.weight,
                c.target_weight,
            )
        )

    held = [p for p in report.positions if p.held]
    for rule in strategy.enabled_rules:
        severity = int(rule.on_breach[-1])
        checks: list[tuple[str, str | None, float, str]] = []  # (key suffix, ticker, value, what)
        if isinstance(rule, PositionWeightRule):
            for p in held:
                if rule.ticker is None or p.ticker == rule.ticker:
                    checks.append((p.ticker, p.ticker, p.weight, f"{p.ticker} weight"))
        elif isinstance(rule, CategoryWeightRule):
            found = next((d for d in report.categories if d.dimension == rule.dimension), None)
            for b in found.buckets if found else []:
                if rule.bucket is None or b.bucket == rule.bucket:
                    checks.append(
                        (
                            f"{rule.dimension}:{b.bucket}",
                            None,
                            b.weight,
                            f"{rule.dimension} {b.bucket} weight",
                        )
                    )
        elif isinstance(rule, CashWeightRule):
            if c.weight is not None:
                checks.append(("cash", None, c.weight, "cash weight"))
        elif isinstance(rule, PositionCountRule):
            checks.append(("count", None, float(len(held)), "number of positions"))
        for suffix, ticker, value, what in checks:
            limit = float(rule.value)
            if _holds(value, rule.operator, limit):
                continue
            shown = f"{int(value)}" if isinstance(rule, PositionCountRule) else _pct(value)
            limit_shown = f"{int(limit)}" if isinstance(rule, PositionCountRule) else _pct(limit)
            findings.append(
                Finding(
                    f"rule:{rule.id}:{suffix}",
                    "rule",
                    ticker,
                    severity,
                    f"Rule {rule.id}: {what} is {shown}; the rule requires "
                    f"{rule.operator} {limit_shown}",
                    value,
                    limit,
                )
            )
    return findings


@dataclass
class BreachSummary:
    opened: list[str]
    still_open: list[str]
    cleared: list[str]
    alerts: list[str]


async def record_breaches(
    pool: asyncpg.Pool,
    findings: list[Finding],
    *,
    job_id: int,
    config_version_id: int | None,
    now: dt.datetime,
    app_url: str | None = None,
) -> BreachSummary:
    notify_stored = await load_active(pool, "notifications", NotificationsConfig)
    notify = notify_stored.body if notify_stored else NotificationsConfig()
    summary = BreachSummary([], [], [], [])
    current = {f.key for f in findings}
    open_rows = {
        r["key"]: r["id"]
        for r in await pool.fetch(
            "select id, key from public.portfolio_breach where cleared_at is null"
        )
    }
    for f in findings:
        if f.key in open_rows:
            await pool.execute(
                "update public.portfolio_breach set last_seen = $2, last_job_id = $3,"
                " observed = $4, detail = $5 where id = $1",
                open_rows[f.key],
                now,
                job_id,
                f.observed,
                f.detail,
            )
            summary.still_open.append(f.key)
            continue
        breach_id = await pool.fetchval(
            "insert into public.portfolio_breach (key, kind, ticker, severity, detail, observed,"
            " limit_value, first_seen, last_seen, first_job_id, last_job_id, config_version_id)"
            " values ($1, $2, $3, $4, $5, $6, $7, $8, $8, $9, $9, $10) returning id",
            f.key,
            f.kind,
            f.ticker,
            f.severity,
            f.detail,
            f.observed,
            f.limit,
            now,
            job_id,
            config_version_id,
        )
        summary.opened.append(f.key)
        # A band flag at severity 1 is for the digest only; at 2 or 3 it alerts.
        if f.severity < 1 or (f.kind == "band" and f.severity < 2):
            continue
        what = {"hard_limit": "hard limit", "band": "target band"}.get(f.kind, "strategy rule")
        if f.key.startswith("risk:"):
            what = "risk limit"
        lines = [f"[Severity {f.severity}] Portfolio: {what} breached", f.detail]
        if app_url:
            lines.append(f"{app_url.rstrip('/')}/")
        created = await create_alerts(
            pool,
            AlertRequest("portfolio", f.severity, f.ticker, f.kind),
            "\n".join(lines),
            notify,
            now=now,
            config_version_id=notify_stored.id if notify_stored else None,
            job_id=job_id,
            dedupe_key=f"breach:{breach_id}",
            breach_id=breach_id,
        )
        if created.ids:
            summary.alerts.append(f"{f.key}: {created.explanation}")
    for key, row_id in open_rows.items():
        if key not in current:
            await pool.execute(
                "update public.portfolio_breach set cleared_at = $2 where id = $1", row_id, now
            )
            summary.cleared.append(key)
    return summary
