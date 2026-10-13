"""Every job type the worker can run, by name.

Scheduled runs come from the Jobs config; on-demand and event runs arrive through the pgmq
queue. Both look the handler up here.
"""

from typing import Literal

from steward.approvals.ai import ai_approvals_handler
from steward.autopilot.job import autopilot_calibration_handler, autopilot_handler
from steward.changes.alerts import change_alerts_handler
from steward.chat.assistant import chat_handler
from steward.disclosure.jobs import (
    disclosure_alerts_handler,
    disclosure_deals_handler,
    disclosure_esef_handler,
    disclosure_estimates_handler,
    disclosure_identifiers_handler,
    disclosure_ingest_handler,
    disclosure_sources_handler,
)
from steward.earnings.calendar import (
    earnings_calendar_full_handler,
    earnings_calendar_handler,
    earnings_due_handler,
)
from steward.earnings.check import earnings_check_handler, thesis_verdicts_handler
from steward.earnings.reconcile import fact_reconcile_handler
from steward.exposures.build import exposure_build_handler
from steward.free_source_alerts import free_source_alerts_handler
from steward.fundamentals.collect import sec_xbrl_handler
from steward.fundamentals.fsds import sec_fsds_backfill_handler
from steward.fundamentals.ir import ir_releases_handler
from steward.fundamentals.ir_discovery import ir_discovery_handler
from steward.fundresearch.issuer_sync import fund_issuer_facts_handler
from steward.funds.drift import fund_characteristics_audit_handler
from steward.graph.jobs import graph_review_handler
from steward.inbox.run import inbox_agent_handler, inbox_scan_handler
from steward.jobs.runner import JobHandler
from steward.lookthrough.collect import etf_look_through_handler
from steward.macro.collect import macro_collect_handler
from steward.macro.public_collect import public_data_collect_handler
from steward.monitor import monitor_handler
from steward.news.ai_search import news_ai_search_handler
from steward.news.analysis_job import news_analysis_handler, news_brief_handler
from steward.news.digest import daily_digest_handler, weekly_digest_handler
from steward.news.escalation import event_escalation_handler
from steward.news.pipeline import news_ingest_handler
from steward.news.triage import news_triage_handler
from steward.ops.watchdog import watchdog_handler
from steward.ownership.collect import ownership_13f_handler
from steward.ownership.control_stakes import control_stakes_handler
from steward.ownership.insider_collect import insider_trades_handler
from steward.ownership.nport_collect import nport_holdings_handler
from steward.ownership.pdmr_collect import pdmr_uk_handler
from steward.portfolio.sync import portfolio_sync_handler
from steward.quant.french_store import factor_returns_handler
from steward.reference.exchange_probes import exchange_probes_handler
from steward.reference.identity import reference_data_handler
from steward.reference.intraday import intraday_prices_handler
from steward.reference.prices import holiday_refresh_handler, price_history_handler
from steward.reference.returns import returns_refresh_handler
from steward.research.retention import research_trace_retention_handler
from steward.research.run import research_handler
from steward.research.web_facts import gap_research_handler
from steward.retrieval.job import document_index_handler
from steward.review.monthly import monthly_review_handler
from steward.sources.audit import source_audit_handler
from steward.sources.fund_probes import fund_probes_handler
from steward.sources.provider_jobs import (
    alpha_vantage_statements_handler,
    companies_house_filings_handler,
    edinet_filings_handler,
    epo_patents_handler,
    fmp_company_handler,
    twelve_data_corporate_actions_handler,
)
from steward.thesis.job import thesis_handler
from steward.thesis.review import review_handler
from steward.valuation.collect import valuation_refresh_handler
from steward.verify_sources.jobs import (
    raw_retention_handler,
    shadow_report_handler,
    shadow_snapshot_handler,
    zero_cost_gate_handler,
)

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
    "public_data_collect",
    "source_audit",
    "event_escalation",
    "monitor",
    "news_ingest",
    "daily_digest",
    "weekly_digest",
    "research",
    "ownership_13f",
    "insider_trades",
    "etf_look_through",
    "monthly_review",
    "chat",
    "sec_xbrl",
    "valuation_refresh",
    "control_stakes",
    "nport_holdings",
    "fund_probes",
    "fund_characteristics_audit",
    "fund_issuer_facts",
    "free_source_alerts",
    "change_alerts",
    "shadow_snapshot",
    "shadow_report",
    "raw_retention",
    "research_trace_retention",
    "zero_cost_gate",
    "returns_refresh",
    "exchange_probes",
    "ir_releases",
    "sec_fsds_backfill",
    "pdmr_uk",
    "graph_review",
    "fmp_company",
    "alpha_vantage_statements",
    "epo_patents",
    "companies_house_filings",
    "edinet_filings",
    "twelve_data_corporate_actions",
    "ai_approvals",
    "autopilot",
    "autopilot_calibration",
    "ir_discovery",
    "news_ai_search",
    "news_triage",
    "news_analysis",
    "news_brief",
    "gap_research",
    "exposure_build",
    "document_index",
    "inbox_scan",
    "inbox_agent",
    "factor_returns",
    "disclosure_sources",
    "disclosure_ingest",
    "disclosure_identifiers",
    "disclosure_deals",
    "disclosure_esef",
    "disclosure_estimates",
    "disclosure_alerts",
    "watchdog",
    "thesis",
    "portfolio_review",
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
    "public_data_collect": public_data_collect_handler,
    "source_audit": source_audit_handler,
    "event_escalation": event_escalation_handler,
    "monitor": monitor_handler,
    "news_ingest": news_ingest_handler,
    "daily_digest": daily_digest_handler,
    "weekly_digest": weekly_digest_handler,
    "research": research_handler,
    "ownership_13f": ownership_13f_handler,
    "insider_trades": insider_trades_handler,
    "etf_look_through": etf_look_through_handler,
    "monthly_review": monthly_review_handler,
    "chat": chat_handler,
    "sec_xbrl": sec_xbrl_handler,
    "valuation_refresh": valuation_refresh_handler,
    "control_stakes": control_stakes_handler,
    "nport_holdings": nport_holdings_handler,
    "fund_probes": fund_probes_handler,
    "fund_characteristics_audit": fund_characteristics_audit_handler,
    "fund_issuer_facts": fund_issuer_facts_handler,
    "free_source_alerts": free_source_alerts_handler,
    "change_alerts": change_alerts_handler,
    "shadow_snapshot": shadow_snapshot_handler,
    "shadow_report": shadow_report_handler,
    "raw_retention": raw_retention_handler,
    "research_trace_retention": research_trace_retention_handler,
    "zero_cost_gate": zero_cost_gate_handler,
    "returns_refresh": returns_refresh_handler,
    "exchange_probes": exchange_probes_handler,
    "ir_releases": ir_releases_handler,
    "sec_fsds_backfill": sec_fsds_backfill_handler,
    "pdmr_uk": pdmr_uk_handler,
    "graph_review": graph_review_handler,
    "fmp_company": fmp_company_handler,
    "alpha_vantage_statements": alpha_vantage_statements_handler,
    "epo_patents": epo_patents_handler,
    "companies_house_filings": companies_house_filings_handler,
    "edinet_filings": edinet_filings_handler,
    "twelve_data_corporate_actions": twelve_data_corporate_actions_handler,
    "ai_approvals": ai_approvals_handler,
    "autopilot": autopilot_handler,
    "autopilot_calibration": autopilot_calibration_handler,
    "watchdog": watchdog_handler,
    "ir_discovery": ir_discovery_handler,
    "news_ai_search": news_ai_search_handler,
    "news_triage": news_triage_handler,
    "news_analysis": news_analysis_handler,
    "news_brief": news_brief_handler,
    "gap_research": gap_research_handler,
    "exposure_build": exposure_build_handler,
    "document_index": document_index_handler,
    "inbox_scan": inbox_scan_handler,
    "inbox_agent": inbox_agent_handler,
    "factor_returns": factor_returns_handler,
    "disclosure_sources": disclosure_sources_handler,
    "disclosure_ingest": disclosure_ingest_handler,
    "disclosure_identifiers": disclosure_identifiers_handler,
    "disclosure_deals": disclosure_deals_handler,
    "disclosure_esef": disclosure_esef_handler,
    "disclosure_estimates": disclosure_estimates_handler,
    "disclosure_alerts": disclosure_alerts_handler,
    "thesis": thesis_handler,
    "portfolio_review": review_handler,
}
