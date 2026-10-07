"""P7: read published estimates for one holding as of a date.

Everything is filtered to `known_at <= as_of`; a corrected IR file replaces its original only once
the
correction is itself known, so a historical view never sees today's correction. Reported actuals
come
from `disclosure_observation` (the latest version known by `as_of`); when none is loaded the answer
says "actual not loaded". Series are returned as separate lists and never merged.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import asyncpg

from steward.disclosure import estimates as est

type Pool = asyncpg.Pool | asyncpg.Connection

VALUE_SQL = """
select v.id, v.snapshot_id, v.metric, v.segment, v.segment_taxonomy, v.non_additive,
       v.restated_prior, v.fiscal_period, v.fiscal_calendar, v.period_start, v.period_end,
       v.currency, v.unit, v.scale, v.basis, v.basis_definition, v.mean, v.median, v.high, v.low,
       v.analyst_count, v.evidence_id, v.original_string,
       s.series, s.published_at, s.known_at, s.panel_description, s.analyst_count as panel_count,
       s.panel_method_version, s.analyst_name, s.broker_name, s.supersedes_id,
       s.corrected_by_id, s.document_version_id,
       cs.source_key, cs.publisher, coalesce(sd.stable_url, cs.url) as url
  from public.estimate_value v
  join public.public_estimate_snapshot s on s.id = v.snapshot_id
  left join public.consensus_source cs on cs.id = s.source_id
  left join public.source_document_version sdv on sdv.id = s.document_version_id
  left join public.source_document sd on sd.id = sdv.document_id
 where s.issuer_id = $1 and s.known_at <= $2
 order by s.known_at, v.id
