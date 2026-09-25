"""Worker process: `uv run python -m worker`.

Runs the in-process scheduler for timed jobs until SIGINT or SIGTERM. The pgmq queue
consumer for event-driven jobs joins this loop when the first queued job exists.
"""

import asyncio
import logging
import signal

from steward import __version__
from steward.logging import configure_logging
from steward.settings import get_settings
from worker.scheduler import build_scheduler

logger = logging.getLogger("worker")


async def run() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    scheduler = build_scheduler()
    scheduler.start()
    logger.info(
        "scheduler started (version %s, %d jobs registered, tz %s)",
        __version__,
        len(scheduler.get_jobs()),
        scheduler.timezone,
    )
    try:
        await stop.wait()
    finally:
        scheduler.shutdown(wait=True)
        logger.info("scheduler stopped")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.secret_values())
    asyncio.run(run())


if __name__ == "__main__":
    main()
