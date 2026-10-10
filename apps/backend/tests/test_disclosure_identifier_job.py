"""Identifier resolution job: suggestions only, scored by exact agreement, never confirmed."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest

from steward.disclosure.identifier_job import (
    IdentifierCandidate,
    IssuerQuery,
    rank,
    resolve_identifiers,
)

ISIN = "US0000000001"


class FakePool:
    """Just enough of an asyncpg pool for the job: one issuer table, one security table and a
    provider_identifier table matched on the SQL fragments the job uses."""

    def __init__(
        self,
        issuers: list[dict[str, Any]],
        securities: list[dict[str, Any]] | None = None,
        identifiers: list[dict[str, Any]] | None = None,
    ) -> None:
        self.issuers = issuers
        self.securities = securities or []
        self.identifiers = identifiers or []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if "from public.issuer" in sql:
            return [i for i in self.issuers if args[0] is None or i["id"] in args[0]]
        assert "from public.security" in sql
        return [s for s in self.securities if s["issuer_id"] == args[0]]

    async def fetchval(self, sql: str, *args: Any) -> int | None:
        if "insert into public.provider_identifier" in sql:
            provider, id_type, identifier, issuer_id, security_id, score, evidence = args
            if any(
                (r["provider"], r["identifier_type"], r["identifier"])
                == (provider, id_type, identifier)
                for r in self.identifiers
            ):
                return None
            self.identifiers.append(
                {
                    "id": len(self.identifiers) + 1,
                    "provider": provider,
                    "identifier_type": id_type,
                    "identifier": identifier,
                    "issuer_id": issuer_id,
                    "security_id": security_id,
                    "match_status": "suggested",
                    "score": score,
                    "evidence": json.loads(evidence),
                }
            )
            return len(self.identifiers)
        id_type, issuer_id, security_id = args  # the confirmed-row check
        hit = any(
            r["identifier_type"] == id_type
            and r["match_status"] == "confirmed"
            and r["issuer_id"] == issuer_id
            and r.get("security_id") == security_id
            for r in self.identifiers
        )
        return 1 if hit else None

    async def fetchrow(self, _sql: str, *args: Any) -> dict[str, Any] | None:
        provider, id_type, identifier = args
        for r in self.identifiers:
            if (r["provider"], r["identifier_type"], r["identifier"]) == (
                provider,
                id_type,
                identifier,
            ):
                return r
        return None

    async def execute(self, _sql: str, row_id: int, score: float, evidence: str) -> None:
        for r in self.identifiers:
            if r["id"] == row_id and r["match_status"] == "suggested":
                r["score"], r["evidence"] = score, json.loads(evidence)


def issuer(
    name: str = "Acme Corp", cik: str | None = None, lei: str | None = None
) -> dict[str, Any]:
    return {"id": 1, "legal_name": name, "cik": cik, "lei": lei}


def lei_source(*found: IdentifierCandidate) -> Any:
    calls: list[IssuerQuery] = []

    async def lookup(query: IssuerQuery) -> Sequence[IdentifierCandidate]:
        calls.append(query)
        return found

    lookup.calls = calls  # type: ignore[attr-defined]
    return lookup


SECURITY = {"id": 10, "issuer_id": 1, "isin": ISIN, "figi": None}


async def test_an_exact_isin_agreement_is_a_high_scoring_suggestion_not_a_confirmation() -> None:
    pool = FakePool([issuer("Acme Corp")], [SECURITY])
    lookup = lei_source(IdentifierCandidate("LEI" + "A" * 17, "Totally Different Name", (ISIN,)))
    result = await resolve_identifiers(pool, lei_lookup=lookup)  # type: ignore[arg-type]
    [s] = result.suggestions
    assert s.score == 0.95 and not s.name_only and not s.ambiguous and s.stored
    [row] = pool.identifiers
    assert row["match_status"] == "suggested" and row["provider"] == "gleif"
    assert row["identifier_type"] == "lei" and row["security_id"] is None
    assert row["evidence"]["agreed_isins"] == [ISIN]
    assert lookup.calls[0].isins == (ISIN,)


async def test_isin_and_cik_agreeing_together_score_highest_and_a_cik_conflict_is_dropped() -> None:
    both = IdentifierCandidate("L1", "Acme", (ISIN,), cik="0000320193")
    clash = IdentifierCandidate("L2", "Acme Corp", (), cik="0000999999")
    ranked = rank([both, clash], "Acme Corp", "320193", (ISIN,))
    assert [(c.identifier, score) for c, score, _ in ranked] == [("L1", 0.98)]


async def test_a_name_only_match_is_capped_and_flagged() -> None:
    pool = FakePool([issuer("Acme Corp")])
    lookup = lei_source(
        IdentifierCandidate("LEI-NAME", "Acme Corporation"),
        IdentifierCandidate("LEI-FAR", "Zenith Mining Ltd"),
    )
    result = await resolve_identifiers(pool, lei_lookup=lookup)  # type: ignore[arg-type]
    [s] = result.suggestions
    assert s.name_only and s.score == 0.6 and s.identifier == "LEI-NAME"
    [row] = pool.identifiers  # the unrelated name is not stored at all
    assert row["match_status"] == "suggested" and row["score"] <= 0.6
    assert "name-only" in row["evidence"]["flag"]


async def test_a_confirmed_row_is_never_touched_or_re_suggested() -> None:
    confirmed = {
        "id": 1,
        "provider": "gleif",
        "identifier_type": "lei",
        "identifier": "LEI-OLD",
        "issuer_id": 1,
        "security_id": None,
        "match_status": "confirmed",
        "score": 1.0,
        "evidence": {"by": "owner"},
    }
    pool = FakePool([issuer()], [SECURITY], [dict(confirmed)])
    lookup = lei_source(IdentifierCandidate("LEI-NEW", "Acme Corp", (ISIN,)))
    result = await resolve_identifiers(pool, lei_lookup=lookup)  # type: ignore[arg-type]
    assert result.suggestions == () and "confirmed LEI exists" in result.skipped[0]
    assert pool.identifiers == [confirmed] and lookup.calls == []


async def test_a_confirmed_identifier_held_by_another_subject_is_left_as_it_is() -> None:
    other = {
        "id": 1,
        "provider": "gleif",
        "identifier_type": "lei",
        "identifier": "LEI-X",
        "issuer_id": 2,
        "security_id": None,
        "match_status": "confirmed",
        "score": 1.0,
        "evidence": {},
    }
    pool = FakePool([issuer()], [SECURITY], [dict(other)])
    lookup = lei_source(IdentifierCandidate("LEI-X", "Acme Corp", (ISIN,)))
    result = await resolve_identifiers(pool, lei_lookup=lookup)  # type: ignore[arg-type]
    [s] = result.suggestions
    assert not s.stored
    assert pool.identifiers == [other]


async def test_two_candidates_are_two_suggestions_each_with_an_ambiguity_note() -> None:
    pool = FakePool([issuer("Acme Corp")])
    lookup = lei_source(
        IdentifierCandidate("LEI-ONE", "Acme Corporation"),
        IdentifierCandidate("LEI-TWO", "Acme Co"),
    )
    result = await resolve_identifiers(pool, lei_lookup=lookup)  # type: ignore[arg-type]
    assert [s.identifier for s in result.suggestions] == ["LEI-ONE", "LEI-TWO"]
    assert all(s.ambiguous and s.name_only and s.score <= 0.6 for s in result.suggestions)
    notes = {r["identifier"]: r["evidence"]["ambiguity_note"] for r in pool.identifiers}
    assert "LEI-TWO" in notes["LEI-ONE"] and "LEI-ONE" in notes["LEI-TWO"]
    assert {r["match_status"] for r in pool.identifiers} == {"suggested"}


async def test_two_exact_agreements_are_ambiguous_and_stay_below_the_decisive_score() -> None:
    ranked = rank(
        [IdentifierCandidate("L1", "A", (ISIN,)), IdentifierCandidate("L2", "B", (ISIN,))],
        "Acme Corp",
        None,
        (ISIN,),
    )
    assert [score for _c, score, _e in ranked] == [0.9, 0.9]
    assert all(e["ambiguous"] for _c, _s, e in ranked)


async def test_a_figi_is_suggested_per_security_and_a_failing_lookup_does_not_stop_the_job() -> (
    None
):
    securities = [
        SECURITY,
        {"id": 11, "issuer_id": 1, "isin": "US0000000002", "figi": None},
        {"id": 12, "issuer_id": 1, "isin": "US0000000003", "figi": "BBG000000003"},
    ]
    pool = FakePool([issuer()], securities)

    async def figi(isin: str) -> Sequence[IdentifierCandidate]:
        if isin == ISIN:
            raise RuntimeError("rate limited")
        return [IdentifierCandidate("BBG000000002", "ACME", (isin,))]

    result = await resolve_identifiers(pool, figi_lookup=figi)  # type: ignore[arg-type]
    [s] = result.suggestions
    assert (s.provider, s.identifier_type, s.security_id, s.score) == ("openfigi", "figi", 11, 0.95)
    assert result.failed == ("security 10: FIGI lookup failed (RuntimeError)",)
    [row] = pool.identifiers
    assert row["match_status"] == "suggested" and row["security_id"] == 11


async def test_an_issuer_that_already_has_a_lei_is_not_looked_up() -> None:
    pool = FakePool([issuer(lei="LEI-HAVE")], [SECURITY])
    lookup = lei_source(IdentifierCandidate("LEI-NEW", "Acme Corp", (ISIN,)))
    result = await resolve_identifiers(pool, lei_lookup=lookup)  # type: ignore[arg-type]
    assert result.suggestions == () and lookup.calls == []


@pytest.mark.parametrize("name", ["Acme Corp", "ACME CORPORATION"])
async def test_a_refreshed_suggestion_updates_its_score_instead_of_duplicating(name: str) -> None:
    pool = FakePool([issuer()])
    lookup = lei_source(IdentifierCandidate("LEI-NAME", name))
    await resolve_identifiers(pool, lei_lookup=lookup)  # type: ignore[arg-type]
    await resolve_identifiers(pool, lei_lookup=lookup)  # type: ignore[arg-type]
    assert len(pool.identifiers) == 1
