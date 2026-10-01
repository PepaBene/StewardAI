"""D7.5: alerts from the free-source data (credit stress, trade exposure, patent legal status,
confirmed company-graph events), with an owner-readable decision trail.

Principles
- Only genuinely new observations alert. An observation is new when its period is later than
  every period of the same series known before it was stored; a revision or backfill of an
  older period is recorded (when its value changed) as `revision_ignored` and never alerts.
  The first load of a series is a `baseline`.
- Alerts are tier-1 synthetic news items (`news.synthetic`), so they go through the existing
  assessment, event rules, notification rules, repeat throttle, daily cap and digests. Their
  URL carries the decision's dedupe key, so a replay never raises a second item.
- On top of that, one alert per (rule, scope) per `throttle_hours` (scope: series, or
  holding plus product); the rest are recorded as `throttled`.
- Company-graph signals alert only when `status = 'confirmed'` and backed by primary
  evidence. Suggested or disputed edges and events are recorded as `review_needed` and
  raise nothing: no news item, no alert, never a thesis breach (this module never writes a
  breach or verdict).
- Every decision lands in `free_source_alert_trail`, one row per (rule, dedupe_key).
  Silent outcomes (a value below its threshold, an identical re-fetch) are only counted in
  the job step detail.
"""

import datetime as dt
import json
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import asyncpg
from pydantic import BaseModel, Field

from steward.activity import held_since
from steward.config.event_rules import CreditStressRule, EventRulesConfig
from steward.config.store import load_active
from steward.jobs.runner import JobContext
from steward.news.synthetic import SyntheticEvent, SyntheticSource, raise_events

MACRO_ALERT_SOURCE = SyntheticSource(
    "free-source-macro", "Free-source macro and trade monitoring", "macro"
)
ENTITY_ALERT_SOURCE = SyntheticSource(
    "free-source-entity", "Free-source company and patent monitoring", "reference"
)
OUTLETS: dict[str, tuple[str, str]] = {
    "fred": ("stlouisfed.org", "FRED"),
    "alfred": ("alfred.stlouisfed.org", "ALFRED"),
    "ecb": ("ecb.europa.eu", "ECB Data Portal"),
    "eurostat": ("ec.europa.eu", "Eurostat"),
    "bis": ("stats.bis.org", "BIS Data Portal"),
    "oecd": ("oecd.org", "OECD"),
    "un_comtrade": ("comtradeplus.un.org", "UN Comtrade"),
}
CONFIRMING_EVIDENCE = {"primary", "regulatory", "owner_review"}
GRAPH_EVENT_TYPE = {
    "deal": "m_and_a",
    "financing": "capital_allocation",
    "leadership": "management_change",
    "relationship": "sector_competitor",
}
GRAPH_EDGE_TYPE = {
    "acquirer": "m_and_a",
    "acquired": "m_and_a",
    "executive": "management_change",
}
DEFAULT_EDGE_EVENT = "sector_competitor"


def _host(url: str) -> str:
    return url.split("//", 1)[-1].split("/", 1)[0] or "unknown"


def _fmt(value: Decimal | float) -> str:
    return f"{Decimal(str(value)).normalize():f}"


def beyond(rule: CreditStressRule, value: Decimal) -> bool:
    threshold = Decimal(str(rule.threshold))
    return value > threshold if rule.direction == "above" else value < threshold


def patent_transition_material(previous: str, current: str) -> bool:
    """A lapse of a granted/active family, or an application that was granted. `unknown` on
    either side is never a transition."""
    if "unknown" in (previous, current) or previous == current:
        return False
    if current == "expired":
        return previous in ("granted", "active")
    return previous == "application" and current in ("granted", "active")


@dataclass
class RunSummary:
    counts: Counter[str] = field(default_factory=Counter)
    raised: list[str] = field(default_factory=list)

    def as_detail(self) -> dict[str, Any]:
        return {"counts": dict(self.counts), "raised": self.raised}


