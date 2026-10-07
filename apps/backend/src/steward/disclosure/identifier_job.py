"""Identifier resolution job (plan P0.2): suggest LEIs and FIGIs for issuers that lack them.

For each issuer without an LEI the injected LEI lookup (GLEIF in production) is asked; for each of
its securities that has an ISIN but no FIGI the injected FIGI lookup (OpenFIGI) is asked. Every
answer is stored as a *suggested* `provider_identifier` row with its score and evidence. Nothing
here confirms anything: a person does that through the existing decision flow.

Scoring (nothing guessed):

  * an exact identifier agreement, the candidate carrying an ISIN the issuer's security has or the
    issuer's CIK, scores 0.95 (0.98 when both agree). Nothing else may score above 0.9;
  * a name-only match is capped at 0.6 and flagged `name_only`; names too different to compare
    are not stored at all;
  * a candidate whose ISIN-less identifiers contradict the issuer (a different CIK) is dropped;
  * more than one candidate for the same subject stores each as its own suggestion with an
    `ambiguity_note` naming the others, and two or more exact agreements are capped at 0.9
    (an identifier agreeing with two entities is no longer decisive);
  * a confirmed row for the subject and identifier type is never touched or re-suggested, a
    rejected identifier is not suggested again, and a suggested row is only refreshed.

The lookups are callables, so the job runs without network access in tests. GLEIF's client has no
name search, so the production LEI lookup resolves by ISIN only (an exact agreement by
construction); a name-based lookup may be injected and is then scored as name-only.
"""

from __future__ import annotations

import difflib
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import asyncpg
import httpx

from steward.reference.disclosure_identity import normalize_name
from steward.sources.gleif import GleifClient
from steward.sources.openfigi import map_isin, resolve

type Pool = asyncpg.Pool | asyncpg.Connection

EXACT_SCORE = 0.95
EXACT_BOTH_SCORE = 0.98
AMBIGUOUS_EXACT_CAP = 0.9
NAME_ONLY_CAP = 0.6
MIN_NAME_SIMILARITY = 0.6


@dataclass(frozen=True, slots=True)
class IdentifierCandidate:
    """What a source says about one candidate identifier. `isins` and `cik` are the identifiers
    the source itself asserts for the candidate entity; `name` is its legal name."""

    identifier: str
    name: str | None = None
    isins: tuple[str, ...] = ()
    cik: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IssuerQuery:
    issuer_id: int
    name: str
    cik: str | None
    isins: tuple[str, ...]


type LeiLookup = Callable[[IssuerQuery], Awaitable[Sequence[IdentifierCandidate]]]
type FigiLookup = Callable[[str], Awaitable[Sequence[IdentifierCandidate]]]


@dataclass(frozen=True, slots=True)
class Suggestion:
    provider: str
    identifier_type: str
    identifier: str
    issuer_id: int
    security_id: int | None
    score: float
    name_only: bool
    ambiguous: bool
    stored: bool  # False when a row for this identifier already existed and was left as is


@dataclass(frozen=True, slots=True)
class IdentifierJobResult:
    issuers_checked: int
    suggestions: tuple[Suggestion, ...]
    skipped: tuple[str, ...]
    failed: tuple[str, ...]


def _cik(value: str | None) -> str | None:
    return str(int(value)) if value and value.strip().isdigit() else None


def score_candidate(
    cand: IdentifierCandidate, issuer_name: str, cik: str | None, isins: Sequence[str]
) -> tuple[float, dict[str, Any]] | None:
    """(score, evidence) for one candidate, or None when it must be dropped."""
    agreed = sorted(set(cand.isins) & set(isins))
    cik_known, cik_theirs = _cik(cik), _cik(cand.cik)
    cik_agrees = cik_known is not None and cik_known == cik_theirs
    if cik_known and cik_theirs and not cik_agrees:
        return None  # a different CIK is a contradiction, not a weaker match
    similarity = 0.0
    if cand.name:
        similarity = difflib.SequenceMatcher(
            None, normalize_name(issuer_name), normalize_name(cand.name)
        ).ratio()
    evidence: dict[str, Any] = {
        "candidate_name": cand.name,
        "issuer_name": issuer_name,
        "agreed_isins": agreed,
        "cik_agrees": cik_agrees,
        "name_similarity": round(similarity, 3),
        **cand.detail,
    }
    if agreed or cik_agrees:
        evidence["name_only"] = False
        return (EXACT_BOTH_SCORE if agreed and cik_agrees else EXACT_SCORE), evidence
    if similarity < MIN_NAME_SIMILARITY:
        return None
    evidence["name_only"] = True
    evidence["flag"] = "name-only match: no identifier agrees; a person must check it"
    return round(min(NAME_ONLY_CAP, NAME_ONLY_CAP * similarity), 3), evidence


