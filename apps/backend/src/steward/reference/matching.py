"""Conservative canonical identity matching."""

import datetime as dt
import json
from dataclasses import dataclass, field

import asyncpg


@dataclass(frozen=True, slots=True)
class Candidate:
    key: str
    identifiers: dict[str, str] = field(default_factory=dict)
    name: str | None = None
    domain: str | None = None
    currency: str | None = None
    security_kind: str | None = None


@dataclass(frozen=True, slots=True)
class Match:
    candidate: Candidate | None
    score: float
    status: str
    reasons: tuple[str, ...]


def match_identity(query: Candidate, candidates: list[Candidate]) -> Match:
    """Identifiers can confirm; names/domains can only suggest; conflicts reject."""
    ranked: list[tuple[float, Candidate, list[str]]] = []
    for candidate in candidates:
        if query.currency and candidate.currency and query.currency != candidate.currency:
            continue
        if (
            query.security_kind
            and candidate.security_kind
            and query.security_kind != candidate.security_kind
        ):
            continue
        reasons: list[str] = []
        score = 0.0
        for kind, weight in (
            ("isin", 1.0),
            ("lei", 0.95),
            ("figi", 0.95),
            ("cik", 0.9),
            ("mic", 0.3),
        ):
            if query.identifiers.get(kind) and query.identifiers.get(
                kind
            ) == candidate.identifiers.get(kind):
                score = max(score, weight)
                reasons.append(kind)
        if (
            query.domain
            and candidate.domain
            and query.domain.casefold() == candidate.domain.casefold()
        ):
            score = max(score, 0.55)
            reasons.append("domain")
        if query.name and candidate.name and query.name.casefold() == candidate.name.casefold():
            score = max(score, 0.5)
            reasons.append("name")
        if reasons:
            ranked.append((score, candidate, reasons))
    if not ranked:
        return Match(None, 0, "unresolved", ())
    ranked.sort(key=lambda row: row[0], reverse=True)
    score, candidate, reasons = ranked[0]
    if len(ranked) > 1 and ranked[1][0] == score:
        return Match(None, score, "unresolved", ("ambiguous",))
    confirmed = score >= 0.9 and any(x in reasons for x in ("isin", "lei", "figi", "cik"))
    return Match(candidate, score, "confirmed" if confirmed else "suggested", tuple(reasons))


async def record_match_decision(
    pool: asyncpg.Pool,
    match: Match,
    *,
    instrument_ticker: str | None = None,
    provider_identifier_id: int | None = None,
    issuer_id: int | None = None,
    security_id: int | None = None,
    listing_id: int | None = None,
    decided_by: str | None = None,
) -> int:
    """Append an automatic or owner decision; previous decisions remain historical evidence."""
    if instrument_ticker is None and provider_identifier_id is None:
        raise ValueError("instrument_ticker or provider_identifier_id is required")
    decision_id = await pool.fetchval(
        """insert into public.identity_match_decision
           (provider_identifier_id,instrument_ticker,candidate_issuer_id,
            candidate_security_id,candidate_listing_id,decision,score,reasons,decided_by)
           values($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9) returning id""",
        provider_identifier_id,
        instrument_ticker,
        issuer_id,
        security_id,
        listing_id,
        match.status,
        match.score,
        json.dumps(match.reasons),
        decided_by,
    )
    return int(decision_id)


async def listing_as_of(
    pool: asyncpg.Pool, security_id: int, as_of: dt.date
) -> list[dict[str, object]]:
    """Resolve only listings and symbols valid at the historical cutoff."""
    rows = await pool.fetch(
        """select id,mic,ticker,currency,status,valid_from,valid_to
           from public.security_listing where security_id=$1
             and (valid_from is null or valid_from <= $2)
             and (valid_to is null or valid_to >= $2)
             and status not in ('terminated','cancelled') order by mic,ticker""",
        security_id,
        as_of,
    )
    return [dict(row) for row in rows]