class _Evaluator:
    def __init__(
        self,
        pool: asyncpg.Pool,
        rules: EventRulesConfig,
        now: dt.datetime,
        job_id: int | None,
        version_id: int | None,
    ) -> None:
        self.pool = pool
        self.rules = rules
        self.cfg = rules.free_source_alerts
        self.now = now
        self.job_id = job_id
        self.version_id = version_id
        self.since = now - dt.timedelta(days=self.cfg.lookback_days)
        self.summary = RunSummary()
        self.raised_this_run = 0

    # -- trail -------------------------------------------------------------------------
    async def _seen(self, rule: str, key: str) -> bool:
        return bool(
            await self.pool.fetchval(
                "select exists (select 1 from public.free_source_alert_trail"
                " where rule = $1 and dedupe_key = $2)",
                rule,
                key,
            )
        )

    async def _record(
        self,
        rule: str,
        key: str,
        scope: str,
        ticker: str | None,
        outcome: str,
        reason: str,
        evidence: dict[str, Any],
        *,
        severity: int | None = None,
        news_item_id: int | None = None,
    ) -> None:
        inserted = await self.pool.fetchval(
            "insert into public.free_source_alert_trail (rule, dedupe_key, scope, ticker, outcome,"
            " severity, reason, evidence, news_item_id, job_id, config_version_id, created_at)"
            " values ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11, $12)"
            " on conflict (rule, dedupe_key) do nothing returning id",
            rule,
            key,
            scope,
            ticker,
            outcome,
            severity,
            reason,
            json.dumps(evidence, default=str),
            news_item_id,
            self.job_id,
            self.version_id,
            self.now,
        )
        if inserted:
            self.summary.counts[outcome] += 1

    async def _note(
        self,
        rule: str,
        key: str,
        scope: str,
        ticker: str | None,
        outcome: str,
        reason: str,
        evidence: dict[str, Any],
    ) -> None:
        """A decision that raises nothing; written once per key."""
        await self._record(rule, key, scope, ticker, outcome, reason, evidence)

    async def _raise(
        self,
        rule: str,
        key: str,
        scope: str,
        event_type: str,
        event: SyntheticEvent,
        source: SyntheticSource,
        reason: str,
        evidence: dict[str, Any],
    ) -> None:
        if await self._seen(rule, key):
            self.summary.counts["deduplicated"] += 1
            return
        ticker = event.ticker
        severity = self.rules.severity(event_type, ticker)
        if severity == 0:
            await self._record(
                rule, key, scope, ticker, "below_severity",
                f"event type {event_type} has severity 0 in the event rules", evidence,
            )  # fmt: skip
            return
        hours = self.cfg.throttle_hours
        if hours > 0:
            recent = await self.pool.fetchval(
                "select id from public.free_source_alert_trail where rule = $1 and scope = $2"
                " and outcome = 'raised' and created_at >= $3 order by id desc limit 1",
                rule,
                scope,
                self.now - dt.timedelta(hours=hours),
            )
            if recent:
                await self._record(
                    rule, key, scope, ticker, "throttled",
                    f"alert {recent} for the same scope was raised within {hours} h",
                    evidence, severity=severity,
                )  # fmt: skip
                return
        if self.raised_this_run >= self.cfg.max_alerts_per_run:
            # Not recorded: the next run decides it again.
            self.summary.counts["run_cap"] += 1
            return
        created = await raise_events(self.pool, source, [event], self.now, self.job_id)
        if not created:
            self.summary.counts["deduplicated"] += 1
            return
        self.raised_this_run += 1
        self.summary.raised.append(key)
        await self._record(
            rule, key, scope, ticker, "raised", reason, evidence,
            severity=severity, news_item_id=created[0],
        )  # fmt: skip

    # -- credit stress -------------------------------------------------------------------
    async def credit_stress(self) -> None:
        for rule in self.cfg.credit_stress:
            if not rule.enabled:
                continue
            rows = await self.pool.fetch(
                "select id, series_id, source, geography, period, value, unit, known_at,"
                " source_url from public.macro_observation where series_id = $1"
                " and ($2 = 'any' or source = $2) and ($3::text is null or geography = $3)"
                " and value is not null and known_at >= $4 order by period, known_at, id",
                rule.series_id,
                rule.source,
                rule.geography,
                self.since,
            )
            baseline_noted: set[tuple[str, str]] = set()
            for row in rows:
                await self._credit_row(rule, row, baseline_noted)

    async def _credit_row(
        self, rule: CreditStressRule, row: asyncpg.Record, baseline_noted: set[tuple[str, str]]
    ) -> None:
        scope = f"{rule.key}:{row['geography']}"
        prior_max = await self.pool.fetchval(
            "select max(period) from public.macro_observation where series_id = $1"
            " and source = $2 and geography = $3 and value is not null and known_at < $4",
            row["series_id"],
            row["source"],
            row["geography"],
            row["known_at"],
        )
        value: Decimal = row["value"]
        period: dt.date = row["period"]
        evidence: dict[str, Any] = {
            "series_id": row["series_id"],
            "source": row["source"],
            "geography": row["geography"],
            "period": period.isoformat(),
            "value": value,
            "unit": row["unit"],
            "threshold": rule.threshold,
            "direction": rule.direction,
            "observation_id": row["id"],
            "known_at": row["known_at"].isoformat(),
            "source_url": row["source_url"],
        }
        if prior_max is None:
            marker = (row["source"], row["geography"])
            if marker not in baseline_noted:
                baseline_noted.add(marker)
                await self._note(
                    "credit_stress",
                    f"credit:{rule.key}:{row['source']}:{row['geography']}:baseline",
                    scope, None, "baseline",
                    "first load of the series; its history is the baseline, nothing alerts",
                    evidence,
                )  # fmt: skip
            return
        if period <= prior_max:
            earliest = await self.pool.fetchval(
                "select value from public.macro_observation where series_id = $1 and source = $2"
                " and geography = $3 and period = $4 and known_at < $5 and value is not null"
                " order by known_at, id limit 1",
                row["series_id"],
                row["source"],
                row["geography"],
                period,
                row["known_at"],
            )
            if earliest is not None and earliest != value:
                evidence["previous_value"] = earliest
                await self._note(
                    "credit_stress",
                    f"credit:{rule.key}:{period}:rev:{_fmt(value)}",
                    scope, None, "revision_ignored",
                    f"{period} was revised from {_fmt(earliest)} to {_fmt(value)}; a revision of "
                    "an old period never alerts",
                    evidence,
                )  # fmt: skip
            else:
                self.summary.counts["refetch_identical"] += 1
            return
        if not beyond(rule, value):
            self.summary.counts["below_threshold"] += 1
            return
        predecessor = await self.pool.fetchval(
            "select value from public.macro_observation where series_id = $1 and source = $2"
            " and geography = $3 and period < $4 and value is not null"
            " order by period desc, known_at desc limit 1",
            row["series_id"],
            row["source"],
            row["geography"],
            period,
        )
        evidence["previous_value"] = predecessor
        if predecessor is not None and beyond(rule, predecessor):
            await self._note(
                "credit_stress",
                f"credit:{rule.key}:{period}",
                scope, None, "persisting",
                f"already beyond the threshold at the previous period ({_fmt(predecessor)}); "
                "the crossing was alerted then",
                evidence,
            )  # fmt: skip
            return
        outlet, outlet_name = OUTLETS.get(row["source"], (_host(row["source_url"]), row["source"]))
        word = "above" if rule.direction == "above" else "below"
        previous = _fmt(predecessor) if predecessor is not None else "n/a"
        event = SyntheticEvent(
            ticker=None,
            event_type="credit_stress",
            headline=f"{rule.label}: {_fmt(value)} {row['unit']} for {period:%Y-%m}, "
            f"{word} {_fmt(rule.threshold)}",
            summary=f"{rule.label} stood at {_fmt(value)} {row['unit']} for {period} "
            f"(previous {previous}), crossing the {_fmt(rule.threshold)} threshold "
            f"({rule.direction}). Source: {row['source_url']}",
            url=f"{row['source_url']}#alert:credit:{rule.key}:{row['geography']}:{period}",
            event_date=period,
            outlet=outlet,
            outlet_name=outlet_name,
        )
        await self._raise(
            "credit_stress",
            f"credit:{rule.key}:{period}",
            scope, "credit_stress", event, MACRO_ALERT_SOURCE,
            f"new {period} observation crossed {rule.threshold} ({rule.direction})",
            evidence,
        )  # fmt: skip

    # -- trade exposure -----------------------------------------------------------------
    async def trade_exposure(self, held: set[str]) -> None:
        if not held:
            return
        exposures = await self.pool.fetch(
            "select distinct isl.ticker, em.hs_code, em.hs_revision, em.geography, em.weight,"
            " em.basis, em.segment from public.exposure_mapping em"
            " join public.security s on s.issuer_id = em.issuer_id"
            " join public.instrument_security_link isl on isl.security_id = s.id"
            " and isl.status = 'confirmed' where isl.ticker = any($1::text[])"
            " and em.hs_code is not null and em.weight >= $2"
            " and em.valid_from <= $3::date and (em.valid_to is null or em.valid_to >= $3::date)",
            sorted(held),
            Decimal(str(self.cfg.trade_min_exposure_weight)),
            self.now.date(),
        )
        for exp in exposures:
            rows = await self.pool.fetch(
                "select id, reporter, partner, flow, hs_revision, hs_code, period, trade_value,"
                " known_at, source_url from public.trade_observation where hs_code = $1"
                " and ($2::text is null or hs_revision = $2)"
                " and ($3::text is null or reporter = $3) and trade_value is not null"
                " and known_at >= $4 order by period, known_at, id",
                exp["hs_code"],
                exp["hs_revision"],
                exp["geography"],
                self.since,
            )
            for row in rows:
                await self._trade_row(exp, row)

    async def _trade_row(self, exp: asyncpg.Record, row: asyncpg.Record) -> None:
        ticker: str = exp["ticker"]
        scope = f"{ticker}:{row['hs_code']}"
        same = (
            "reporter = $1 and partner = $2 and flow = $3 and hs_revision = $4 and hs_code = $5"
            " and trade_value is not null"
        )
        keys = (row["reporter"], row["partner"], row["flow"], row["hs_revision"], row["hs_code"])
        period: dt.date = row["period"]
        value: Decimal = row["trade_value"]
        prior_max = await self.pool.fetchval(
            f"select max(period) from public.trade_observation where {same} and known_at < $6",  # noqa: S608 - `same` is a constant fragment
            *keys,
            row["known_at"],
        )
        base_key = (
            f"trade:{ticker}:{row['reporter']}:{row['partner']}:{row['flow']}:{row['hs_code']}"
        )
        evidence: dict[str, Any] = {
            "ticker": ticker,
            "segment": exp["segment"],
            "exposure_weight": exp["weight"],
            "exposure_basis": exp["basis"],
            "reporter": row["reporter"],
            "partner": row["partner"],
            "flow": row["flow"],
            "hs_revision": row["hs_revision"],
            "hs_code": row["hs_code"],
            "period": period.isoformat(),
            "trade_value": value,
            "observation_id": row["id"],
            "source_url": row["source_url"],
        }
        if prior_max is None:
            await self._note(
                "trade_exposure", f"{base_key}:baseline", scope, ticker, "baseline",
                "first load of this trade flow; nothing alerts", evidence,
            )  # fmt: skip
            return
        if period <= prior_max:
            earliest = await self.pool.fetchval(
                f"select trade_value from public.trade_observation where {same}"  # noqa: S608 - `same` is a constant fragment
                " and period = $6 and known_at < $7 order by known_at, id limit 1",
                *keys,
                period,
                row["known_at"],
            )
            if earliest is not None and earliest != value:
                evidence["previous_value"] = earliest
                await self._note(
                    "trade_exposure", f"{base_key}:{period}:rev:{_fmt(value)}", scope, ticker,
                    "revision_ignored",
                    f"{period} was revised from {_fmt(earliest)} to {_fmt(value)}; a revision "
                    "of an old period never alerts",
                    evidence,
                )  # fmt: skip
            else:
                self.summary.counts["refetch_identical"] += 1
            return
        prior_year = await self.pool.fetchval(
            f"select trade_value from public.trade_observation where {same}"  # noqa: S608 - `same` is a constant fragment
            " and period = ($6::date - interval '1 year')::date and known_at <= $7"
            " order by known_at desc, id desc limit 1",
            *keys,
            period,
            row["known_at"],
        )
        if prior_year is None or prior_year <= 0:
            await self._note(
                "trade_exposure", f"{base_key}:{period}", scope, ticker, "no_baseline",
                "no positive value for the same period a year earlier; the change is unknown",
                evidence,
            )  # fmt: skip
            return
        change = (value / prior_year - 1) * 100
        evidence.update(prior_year_value=prior_year, change_pct=change)
        if abs(change) < Decimal(str(self.cfg.trade_min_change_pct)):
            self.summary.counts["below_threshold"] += 1
            return
        flow = "imports" if row["flow"] == "import" else "exports"
        outlet, outlet_name = OUTLETS["un_comtrade"]
        event = SyntheticEvent(
            ticker=ticker,
            event_type="trade_exposure",
            headline=f"{ticker}: HS {row['hs_code']} {flow} of reporter {row['reporter']} "
            f"{change:+.1f}% year on year ({period:%Y-%m})",
            summary=f"UN Comtrade: HS {row['hs_code']} {flow} (reporter {row['reporter']}, "
            f"partner {row['partner']}) were {_fmt(value)} USD for {period}, against "
            f"{_fmt(prior_year)} a year earlier. Mapped exposure of {ticker}: "
            f"{_fmt(exp['weight'])} ({exp['basis']}). National trade, not company revenue. "
            f"Source: {row['source_url']}",
            url=f"{row['source_url']}#alert:{base_key}:{period}",
            event_date=period,
            outlet=outlet,
            outlet_name=outlet_name,
        )
        await self._raise(
            "trade_exposure", f"{base_key}:{period}", scope, "trade_exposure", event,
            MACRO_ALERT_SOURCE,
            f"year-on-year change {change:+.1f}% on a mapped exposure", evidence,
        )  # fmt: skip

    # -- patents ---------------------------------------------------------------------
    async def patent_status(self, held: set[str]) -> None:
        if not held:
            return
        rows = await self.pool.fetch(
            "select f.family_key, f.legal_status, f.family_title, f.source_url, o.ticker,"
            " o.ownership_basis, o.ownership_confidence from public.patent_family f"
            " join public.patent_family_owner o on o.family_id = f.id"
            " where o.ticker = any($1::text[]) and o.ownership_basis <> 'suggested'"
            " and o.ownership_confidence >= $2 order by f.family_key, o.ticker",
            sorted(held),
            Decimal(str(self.cfg.patent_min_ownership_confidence)),
        )
        for row in rows:
            subject = f"{row['ticker']}:{row['family_key']}"
            current: str = row["legal_status"]
            previous = await self.pool.fetchval(
                "select state from public.free_source_alert_state"
                " where rule = 'patent_status' and subject_key = $1",
                subject,
            )
            if previous is None:
                await self._set_state(subject, current)
                self.summary.counts["patent_baseline"] += 1
                continue
            if previous == current:
                continue
            evidence: dict[str, Any] = {
                "ticker": row["ticker"],
                "family_key": row["family_key"],
                "previous_status": previous,
                "legal_status": current,
                "ownership_basis": row["ownership_basis"],
                "ownership_confidence": row["ownership_confidence"],
                "source_url": row["source_url"],
            }
            key = f"patent:{subject}:{previous}>{current}"
            scope = f"{row['ticker']}:patent"
            if current == "unknown":
                continue  # unknown is the absence of a reading: keep the last known status
            if not patent_transition_material(previous, current):
                await self._note(
                    "patent_status", key, scope, row["ticker"], "not_material",
                    f"{previous} to {current} is not a material legal-status change", evidence,
                )  # fmt: skip
            else:
                title = row["family_title"] or row["family_key"]
                event = SyntheticEvent(
                    ticker=row["ticker"],
                    event_type="patent_status",
                    headline=f"{row['ticker']}: patent family {row['family_key']} is now "
                    f"{current} (was {previous})",
                    summary=f"Legal status of {title} changed from {previous} to {current}. "
                    f"Ownership is {row['ownership_basis']} "
                    f"(confidence {_fmt(row['ownership_confidence'])}). "
                    f"Source: {row['source_url']}",
                    url=f"{row['source_url']}#alert:{key}",
                    event_date=None,
                    outlet=_host(row["source_url"]),
                    outlet_name=_host(row["source_url"]),
                )
                await self._raise(
                    "patent_status", key, scope, "patent_status", event, ENTITY_ALERT_SOURCE,
                    f"legal status {previous} to {current}", evidence,
                )  # fmt: skip
            await self._set_state(subject, current)

    async def _set_state(self, subject: str, state: str) -> None:
        await self.pool.execute(
            "insert into public.free_source_alert_state (rule, subject_key, state, updated_at)"
            " values ('patent_status', $1, $2, $3) on conflict (rule, subject_key)"
            " do update set state = excluded.state, updated_at = excluded.updated_at",
            subject,
            state,
            self.now,
        )

    # -- company graph ---------------------------------------------------------------
    async def company_graph(self, held: set[str]) -> None:
        if not held:
            return
        events = await self.pool.fetch(
            "select id, ticker, event_type, event_state, status, announced_at, counterparty_key,"
            " source, source_url, evidence_id, known_at from public.company_event"
            " where ticker = any($1::text[]) and event_type = any($2::text[])"
            " and known_at >= $3 and status in ('confirmed', 'suggested', 'disputed')"
            " order by known_at, id",
            sorted(held),
            list(self.cfg.graph_event_types),
            self.since,
        )
        for ev in events:
            await self._graph_event(ev)
        edges = await self.pool.fetch(
            "select id, subject_key, object_key, object_name, edge_type, status, evidence_tier,"
            " evidence_id, passage, source, source_url, known_at from public.company_graph_edge"
            " where subject_key = any($1::text[]) and edge_type = any($2::text[])"
            " and greatest(known_at, coalesce(decided_at, known_at)) >= $3"
            " and status in ('confirmed', 'suggested', 'disputed')"
            " order by known_at, id",
            sorted(held),
            list(self.cfg.graph_edge_types),
            self.since,
        )
        for edge in edges:
            await self._graph_edge(edge)

    async def _graph_event(self, ev: asyncpg.Record) -> None:
        ticker: str = ev["ticker"]
        scope = f"{ticker}:{ev['event_type']}"
        evidence: dict[str, Any] = {
            "company_event_id": ev["id"],
            "event_type": ev["event_type"],
            "event_state": ev["event_state"],
            "status": ev["status"],
            "counterparty_key": ev["counterparty_key"],
            "source": ev["source"],
            "source_url": ev["source_url"],
            "evidence_id": ev["evidence_id"],
        }
        key = f"graph:event:{ev['id']}:{ev['status']}"
        if ev["status"] != "confirmed":
            await self._note(
                "company_graph", key, scope, ticker, "review_needed",
                f"{ev['status']} company event: review it; it cannot raise an alert or a "
                "thesis breach until confirmed",
                evidence,
            )  # fmt: skip
            return
        if ev["event_state"] == "unknown":
            await self._note(
                "company_graph", key, scope, ticker, "not_material",
                "confirmed event with an unknown state; nothing to act on", evidence,
            )  # fmt: skip
            return
        event_type = GRAPH_EVENT_TYPE[ev["event_type"]]
        counterparty = f" with {ev['counterparty_key']}" if ev["counterparty_key"] else ""
        event = SyntheticEvent(
            ticker=ticker,
            event_type=event_type,
            headline=f"{ticker}: confirmed {ev['event_type']} event {ev['event_state']}"
            f"{counterparty}",
            summary=f"Company event for {ticker}: {ev['event_type']} {ev['event_state']}"
            f"{counterparty}, confirmed from {ev['source']}. Source: {ev['source_url']}",
            url=f"{ev['source_url']}#alert:{key}",
            event_date=ev["announced_at"],
            outlet=_host(ev["source_url"]),
            outlet_name=ev["source"],
        )
        await self._raise(
            "company_graph", key, scope, event_type, event, ENTITY_ALERT_SOURCE,
            f"confirmed {ev['event_type']} event ({ev['event_state']})", evidence,
        )  # fmt: skip

    async def _graph_edge(self, edge: asyncpg.Record) -> None:
        ticker: str = edge["subject_key"]
        scope = f"{ticker}:{edge['edge_type']}"
        evidence: dict[str, Any] = {
            "edge_id": edge["id"],
            "edge_type": edge["edge_type"],
            "object_key": edge["object_key"],
            "object_name": edge["object_name"],
            "status": edge["status"],
            "evidence_tier": edge["evidence_tier"],
            "evidence_id": edge["evidence_id"],
            "passage": edge["passage"],
            "source_url": edge["source_url"],
        }
        key = f"graph:edge:{edge['id']}:{edge['status']}"
        proven = edge["evidence_tier"] in CONFIRMING_EVIDENCE and bool(
            edge["evidence_id"] or edge["passage"]
        )
        if edge["status"] != "confirmed" or not proven:
            why = (
                f"{edge['status']} relationship"
                if edge["status"] != "confirmed"
                else "confirmed without primary evidence and a cited passage"
            )
            await self._note(
                "company_graph", key, scope, ticker, "review_needed",
                f"{why}: review it; it cannot raise an alert or a thesis breach",
                evidence,
            )  # fmt: skip
            return
        event_type = GRAPH_EDGE_TYPE.get(edge["edge_type"], DEFAULT_EDGE_EVENT)
        event = SyntheticEvent(
            ticker=ticker,
            event_type=event_type,
            headline=f"{ticker}: confirmed {edge['edge_type']} relationship with "
            f"{edge['object_name']}",
            summary=f"{ticker} has a confirmed {edge['edge_type']} relationship with "
            f"{edge['object_name']} ({edge['evidence_tier']} evidence). "
            f"Passage: {(edge['passage'] or '')[:300]} Source: {edge['source_url']}",
            url=f"{edge['source_url']}#alert:{key}",
            event_date=None,
            outlet=_host(edge["source_url"]),
            outlet_name=edge["source"],
        )
        await self._raise(
            "company_graph", key, scope, event_type, event, ENTITY_ALERT_SOURCE,
            f"confirmed {edge['edge_type']} edge", evidence,
        )  # fmt: skip


