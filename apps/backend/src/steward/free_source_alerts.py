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
- Verification gates (Phase 8, `enforce_verification_gates`): before an alert of a class is
  raised the matching gate is consulted (the domain reconciliation gate for credit stress,
  trade exposure and patents; the signal publication gate for confirmed graph events and
  relationships). A closed gate, and a gate with no audit data at all, raises nothing: the
  decision is recorded as `blocked_by_gate` and re-evaluated on the next run, when the gate
  may have opened. Observations that were deferred (a gate, the per-run cap) hold the
  watermark back so they stay inside the next run's window.
- The window starts at the last fully evaluated run (the watermark), at least
  `lookback_days` back and at most `max_catchup_days`, so a job outage loses no alert.
- Trade exposure joins the exposure's ISO geography to the Comtrade reporter through
  `steward.geo_codes` (Comtrade stores M49 numerics such as 842, exposures store `US`).
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
from steward.data_context.common import provenance_of
from steward.geo_codes import (
    has_column,
    has_iso_columns,
    reporter_codes,
    reporter_match_sql,
    to_iso,
)
from steward.jobs.runner import JobContext
from steward.news.synthetic import SyntheticEvent, SyntheticSource, raise_events
from steward.verify_sources.enforce import conclusion_gate, signal_gate

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
CONFIRMING_PROVENANCE = {"primary", "owner"}
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

# Phase 8 gates per alert class. A domain gate needs reconciliation samples; a signal gate needs
# a recent signal audit. Graph signals without a defined publication signal (leadership,
# plain relationships) are not gated: Phase 8 defines none for them.
DOMAIN_GATES = {
    "credit_stress": "credit_stress_alert",
    "trade_exposure": "trade_exposure_alert",
    "patent_status": "patent_alert",
}
GRAPH_EVENT_SIGNAL = {"deal": "deal_state", "financing": "deal_state"}
GRAPH_EDGE_SIGNAL = {
    "acquirer": "deal_state",
    "acquired": "deal_state",
    "customer": "customer_concentration",
    "supplier": "supplier_dependency",
}
WATERMARK_RULE = "free_source_alerts"
WATERMARK_KEY = "watermark"


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


@dataclass(frozen=True)
class GateVerdict:
    allowed: bool
    gate: str
    reason: str


