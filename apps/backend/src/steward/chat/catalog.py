"""The Research Data Catalog: which datasets exist, which read-only tool answers questions
about each, and what is still not reachable from chat.

This is the single list the capability map in `docs/plans/ai_research_capability_map.md` is
generated from, and the tests hold it against the real tool definitions: a tool without a
catalog entry, an entry without a tool, or a dataset that names a tool which does not exist
fails the build, so the map cannot drift from the code.
"""
# ruff: noqa: E501  (the tool table is one row per line)

from dataclasses import dataclass


@dataclass(frozen=True)
class Dataset:
    id: str
    title: str
    tables: tuple[str, ...]
    tools: tuple[str, ...]
    freshness: str  # how often it is updated, as the collectors schedule it
    gap: str | None = None  # what chat still cannot reach, or None when covered
    research_pipeline: str | None = None  # how the research report reads it, if it does


@dataclass(frozen=True)
class ToolInfo:
    datasets: tuple[str, ...]
    service: str  # the read service the tool wraps
    bounds: str  # row, date and size limits
    scope: str  # "owner" for the owner's own records, "global" for shared reference data
    provenance: str  # how a returned record is traced to its source
    writes: bool = False  # True for a tool that stores anything; a research run never calls it


DATASETS: tuple[Dataset, ...] = (
    Dataset(
        "portfolio",
        "Portfolio positions, cash, allocation and drift",
        ("account_snapshot", "position_snapshot", "instrument"),
        ("portfolio_overview", "holding", "over_cap_positions", "theme_exposure"),
        "daily sync",
        research_pipeline="gather: position section",
    ),
    Dataset(
        "transactions",
        "Orders, dividends and cash movements",
        ("transaction", "transaction_fee"),
        ("transactions", "decision_scorecard", "performance_explain"),
        "daily sync",
    ),
    Dataset(
        "prices",
        "Daily closes, quotes and FX",
        ("price_bar", "market_quote", "fx_rate"),
        ("price_history", "fx_rates", "latest_quote", "listing_liquidity"),
        "daily; quotes intraday",
        gap="No total-return or adjusted series; benchmark comparison is price return only.",
        research_pipeline="gather: price series",
    ),
    Dataset(
        "fundamentals",
        "Filed statements and the ratios computed from them",
        ("financial_statement_fact", "fundamentals", "xbrl_filing"),
        ("fundamentals", "holding", "statement_lines", "share_counts", "segment_revenue"),
        "on each filing",
        gap="Statement lines and segments come only from stored filings (SEC XBRL and parsed "
        "facts); other markets have no raw lines.",
        research_pipeline="gather: financials section",
    ),
    Dataset(
        "valuation",
        "Scenario DCF, reverse DCF and reference multiples",
        ("company_valuation_scenario",),
        ("valuation_scenarios", "start_size", "idea_factor_fit"),
        "computed on request",
        research_pipeline="valuation section (code-owned)",
    ),
    Dataset(
        "filed_evidence",
        "Filed notes, control stakes, 13F, N-PORT, insider activity, deals, dependencies, "
        "consensus",
        (
            "fact",
            "evidence",
            "control_stake_filing",
            "form13f_filing",
            "nport_holding",
            "insider_filing",
            "deal_event",
            "entity_relationship",
            "estimate_value",
        ),
        ("filing_evidence", "company_context", "event_paths", "event_feed"),
        "on each filing",
        research_pipeline="gather: ownership, relationships, risk and liquidity sections",
    ),
    Dataset(
        "documents",
        "Passages of archived filings and results releases, your reports, theses and notes, and news",
        ("document_chunk",),
        ("search_documents",),
        "daily index run",
        gap="Only documents the index job has processed; filings that were never archived "
        "(fetched on the fly by the exposure builder) and disclosure-register documents are not "
        "indexed yet.",
    ),
    Dataset(
        "agent_notes",
        "The owner's working notes for the assistant, general or per holding",
        ("agent_note",),
        ("agent_notes",),
        "when the owner edits them",
    ),
    Dataset(
        "news",
        "Stored news, wire articles, story clusters and threads",
        ("news_item", "news_article", "story_cluster", "story_thread", "news_embedding"),
        ("search_news", "search_wire", "get_story_thread", "event_feed"),
        "continuous",
        research_pipeline="gather: news section",
    ),
    Dataset(
        "macro",
        "Macro and commodity series; country context",
        ("macro_observation", "macro_series"),
        ("macro_series", "country_context"),
        "per series release",
    ),
    Dataset(
        "reference",
        "Sector, country and industry context for a company",
        ("issuer", "security", "listing"),
        ("company_context", "country_context", "corporate_actions", "peers"),
        "on change",
    ),
    Dataset(
        "strategy",
        "The owner's strategy: goals, targets, hard limits, rules, risk limits",
        ("config_version",),
        ("strategy_rules", "portfolio_overview"),
        "owner edits",
        research_pipeline="strategy block in the research brief",
    ),
    Dataset(
        "theses",
        "Per-holding theses, KPIs, verdicts and the reports written about them",
        ("config_version", "verdict", "report", "thesis_memo", "portfolio_review"),
        ("thesis_and_reports", "holding", "search_reports", "thesis_memos", "portfolio_review"),
        "owner edits; verdicts on each earnings check",
        research_pipeline="thesis section",
    ),
    Dataset(
        "risk",
        "Ex-ante risk, factor tilts, stress tests, past episodes and their history",
        ("risk_report_snapshot", "factor_return", "risk_scenario"),
        (
            "risk_forecast",
            "stress_test",
            "past_episodes",
            "factor_tilts",
            "model_check",
            "position_risk",
            "risk_whatif",
            "risk_history",
            "idea_factor_fit",
        ),
        "daily",
    ),
    Dataset(
        "trade_planning",
        "Sizing, deposits, rotation and switch effects",
        ("switch_log",),
        ("start_size", "deposit_allocation", "rotation_candidates", "switch_effect"),
        "computed on request",
    ),
    Dataset(
        "funds",
        "Fund holdings, look-through and overlap",
        ("etf_holding", "etf_snapshot", "nport_holding"),
        ("fund_overlap", "etf_breakdown", "fund_nav"),
        "per fund filing",
    ),
    Dataset(
        "freshness",
        "When each dataset was last updated, and provider coverage",
        ("coverage_status", "sync_state"),
        ("data_freshness", "coverage_review"),
        "live",
    ),
    Dataset(
        "web",
        "Open web through the model provider's search, limited to the allowed outlet tiers",
        (),
        (),
        "live",
        gap="Provider-side search; the URLs it returns are not stored unless cited.",
    ),
    Dataset(
        "actions",
        "Proposed jobs and changes the owner confirms",
        ("chat_action", "owner_action"),
        ("propose_job",),
        "live",
    ),
    Dataset(
        "engine",
        "What the automatic engine did and what waits for the owner",
        ("auto_decision", "ops_task"),
        ("engine_activity", "show_visual"),
        "live",
    ),
)

