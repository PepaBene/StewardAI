"""Download, store and read the Ken French factor files (quant plan Q4.1).

`factor_returns_handler` is the scheduled job: it fetches the six zip files of
`french.FILES`, parses them and upserts the daily rows into `public.factor_return`. One file
failing leaves the others' rows in place and is recorded in the job step, so the stored
coverage (first date, last date, days per region and factor) always says what is there.
"""

import datetime as dt
import logging

import asyncpg
import httpx

from steward import __version__
from steward.jobs.runner import JobContext
from steward.quant import french

log = logging.getLogger(__name__)

# The library revises its files in place (new months are appended, history is sometimes
# restated), so a run replaces everything it downloaded.
_UPSERT = (
    "insert into public.factor_return (region, factor, date, value) values ($1, $2, $3, $4)"
    " on conflict (region, factor, date) do update set value = excluded.value, fetched_at = now()"
)


# Older rows are not stored: the models look back a few years, and the US momentum file alone
# reaches back to 1926.
FIRST_DATE = dt.date(2000, 1, 1)


async def store_factors(pool: asyncpg.Pool, region: str, factors: french.Factors) -> int:
    rows = [
        (region, factor, day, value)
        for factor, series in factors.items()
        for day, value in series
        if day >= FIRST_DATE
    ]
    async with pool.acquire() as conn, conn.transaction():
        await conn.executemany(_UPSERT, rows)
    return len(rows)


async def load_factors(
    pool: asyncpg.Pool, region: str, since: dt.date | None = None
) -> french.Factors:
    """Stored daily factor returns of a region, oldest first."""
    rows = await pool.fetch(
        "select factor, date, value from public.factor_return where region = $1"
        " and ($2::date is null or date >= $2) order by date",
        region,
        since,
    )
    out: french.Factors = {}
    for r in rows:
        out.setdefault(r["factor"], []).append((r["date"], float(r["value"])))
    return out


async def factor_coverage(
    pool: asyncpg.Pool, region: str
) -> dict[str, tuple[dt.date, dt.date, int]]:
    """Factor -> (first date, last date, days) stored for the region."""
    rows = await pool.fetch(
        "select factor, min(date) as first, max(date) as last, count(*) as days"
        " from public.factor_return where region = $1 group by factor",
        region,
    )
    return {r["factor"]: (r["first"], r["last"], int(r["days"])) for r in rows}


async def refresh_factors(ctx: JobContext, http: httpx.AsyncClient) -> None:
    for file in french.FILES:
        async with ctx.step(f"factors:{file.name}") as step:
            try:
                response = await http.get(file.url)
                response.raise_for_status()
                factors = french.parse_zip(response.content)
            except (httpx.HTTPError, french.FrenchFormatError, OSError) as exc:
                step.detail["error"] = str(exc)[:300]
                log.warning("factor file %s failed: %s", file.name, exc)
                continue
            step.detail["rows"] = await store_factors(ctx.pool, file.region, factors)
            step.detail["last_date"] = str(max(s[-1][0] for s in factors.values()))


async def factor_returns_handler(ctx: JobContext) -> None:
    async with httpx.AsyncClient(
        timeout=60.0,
        headers={"User-Agent": f"StewardAI/{__version__}"},
        follow_redirects=True,
    ) as http:
        await refresh_factors(ctx, http)
