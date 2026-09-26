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

from steward.config.research import ResearchProfilesConfig
from steward.config.store import load_active
from steward.config.thesis import ThesesConfig
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
        "description": "Stored news items, newest first, optionally for one holding and/or "
        "containing words, within the last N days.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "query": {"type": "string"},
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
        rows = await self.pool.fetch(
            "select i.id, i.url, i.headline, i.summary, i.outlet, i.tier, i.published_at,"
            " c.severity, c.confidence from public.news_item i left join public.story_cluster c"
            " on c.id = i.cluster_id where ($1::text is null or $1 = any(i.tickers))"
            " and ($2::text is null or i.headline ilike '%' || $2 || '%'"
            " or i.summary ilike '%' || $2 || '%') and i.published_at >= $3"
            " order by i.published_at desc limit 15",
            ticker,
            query,
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
