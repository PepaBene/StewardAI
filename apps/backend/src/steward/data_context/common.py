"""Shared building blocks: provenance classes, coverage gaps and denominator-bearing shares."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

Provenance = Literal["primary", "vendor", "owner", "community", "unclassified"]

# Exact source / provider names (as the collectors store them), not substrings: "sec" must
# not match "sector_feed", nor "bis" a name like "bistro_data". A family prefix (`sec_xbrl`,
# `epo_ops`) counts only when the prefix is followed by an underscore.
PRIMARY_SOURCES = frozenset(
    {
        "sec", "edgar", "xbrl", "esef", "esma", "firds", "pse", "gleif", "cnb", "ecb", "fred",
        "alfred", "eurostat", "world_bank", "oecd", "bis", "un_comtrade", "comtrade", "epo",
        "epo_ops", "issuer", "issuer_ir", "ir_release", "form13f", "form4", "nport",
        "schedule13", "regulator", "public_macro", "filing",
    }
)  # fmt: skip
PRIMARY_PREFIXES = (
    "sec_", "edgar_", "esef_", "esma_", "epo_", "oecd_", "ecb_", "fred_", "bis_", "eurostat_",
    "world_bank_", "gleif_", "cnb_", "issuer_", "regulator_",
)  # fmt: skip
VENDOR_SOURCES = frozenset(
    {
        "alpha_vantage", "alphavantage", "finnhub", "fmp", "eodhd", "twelve_data", "diffbot",
        "pdl", "people_data_labs", "yahoo", "stooq", "openfigi",
    }
)  # fmt: skip
VENDOR_PREFIXES = (
    "alpha_vantage_", "finnhub_", "fmp_", "eodhd_", "twelve_data_", "diffbot_", "pdl_",
    "people_data_labs_", "yahoo_", "stooq_", "openfigi_",
)  # fmt: skip
OWNER_SOURCES = frozenset({"owner", "owner_review", "owner_entered", "manual"})


def provenance_of(source: str | None) -> Provenance:
    """Primary (regulator, central bank, issuer) versus vendor (aggregator) by exact source
    name; anything not recognised is `unclassified`, never guessed from a substring."""
    if not source:
        return "unclassified"
    name = source.casefold().replace("-", "_").replace(" ", "_").split(":", 1)[0]
    if name in OWNER_SOURCES or name.startswith("owner_"):
        return "owner"
    if name == "wikidata" or name.startswith("wikidata_"):
        return "community"
    if name in VENDOR_SOURCES or name.startswith(VENDOR_PREFIXES):
        return "vendor"
    if name in PRIMARY_SOURCES or name.startswith(PRIMARY_PREFIXES):
        return "primary"
    return "unclassified"


def provenance_counts(sources: list[str | None]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for source in sources:
        key = provenance_of(source)
        counts[key] = counts.get(key, 0) + 1
    return counts


class Gap(BaseModel):
    domain: str
    state: Literal["missing", "not_covered", "unresolved", "partial", "stale"]
    reason: str


class Share(BaseModel):
    """A ratio shown with the numerator and the denominator it was divided by."""

    numerator: Decimal
    denominator: Decimal
    ratio: Decimal | None = Field(description="numerator / denominator; null for a zero base.")
    denominator_basis: str

    @classmethod
    def of(cls, numerator: Decimal, denominator: Decimal, basis: str) -> Share:
        return cls(
            numerator=numerator,
            denominator=denominator,
            ratio=numerator / denominator if denominator > 0 else None,
            denominator_basis=basis,
        )


def as_json(value: object) -> Any:
    """asyncpg returns jsonb as text here (no codec is registered); decode it, keep objects."""
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def to_date(value: dt.datetime | dt.date | None) -> dt.date | None:
    if value is None:
        return None
    return value.date() if isinstance(value, dt.datetime) else value


def cutoff(as_of: dt.datetime | None) -> dt.datetime:
    return as_of or dt.datetime.now(dt.UTC)


async def resolve_identity(pool: asyncpg.Pool, ticker: str) -> dict[str, object]:
    """Issuer and security ids of a held ticker via confirmed identity links only."""
    row = await pool.fetchrow(
        """select l.status,l.security_id,s.issuer_id,i.price_symbol
           from public.instrument i
           left join public.instrument_security_link l on l.ticker=i.t212_ticker
           left join public.security s on s.id=l.security_id
           where i.t212_ticker=$1""",
        ticker,
    )
    if row is None:
        return {"known": False, "status": None, "security_id": None, "issuer_id": None}
    confirmed = row["status"] == "confirmed"
    return {
        "known": True,
        "status": row["status"],
        "security_id": row["security_id"] if confirmed else None,
        "issuer_id": row["issuer_id"] if confirmed else None,
        "price_symbol": row["price_symbol"],
    }
