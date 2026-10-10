"""Deciding an identifier suggestion: final, noted when flagged, fills only empty identifiers."""

import json
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest
from fastapi import HTTPException

from api.disclosure_jobs_routes import (
    IdentifierDecisionIn,
    decide_identifier_suggestion,
    list_identifier_suggestions,
)

pytestmark = pytest.mark.db

OWNER: Any = SimpleNamespace(user_id="u1")
LEI = "5493001KJTIIGC8Y1R12"


async def suggestion(pool: asyncpg.Pool, *, flagged: bool) -> tuple[int, int]:
    issuer = await pool.fetchval(
        "insert into public.issuer (legal_name) values ('Acme Corp') returning id"
    )
    sid = await pool.fetchval(
        """insert into public.provider_identifier
             (provider, identifier_type, identifier, issuer_id, match_status, score, evidence)
           values ('gleif','lei',$1,$2,'suggested',0.5,$3::jsonb) returning id""",
        LEI,
        issuer,
        json.dumps({"name_only": flagged, "ambiguous": False}),
    )
    return sid, issuer


async def test_confirm_fills_an_empty_lei_and_is_final(backend_pool: asyncpg.Pool) -> None:
    sid, issuer = await suggestion(backend_pool, flagged=False)
    out = await decide_identifier_suggestion(
        sid, IdentifierDecisionIn(decision="confirmed"), backend_pool, OWNER
    )
    assert out.match_status == "confirmed"
    assert await backend_pool.fetchval("select lei from public.issuer where id=$1", issuer) == LEI
    with pytest.raises(HTTPException) as again:
        await decide_identifier_suggestion(
            sid, IdentifierDecisionIn(decision="rejected"), backend_pool, OWNER
        )
    assert again.value.status_code == 409
    assert await list_identifier_suggestions(backend_pool, OWNER) == []


async def test_a_flagged_match_needs_a_note_to_confirm(backend_pool: asyncpg.Pool) -> None:
    sid, issuer = await suggestion(backend_pool, flagged=True)
    with pytest.raises(HTTPException) as err:
        await decide_identifier_suggestion(
            sid, IdentifierDecisionIn(decision="confirmed", note="ok"), backend_pool, OWNER
        )
    assert err.value.status_code == 422
    assert await backend_pool.fetchval("select lei from public.issuer where id=$1", issuer) is None
    out = await decide_identifier_suggestion(
        sid,
        IdentifierDecisionIn(decision="confirmed", note="Checked against the annual report"),
        backend_pool,
        OWNER,
    )
    assert out.decision_note == "Checked against the annual report"


async def test_reject_leaves_the_issuer_alone(backend_pool: asyncpg.Pool) -> None:
    sid, issuer = await suggestion(backend_pool, flagged=False)
    await decide_identifier_suggestion(
        sid, IdentifierDecisionIn(decision="rejected"), backend_pool, OWNER
    )
    assert await backend_pool.fetchval("select lei from public.issuer where id=$1", issuer) is None
