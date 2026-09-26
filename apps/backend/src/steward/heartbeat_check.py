"""Container health check for the worker: `python -m steward.heartbeat_check`.

Exits 0 when the worker's heartbeat row is younger than `STALE_AFTER`, 1 otherwise (or
when the database cannot be reached).
"""

import asyncio
import sys

from steward.db import create_pool
from steward.heartbeat import worker_status
from steward.settings import get_settings


async def _check() -> bool:
    pool = await create_pool(get_settings(), max_size=1)
    try:
        return (await worker_status(pool)).alive
    finally:
        await pool.close()


def main() -> None:
    try:
        alive = asyncio.run(_check())
    except Exception as exc:  # the health check must answer, whatever went wrong
        print(f"unhealthy: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
    print("healthy" if alive else "unhealthy: heartbeat is stale")
    sys.exit(0 if alive else 1)


if __name__ == "__main__":
    main()
