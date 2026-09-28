"""Every job type the worker can run, by name.

Scheduled runs come from the Jobs config; on-demand and event runs arrive through the pgmq
queue. Both look the handler up here.
"""

from typing import Literal

from steward.chat.assistant import chat_handler
from steward.earnings.calendar import (
    earnings_calendar_full_handler,
    earnings_calendar_handler,
    earnings_due_handler,
)
from steward.earnings.check import earnings_check_handler, thesis_verdicts_handler
from steward.earnings.reconcile import fact_reconcile_handler
from steward.fundamentals.collect import sec_xbrl_handler
from steward.jobs.runner import JobHandler
from steward.lookthrough.collect import etf_look_through_handler
from steward.macro.collect import macro_collect_handler
from steward.monitor import monitor_handler
from steward.news.digest import daily_digest_handler, weekly_digest_handler
from steward.news.escalation import event_escalation_handler
from steward.news.pipeline import news_ingest_handler
from steward.ownership.collect import ownership_13f_handler
from steward.portfolio.sync import portfolio_sync_handler
from steward.reference.identity import reference_data_handler
from steward.reference.intraday import intraday_prices_handler
from steward.reference.prices import holiday_refresh_handler, price_history_handler
from steward.research.run import research_handler
from steward.review.monthly import monthly_review_handler
from steward.valuation.collect import valuation_refresh_handler

JobType = Literal[
    "portfolio_sync",
    "earnings_check",
    "thesis_verdicts",
    "earnings_calendar",
    "earnings_calendar_full",
    "earnings_due",
    "fact_reconcile",
    "reference_data",
    "price_history",
    "intraday_prices",
    "holiday_refresh",
    "macro_collect",
    "event_escalation",
    "monitor",
    "news_ingest",
    "daily_digest",
    "weekly_digest",
    "research",
    "ownership_13f",
    "etf_look_through",
    "monthly_review",
    "chat",
    "sec_xbrl",
    "valuation_refresh",
]

JOB_HANDLERS: dict[str, JobHandler] = {
    "portfolio_sync": portfolio_sync_handler,
    "earnings_check": earnings_check_handler,
    "thesis_verdicts": thesis_verdicts_handler,
    "earnings_calendar": earnings_calendar_handler,
    "earnings_calendar_full": earnings_calendar_full_handler,
    "earnings_due": earnings_due_handler,
    "fact_reconcile": fact_reconcile_handler,
    "reference_data": reference_data_handler,
    "price_history": price_history_handler,
    "intraday_prices": intraday_prices_handler,
    "holiday_refresh": holiday_refresh_handler,
    "macro_collect": macro_collect_handler,
    "event_escalation": event_escalation_handler,
    "monitor": monitor_handler,
    "news_ingest": news_ingest_handler,
    "daily_digest": daily_digest_handler,
    "weekly_digest": weekly_digest_handler,
    "research": research_handler,
    "ownership_13f": ownership_13f_handler,
    "etf_look_through": etf_look_through_handler,
    "monthly_review": monthly_review_handler,
    "chat": chat_handler,
    "sec_xbrl": sec_xbrl_handler,
    "valuation_refresh": valuation_refresh_handler,
}
