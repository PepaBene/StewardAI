"""B46: derived claims offered to research notes and chat pass the Phase 8 gates first."""

from __future__ import annotations

import datetime as dt

import asyncpg
import pytest

from steward.data_context.research import company_context
from steward.verify_sources.signal_audit import AuditObservation, record_audit_result
from tests.test_data_context_phase7 import seed_company, seed_world
from tests.test_phase7_audit_fixes import forward

TICKER = "EXM_US_EQ"


async def _seed(pool: asyncpg.Pool) -> None:
    ids = await seed_world(pool)
    await seed_company(pool, ids)
    await forward(pool, "issuer_ir", "2026-Q1", "1.10", "2026-05-01")


async def _pass_audit(pool: asyncpg.Pool, signal: str) -> None:
    sample = [AuditObservation(f"{signal}-{i}", True, True) for i in range(60)]
    await record_audit_result(pool, signal, sample, reviewer="owner")


def _gap_text(context: object, domain: str) -> str:
    return " ".join(g.reason for g in context.gaps if g.domain == domain)  # type: ignore[attr-defined]


@pytest.mark.db
async def test_a_fresh_deployment_withholds_derived_claims_and_says_so(
    backend_pool: asyncpg.Pool,
) -> None:
    await _seed(backend_pool)
    context = await company_context(backend_pool, TICKER)
    domains = {c.domain for c in context.claims}
    assert "financial" in domains  # stored facts are observations, not derived signals
    assert "business_network" not in domains and "forward" not in domains
    assert "Withheld, not missing" in _gap_text(context, "business_network")
    assert "signal supplier_dependency" in _gap_text(context, "business_network")
    assert "signal analyst_consensus_vintage" in _gap_text(context, "forward")
    # Opting out of enforcement (as the event rules can) returns them.
    open_ = await company_context(backend_pool, TICKER, enforce_gates=False)
    assert {"business_network", "forward"} <= {c.domain for c in open_.claims}


@pytest.mark.db
async def test_a_passing_audit_opens_exactly_its_own_signal(backend_pool: asyncpg.Pool) -> None:
    await _seed(backend_pool)
    await _pass_audit(backend_pool, "supplier_dependency")
    context = await company_context(backend_pool, TICKER)
    assert [c.text_value for c in context.claims if c.domain == "business_network"] == ["Acme Ltd"]
    # Consensus has no audit: still withheld.
    assert "forward" not in {c.domain for c in context.claims}
    assert "Withheld" in _gap_text(context, "forward")


@pytest.mark.db
async def test_a_gate_that_cannot_be_evaluated_withholds(backend_pool: asyncpg.Pool) -> None:
    await _seed(backend_pool)
    await _pass_audit(backend_pool, "supplier_dependency")

    class AuditTableDown:
        """The real pool, except that reading the audit table fails."""

        def __init__(self, pool: asyncpg.Pool) -> None:
            self._pool = pool

        def __getattr__(self, name: str) -> object:
            target = getattr(self._pool, name)
            if name not in {"fetch", "fetchrow", "fetchval"}:
                return target

            async def guarded(query: str, *args: object, **kwargs: object) -> object:
                if "signal_audit_result" in query:
                    raise OSError("database unreachable")
                return await target(query, *args, **kwargs)

            return guarded

    context = await company_context(AuditTableDown(backend_pool), TICKER)  # type: ignore[arg-type]
    assert "business_network" not in {c.domain for c in context.claims}
    assert "gate unavailable" in _gap_text(context, "business_network")


@pytest.mark.db
async def test_an_audit_dated_after_the_cutoff_does_not_open_a_historical_report(
    backend_pool: asyncpg.Pool,
) -> None:
    await _seed(backend_pool)
    await _pass_audit(backend_pool, "supplier_dependency")
    past = dt.datetime(2026, 9, 10, tzinfo=dt.UTC)
    context = await company_context(backend_pool, TICKER, past)
    assert "business_network" not in {c.domain for c in context.claims}