def rank(
    candidates: Sequence[IdentifierCandidate],
    issuer_name: str,
    cik: str | None,
    isins: Sequence[str],
) -> list[tuple[IdentifierCandidate, float, dict[str, Any]]]:
    """Scored candidates, best first, one per identifier, with the ambiguity rules applied."""
    best: dict[str, tuple[IdentifierCandidate, float, dict[str, Any]]] = {}
    for cand in candidates:
        scored = score_candidate(cand, issuer_name, cik, isins)
        if scored is None:
            continue
        if cand.identifier not in best or scored[0] > best[cand.identifier][1]:
            best[cand.identifier] = (cand, scored[0], scored[1])
    ranked = sorted(best.values(), key=lambda t: (-t[1], t[0].identifier))
    if len(ranked) < 2:
        for _c, _s, evidence in ranked:
            evidence["ambiguous"] = False
        return ranked
    exact = sum(1 for _c, _s, e in ranked if not e["name_only"])
    out: list[tuple[IdentifierCandidate, float, dict[str, Any]]] = []
    for cand, score, evidence in ranked:
        others = [o.identifier for o, _s, _e in ranked if o.identifier != cand.identifier]
        evidence["ambiguous"] = True
        evidence["ambiguity_note"] = f"{len(ranked)} candidates; the others are {', '.join(others)}"
        if exact >= 2 and not evidence["name_only"]:
            score = min(score, AMBIGUOUS_EXACT_CAP)
        out.append((cand, score, evidence))
    return out


async def _has_confirmed(
    pool: Pool, identifier_type: str, issuer_id: int, security_id: int | None
) -> bool:
    found = await pool.fetchval(
        """select 1 from public.provider_identifier
            where identifier_type=$1 and match_status='confirmed' and issuer_id=$2
              and security_id is not distinct from $3 limit 1""",
        identifier_type,
        issuer_id,
        security_id,
    )
    return found is not None


async def _store(
    pool: Pool,
    *,
    provider: str,
    identifier_type: str,
    cand: IdentifierCandidate,
    score: float,
    evidence: dict[str, Any],
    issuer_id: int,
    security_id: int | None,
) -> bool:
    """Insert the suggestion; an existing row is only refreshed when it is itself a suggestion."""
    existing = await pool.fetchrow(
        """select id, match_status from public.provider_identifier
            where provider=$1 and identifier_type=$2 and identifier=$3 and valid_from is null""",
        provider,
        identifier_type,
        cand.identifier,
    )
    payload = json.dumps(evidence, default=str)
    if existing is None:
        inserted = await pool.fetchval(
            """insert into public.provider_identifier
                 (provider, identifier_type, identifier, issuer_id, security_id, match_status,
                  score, evidence)
               values($1,$2,$3,$4,$5,'suggested',$6,$7::jsonb)
               on conflict (provider, identifier_type, identifier,
                            (coalesce(valid_from, date '0001-01-01'))) do nothing
               returning id""",
            provider,
            identifier_type,
            cand.identifier,
            issuer_id,
            security_id,
            score,
            payload,
        )
        return inserted is not None
    if existing["match_status"] == "suggested":
        await pool.execute(
            "update public.provider_identifier set score=$2, evidence=$3::jsonb"
            " where id=$1 and match_status='suggested'",
            existing["id"],
            score,
            payload,
        )
        return True
    return False  # confirmed or rejected: a person already decided