class _Gates:
    """Thin adapter over the Phase 8 gates, cached per run. `domain:<conclusion>` calls
    `enforce.conclusion_gate`; `signal:<name>` calls `enforce.signal_gate` (the decision
    `require_publishable` raises on, evaluated at the run's clock); both fail closed."""

    def __init__(self, pool: asyncpg.Pool, enabled: bool, now: dt.datetime) -> None:
        self.pool = pool
        self.enabled = enabled
        self.now = now
        self._cache: dict[str, GateVerdict] = {}

    async def check(self, gate: str) -> GateVerdict:
        if not self.enabled:
            return GateVerdict(True, gate, "gates not enforced")
        cached = self._cache.get(gate)
        if cached is not None:
            return cached
        kind, _, name = gate.partition(":")
        # The production entry points of verify_sources.enforce: both fail closed (a gate that
        # cannot be evaluated refuses) and report every reason.
        if kind == "domain":
            decision = await conclusion_gate(self.pool, name, now=self.now)
        else:
            decision = await signal_gate(self.pool, name, now=self.now)
        verdict = GateVerdict(
            decision.allowed, gate, "open" if decision.allowed else "; ".join(decision.reasons)
        )
        self._cache[gate] = verdict
        return verdict


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
        self.gates = _Gates(pool, self.cfg.enforce_verification_gates, now)
        self.deferred = 0
        self.has_iso = False
        self.event_decided = False

    async def prepare(self) -> None:
        """Window start (watermark) and the optional columns other streams add."""
        floor = self.now - dt.timedelta(days=self.cfg.lookback_days)
        mark = await self.pool.fetchval(
            "select state from public.free_source_alert_state where rule = $1 and subject_key = $2",
            WATERMARK_RULE,
            WATERMARK_KEY,
        )
        self.since = floor
        if mark:
            try:
                last = dt.datetime.fromisoformat(mark)
            except ValueError:
                last = None
            if last is not None:
                earliest = self.now - dt.timedelta(days=self.cfg.max_catchup_days)
                self.since = min(floor, max(last, earliest))
        self.has_iso = await has_iso_columns(self.pool)
        self.event_decided = await has_column(self.pool, "company_event", "decided_at")

    async def finish(self) -> None:
        """Advance the watermark to this run's clock unless something was deferred (a closed
        gate, the per-run cap): then it stays at this run's window start, so the deferred
        observations are inside the next run's window as well."""
        mark = self.now if not self.deferred else self.since
        await self.pool.execute(
            "insert into public.free_source_alert_state (rule, subject_key, state, updated_at)"
            " values ($1, $2, $3, $4) on conflict (rule, subject_key)"
            " do update set state = excluded.state, updated_at = excluded.updated_at",
            WATERMARK_RULE,
            WATERMARK_KEY,
            mark.isoformat(),
            self.now,
        )

    # -- trail -------------------------------------------------------------------------
    async def _seen(self, rule: str, key: str) -> bool:
        # A gate-blocked row is provisional: the decision is made again once the gate opens.
        return bool(
            await self.pool.fetchval(
                "select exists (select 1 from public.free_source_alert_trail"
                " where rule = $1 and dedupe_key = $2 and outcome <> 'blocked_by_gate')",
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
            "insert into public.free_source_alert_trail as t (rule, dedupe_key, scope, ticker,"
            " outcome, severity, reason, evidence, news_item_id, job_id, config_version_id,"
            " created_at) values ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11, $12)"
            " on conflict (rule, dedupe_key) do update set scope = excluded.scope,"
            " ticker = excluded.ticker, outcome = excluded.outcome, severity = excluded.severity,"
            " reason = excluded.reason, evidence = excluded.evidence,"
            " news_item_id = excluded.news_item_id, job_id = excluded.job_id,"
            " config_version_id = excluded.config_version_id, created_at = excluded.created_at"
            " where t.outcome = 'blocked_by_gate' and excluded.outcome <> 'blocked_by_gate'"
            " returning id",
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
        gate: str | None = None,
    ) -> bool:
        """Decide one alert. Returns True when the decision is settled (raised, throttled,
        below severity or already recorded) and False when it is deferred (a closed gate or
        the per-run cap): a caller that keeps state must not advance it for a deferred one."""
        if await self._seen(rule, key):
            self.summary.counts["deduplicated"] += 1
            return True
        ticker = event.ticker
        severity = self.rules.severity(event_type, ticker)
        if severity == 0:
            await self._record(
                rule, key, scope, ticker, "below_severity",
                f"event type {event_type} has severity 0 in the event rules", evidence,
            )  # fmt: skip
            return True
        if gate is not None and self.cfg.enforce_verification_gates:
            verdict = await self.gates.check(gate)
            evidence = {**evidence, "gate": {"name": gate, "allowed": verdict.allowed}}
            if not verdict.allowed:
                evidence["gate"]["reason"] = verdict.reason
                await self._record(
                    rule, key, scope, ticker, "blocked_by_gate",
                    f"the {gate} verification gate is closed: {verdict.reason}",
                    evidence, severity=severity,
                )  # fmt: skip
                self.deferred += 1
                return False
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
                return True
        if self.raised_this_run >= self.cfg.max_alerts_per_run:
            # Not recorded: the next run decides it again.
            self.summary.counts["run_cap"] += 1
            self.deferred += 1
            return False
        created = await raise_events(self.pool, source, [event], self.now, self.job_id)
        if not created:
            # The item exists without a trail row: an earlier run stopped between creating it
            # and recording it. Record it now instead of leaving it untrailed.
            existing = await self.pool.fetchval(
                "select id from public.news_item where url = $1", event.url
            )
            await self._record(
                rule, key, scope, ticker, "raised",
                f"{reason} (recovered: the news item {existing} existed without a trail row)",
                evidence, severity=severity, news_item_id=existing,
            )  # fmt: skip
            self.summary.counts["recovered"] += 1
            return True
        self.raised_this_run += 1
        self.summary.raised.append(key)
        await self._record(
            rule, key, scope, ticker, "raised", reason, evidence,
            severity=severity, news_item_id=created[0],
        )  # fmt: skip
        return True

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
        # The dedupe key and the throttle scope carry the source AND the geography: two
        # geographies (or two sources) crossing in the same period are separate alerts.
        series_key = f"{rule.key}:{row['source']}:{row['geography']}"
        scope = series_key
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
                    f"credit:{series_key}:baseline",
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
                    f"credit:{series_key}:{period}:rev:{_fmt(value)}",
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
                f"credit:{series_key}:{period}",
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
            url=f"{row['source_url']}#alert:credit:{series_key}:{period}",
            event_date=period,
            outlet=outlet,
            outlet_name=outlet_name,
        )
        await self._raise(
            "credit_stress",
            f"credit:{series_key}:{period}",
            scope, "credit_stress", event, MACRO_ALERT_SOURCE,
            f"new {period} observation crossed {rule.threshold} ({rule.direction})",
            evidence,
            gate=f"domain:{DOMAIN_GATES['credit_stress']}",
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
            # Exposures hold ISO geographies; Comtrade holds M49 numerics (842 for the US).
            geography: str | None = exp["geography"]
            codes = reporter_codes(geography) if geography else None
            reporter_match = reporter_match_sql(self.has_iso, 3) if codes else "$3::text[] is null"
            rows = await self.pool.fetch(
                "select id, reporter, partner, flow, hs_revision, hs_code, period, trade_value,"  # noqa: S608 - fragment is built from constants
                " known_at, source_url from public.trade_observation where hs_code = $1"
                f" and ($2::text is null or hs_revision = $2) and ({reporter_match})"
                " and trade_value is not null and known_at >= $4"
                " order by period, known_at, id",
                exp["hs_code"],
                exp["hs_revision"],
                codes,
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
        reporter_iso = to_iso(row["reporter"])
        evidence: dict[str, Any] = {
            "ticker": ticker,
            "segment": exp["segment"],
            "exposure_weight": exp["weight"],
            "exposure_basis": exp["basis"],
            "exposure_geography": exp["geography"],
            "reporter": row["reporter"],
            "reporter_iso": reporter_iso,
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
        shown = f"{reporter_iso} ({row['reporter']})" if reporter_iso else row["reporter"]
        event = SyntheticEvent(
            ticker=ticker,
            event_type="trade_exposure",
            headline=f"{ticker}: HS {row['hs_code']} {flow} of reporter {shown} "
            f"{change:+.1f}% year on year ({period:%Y-%m})",
            summary=f"UN Comtrade: HS {row['hs_code']} {flow} (reporter {shown}, "
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
            gate=f"domain:{DOMAIN_GATES['trade_exposure']}",
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
            " and o.ownership_confidence >= $2"
            " and (o.valid_from is null or o.valid_from <= $3::date)"
            " and (o.valid_to is null or o.valid_to >= $3::date)"
            " order by f.family_key, o.ticker",
            sorted(held),
            Decimal(str(self.cfg.patent_min_ownership_confidence)),
            self.now.date(),
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
                await self._set_state(subject, current, 0)
                self.summary.counts["patent_baseline"] += 1
                continue
            if previous == current:
                continue
            if current == "unknown":
                continue  # unknown is the absence of a reading: keep the last known status
            # Every observed transition has its own sequence number, so a status that flaps
            # (granted > expired > granted > expired) alerts each time, not once ever.
            seq = 1 + int(
                await self.pool.fetchval(
                    "select state from public.free_source_alert_state"
                    " where rule = 'patent_status_seq' and subject_key = $1",
                    subject,
                )
                or 0
            )
            evidence: dict[str, Any] = {
                "ticker": row["ticker"],
                "family_key": row["family_key"],
                "previous_status": previous,
                "legal_status": current,
                "transition_sequence": seq,
                "ownership_basis": row["ownership_basis"],
                "ownership_confidence": row["ownership_confidence"],
                "source_url": row["source_url"],
            }
            key = f"patent:{subject}:{previous}>{current}:{seq}"
            scope = f"{row['ticker']}:patent"
            settled = True
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
                settled = await self._raise(
                    "patent_status", key, scope, "patent_status", event, ENTITY_ALERT_SOURCE,
                    f"legal status {previous} to {current}", evidence,
                    gate=f"domain:{DOMAIN_GATES['patent_status']}",
                )  # fmt: skip
            if settled:
                # State advances only once the outcome is recorded; a deferred decision (the
                # run cap, a closed gate) is taken again next run from the same previous state.
                await self._set_state(subject, current, seq)

    async def _set_state(self, subject: str, state: str, seq: int) -> None:
        for rule, value in (("patent_status", state), ("patent_status_seq", str(seq))):
            await self.pool.execute(
                "insert into public.free_source_alert_state (rule, subject_key, state, updated_at)"
                " values ($1, $2, $3, $4) on conflict (rule, subject_key)"
                " do update set state = excluded.state, updated_at = excluded.updated_at",
                rule,
                subject,
                value,
                self.now,
            )

    # -- company graph ---------------------------------------------------------------
    async def company_graph(self, held: set[str]) -> None:
        if not held:
            return
        # A confirmation that arrives later than the event's first sighting is new: the window
        # follows the decision time too (company_event.decided_at, when that column exists).
        moment = (
            "greatest(ev.known_at, coalesce(ev.decided_at, ev.known_at))"
            if self.event_decided
            else "ev.known_at"
        )
        events = await self.pool.fetch(
            "select ev.id, ev.ticker, ev.event_type, ev.event_state, ev.status, ev.announced_at,"  # noqa: S608 - `moment` is a constant fragment
            " ev.counterparty_key, ev.source, ev.source_url, ev.evidence_id,"
            f" ev.known_at, {moment} as moment, se.id is not null as evidence_found"
            " from public.company_event ev"
            " left join public.source_evidence se on se.id = ev.evidence_id"
            " where ev.ticker = any($1::text[]) and ev.event_type = any($2::text[])"
            f" and {moment} >= $3 and ev.status in ('confirmed', 'suggested', 'disputed')"
            " order by ev.known_at, ev.id",
            sorted(held),
            list(self.cfg.graph_event_types),
            self.since,
        )
        for ev in events:
            await self._graph_event(ev)
        edges = await self.pool.fetch(
            "select e.id, e.subject_key, e.object_key, e.object_name, e.edge_type, e.status,"
            " e.evidence_tier, e.evidence_id, e.passage, e.source, e.source_url, e.known_at,"
            " se.id is not null as evidence_found from public.company_graph_edge e"
            " left join public.source_evidence se on se.id = e.evidence_id"
            " where e.subject_key = any($1::text[]) and e.edge_type = any($2::text[])"
            " and greatest(e.known_at, coalesce(e.decided_at, e.known_at)) >= $3"
            " and e.status in ('confirmed', 'suggested', 'disputed')"
            " order by e.known_at, e.id",
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
        # A "confirmed" status is only a caller's assertion: it counts when it rests on a
        # stored evidence record and a primary (or owner) source.
        if not (ev["evidence_found"] and provenance_of(ev["source"]) in CONFIRMING_PROVENANCE):
            await self._note(
                "company_graph", key, scope, ticker, "review_needed",
                "confirmed company event without stored evidence from a primary source: "
                "review it; it cannot raise an alert or a thesis breach",
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
        signal = GRAPH_EVENT_SIGNAL.get(ev["event_type"])
        await self._raise(
            "company_graph", key, scope, event_type, event, ENTITY_ALERT_SOURCE,
            f"confirmed {ev['event_type']} event ({ev['event_state']})", evidence,
            gate=f"signal:{signal}" if signal else None,
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
        # The tier is asserted by the writer; it counts when it rests on a stored evidence
        # record, or on a cited passage from a primary (or owner) source.
        proven = edge["evidence_tier"] in CONFIRMING_EVIDENCE and (
            edge["evidence_found"]
            or (bool(edge["passage"]) and provenance_of(edge["source"]) in CONFIRMING_PROVENANCE)
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
        signal = GRAPH_EDGE_SIGNAL.get(edge["edge_type"])
        await self._raise(
            "company_graph", key, scope, event_type, event, ENTITY_ALERT_SOURCE,
            f"confirmed {edge['edge_type']} edge", evidence,
            gate=f"signal:{signal}" if signal else None,
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
    await ev.prepare()
    if held is None:
        held = set(await held_since(pool))
    await ev.credit_stress()
    await ev.trade_exposure(held)
    await ev.patent_status(held)
    await ev.company_graph(held)
    await ev.finish()
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
        "persisting, review_needed, below_severity or blocked_by_gate."
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