TOOLS: dict[str, ToolInfo] = {
    "portfolio_overview": ToolInfo(("portfolio", "strategy"), "portfolio.read.portfolio_overview", "one snapshot", "owner", "snapshot date as source"),
    "holding": ToolInfo(("portfolio", "fundamentals", "theses"), "earnings.read.holding_detail", "30 verified facts", "owner", "fact id and filing URL per fact"),
    "search_news": ToolInfo(("news",), "SQL full-text on news_item", "15 items, 365 days", "global", "news URL, outlet tier"),
    "search_wire": ToolInfo(("news",), "SQL full-text and embeddings on news_article", "15 items, 90 days", "global", "article URL, outlet tier"),
    "get_story_thread": ToolInfo(("news",), "news.read_analysis.thread_detail", "one thread", "global", "story ids"),
    "search_reports": ToolInfo(("theses",), "SQL full-text on report", "8 reports", "owner", "report id"),
    "country_context": ToolInfo(("macro", "reference"), "data_context.research.country_context", "as-of cutoff", "global", "claim ids with known-as-of"),
    "company_context": ToolInfo(("filed_evidence", "reference"), "data_context.research.company_context", "as-of cutoff", "global", "claim ids with known-as-of"),
    "stress_test": ToolInfo(("risk",), "analytics_tools.stress_test", "configured episodes", "owner", "episode and method version"),
    "risk_forecast": ToolInfo(("risk",), "analytics_tools.risk_forecast", "latest report", "owner", "method version"),
    "past_episodes": ToolInfo(("risk",), "analytics_tools.past_episodes", "configured episodes", "owner", "episode dates"),
    "factor_tilts": ToolInfo(("risk",), "analytics_tools.factor_tilts", "one region", "owner", "factor model version"),
    "idea_factor_fit": ToolInfo(("risk", "valuation"), "analytics_tools.idea_factor_fit", "few symbols", "owner", "factor model version"),
    "model_check": ToolInfo(("risk",), "analytics_tools.model_check", "latest report", "owner", "method version"),
    "position_risk": ToolInfo(("risk",), "analytics_tools.position_risk", "one position", "owner", "method version"),
    "risk_whatif": ToolInfo(("risk",), "analytics_tools.risk_whatif", "few trades", "owner", "method version"),
    "start_size": ToolInfo(("valuation", "trade_planning"), "analytics_tools.start_size_for", "one ticker", "owner", "strategy thresholds"),
    "deposit_allocation": ToolInfo(("trade_planning",), "analytics_tools.deposit_allocation", "one amount", "owner", "strategy targets"),
    "rotation_candidates": ToolInfo(("trade_planning",), "analytics_tools.rotation_candidates", "ranked list", "owner", "strategy targets"),
    "switch_effect": ToolInfo(("trade_planning",), "analytics_tools.switch_effect", "one pair", "owner", "method version"),
    "over_cap_positions": ToolInfo(("portfolio",), "analytics_tools.over_cap_positions", "positions over cap", "owner", "strategy limits"),
    "theme_exposure": ToolInfo(("portfolio",), "analytics_tools.theme", "one theme", "owner", "mapping ids"),
    "event_paths": ToolInfo(("filed_evidence",), "analytics_tools.event_reach", "bounded graph walk", "owner", "edge ids"),
    "search_documents": ToolInfo(("documents",), "retrieval.search.search (full text + embeddings, RRF)", "20 passages, 2 per document", "owner", "passage text with source URL or app page and date"),
    "agent_notes": ToolInfo(("agent_notes",), "SQL on agent_note", "20 active notes", "owner", "note id and edit date"),
    "thesis_memos": ToolInfo(("theses",), "SQL on thesis_memo", "8 memos, or 3 in full for one ticker", "owner", "memo id and date"),
    "portfolio_review": ToolInfo(("theses",), "SQL on portfolio_review", "the latest review", "owner", "review id and date"),
    "event_feed": ToolInfo(("filed_evidence", "news"), "events.feed.build", "25 items, 90 days", "owner", "story URL and outlet tier per item"),
    "fund_overlap": ToolInfo(("funds",), "analytics_tools.fund_overlap", "one ticker", "owner", "fund snapshot"),
    "performance_explain": ToolInfo(("transactions",), "analytics_tools.performance_explain", "up to a year", "owner", "snapshot dates"),
    "decision_scorecard": ToolInfo(("transactions",), "analytics_tools.decision_scorecard", "last year", "owner", "journal ids"),
    "propose_job": ToolInfo(("actions",), "chat_action insert, owner_action insert", "one proposal", "owner", "action id", writes=True),
    "show_visual": ToolInfo(("engine",), "SQL on position_snapshot and auto_decision", "30 bars; one chart", "owner", "source id with the snapshot date"),
    "engine_activity": ToolInfo(("engine",), "SQL on ops_task and auto_decision", "30 items per list; 7 days of counts", "owner", "task or decision id"),
    "filing_evidence": ToolInfo(("filed_evidence",), "data_context.evidence", "10 items by default, 25 at most, as-of cutoff", "global", "source URL, passage and known-at per item"),
    "fundamentals": ToolInfo(("fundamentals",), "company.ratios.build_ratios", "80 ratios, or 300 points with periods", "global", "ratio group source with filed periods"),
    "valuation_scenarios": ToolInfo(("valuation",), "company.valuation.build_valuation", "5 scenarios: the owner's newest saved set, else defaults", "owner", "method version, price date and whether the assumptions are saved or default"),
    "price_history": ToolInfo(("prices",), "company.service.load_market", "120 points, 10 years, optional benchmark", "global", "last close date, thinning stated"),
    "fx_rates": ToolInfo(("prices",), "SQL on fx_rate", "60 fixings, 365 days", "global", "fixing date and source"),
    "latest_quote": ToolInfo(("prices",), "SQL on market_quote", "latest per provider", "global", "quote time and provider"),
    "listing_liquidity": ToolInfo(("prices",), "SQL on listing_liquidity, price_bar", "30 daily bars", "global", "ISIN record and last bar date"),
    "statement_lines": ToolInfo(("fundamentals",), "SQL on financial_statement_fact", "60 lines", "global", "filing URL per line"),
    "share_counts": ToolInfo(("fundamentals",), "SQL on shares_outstanding", "8 records", "global", "method and as-of date"),
    "segment_revenue": ToolInfo(("fundamentals",), "SQL on segment_revenue, xbrl_filing", "60 rows", "global", "filing instance URL"),
    "corporate_actions": ToolInfo(("reference",), "SQL on corporate_action", "60 actions, 10 years", "global", "source URL per action"),
    "peers": ToolInfo(("reference",), "SQL on peer_set", "25 peers", "global", "peer basis and status"),
    "etf_breakdown": ToolInfo(("funds",), "SQL on etf_snapshot, etf_holding", "25 holdings, 10 groups", "global", "snapshot date"),
    "fund_nav": ToolInfo(("funds",), "SQL on fund_nav_point", "30 points", "owner", "NAV source URL"),
    "coverage_review": ToolInfo(("freshness",), "SQL on coverage_status", "60 rows", "global", "provider, dataset and checked time"),
    "macro_series": ToolInfo(("macro",), "SQL on macro_observation", "60 periods", "global", "source URL and vintage"),
    "strategy_rules": ToolInfo(("strategy",), "config.store.load_active", "one version", "owner", "config version id"),
    "transactions": ToolInfo(("transactions",), "portfolio.transactions.list_transactions", "50 per page, cursor", "owner", "scope and totals over all matches"),
    "risk_history": ToolInfo(("risk",), "SQL on risk_report_snapshot", "30 snapshots", "owner", "snapshot id and method version"),
    "thesis_and_reports": ToolInfo(("theses",), "config.load_active, verdict, report", "last 2 reports", "owner", "config version, verdict and report ids"),
    "data_freshness": ToolInfo(("freshness",), "SQL max() probes, coverage_status", "8 datasets", "global", "latest record date per dataset"),
}  # fmt: skip


