"""Phase 1 sign-off on live data: `uv run python -m steward.signoff [--runs 10]`.

Checks what the database can prove about the Phase 1 acceptance criteria and prints the
figures the owner compares by hand with the Trading212 app:

- consecutive scheduled portfolio syncs: the newest scheduled runs, counted until the first
  failure, and whether each ran on its own day (one run per day);
- one snapshot row per position per day: each of those runs' snapshot has exactly as many
  rows as positions Trading212 returned in that run;
- at least 3 positions in at least 3 currencies in the latest snapshot;
- totals: Trading212's own total next to the sum of its positions plus cash (both in the
  account currency), and the base-currency total the Overview shows.

The Realtime check (a sync started in a second tab updates the first within 2 s) needs two
browsers and is listed as a manual step.
"""

import argparse
import asyncio
import datetime as dt
import json
import sys
from dataclasses import dataclass, field
from decimal import Decimal

import asyncpg

from steward.db import create_pool
from steward.settings import get_settings


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class SignoffReport:
    checks: list[Check] = field(default_factory=list)
    figures: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


async def signoff(pool: asyncpg.Pool, required_runs: int = 10) -> SignoffReport:
    report = SignoffReport()
    runs = await pool.fetch(
        "select id, status, error, created_at, finished_at from public.job"
        " where job_type = 'portfolio_sync' and trigger = 'schedule'"
        " and status in ('succeeded', 'failed') order by id desc limit $1",
        max(required_runs * 2, 20),
    )
    streak = []
    for run in runs:
        if run["status"] != "succeeded":
            break
        streak.append(run)
    days = [r["created_at"].date() for r in streak]
    distinct_days = len(set(days)) == len(days)
    report.checks.append(
        Check(
            f"{required_runs} consecutive scheduled syncs",
            len(streak) >= required_runs and distinct_days,
            f"{len(streak)} in a row since the last failure"
            + ("" if distinct_days else "; more than one scheduled run on the same day")
            + (
                f" (last failure: job {runs[len(streak)]['id']})" if len(streak) < len(runs) else ""
            ),
        )
    )

    mismatched = []
    for run in streak[:required_runs]:
        detail = await pool.fetchval(
            "select detail from public.job_step"
            " where job_id = $1 and name = 'trading212_positions'",
            run["id"],
        )
        expected = json.loads(detail).get("positions") if detail else None
        rows = await pool.fetchval(
            "select count(*) from public.position_snapshot where job_id = $1", run["id"]
        )
        # A later re-sync of the same day replaces the rows (the account row too); then the
        # day's newest run wins. The run's own snapshot step records which day it wrote.
        step = await pool.fetchval(
            "select detail from public.job_step where job_id = $1 and name = 'snapshot'",
            run["id"],
        )
        written = json.loads(step).get("snapshot_date") if step else None
        replaced = await pool.fetchval(
            "select exists (select 1 from public.account_snapshot where job_id <> $1"
            " and snapshot_date = coalesce($2::date, (select snapshot_date from"
            " public.account_snapshot where job_id = $1)))",
            run["id"],
            dt.date.fromisoformat(written) if written else None,
        )
        if not replaced and expected is not None and rows != expected:
            mismatched.append(f"job {run['id']}: {rows} rows for {expected} positions")
    report.checks.append(
        Check(
            "one snapshot row per position per day",
            not mismatched and bool(streak),
            "; ".join(mismatched) or f"checked {min(len(streak), required_runs)} runs",
        )
    )

    latest = await pool.fetchrow(
        "select * from public.account_snapshot order by snapshot_date desc limit 1"
    )
    if latest is None:
        report.checks.append(Check("latest snapshot", False, "no snapshot yet"))
        return report
    positions = await pool.fetch(
        "select ticker, instrument_currency, value_account, value_base"
        " from public.position_snapshot where snapshot_date = $1",
        latest["snapshot_date"],
    )
    currencies = {p["instrument_currency"].upper().replace("GBX", "GBP") for p in positions}
    report.checks.append(
        Check(
            "at least 3 positions in 3 currencies",
            len(positions) >= 3 and len(currencies) >= 3,
            f"{len(positions)} positions in {len(currencies)} currencies"
            f" ({', '.join(sorted(currencies))})",
        )
    )
    cash = latest["cash_available"] + latest["cash_in_pies"] + latest["cash_reserved"]
    summed = sum((p["value_account"] for p in positions), Decimal(0)) + cash
    ccy = latest["account_currency"]
    report.figures = {
        "snapshot date": str(latest["snapshot_date"]),
        f"Trading212 total ({ccy})": f"{latest['total_value']:,.2f}",
        f"positions + cash ({ccy})": f"{summed:,.2f}",
        f"Overview total ({latest['base_currency']}, {latest['fx_source'].upper()} rates)": (
            f"{latest['total_base']:,.2f}"
        ),
    }
    gap = abs(summed - latest["total_value"])
    report.checks.append(
        Check(
            "positions + cash equal Trading212's total",
            gap <= Decimal("0.02") * max(1, len(positions)),
            f"difference {gap:,.2f} {ccy}",
        )
    )
    return report


def render(report: SignoffReport) -> str:
    lines = ["Phase 1 sign-off", ""]
    for c in report.checks:
        lines.append(f"[{'PASS' if c.ok else 'FAIL'}] {c.name}: {c.detail}")
    if report.figures:
        lines += ["", "Compare with the Trading212 app (same day, within FX rounding):"]
        lines += [f"  {k}: {v}" for k, v in report.figures.items()]
    lines += [
        "",
        "Manual: open the app in two tabs, press Sync now in one; the other tab's sync",
        "indicator must change within 2 seconds without a reload.",
    ]
    return "\n".join(lines)


async def _main(runs: int) -> int:
    pool = await create_pool(get_settings())
    try:
        report = await signoff(pool, runs)
    finally:
        await pool.close()
    print(render(report))
    return 0 if report.ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=10, help="consecutive runs required")
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args.runs)))


if __name__ == "__main__":
    main()
