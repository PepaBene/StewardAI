"""Every job type the worker can run, by name.

Scheduled runs come from the Jobs config; on-demand runs arrive through the pgmq queue.
Both look the handler up here.
"""

from typing import Literal

from steward.jobs.runner import JobHandler
from steward.portfolio.sync import portfolio_sync_handler

JobType = Literal["portfolio_sync"]

JOB_HANDLERS: dict[str, JobHandler] = {
    "portfolio_sync": portfolio_sync_handler,
}