"""


def _aware(value: dt.datetime | None) -> dt.datetime:
    now = value or dt.datetime.now(dt.UTC)
    return now if now.tzinfo else now.replace(tzinfo=dt.UTC)


def _s(v: Decimal | None) -> str | None:
    return None if v is None else str(v)


async def issuer_for_ticker(pool: Pool, ticker: str) -> Any:
    return await pool.fetchrow(
        """select i.id, i.legal_name from public.instrument n
             join public.issuer i on i.cik = n.cik where n.t212_ticker = $1 limit 1""",
        ticker,
    )


def to_value(r: Any) -> est.Value:
    return est.Value(
        id=int(r["id"]),
        snapshot_id=int(r["snapshot_id"]),
        series=r["series"],
        published_at=r["published_at"],
        known_at=r["known_at"],
        metric=r["metric"],
        fiscal_period=r["fiscal_period"],
        period_end=r["period_end"],
        unit=r["unit"],
        basis=r["basis"],
        scale=int(r["scale"]),
        segment=r["segment"],
        segment_taxonomy=r["segment_taxonomy"],
        non_additive=r["non_additive"],
        restated_prior=r["restated_prior"],
        fiscal_calendar=r["fiscal_calendar"],
        period_start=r["period_start"],
        currency=r["currency"],
        basis_definition=r["basis_definition"],
        mean=r["mean"],
        median=r["median"],
        high=r["high"],
        low=r["low"],
        analyst_count=r["analyst_count"],
        panel_method=r["panel_method_version"],
        panel_description=r["panel_description"],
        broker=r["broker_name"],
        analyst=r["analyst_name"],
        supersedes_snapshot_id=r["supersedes_id"],
        source=r["publisher"],
        document_url=r["url"],
        evidence_id=r["evidence_id"],
        original_string=r["original_string"],
    )


async def _actuals(
    pool: Pool, issuer_id: int, metrics: set[str], when: dt.datetime
) -> list[est.Actual]:
    if not metrics:
        return []
    rows = await pool.fetch(
        """select o.id, o.metric, o.segment, o.period_end, o.value, o.unit, o.scale, o.currency,
                  o.basis, o.dimensions, o.evidence_id, o.known_at, o.supersedes_id,
                  coalesce(sdv.published_at, o.known_at) as released_at,
                  sd.stable_url as url
             from public.disclosure_observation o
             join public.source_document_version sdv on sdv.id = o.document_version_id
             join public.source_document sd on sd.id = sdv.document_id
            where o.issuer_id = $1 and o.known_at <= $2 and o.value_state = 'reported'
              and o.value is not null and o.review_state <> 'rejected'
              and lower(o.metric) = any($3::text[])
            order by o.known_at, o.id""",
        issuer_id,
        when,
        sorted(metrics),
    )
    replaced = {r["supersedes_id"] for r in rows if r["supersedes_id"] is not None}
    latest: dict[tuple[str, str, dt.date], Any] = {}
    for r in rows:
        if r["id"] in replaced:
            continue
        latest[(r["metric"].lower(), (r["segment"] or "").lower(), r["period_end"])] = r
    return [
        est.Actual(
            observation_id=int(r["id"]),
            metric=r["metric"],
            period_end=r["period_end"],
            value=r["value"],
            unit=r["unit"],
            scale=int(r["scale"]),
            currency=r["currency"],
            basis=r["basis"],
            segment=r["segment"],
            released_at=r["released_at"],
            evidence_id=r["evidence_id"],
            document_url=r["url"],
        )
        for r in latest.values()
    ]


async def sources_for(pool: Pool, issuer_id: int) -> list[est.ConsensusSourceRow]:
    rows = await pool.fetch(
        """select source_key, publisher, origin, format, access_state, gap_reason, url, cadence,
                  tested_on, selected_metrics_only, selected_brokers_only
             from public.consensus_source where issuer_id = $1 order by id""",
        issuer_id,
    )
    return [est.ConsensusSourceRow(**dict(r)) for r in rows]


async def gate(pool: Pool) -> est.RevisionGate:
    rows = await pool.fetch(
        "select earlier_value_id, later_value_id, correct from public.estimate_pair_review order "
        "by id"
    )
    return est.revision_gate([dict(r) for r in rows])


def _line(v: est.Value, r: Any) -> dict[str, Any]:
    return {
        "id": v.id,
        "snapshot_id": v.snapshot_id,
        "series": v.series,
        "metric": v.metric,
        "segment": v.segment,
        "segment_taxonomy": v.segment_taxonomy,
        "non_additive": v.non_additive,
        "fiscal_period": v.fiscal_period,
        "fiscal_calendar": v.fiscal_calendar,
        "period_start": v.period_start,
        "period_end": v.period_end,
        "currency": v.currency,
        "unit": v.unit,
        "scale": v.scale,
        "basis": v.basis,
        "basis_definition": v.basis_definition,
        "mean": _s(v.mean),
        "median": _s(v.median),
        "high": _s(v.high),
        "low": _s(v.low),
        "analyst_count": v.analyst_count,
        "panel_analyst_count": r["panel_count"],
        "panel_description": v.panel_description,
        "panel_method": v.panel_method,
        "source": v.source,
        "broker": v.broker,
        "analyst": v.analyst,
        "published_at": v.published_at,
        "known_at": v.known_at,
        "supersedes_snapshot_id": v.supersedes_snapshot_id,
        "corrected": r["corrected_by_id"] is not None
        and r["corrected_by_id"] in r["_known_snapshots"],
        "document_url": v.document_url,
        "evidence_id": v.evidence_id,
        "original_string": v.original_string,
        "problems": est.problems(v),
    }


def _pair(p: est.Pair) -> dict[str, Any]:
    return {
        "earlier_id": p.earlier_id,
        "later_id": p.later_id,
        "series": p.series,
        "metric": p.metric,
        "segment": p.segment,
        "fiscal_period": p.fiscal_period,
        "earlier_known_at": p.earlier_known_at,
        "later_known_at": p.later_known_at,
        "comparable": p.comparable,
        "break_reasons": list(p.reasons),
        "statistic": p.statistic,
        "earlier_value": _s(p.earlier_value),
        "later_value": _s(p.later_value),
        "change": _s(p.change),
        "change_pct": _s(p.change_pct),
        "pct_note": p.pct_note,
        "elapsed_days": p.elapsed_days,
        "analyst_count_change": p.analyst_count_change,
        "analyst_count_note": p.analyst_count_note,
        "direction": p.direction,
        "note": p.note,
        "formula_version": p.formula_version,
    }


def _event(e: est.EventComparison) -> dict[str, Any]:
    return {
        "status": e.status,
        "metric": e.metric,
        "segment": e.segment,
        "fiscal_period": e.fiscal_period,
        "period_end": e.period_end,
        "release_at": e.release_at,
        "estimate_value_id": e.estimate_value_id,
        "estimate_known_at": e.estimate_snapshot_known_at,
        "statistic": e.statistic,
        "estimate": _s(e.estimate),
        "actual": _s(e.actual),
        "surprise": _s(e.surprise),
        "surprise_pct": _s(e.surprise_pct),
        "pct_note": e.pct_note,
        "analyst_count": e.analyst_count,
        "basis": e.basis,
        "currency": e.currency,
        "unit": e.unit,
        "later_files_only": e.later_files_only,
        "warnings": list(e.warnings),
        "actual_observation_id": e.actual_observation_id,
        "formula_version": e.formula_version,
        "formula": e.formula,
    }


def _bridge(b: est.SegmentBridge) -> dict[str, Any]:
    return {
        "metric": b.metric,
        "fiscal_period": b.fiscal_period,
        "period_end": b.period_end,
        "lines": [
            {
                "segment": ln.segment,
                "estimate_value_id": ln.estimate_value_id,
                "actual_observation_id": ln.actual_observation_id,
                "estimate": _s(ln.estimate),
                "actual": _s(ln.actual),
                "delta": _s(ln.delta),
                "delta_pct": _s(ln.delta_pct),
                "status": ln.status,
                "non_additive": ln.non_additive,
                "flags": list(ln.flags),
                "estimate_evidence_id": ln.estimate_evidence_id,
                "actual_evidence_id": ln.actual_evidence_id,
            }
            for ln in b.lines
        ],
        "additive_estimate_total": _s(b.additive_estimate_total),
        "additive_actual_total": _s(b.additive_actual_total),
        "unmapped_estimates": list(b.unmapped_estimates),
        "notes": list(b.notes),
        "inputs": list(b.inputs),
    }


async def estimate_view(
    pool: Pool, ticker: str, as_of: dt.datetime | None = None
) -> dict[str, Any]:
    when = _aware(as_of)
    issuer = await issuer_for_ticker(pool, ticker)
    g = await gate(pool)
    gate_out = {
        "reviewed": g.reviewed,
        "needed": g.needed,
        "correct": g.correct,
        "rate": _s(g.rate),
        "bar": str(g.bar),
        "met": g.met,
        "reason": g.reason,
    }
    empty_lanes: dict[str, list[dict[str, Any]]] = {s: [] for s in est.SERIES}
    if issuer is None:
        return {
            "ticker": ticker,
            "as_of": when,
            "issuer": None,
            "lanes": empty_lanes,
            "pairs": [],
            "events": [],
            "segment_bridges": [],
            "coverage": [
                {
                    "kind": "issuer",
                    "state": "unresolved_identity",
                    "detail": (
                        "This ticker is not linked to an issuer, so no estimates can be read."
                    ),
                }
            ],
            "sources": [],
            "revision_gate": gate_out,
            "formula": {"surprise": est.SURPRISE_FORMULA, "version": est.SURPRISE_FORMULA_VERSION},
            "notes": [],
        }
    issuer_id = int(issuer["id"])
    rows = await pool.fetch(VALUE_SQL, issuer_id, when)
    values = [to_value(r) for r in rows]
    raw = {v.id: dict(r) for v, r in zip(values, rows, strict=True)}
    known_snapshots = {v.snapshot_id for v in values}
    for r in raw.values():
        r["_known_snapshots"] = known_snapshots
    live = est.visible(values, when)
    lane = est.lanes(live)
    sources = await sources_for(pool, issuer_id)
    gaps = est.lane_gaps(lane, source_gaps=est.source_gap_texts(sources))

    consensus = [
        v for v in live if v.series == "published_analyst_consensus" and not est.problems(v)
    ]
    # Every consensus line known by `as_of`, including files a later correction replaced: a surprise
    # judges supersession at the release instant, not at `as_of`.
    consensus_all = [
        v for v in values if v.series == "published_analyst_consensus" and not est.problems(v)
    ]
    metrics = {v.metric.lower() for v in consensus}
    actuals = await _actuals(pool, issuer_id, metrics, when)
    by_key = {(a.metric.lower(), (a.segment or "").lower(), a.period_end): a for a in actuals}

    events: list[dict[str, Any]] = []
    seen: set[tuple[str, dt.date | None]] = set()
    for v in sorted(consensus, key=lambda v: (v.period_end or dt.date.min, v.metric)):
        if v.segment or (v.metric.lower(), v.period_end) in seen:
            continue
        seen.add((v.metric.lower(), v.period_end))
        a = by_key.get((v.metric.lower(), "", v.period_end)) if v.period_end else None
        events.append(
            _event(
                est.surprise(
                    a,
                    consensus_all,
                    metric=v.metric,
                    segment=None,
                    release_at=a.released_at if a else None,
                )
            )
        )

    bridges: list[dict[str, Any]] = []
    seg_periods = sorted(
        {(v.metric, v.period_end) for v in consensus if v.segment and v.period_end},
        key=lambda t: (t[1], t[0]),
    )
    for metric, period_end in seg_periods:
        seg_actuals = [
            a
            for a in actuals
            if a.segment and a.metric.lower() == metric.lower() and a.period_end == period_end
        ]
        release = min((a.released_at for a in seg_actuals if a.released_at), default=None)
        pool_est = est.visible(consensus_all, release, strict=True) if release else consensus
        b = est.segment_bridge(pool_est, seg_actuals, metric=metric, period_end=period_end)
        out = _bridge(b)
        if not seg_actuals:
            out["notes"].append("Segment actuals are not loaded for this period.")
        bridges.append(out)

    notes: list[str] = []
    if not values:
        notes.append("No estimate snapshots are known for this holding by that date.")
    return {
        "ticker": ticker,
        "as_of": when,
        "issuer": {"id": issuer_id, "name": issuer["legal_name"]},
        "lanes": {
            s: [_line(v, raw[v.id]) for v in lane[s]]  # type: ignore[index]
            for s in est.SERIES
        },
        "pairs": [_pair(p) for p in est.revision_pairs(live)],
        "events": events,
        "segment_bridges": bridges,
        "coverage": [{"kind": g_.kind, "state": g_.state, "detail": g_.detail} for g_ in gaps],
        "sources": [
            {
                "source_key": s.source_key,
                "publisher": s.publisher,
                "origin": s.origin,
                "format": s.format,
                "access_state": s.access_state,
                "gap_reason": s.gap_reason,
                "url": s.url,
                "cadence": s.cadence,
                "tested_on": s.tested_on,
                "selected_metrics_only": s.selected_metrics_only,
                "selected_brokers_only": s.selected_brokers_only,
            }
            for s in sources
        ],
        "revision_gate": gate_out,
        "formula": {"surprise": est.SURPRISE_FORMULA, "version": est.SURPRISE_FORMULA_VERSION},
        "notes": notes,
    }
