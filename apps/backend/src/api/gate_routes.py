"""Owner-only read models for the Phase 8 verification gates (D8.2, D8.3, D8.5, D8.6)."""

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path

from api.auth import Owner
from api.db import Pool
from steward.verify_sources.signal_audit import (
    SIGNAL_REQUIREMENTS,
    coverage_by_jurisdiction_and_metric,
    publish_gate,
)
from steward.verify_sources.thresholds import (
    CONCLUSION_DOMAINS,
    DOMAINS,
    domain_gate,
)
from steward.verify_sources.zero_cost import audit_zero_cost

router = APIRouter(prefix="/verification", tags=["verification"])


@router.get("/zero-cost-gate")
async def get_zero_cost_gate(pool: Pool, _owner: Owner) -> dict[str, Any]:
    """Read-only audit of the endpoint manifest: nothing paid is reachable."""
    return (await audit_zero_cost(pool)).to_json()


@router.get("/domain-gates")
async def get_domain_gates(pool: Pool, _owner: Owner) -> dict[str, Any]:
    """Per-domain reconciliation gate and which conclusions each domain unlocks."""
    decisions = {d: await domain_gate(pool, d) for d in DOMAINS}
    return {
        "domains": {
            d: {
                "allowed": x.allowed,
                "threshold_version": x.version,
                "samples": x.sample_count,
                "distinct_subjects": x.subject_count,
                "failures": x.failures,
                "reasons": list(x.reasons),
            }
            for d, x in decisions.items()
        },
        "conclusions": {
            name: {
                "domains": list(domains),
                "allowed": all(decisions[d].allowed for d in domains),
            }
            for name, domains in CONCLUSION_DOMAINS.items()
        },
    }


@router.get("/signals")
async def get_signal_gates(pool: Pool, _owner: Owner) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in SIGNAL_REQUIREMENTS:
        d = await publish_gate(pool, name)
        out[name] = {
            "publishable": d.allowed,
            "sample_size": d.sample_size,
            "evidence_precision": None if d.precision is None else float(d.precision),
            "pit_checked": d.pit_checked,
            "pit_violations": d.pit_violations,
            "audited_at": d.audited_at,
            "reviewer": d.reviewer,
            "reasons": list(d.reasons),
        }
    return out


@router.get("/signals/{signal_name}")
async def get_signal_gate(
    pool: Pool,
    _owner: Owner,
    signal_name: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{0,63}$")],
) -> dict[str, Any]:
    if signal_name not in SIGNAL_REQUIREMENTS:
        raise HTTPException(404, "unknown signal")
    gates: dict[str, Any] = await get_signal_gates(pool, _owner)
    result: dict[str, Any] = gates[signal_name]
    return result


@router.get("/coverage")
async def get_measured_coverage(pool: Pool, _owner: Owner) -> list[dict[str, Any]]:
    """Measured coverage by jurisdiction and metric (absent = no coverage, never zero)."""
    return await coverage_by_jurisdiction_and_metric(pool)


@router.get("/shadow-report")
async def get_latest_shadow_report(pool: Pool, _owner: Owner) -> dict[str, Any]:
    row = await pool.fetchrow(
        "select report, markdown, generated_at from public.source_shadow_report"
        " order by generated_at desc limit 1"
    )
    if row is None:
        raise HTTPException(404, "no shadow report has been generated yet")
    import json

    report = row["report"]
    return {
        "generated_at": row["generated_at"],
        "report": json.loads(report) if isinstance(report, str) else report,
        "markdown": row["markdown"],
    }
