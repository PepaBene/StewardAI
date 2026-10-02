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
    # Which kind of thing the evidence identifies: "security" (ISIN / FIGI agree), "issuer"
    # (only LEI / CIK agree: the same company, not necessarily the same instrument), "listing"
    # (venue only) or "none". An issuer-level match is never a security confirmation (A7).
    level: str = "none"
    issuer_confirmed: bool = False


SECURITY_LEVEL = ("isin", "figi")
ISSUER_LEVEL = ("lei", "cik")
WEIGHTS = {"isin": 1.0, "figi": 0.95, "lei": 0.95, "cik": 0.9, "mic": 0.3}


def normalize_identifier(kind: str, value: str | None) -> str | None:
    """Canonical form of an identifier, so formatting never decides a match (or a conflict).

    ISIN, FIGI, LEI: upper case without spaces or hyphens.  CIK: ten digits, zero padded
    (`320193`, `0000320193` and ` 320193 ` are one company).  MIC: upper case.  Anything that
    normalizes to nothing is unknown, not a value that could conflict."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if kind == "cik":
        digits = text.upper().removeprefix("CIK").strip()
        return digits.zfill(10) if digits.isdigit() and int(digits) > 0 else None
    text = "".join(text.upper().split())
    if kind in ("isin", "figi", "lei"):
        text = text.replace("-", "")
    return text or None


def _identifiers(candidate: Candidate) -> dict[str, str]:
    found: dict[str, str] = {}
    for kind, raw in candidate.identifiers.items():
        normalized = normalize_identifier(kind, raw)
        if normalized is not None:
            found[kind] = normalized
    return found


def _conflicts(query: dict[str, str], candidate: dict[str, str]) -> list[str]:
    """Identifiers both sides state and that differ.  Two ISINs (or FIGIs) are two securities,
    even of one issuer (an ADR and its ordinary share); two LEIs / CIKs are two issuers."""
    return [
        kind
        for kind in (*SECURITY_LEVEL, *ISSUER_LEVEL)
        if kind in query and kind in candidate and query[kind] != candidate[kind]
    ]


def match_identity(query: Candidate, candidates: list[Candidate]) -> Match:
    """Identifiers can confirm; names/domains can only suggest; conflicts reject.

    ISIN / FIGI confirm a security.  LEI / CIK identify the issuer only: they confirm an
    issuer-level query, but never a security (an ADR and the ordinary share of one issuer share
    the LEI).  A candidate whose ISIN, FIGI, LEI or CIK differs from the query's is rejected
    outright, whatever else agrees."""
    q_ids = _identifiers(query)
    ranked: list[tuple[float, Candidate, list[str]]] = []
    rejected: list[str] = []
    for candidate in candidates:
        if query.currency and candidate.currency and query.currency != candidate.currency:
            continue
        if (
            query.security_kind
            and candidate.security_kind
            and query.security_kind != candidate.security_kind
        ):
            continue
        c_ids = _identifiers(candidate)
        conflicting = _conflicts(q_ids, c_ids)
        if conflicting:
            rejected.append(f"{candidate.key}:conflicting-{'+'.join(conflicting)}")
            continue
        reasons: list[str] = []
        score = 0.0
        for kind, weight in WEIGHTS.items():
            if kind in q_ids and q_ids.get(kind) == c_ids.get(kind):
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
        return Match(None, 0, "unresolved", tuple(rejected))
    ranked.sort(key=lambda row: (-row[0], row[1].key))
    score, candidate, reasons = ranked[0]
    has_security_id = any(x in reasons for x in SECURITY_LEVEL)
    has_issuer_id = any(x in reasons for x in ISSUER_LEVEL)
    query_names_security = any(kind in q_ids for kind in SECURITY_LEVEL)
    tied = [row for row in ranked if row[0] == score]
    if len(tied) > 1:
        # Candidates sharing only the issuer are equally the right issuer; for a question about
        # the issuer that is not ambiguity.  For anything narrower it is.
        same_issuer = has_issuer_id and not has_security_id and not query_names_security
        if not same_issuer:
            return Match(None, score, "unresolved", ("ambiguous", *rejected))
    if has_security_id and score >= 0.9:
        level, status = "security", "confirmed"
    elif has_issuer_id and score >= 0.9:
        # Confirms the issuer.  A query that names a security (an ISIN / FIGI the candidate
        # lacks) is only *suggested* that security.
        level, status = "issuer", "suggested" if query_names_security else "confirmed"
    elif "mic" in reasons:
        level, status = "listing", "suggested"
    else:
        level, status = "none", "suggested"
    return Match(
        candidate, score, status, tuple(reasons), level=level, issuer_confirmed=has_issuer_id
    )


async def record_match_decision(
    pool: asyncpg.Pool | asyncpg.Connection,
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
    if match.status == "confirmed" and match.level == "issuer" and (security_id or listing_id):
        raise ValueError("an issuer-level match (LEI / CIK) cannot confirm a security or listing")
    reasons = (*match.reasons, f"level:{match.level}") if match.level != "none" else match.reasons
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
        json.dumps(reasons),
        decided_by,
    )
    return int(decision_id)


async def listing_as_of(
    pool: asyncpg.Pool | asyncpg.Connection,
    security_id: int,
    as_of: dt.date,
    *,
    known_at: dt.datetime | None = None,
) -> list[dict[str, object]]:
    """The listings (and symbols) that were valid on `as_of`, as known at `known_at`.

    Validity is the listing's own `valid_from` / `valid_to` window, not its *current* status: a
    line delisted last month is still a listing on a screen dated before the delisting, with the
    symbol it had then.  `known_at` (default: now) hides rows recorded after the cutoff, so a
    historical replay cannot see what was learned later.  A terminated or cancelled line whose
    end date is unknown is returned with `end_unknown=True` rather than silently dropped or
    silently trusted; callers decide.
    """
    cutoff = known_at or dt.datetime.now(dt.UTC)
    rows = await pool.fetch(
        """select id,mic,ticker,currency,status,valid_from,valid_to,known_at,
                  (status in ('terminated','cancelled') and valid_to is null) as end_unknown
           from public.security_listing where security_id=$1
             and (valid_from is null or valid_from <= $2)
             and (valid_to is null or valid_to >= $2)
             and known_at <= $3
           order by mic,ticker,valid_from nulls first""",
        security_id,
        as_of,
        cutoff,
    )
    return [dict(row) for row in rows]
