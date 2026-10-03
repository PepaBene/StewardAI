"""B20: the macro context text is built only from stored mappings and observations (and the
exposure arithmetic is live), and never asserts a sensitivity nobody configured."""

from __future__ import annotations

import datetime as dt

import asyncpg
import pytest

from steward.macro.read import context_for_holding
from tests.test_macro_fixes import _holding, _issuer, _macro, _map


@pytest.mark.db
async def test_every_explanation_states_its_stored_numbers_and_claims_no_sensitivity(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    issuer = await _issuer(pool)
    await _holding(pool, "MAPPED", issuer)
    await _map(pool, issuer, "0.5", geography="DE")  # has an indicator
    await _map(pool, issuer, "0.25", geography="JP")  # no indicator known
    await _map(pool, issuer, "0.4", hs_code="8542", hs_revision="HS2022")  # product cut
    await pool.execute("update exposure_mapping set created_at='2026-02-01'")
    await _macro(pool, "DE", "2026-06-01", "110", 0, "2026-07-10")
    context = await context_for_holding(pool, "MAPPED", dt.datetime(2026, 9, 1, tzinfo=dt.UTC))
    text = {(e.geography, e.hs_code): e.explanation for e in context.effects}
    assert "50.0% of" in text[("DE", None)] and "provide dated context" in text[("DE", None)]
    assert "No company-specific sensitivity is configured" in text[("DE", None)]
    assert "25.0%" in text[("JP", None)] and "no eligible country indicator" in text[("JP", None)]
    assert "40.0%" in text[(None, "8542")] and "HS 8542" in text[(None, "8542")]
    assert "do not establish company supply contracts or sensitivities" in text[(None, "8542")]
    for sentence in text.values():
        assert not any(w in sentence.lower() for w in ("will ", "expected to", "benefit", "hurt"))
    # The exposure module drives the numbers shown beside the text (dead code in the audit).
    assert context.exposure["mapped"] is not None
