"""The public-disclosure source registry (plan P0.1 / P1.2).

Each spec is one endpoint, form or issuer page with its access method, rate limit, formats and
the technical condition under which the pilot is stopped. `seed_sources` writes them to
`disclosure_source` (and a `provider_access` row so the shared governor and fetch audit apply).
A source starts `untested` and disabled: it may be enabled only after `record_access_test`
stored an `available` result. Documentation or a marketing claim enables nothing.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum

import asyncpg


class SourceAccess(StrEnum):
    UNTESTED = "untested"
    AVAILABLE = "available"
    AUTHENTICATION_FAILED = "authentication_failed"
    RETRIEVAL_FAILED = "retrieval_failed"
    PAID_ONLY = "paid_only"
    NOT_COVERED = "not_covered"


class SourceNotEnabled(RuntimeError):
    """A fetch was attempted for a source that is disabled or has no passing access test."""


@dataclass(frozen=True, slots=True)
class SourceSpec:
    source_key: str
    provider: str
    endpoint: str
    jurisdiction: str
    regulator: str
    access_method: str
    documents: tuple[str, ...]
    stop_condition: str
    rate_per_second: float | None = None
    requires_user_agent: bool = False
    publication_lag: dt.timedelta | None = None
    credential_env: str | None = None


SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec(
        "sec.submissions",
        "sec",
        "submissions",
        "US",
        "SEC",
        "none",
        ("json",),
        "HTTP 403/429 after declared User-Agent and 10 req/s ceiling respected",
        rate_per_second=8,
        requires_user_agent=True,
        publication_lag=dt.timedelta(minutes=5),
    ),
    SourceSpec(
        "sec.archive",
        "sec",
        "archive",
        "US",
        "SEC",
        "none",
        ("html", "xml", "txt", "pdf", "json"),
        "Archive URL not stable for an accession, or fair-access block",
        rate_per_second=8,
        requires_user_agent=True,
        publication_lag=dt.timedelta(minutes=5),
    ),
    SourceSpec(
        "sec.companyfacts",
        "sec",
        "companyfacts",
        "US",
        "SEC",
        "none",
        ("json",),
        "companyfacts disagrees with the filed instance beyond rounding",
        rate_per_second=8,
        requires_user_agent=True,
        publication_lag=dt.timedelta(hours=24),
    ),
    SourceSpec(
        "sec.fsn_bulk",
        "sec",
        "financial_statement_notes",
        "US",
        "SEC",
        "none",
        ("zip", "tsv"),
        "Monthly set cannot be reconciled to the filed version",
        rate_per_second=1,
        requires_user_agent=True,
        publication_lag=dt.timedelta(days=45),
    ),
    SourceSpec(
        "sec.nport_bulk",
        "sec",
        "nport_bulk",
        "US",
        "SEC",
        "none",
        ("zip", "tsv"),
        "Quarterly set lacks the matched funds",
        rate_per_second=1,
        requires_user_agent=True,
        publication_lag=dt.timedelta(days=60),
    ),
    SourceSpec(
        "esef.filings_xbrl_org",
        "esef",
        "filings",
        "EU",
        "ESMA/filings.xbrl.org",
        "none",
        ("json", "xbrl", "zip"),
        "Package unavailable for an issuer in the corpus",
        rate_per_second=2,
        publication_lag=dt.timedelta(days=1),
    ),
    SourceSpec(
        "fca.nsm",
        "fca",
        "national_storage_mechanism",
        "GB",
        "FCA",
        "none",
        ("html", "pdf", "json"),
        "No public read route (only the Primary Information Provider submission API)",
        rate_per_second=1,
    ),
    SourceSpec(
        "companies_house.api",
        "companies_house",
        "api",
        "GB",
        "Companies House",
        "api_key",
        ("json",),
        "Free API key refused or rate limit below the pilot's need",
        rate_per_second=2,
        credential_env="COMPANIES_HOUSE_API_KEY",
    ),
    SourceSpec(
        "edinet.api",
        "edinet",
        "api",
        "JP",
        "FSA EDINET",
        "registration",
        ("json", "zip", "xbrl"),
        "No free API key, or taxonomy/identity mapping fails",
        rate_per_second=1,
        credential_env="EDINET_API_KEY",
    ),
    SourceSpec(
        "ted.search",
        "ted",
        "search",
        "EU",
        "EU TED",
        "none",
        ("json",),
        "Search API cannot target buyers/suppliers by name",
        rate_per_second=1,
    ),
    SourceSpec(
        "gleif.level2",
        "gleif",
        "level2",
        "XX",
        "GLEIF",
        "none",
        ("json",),
        "Relationship records absent for the corpus issuers",
        rate_per_second=2,
    ),
    SourceSpec(
        "sedarplus.portal",
        "sedarplus",
        "portal",
        "CA",
        "CSA SEDAR+",
        "browser_session",
        ("html", "pdf"),
        "Live check 2026-10-08: a bot check (hCaptcha) a person must pass precedes search; rows "
        "carry profile number, document name, submit time to the second with its zone, "
        "jurisdiction and size, but no document id (links are session tokens); Export and "
        "Generate URL exist; fall back to issuer files and the CSA Data Distribution Service",
        rate_per_second=0.2,
    ),
    SourceSpec(
        "issuer_ir.consensus",
        "issuer_ir",
        "consensus_pages",
        "XX",
        "Issuer IR",
        "none",
        ("html", "pdf", "xlsx"),
        "No versioned file or metric definitions for the issuer",
        rate_per_second=0.5,
    ),
)

SPEC_BY_KEY = {spec.source_key: spec for spec in SOURCES}


def check_enable(access_state: str, enabled: bool) -> None:
    """The rule the database also enforces: enabled implies a passing access test."""
    if enabled and access_state != SourceAccess.AVAILABLE.value:
        raise SourceNotEnabled(
            f"source cannot be enabled while its access state is {access_state!r}"
        )


async def seed_sources(pool: asyncpg.Pool | asyncpg.Connection) -> int:
    """Insert missing specs (disabled, untested). Existing rows keep their test results."""
    count = 0
    for spec in SOURCES:
        await pool.execute(
            """insert into public.provider_access(provider, endpoint, state, credential_env, detail)
               values($1,$2,'access_unverified',$3,'disclosure source, not yet tested')
               on conflict(provider, endpoint) do nothing""",
            spec.provider,
            spec.endpoint,
            spec.credential_env,
        )
        inserted = await pool.fetchval(
            """insert into public.disclosure_source
                 (source_key, provider, endpoint, jurisdiction, regulator, access_method,
                  rate_limit_per_second, requires_user_agent, document_formats,
                  publication_lag, stop_condition)
               values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
               on conflict(source_key) do nothing returning id""",
            spec.source_key,
            spec.provider,
            spec.endpoint,
            spec.jurisdiction,
            spec.regulator,
            spec.access_method,
            spec.rate_per_second,
            spec.requires_user_agent,
            list(spec.documents),
            spec.publication_lag,
            spec.stop_condition,
        )
        count += inserted is not None
    return count


async def record_access_test(
    pool: asyncpg.Pool | asyncpg.Connection,
    source_key: str,
    state: SourceAccess,
    detail: str,
    *,
    tested_on: dt.date | None = None,
) -> None:
    """Store a real access-test result. Anything but `available` also disables the source."""
    await pool.execute(
        """update public.disclosure_source
              set access_state=$2, access_detail=$3, tested_on=$4, last_checked_at=now(),
                  enabled = enabled and $2 = 'available'
            where source_key=$1""",
        source_key,
        state.value,
        detail,
        tested_on or dt.datetime.now(dt.UTC).date(),
    )


async def set_enabled(
    pool: asyncpg.Pool | asyncpg.Connection, source_key: str, enabled: bool
) -> None:
    row = await pool.fetchrow(
        "select access_state from public.disclosure_source where source_key=$1", source_key
    )
    if row is None:
        raise KeyError(source_key)
    check_enable(row["access_state"], enabled)
    await pool.execute(
        "update public.disclosure_source set enabled=$2 where source_key=$1", source_key, enabled
    )
