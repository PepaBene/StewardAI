"""The chat assistant's tools: read-only database queries, and job proposals.

Every tool result lists its records with registry ids (S1, S2, ...) that the answer cites;
each id points at the same pages as the rest of the app (/evidence/<fact>, /holdings/<t>,
/reports/<id>, /research/<id>, /reviews/<id>) or at the source's own URL.

`propose_job` never starts anything: it stores a proposed action, which the owner confirms
or cancels in the chat panel. Only a confirmed action creates a job (trigger `chat`).
"""

import datetime as dt
import json
from typing import Any

import asyncpg

from steward.chat import analytics_tools
from steward.chat.analytics_tools import SELECTOR_KINDS
from steward.config.research import ResearchProfilesConfig
from steward.config.store import load_active
from steward.config.thesis import ThesesConfig
from steward.data_context.research import (
    Claim,
    ResearchContext,
    claim_sentence,
    company_context,
    country_context,
)
from steward.earnings.read import holding_detail
from steward.portfolio.read import portfolio_overview
from steward.research.sources import Registry

ACTION_KINDS = ("earnings_check", "research", "portfolio_sync")
REPORT_PATHS = {
    "research": "/research/{}",
    "review": "/reviews/{}",
    "escalation": "/escalations/{}",
}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "portfolio_overview",
        "description": "Portfolio totals, allocation by holding, currency and category, drift "
        "against the strategy's targets, as of the latest sync.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "holding",
        "description": "One holding: position, thesis, latest KPI verdicts and verified "
        "figures (with their sources).",
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string", "description": "e.g. AAPL_US_EQ"}},
            "required": ["ticker"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_news",
        "description": "Stored news items within the last N days, optionally for one holding "
        "and/or matching a text query. Without a query: newest first. With a query: full-text "
        "search over headline and summary, best matches first (relevance weighted by recency), "
        "each with an excerpt around the matched words.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "query": {
                    "type": "string",
                    "description": "Search-engine syntax. Several words must all appear, in any "
                    'word form (cuts/cut, rates/rate); "quoted words" must appear as a '
                    "phrase; OR between words matches either; -word excludes. E.g. "
                    "'guidance OR outlook -rumour', '\"share buyback\"'.",
                },
                "days": {"type": "integer", "minimum": 1, "maximum": 365},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "search_reports",
        "description": "Full-text search over digests, monthly reviews, research reports "
        "and impact notes, newest first.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "country_context",
        "description": "Stored country observations (macro series with their latest change, "
        "latest product trade) for an exposure geography, each with the date it became known. "
        "Numbers are stored values or latest-minus-previous; they are context, not a "
        "company-specific effect. Cite the source_id of each claim you use.",
        "input_schema": {
            "type": "object",
            "properties": {
                "geography": {"type": "string", "description": "Country code, e.g. DE or US"},
                "as_of": {"type": "string", "description": "Optional ISO date cutoff."},
            },
            "required": ["geography"],
            "additionalProperties": False,
        },
    },
    {
        "name": "company_context",
        "description": "Stored company observations (financial history, forward data, "
        "company research, confirmed business-network edges, patents) with the date each "
        "became known and listed gaps. Cite the source_id of each claim you use.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "e.g. AAPL_US_EQ"},
                "as_of": {"type": "string", "description": "Optional ISO date cutoff."},
            },
            "required": ["ticker"],
            "additionalProperties": False,
        },
    },
    {
        "name": "stress_test",
        "description": "What the portfolio would lose in the named scenarios (equity fall, "
        "rate rise, wider credit spreads, a fall in each foreign currency), computed from "
        "measured sensitivities. Use for 'what happens if EUR falls' or 'what if stocks drop'. "
        "Report the formula and the unshocked weight; never extend a result to holdings it "
        "did not move.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "risk_forecast",
        "description": "The volatility forecast from today's holdings (not from past returns "
        "of the old mix), the holdings that bring most of the risk, the diversification, and "
        "the weight the model had to leave out. Use for 'how risky is my portfolio' or 'what "
        "drives my risk'. Report the left-out weight and the warnings.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "past_episodes",
        "description": "What today's holdings would have lost in past market episodes (2008, "
        "2011, the end of the CZK floor, 2020, 2022 and any the owner added). Use for 'how "
        "would I have done in 2008'. Quote the caution when a loss leans on a proxy estimate; "
        "such a loss is probably understated.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "factor_tilts",
        "description": "What the portfolio leans on beyond the market: size, value, "
        "profitability, investment, momentum, currencies and the US 10-year yield, against a "
        "world-equity benchmark. Use for 'am I really diversified' or 'what is my hidden bet'. "
        "A loading counts as measured only when its t-statistic is at least 2.",
        "input_schema": {
            "type": "object",
            "properties": {"region": {"type": "string", "enum": ["developed", "europe", "us"]}},
            "additionalProperties": False,
        },
    },
    {
        "name": "idea_factor_fit",
        "description": "For candidate ideas with stored daily prices, how much each would "
        "reduce the portfolio's largest style tilt (size, value, profitability, investment, "
        "momentum or market) if held at a stated weight. A tie-breaker between comparable "
        "ideas, not a reason to take a weaker one. An idea without stored prices comes back "
        "with the reason; do not estimate it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 10,
                    "description": "Price symbols as stored, e.g. MSFT or msft.us.",
                },
                "currency": {"type": "string", "description": "Quote currency, default USD."},
                "weight": {"type": "number", "description": "Portfolio share, 0.005 to 0.2."},
            },
            "required": ["symbols"],
            "additionalProperties": False,
        },
    },
    {
        "name": "model_check",
        "description": "Whether the risk model's volatility and value-at-risk forecasts held up "
        "against what happened (weekly back-test on today's holdings, Kupiec and Christoffersen "
        "tests). Use before leaning on the model's figures, or when asked 'can I trust the risk "
        "numbers'. Says so when too little history exists.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "position_risk",
        "description": "One holding's risk for a thesis review: its share of the portfolio's "
        "risk against its weight, the holdings it moves with, and its worst past episodes. "
        "Expected Shortfall is not modelled yet and the answer says so.",
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string", "description": "A held ticker."}},
            "required": ["ticker"],
            "additionalProperties": False,
        },
    },
    {
        "name": "risk_whatif",
        "description": "What proposed buys or sells, or a deposit, do to the portfolio's "
        "ex-ante risk: forecast volatility, effective bets, each position's share of risk, tier "
        "risk budgets and every ex-ante limit, before and after. Use for 'what does buying X do "
        "to my risk?'. Sizes are in the base currency. Expected Shortfall and drawdown "
        "probability limits are tested (a new or deeper breach is listed) but their figures "
        "are not shown here; say so.",
        "input_schema": {
            "type": "object",
            "properties": {
                "trades": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 10,
                    "items": {
                        "type": "object",
                        "properties": {
                            "side": {"type": "string", "enum": ["buy", "sell"]},
                            "ticker": {"type": "string"},
                            "value_base": {
                                "type": "number",
                                "exclusiveMinimum": 0,
                                "maximum": 1e9,
                                "description": "Size, base currency.",
                            },
                        },
                        "required": ["side", "ticker", "value_base"],
                    },
                },
                "deposit": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1e9,
                    "description": "Cash added alongside, base currency.",
                },
            },
            "required": ["trades"],
            "additionalProperties": False,
        },
    },
    {
        "name": "start_size",
        "description": "The suggested starting weight for a stock: the smaller of the tier's "
        "starting cap (principle 8b) and the largest weight that raises no new ex-ante limit "
        "breach, with the quarter-Kelly upper bound and its inputs where a thesis exists.",
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
            "additionalProperties": False,
        },
    },
    {
        "name": "deposit_allocation",
        "description": "Where a deposit or sale proceeds should go under principle 8c: core "
        "first while below target, then the most under-represented tier, sector or country, "
        "then the best idea above the hurdle, else a core ETF. Whole shares, every rule, cap, "
        "band and ex-ante limit respected, risk impact per line. Read-only. Use for 'where "
        "should my next deposit go?'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "amount": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1e9,
                    "description": "Base currency.",
                }
            },
            "required": ["amount"],
            "additionalProperties": False,
        },
    },
    {
        "name": "rotation_candidates",
        "description": "The theses ranked by risk-adjusted expected return (scenario-weighted "
        "return annualised, less the cash rate, over volatility), held and bench ideas together, "
        "and for each holding the best idea that beats it with the gap against the switching "
        "hurdle (tax on the lots, costs, safety margin), the equal-risk size and whether every "
        "limit holds after the switch. Flags only. Use for 'what should I rotate?'. A row "
        "with a missing input says why; never invent a figure.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "switch_effect",
        "description": "What selling one holding to buy another does: the expected-return gap "
        "against the switching hurdle, the tax on the lots sold, the equal-risk size of the "
        "purchase and the rule, cap and risk-limit effects on the book after the switch. Use for "
        "'what does switching X for Y do to my risk?'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sell": {"type": "string", "description": "A held ticker."},
                "buy": {"type": "string", "description": "The ticker to buy."},
                "sell_value_base": {
                    "type": "number",
                    "description": "Amount sold, base currency; default the whole position.",
                },
            },
            "required": ["sell", "buy"],
            "additionalProperties": False,
        },
    },
    {
        "name": "over_cap_positions",
        "description": "Positions above the single-stock cap (principle 4f): months until "
        "deposits alone dilute each below the cap, and the sale that trims it to 8 % (10 % above "
        "the 15 % backstop). Whether the thesis still projects growth is the owner's call.",
        "input_schema": {
            "type": "object",
            "properties": {
                "monthly_deposit": {
                    "type": "number",
                    "description": "Expected deposit per month; default the last year's average.",
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "theme_exposure",
        "description": "How much of the portfolio is exposed to one sector, industry, country, "
        "currency, revenue country, commodity, trade product or issuer, directly and through "
        "funds, with the unknown weight and a confidence level. Use for 'which holdings rely "
        "on Chinese exports' (kind revenue_country or trade_product). If the unknown weight "
        "is large, say so.",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": SELECTOR_KINDS},
                "value": {"type": "string", "description": "Name or code, e.g. CN, Technology."},
            },
            "required": ["kind", "value"],
            "additionalProperties": False,
        },
    },
    {
        "name": "event_paths",
        "description": "Where an event at one company (a supplier outage, a lost customer) "
        "reaches the portfolio along supply-chain links you or filings recorded, with each "
        "path's weakest link, the weight at its end and the thesis indicators to watch. Use "
        "for 'what does a TSMC outage do to me'. Say whether the event is confirmed, reported "
        "or a rumour; only a confirmed one on strong links is rated high. If no path is found, "
        "say the graph has none; do not infer links.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "e.g. TSM"},
                "direction": {
                    "type": "string",
                    "enum": ["downstream", "upstream"],
                    "description": "downstream: a supplier's trouble reaches its customers; "
                    "upstream: a lost customer reaches its suppliers.",
                },
                "state": {"type": "string", "enum": ["confirmed", "reported", "rumoured"]},
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "fund_overlap",
        "description": "Which held funds also hold a stock and how much of the portfolio reaches "
        "it twice. Use for 'which fund duplicates this stock'. If the ticker is not found, say "
        "the report covers only the largest issuers; do not guess.",
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string", "description": "e.g. AAPL or AAPL_US_EQ"}},
            "required": ["ticker"],
            "additionalProperties": False,
        },
    },
    {
        "name": "performance_explain",
        "description": "Where the portfolio's gain over the last N days came from (price, FX, "
        "income, fees per holding, reconciled with a disclosed residual) and how it compares "
        "with the benchmark. Use for 'why did I underperform'. Report unattributed holdings and "
        "whether the benchmark comparison is like for like.",
        "input_schema": {
            "type": "object",
            "properties": {"days": {"type": "integer", "minimum": 7, "maximum": 3650}},
            "additionalProperties": False,
        },
    },
    {
        "name": "decision_scorecard",
        "description": "How the owner's recorded decisions have done over the last year: sell "
        "targets reached, returns by conviction, bought against passed, with sample sizes. "
        "Sections with too few decisions say so; report that instead of a number.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "propose_job",
        "description": "Propose starting a job: re-run a holding's earnings check, start a "
        "research report, or run the portfolio sync. Nothing starts until the owner "
        "confirms it in the chat; say that you proposed it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": list(ACTION_KINDS)},
                "ticker": {"type": "string"},
                "profile": {"type": "string", "description": "Research profile id."},
                "reason": {"type": "string"},
            },
            "required": ["kind", "reason"],
            "additionalProperties": False,
        },
    },
]