async def evaluate_free_source_alerts(
    pool: asyncpg.Pool,
    rules: EventRulesConfig,
    *,
    now: dt.datetime,
    job_id: int | None = None,
    config_version_id: int | None = None,
    held: set[str] | None = None,
) -> RunSummary:
    """Evaluate every rule once. `held`: the holdings the entity rules cover (default: the
    open positions of the latest snapshot)."""
    ev = _Evaluator(pool, rules, now, job_id, config_version_id)
    if not rules.free_source_alerts.enabled:
        return ev.summary
    if held is None:
        held = set(await held_since(pool))
    await ev.credit_stress()
    await ev.trade_exposure(held)
    await ev.patent_status(held)
    await ev.company_graph(held)
    return ev.summary


async def free_source_alerts_handler(ctx: JobContext) -> None:
    """Job `free_source_alerts`: evaluate the rules over the observations stored since the
    last collectors ran. Runs after macro_collect, public_macro, company_research."""
    now = dt.datetime.now(dt.UTC)
    async with ctx.step("load_config") as step:
        stored = await load_active(ctx.pool, "event_rules", EventRulesConfig)
        await ctx.record_config_version(stored.id if stored else None)
        rules = stored.body if stored else EventRulesConfig()
        step.detail["enabled"] = rules.free_source_alerts.enabled
    async with ctx.step("evaluate") as step:
        summary = await evaluate_free_source_alerts(
            ctx.pool,
            rules,
            now=now,
            job_id=ctx.job_id,
            config_version_id=stored.id if stored else None,
        )
        step.detail.update(summary.as_detail())


