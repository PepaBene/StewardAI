"""Fill an empty database once: `uv run python -m worker.backfill [job types...]`.

Runs the data-collecting jobs one after another, in dependency order, through the same job
runner as the worker (each is a normal `manual` job with its steps in the job log), and
prints how each went. Without arguments it runs the default sequence below; the 13F job
loads two quarterly data sets per run, so it is repeated `--ownership-runs` times. Jobs
that need Claude are skipped when ANTHROPIC_API_KEY is not set.

Run it where the database is reachable (the worker's host), with the same environment.
"""

import argparse
import asyncio
import sys
import time

from steward.db import create_pool
from steward.jobs.registry import JOB_HANDLERS
from steward.jobs.runner import run_job
from steward.logging import configure_logging
from steward.settings import get_settings

# Portfolio first (the other jobs work on its holdings), then identity (price symbols,
# CUSIPs for 13F), then market data, calendars, macro, ownership and news.
DEFAULT_SEQUENCE = [
    "portfolio_sync",
    "reference_data",
    "price_history",
    "holiday_refresh",
    "macro_collect",
    "earnings_calendar_full",
    "ownership_13f",
    "news_ingest",
    "monitor",
]
NEEDS_CLAUDE = {"news_ingest"}


async def backfill(job_types: list[str], ownership_runs: int) -> int:
    settings = get_settings()
    if settings.database_url is None:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2
    unknown = [j for j in job_types if j not in JOB_HANDLERS]
    if unknown:
        print(f"Unknown job types: {', '.join(unknown)}", file=sys.stderr)
        return 2
    pool = await create_pool(settings, max_size=4)
    failed = 0
    try:
        for job_type in job_types:
            if job_type in NEEDS_CLAUDE and settings.anthropic_api_key is None:
                print(f"skip     {job_type}: ANTHROPIC_API_KEY is not set")
                continue
            runs = ownership_runs if job_type == "ownership_13f" else 1
            for run in range(runs):
                label = f"{job_type} ({run + 1}/{runs})" if runs > 1 else job_type
                started = time.monotonic()
                outcome = await run_job(pool, job_type, JOB_HANDLERS[job_type], trigger="manual")
                job = await pool.fetchrow(
                    "select id, error from public.job where job_type = $1 order by id desc limit 1",
                    job_type,
                )
                took = time.monotonic() - started
                detail = f"job #{job['id']}" if job else ""
                if outcome != "succeeded":
                    failed += 1
                    detail += f": {(job['error'] or '') if job else ''}"[:300]
                print(f"{outcome:<8} {label} in {took:.0f}s {detail}".rstrip())
    finally:
        await pool.close()
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("job_types", nargs="*", default=DEFAULT_SEQUENCE)
    parser.add_argument(
        "--ownership-runs",
        type=int,
        default=4,
        help="13F runs (two quarterly data sets each); default 4, about two years.",
    )
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level, settings.secret_values())
    sys.exit(asyncio.run(backfill(args.job_types, max(1, args.ownership_runs))))


if __name__ == "__main__":
    main()