class ChatTools:
    def __init__(
        self,
        pool: asyncpg.Pool,
        registry: Registry,
        conversation_id: int,
        message_id: int | None,
    ) -> None:
        self.pool = pool
        self.registry = registry
        self.conversation_id = conversation_id
        self.message_id = message_id
        self.used: list[str] = []
        self.actions: list[int] = []

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.used.append(name)
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return {"error": f"unknown tool {name}"}
        try:
            result: dict[str, Any] = await handler(**args)
        except TypeError as exc:
            return {"error": f"bad arguments: {exc}"}
        return result

    async def _owner(self) -> str | None:
        return await analytics_tools.conversation_owner(self.pool, self.conversation_id)

    async def _tool_stress_test(self) -> dict[str, Any]:
        owner = await self._owner()
        if owner is None:
            return {"error": "this conversation has no owner"}
        return await analytics_tools.stress_test(self.pool, self.registry, owner)

    async def _tool_risk_forecast(self) -> dict[str, Any]:
        return await analytics_tools.risk_forecast(self.pool, self.registry)

    async def _tool_past_episodes(self) -> dict[str, Any]:
        return await analytics_tools.past_episodes(self.pool, self.registry)

    async def _tool_factor_tilts(self, region: str = "developed") -> dict[str, Any]:
        return await analytics_tools.factor_tilts(self.pool, self.registry, region)

    async def _tool_idea_factor_fit(
        self, symbols: list[str], currency: str = "USD", weight: float = 0.05
    ) -> dict[str, Any]:
        return await analytics_tools.idea_factor_fit(
            self.pool, self.registry, symbols, currency.upper(), weight
        )

    async def _tool_model_check(self) -> dict[str, Any]:
        return await analytics_tools.model_check(self.pool, self.registry)

    async def _tool_position_risk(self, ticker: str) -> dict[str, Any]:
        return await analytics_tools.position_risk(self.pool, self.registry, ticker)

    async def _tool_risk_whatif(
        self, trades: list[dict[str, Any]], deposit: float = 0.0
    ) -> dict[str, Any]:
        return await analytics_tools.risk_whatif(self.pool, self.registry, trades, deposit)

    async def _tool_start_size(self, ticker: str) -> dict[str, Any]:
        return await analytics_tools.start_size_for(self.pool, self.registry, ticker)

    async def _tool_deposit_allocation(self, amount: float) -> dict[str, Any]:
        return await analytics_tools.deposit_allocation(self.pool, self.registry, amount)

    async def _tool_rotation_candidates(self) -> dict[str, Any]:
        owner = await self._owner()
        if owner is None:
            return {"error": "this conversation has no owner"}
        return await analytics_tools.rotation_candidates(self.pool, self.registry, owner)

    async def _tool_switch_effect(
        self, sell: str, buy: str, sell_value_base: float | None = None
    ) -> dict[str, Any]:
        owner = await self._owner()
        if owner is None:
            return {"error": "this conversation has no owner"}
        return await analytics_tools.switch_effect(
            self.pool, self.registry, owner, sell, buy, sell_value_base
        )

    async def _tool_over_cap_positions(
        self, monthly_deposit: float | None = None
    ) -> dict[str, Any]:
        return await analytics_tools.over_cap_positions(self.pool, self.registry, monthly_deposit)

    async def _tool_theme_exposure(self, kind: str, value: str) -> dict[str, Any]:
        owner = await self._owner()
        if owner is None:
            return {"error": "this conversation has no owner"}
        return await analytics_tools.theme(self.pool, self.registry, owner, kind, value)

    async def _tool_event_paths(
        self, symbol: str, direction: str = "downstream", state: str = "reported"
    ) -> dict[str, Any]:
        owner = await self._owner()
        if owner is None:
            return {"error": "this conversation has no owner"}
        return await analytics_tools.event_reach(
            self.pool, self.registry, owner, symbol, direction, state
        )

    async def _tool_fund_overlap(self, ticker: str) -> dict[str, Any]:
        owner = await self._owner()
        if owner is None:
            return {"error": "this conversation has no owner"}
        return await analytics_tools.fund_overlap(self.pool, self.registry, owner, ticker)

    async def _tool_performance_explain(self, days: int = 365) -> dict[str, Any]:
        owner = await self._owner()
        if owner is None:
            return {"error": "this conversation has no owner"}
        return await analytics_tools.performance_explain(self.pool, self.registry, owner, days)

    async def _tool_decision_scorecard(self) -> dict[str, Any]:
        owner = await self._owner()
        if owner is None:
            return {"error": "this conversation has no owner"}
        return await analytics_tools.decision_scorecard(self.pool, self.registry, owner)

    async def _tool_portfolio_overview(self) -> dict[str, Any]:
        overview = await portfolio_overview(self.pool)
        if overview.freshness is None or overview.base is None:
            return {"note": "no portfolio snapshot yet"}
        sid = self.registry.add(
            "position",
            f"snapshot:{overview.freshness.snapshot_date}",
            f"Portfolio snapshot {overview.freshness.snapshot_date}",
            app_path="/",
            tier=1,
            snippet=f"total {overview.base.total} {overview.base.base_currency}",
            as_of=str(overview.freshness.snapshot_date),
        )
        drift = overview.drift
        return {
            "source_id": sid,
            "as_of": str(overview.freshness.snapshot_date),
            "total": overview.base.total,
            "cash": overview.base.cash,
            "currency": overview.base.base_currency,
            "allocation": [a.model_dump() for a in overview.allocation],
            "positions_out_of_band": [
                p.model_dump(include={"ticker", "weight", "target_weight", "status"})
                for p in (drift.positions if drift else [])
                if p.status not in ("in_band", "no_target")
            ],
        }

    async def _tool_holding(self, ticker: str) -> dict[str, Any]:
        detail = await holding_detail(self.pool, ticker)
        if detail is None:
            return {"error": f"no holding {ticker}"}
        data = detail.model_dump(mode="json")
        facts = []
        for f in data.get("facts", [])[:30]:
            if f.get("status") != "verified":
                continue
            sid = self.registry.add(
                "fact",
                f["id"],
                f"{f['metric']} {f['period']}: {f['value_as_written']}",
                url=f.get("source_url"),
                app_path=f"/evidence/{f['id']}",
                tier=1,
                snippet=f.get("snippet"),
                as_of=f["period"],
            )
            facts.append(
                {
                    "source_id": sid,
                    **{
                        k: f.get(k)
                        for k in (
                            "metric",
                            "period",
                            "value_as_written",
                            "scale",
                            "currency",
                            "basis",
                        )
                    },
                }
            )
        hid = self.registry.add(
            "position",
            f"holding:{ticker}",
            f"Holding page {ticker}",
            app_path=f"/holdings/{ticker}",
            tier=1,
            snippet=json.dumps(data.get("position"), default=str)[:500],
        )
        return {
            "source_id": hid,
            "position": data.get("position"),
            "thesis": data.get("thesis"),
            "kpis": data.get("kpis"),
            "facts": facts,
        }

    async def _tool_search_news(
        self, ticker: str | None = None, query: str | None = None, days: int = 30
    ) -> dict[str, Any]:
        # Full-text search (stemmed, ranked by relevance over recency). A query of stop words
        # only ("IT", "the") has no lexemes and falls back to a substring match.
        rows = await self.pool.fetch(
            "with q as (select case when $2::text is null then null"
            " else websearch_to_tsquery('english', $2::text) end as tsq),"
            " hits as (select i.id, i.url, i.headline, i.summary, i.outlet, i.tier,"
            " i.published_at, c.severity, c.confidence, coalesce(numnode(q.tsq), 0) > 0 as fts,"
            " case when coalesce(numnode(q.tsq), 0) > 0 then ts_rank_cd(i.search, q.tsq)"
            " / (1 + greatest(0, extract(epoch from now() - i.published_at)) / 86400 / 30)"
            " else 0 end as score"
            " from q, public.news_item i left join public.story_cluster c on c.id = i.cluster_id"
            " where ($1::text is null or $1 = any(i.tickers)) and i.published_at >= $3"
            " and ($2::text is null or case when coalesce(numnode(q.tsq), 0) > 0"
            " then i.search @@ q.tsq else i.headline ilike '%' || $2::text || '%'"
            " or i.summary ilike '%' || $2::text || '%' end)"
            " order by score desc, i.published_at desc limit 15)"
            " select hits.*, case when hits.fts then ts_headline('english',"
            " coalesce(hits.summary, hits.headline), q.tsq,"
            " 'MaxFragments=2, MaxWords=30, MinWords=8, StartSel=**, StopSel=**') end as excerpt"
            " from hits, q order by hits.score desc, hits.published_at desc",
            ticker,
            query or None,
            dt.datetime.now(dt.UTC) - dt.timedelta(days=max(1, min(365, days))),
        )
        items = []
        for r in rows:
            sid = self.registry.add(
                "news_item",
                r["id"],
                r["headline"],
                url=r["url"],
                tier=r["tier"],
                snippet=(r["summary"] or "")[:600] or None,
                as_of=str(r["published_at"].date()),
            )
            items.append(
                {
                    "source_id": sid,
                    "headline": r["headline"],
                    "outlet": r["outlet"],
                    "tier": r["tier"],
                    "published": str(r["published_at"].date()),
                    "severity": r["severity"],
                    "confidence": r["confidence"],
                    "summary": (r["summary"] or "")[:400],
                    **({"excerpt": r["excerpt"]} if r["excerpt"] else {}),
                }
            )
        return {"items": items}

    async def _tool_search_reports(self, query: str) -> dict[str, Any]:
        rows = await self.pool.fetch(
            "select id, type, title, created_at, ts_headline('simple', body_md,"
            " websearch_to_tsquery('simple', $1), 'MaxFragments=2, MaxWords=40') as excerpt"
            " from public.report where search @@ websearch_to_tsquery('simple', $1)"
            " order by created_at desc limit 8",
            query,
        )
        hits = []
        for r in rows:
            path = REPORT_PATHS.get(r["type"], "/reports/{}").format(r["id"])
            sid = self.registry.add(
                "report",
                r["id"],
                r["title"],
                app_path=path,
                tier=1,
                snippet=r["excerpt"],
                as_of=str(r["created_at"].date()),
            )
            hits.append(
                {
                    "source_id": sid,
                    "title": r["title"],
                    "type": r["type"],
                    "date": str(r["created_at"].date()),
                    "excerpt": r["excerpt"],
                }
            )
        return {"reports": hits}

    def _register_context(self, context: ResearchContext, app_path: str | None) -> dict[str, Any]:
        claims: list[dict[str, Any]] = []
        for claim in context.claims:
            claims.append(self._register_claim(claim, app_path))
        return {
            "scope": context.scope,
            "subject": context.subject,
            "as_of": context.as_of.isoformat(),
            "claims": claims,
            "gaps": [g.model_dump() for g in context.gaps],
            "method": context.method,
        }

    def _register_claim(self, claim: Claim, app_path: str | None) -> dict[str, Any]:
        sid = self.registry.add(
            "observation",
            claim.claim_id,
            claim.label,
            url=claim.source_url,
            app_path=app_path,
            tier=1 if claim.provenance == "primary" else 2,
            snippet=claim_sentence(claim),
            as_of=str(claim.known_as_of),
        )
        data = claim.model_dump(mode="json")
        data["source_id"] = sid
        return data

    @staticmethod
    def _cutoff(as_of: str | None) -> dt.datetime | None:
        if not as_of:
            return None
        day = dt.date.fromisoformat(as_of)
        return dt.datetime.combine(day, dt.time.max, dt.UTC)

    async def _tool_country_context(
        self, geography: str, as_of: str | None = None
    ) -> dict[str, Any]:
        try:
            cutoff = self._cutoff(as_of)
        except ValueError:
            return {"error": "as_of must be an ISO date"}
        context = await country_context(self.pool, geography.strip().upper(), cutoff)
        return self._register_context(context, None)

    async def _tool_company_context(self, ticker: str, as_of: str | None = None) -> dict[str, Any]:
        try:
            cutoff = self._cutoff(as_of)
        except ValueError:
            return {"error": "as_of must be an ISO date"}
        context = await company_context(self.pool, ticker, cutoff)
        return self._register_context(context, f"/holdings/{ticker}")

    async def _tool_propose_job(
        self,
        kind: str,
        reason: str,
        ticker: str | None = None,
        profile: str | None = None,
    ) -> dict[str, Any]:
        if kind not in ACTION_KINDS:
            return {"error": f"kind must be one of {', '.join(ACTION_KINDS)}"}
        params: dict[str, Any] = {}
        if kind in ("earnings_check", "research"):
            if not ticker:
                return {"error": f"{kind} needs a ticker"}
            params["ticker"] = ticker
        if kind == "earnings_check":
            theses = await load_active(self.pool, "theses", ThesesConfig)
            known = {t.ticker for t in (theses.body.theses if theses else [])}
            held = await self.pool.fetchval(
                "select exists (select 1 from public.instrument where t212_ticker = $1)", ticker
            )
            if ticker not in known and not held:
                return {"error": f"{ticker} is not a holding"}
        if kind == "research":
            stored = await load_active(self.pool, "research_profiles", ResearchProfilesConfig)
            chosen = (stored.body if stored else ResearchProfilesConfig()).get(profile)
            if chosen is None:
                return {"error": f"no research profile {profile!r}"}
            params["profile"] = chosen.id
        action_id: int = await self.pool.fetchval(
            "insert into public.chat_action (conversation_id, message_id, kind, params, reason)"
            " values ($1, $2, $3, $4::jsonb, $5) returning id",
            self.conversation_id,
            self.message_id,
            kind,
            json.dumps(params),
            reason[:500],
        )
        self.actions.append(action_id)
        return {
            "proposed_action": action_id,
            "status": "waiting for the owner's confirmation in the chat panel",
        }
