"""Every job type the worker can run, by name.

Scheduled runs come from the Jobs config; on-demand and event runs arrive through the pgmq
queue. Both look the handler up here.
"""

from typing import Literal

from steward.earnings.calendar import earnings_calendar_handler, earnings_due_handler
from steward.earnings.check import earnings_check_handler, thesis_verdicts_handler
from steward.jobs.runner import JobHandler
from steward.portfolio.sync import portfolio_sync_handler

JobType = Literal[
    "portfolio_sync", "earnings_check", "thesis_verdicts", "earnings_calendar", "earnings_due"
]

JOB_HANDLERS: dict[str, JobHandler] = {
    "portfolio_sync": portfolio_sync_handler,
    "earnings_check": earnings_check_handler,
    "thesis_verdicts": thesis_verdicts_handler,
    "earnings_calendar": earnings_calendar_handler,
    "earnings_due": earnings_due_handler,
}