async def _suggest(
    pool: Pool,
    *,
    provider: str,
    identifier_type: str,
    candidates: Sequence[IdentifierCandidate],
    issuer: IssuerQuery,
    security_id: int | None,
) -> list[Suggestion]:
    out: list[Suggestion] = []
    for cand, score, evidence in rank(candidates, issuer.name, issuer.cik, issuer.isins):
        stored = await _store(
            pool,
            provider=provider,
            identifier_type=identifier_type,
            cand=cand,
            score=score,
            evidence=evidence,
            issuer_id=issuer.issuer_id,
            security_id=security_id,
        )
        out.append(
            Suggestion(
                provider,
                identifier_type,
                cand.identifier,
                issuer.issuer_id,
                security_id,
                score,
                bool(evidence["name_only"]),
                bool(evidence["ambiguous"]),
                stored,
            )
        )
    return out


async def resolve_identifiers(
    pool: Pool,
    *,
    lei_lookup: LeiLookup | None = None,
    figi_lookup: FigiLookup | None = None,
    issuer_ids: Sequence[int] | None = None,
) -> IdentifierJobResult:
    """Suggest the missing LEI of each issuer and the missing FIGI of each of its securities.
    One failing lookup is reported and the job goes on."""
    rows = await pool.fetch(
        """select id, legal_name, cik, lei from public.issuer
            where ($1::bigint[] is null or id = any($1)) order by id""",
        list(issuer_ids) if issuer_ids is not None else None,
    )
    suggestions: list[Suggestion] = []
    skipped: list[str] = []
    failed: list[str] = []
    for row in rows:
        issuer_id = int(row["id"])
        securities = await pool.fetch(
            "select id, isin, figi from public.security where issuer_id=$1 order by id", issuer_id
        )
        isins = tuple(str(s["isin"]) for s in securities if s["isin"])
        query = IssuerQuery(issuer_id, row["legal_name"], row["cik"], isins)
        if lei_lookup is not None and row["lei"] is None:
            if await _has_confirmed(pool, "lei", issuer_id, None):
                skipped.append(f"issuer {issuer_id}: a confirmed LEI exists")
            else:
                try:
                    found = await lei_lookup(query)
                    suggestions += await _suggest(
                        pool,
                        provider="gleif",
                        identifier_type="lei",
                        candidates=found,
                        issuer=query,
                        security_id=None,
                    )
                except Exception as exc:
                    failed.append(f"issuer {issuer_id}: LEI lookup failed ({type(exc).__name__})")
        if figi_lookup is None:
            continue
        for sec in securities:
            if sec["figi"] is not None or not sec["isin"]:
                continue
            security_id = int(sec["id"])
            if await _has_confirmed(pool, "figi", issuer_id, security_id):
                skipped.append(f"security {security_id}: a confirmed FIGI exists")
                continue
            try:
                found = await figi_lookup(str(sec["isin"]))
                suggestions += await _suggest(
                    pool,
                    provider="openfigi",
                    identifier_type="figi",
                    candidates=found,
                    issuer=IssuerQuery(issuer_id, query.name, query.cik, (str(sec["isin"]),)),
                    security_id=security_id,
                )
            except Exception as exc:
                failed.append(f"security {security_id}: FIGI lookup failed ({type(exc).__name__})")
    return IdentifierJobResult(len(rows), tuple(suggestions), tuple(skipped), tuple(failed))


def gleif_lei_lookup(client: GleifClient) -> LeiLookup:
    """LEI by ISIN through GLEIF; the ISIN asked with is the one the candidate is said to carry."""

    async def lookup(issuer: IssuerQuery) -> list[IdentifierCandidate]:
        found: list[IdentifierCandidate] = []
        for isin in issuer.isins:
            entity = await client.for_isin(isin)
            if entity is not None:
                found.append(
                    IdentifierCandidate(
                        entity.lei,
                        entity.legal_name,
                        (isin,),
                        detail={"source": "gleif lei-records by isin", "status": entity.status},
                    )
                )
        return found

    return lookup


def openfigi_figi_lookup(http: httpx.AsyncClient, api_key: str | None = None) -> FigiLookup:
    """The share-class FIGI(s) OpenFIGI maps an ISIN to, one candidate per distinct share class."""

    async def lookup(isin: str) -> list[IdentifierCandidate]:
        listings = await map_isin(http, isin, api_key)
        classes = dict.fromkeys(x.share_class_figi for x in listings if x.share_class_figi)
        home = resolve(listings, isin).home
        return [
            IdentifierCandidate(
                figi,
                home.name if home else None,
                (isin,),
                detail={"source": "openfigi v3 mapping", "level": "share_class"},
            )
            for figi in classes
        ]

    return lookup