def problems(tool_names: set[str]) -> list[str]:
    """Every way the catalog and the real tool list disagree; empty when they match."""
    out: list[str] = []
    dataset_ids = {d.id for d in DATASETS}
    for name in sorted(tool_names - TOOLS.keys()):
        out.append(f"tool {name} has no catalog entry")
    for name in sorted(TOOLS.keys() - tool_names):
        out.append(f"catalog lists {name} but there is no such tool")
    for name, info in TOOLS.items():
        for dataset in info.datasets:
            if dataset not in dataset_ids:
                out.append(f"tool {name} names unknown dataset {dataset}")
            elif name not in tools_for(dataset):
                out.append(f"tool {name} names dataset {dataset}, which does not list it back")
    for d in DATASETS:
        for name in d.tools:
            if name not in tool_names:
                out.append(f"dataset {d.id} names missing tool {name}")
            elif name in TOOLS and d.id not in TOOLS[name].datasets:
                out.append(f"dataset {d.id} lists {name}, which does not list it back")
    return out


def tools_for(dataset_id: str) -> tuple[str, ...]:
    return next((d.tools for d in DATASETS if d.id == dataset_id), ())


def capability_map() -> str:
    """The catalog as the markdown tables of the capability map."""
    lines = [
        "| Dataset | Tables | Chat tools | Updated | Gap in chat |",
        "|---|---|---|---|---|",
    ]
    for d in DATASETS:
        lines.append(
            f"| {d.title} | {', '.join(f'`{t}`' for t in d.tables) or '—'} | "
            f"{', '.join(f'`{t}`' for t in d.tools) or '—'} | {d.freshness} | {d.gap or 'none'} |"
        )
    lines += [
        "",
        "| Tool | Datasets | Wraps | Bounds | Scope | Provenance |",
        "|---|---|---|---|---|---|",
    ]
    for name, info in TOOLS.items():
        lines.append(
            f"| `{name}` | {', '.join(info.datasets)} | `{info.service}` | {info.bounds} | "
            f"{info.scope} | {info.provenance} |"
        )
    return "\n".join(lines)