# -- the owner's decision trail ---------------------------------------------------------


class TrailRow(BaseModel):
    id: int
    created_at: dt.datetime
    rule: str
    scope: str
    ticker: str | None
    outcome: str = Field(
        description="raised, throttled, revision_ignored, baseline, not_material, no_baseline, "
        "persisting, review_needed or below_severity."
    )
    severity: int | None
    reason: str
    evidence: dict[str, Any]
    news_item_id: int | None
    job_id: int | None


class TrailOut(BaseModel):
    rows: list[TrailRow]
    next_before_id: int | None


async def read_trail(
    pool: asyncpg.Pool,
    *,
    rule: str | None = None,
    outcome: str | None = None,
    ticker: str | None = None,
    limit: int = 100,
    before_id: int | None = None,
) -> TrailOut:
    rows = await pool.fetch(
        "select id, created_at, rule, scope, ticker, outcome, severity, reason, evidence,"
        " news_item_id, job_id from public.free_source_alert_trail"
        " where ($1::text is null or rule = $1) and ($2::text is null or outcome = $2)"
        " and ($3::text is null or ticker = $3) and ($4::bigint is null or id < $4)"
        " order by id desc limit $5",
        rule,
        outcome,
        ticker,
        before_id,
        limit + 1,
    )
    page = rows[:limit]
    return TrailOut(
        rows=[TrailRow(**{**dict(r), "evidence": json.loads(r["evidence"])}) for r in page],
        next_before_id=page[-1]["id"] if len(rows) > limit else None,
    )
