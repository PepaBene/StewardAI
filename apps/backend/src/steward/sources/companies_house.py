"""Companies House (UK) company profile and filing history for holdings whose profile carries a
`companies_house_number` (D3.5).

Free REST API: HTTP Basic with the key as the username and an empty password; the published
allowance is about 600 requests per 5 minutes. The collector asks for two requests per holding
(profile, filing history) through the governor at background priority, and the job configuration's
`providers.companies_house` daily budget is the hard stop far below that ceiling.

Storage: profile text fields go to `company_research_observation` (provider `companies_house`,
dataset `profile`, match confidence 1 because the company number is an exact key); every filing
is a `source_document` version (regulator `companies_house`, external id
`<number>/<transaction_id>`) with its description as evidence, linked to the holding through
`source_record_evidence` (domain table `holding_filing_event`). Nothing is parsed out of the
filed documents themselves.

Field names (`company_name`, `company_status`, `items[].transaction_id`, `category`, `date`,
`description`, `type`) follow the public Companies House API reference and have NOT been checked
against a live response (no key was available when this was written).
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import asyncpg
import httpx

from steward.sources.base import AccessResult, AccessState
from steward.sources.company_research import store_research_observation
from steward.sources.documents import link_public_document
from steward.sources.free_market import FreeSourceError
from steward.sources.governed_http import governed_get
from steward.sources.governor import Budget
from steward.sources.store import CoverageUpdate, update_coverage

PROVIDER = "companies_house"
API_BASE = "https://api.company-information.service.gov.uk"
PUBLIC_BASE = "https://find-and-update.company-information.service.gov.uk"
FILING_PAGE_SIZE = 25
DOMAIN_TABLE = "holding_filing_event"
PROFILE_FIELDS = ("company_name", "company_status", "type", "jurisdiction", "date_of_creation")
NUMBER_PATTERN_LENGTH = 8


@dataclass(frozen=True, slots=True)
class CompanyProfile:
    number: str
    fields: dict[str, str]


@dataclass(frozen=True, slots=True)
class Filing:
    transaction_id: str
    filed_on: dt.date
    category: str
    kind: str
    description: str
    raw: dict[str, Any]


def _text(value: object) -> str | None:
    return " ".join(value.split()) if isinstance(value, str) and value.strip() else None


def parse_profile(payload: object, number: str) -> CompanyProfile | None:
    """Text fields the profile actually carries. A body for another company number is rejected
    rather than attributed to the holding."""
    if not isinstance(payload, dict):
        raise FreeSourceError("Companies House profile schema changed")
    if str(payload.get("company_number", number)).upper() != number.upper():
        return None
    fields: dict[str, str] = {}
    for name in PROFILE_FIELDS:
        if (text := _text(payload.get(name))) is not None:
            fields[name] = text
    sic = payload.get("sic_codes")
    if isinstance(sic, list) and (codes := [str(c) for c in sic if isinstance(c, str | int)]):
        fields["sic_codes"] = ",".join(codes)
    address = payload.get("registered_office_address")
    if isinstance(address, dict):
        parts = [_text(address.get(k)) for k in ("locality", "postal_code", "country")]
        if joined := ", ".join(p for p in parts if p):
            fields["registered_office"] = joined
    accounts = payload.get("accounts")
    if isinstance(accounts, dict):
        if isinstance(nxt := accounts.get("next_due"), str):
            fields["accounts_next_due"] = nxt
        last = accounts.get("last_accounts")
        if isinstance(last, dict) and isinstance(made := last.get("made_up_to"), str):
            fields["accounts_last_made_up_to"] = made
    return CompanyProfile(number.upper(), fields) if fields else None


def parse_filing_history(payload: object) -> list[Filing]:
    """Filing rows with a transaction id and a valid date; anything else is skipped."""
    if not isinstance(payload, dict):
        raise FreeSourceError("Companies House filing-history schema changed")
    items = payload.get("items")
    if items is None:
        return []
    if not isinstance(items, list):
        raise FreeSourceError("Companies House filing-history schema changed")
    out: list[Filing] = []
    for row in items:
        if not isinstance(row, dict) or not isinstance(tx := row.get("transaction_id"), str):
            continue
        try:
            filed_on = dt.date.fromisoformat(str(row.get("date")))
        except ValueError:
            continue
        out.append(
            Filing(
                transaction_id=tx,
                filed_on=filed_on,
                category=_text(row.get("category")) or "",
                kind=_text(row.get("type")) or "",
                description=_text(row.get("description")) or "",
                raw={k: row.get(k) for k in ("category", "date", "description", "type", "links")},
            )
        )
    return out


def filing_url(number: str, transaction_id: str) -> str:
    return f"{PUBLIC_BASE}/company/{number}/filing-history/{transaction_id}"


def endpoint_budget(provider_settings: Any) -> Budget:
    from steward.verify_sources.budgets import settings_budget

    return settings_budget(provider_settings)


async def collect_companies_house(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    *,
    ticker: str,
    number: str,
    api_key: str,
    provider_settings: Any,
) -> dict[str, int]:
    """Profile and the latest filings of one UK company. Two credits at most."""
    budget = endpoint_budget(provider_settings)
    auth = httpx.BasicAuth(api_key, "")
    now = dt.datetime.now(dt.UTC)
    counts = {"profile_fields": 0, "filings": 0}

    response = await governed_get(
        pool,
        http,
        provider=PROVIDER,
        endpoint="profile",
        url=f"{API_BASE}/company/{number}",
        budget=budget,
        auth=auth,
    )
    profile = parse_profile(_json(response), number) if response is not None else None
    source = f"{PUBLIC_BASE}/company/{number}"
    if profile is None:
        await update_coverage(
            pool,
            CoverageUpdate(
                PROVIDER,
                "profile",
                ticker,
                AccessResult(AccessState.NOT_COVERED, "no Companies House profile for the number"),
            ),
        )
    else:
        async with pool.acquire() as conn, conn.transaction():
            for name, text in profile.fields.items():
                await store_research_observation(
                    conn,
                    ticker=ticker,
                    provider=PROVIDER,
                    dataset="profile",
                    metric=name,
                    text_value=text,
                    source_url=source,
                    match_confidence=Decimal(1),
                    raw={"company_number": profile.number},
                )
        counts["profile_fields"] = len(profile.fields)
        await update_coverage(
            pool,
            CoverageUpdate(
                PROVIDER, "profile", ticker, AccessResult(AccessState.AVAILABLE, "profile stored")
            ),
        )

    response = await governed_get(
        pool,
        http,
        provider=PROVIDER,
        endpoint="filing_history",
        url=f"{API_BASE}/company/{number}/filing-history",
        params={"items_per_page": FILING_PAGE_SIZE},
        budget=budget,
        auth=auth,
    )
    filings = parse_filing_history(_json(response)) if response is not None else []
    for filing in filings:
        content = json.dumps(
            {"number": number, "transaction_id": filing.transaction_id, **filing.raw},
            sort_keys=True,
            default=str,
        ).encode()
        await link_public_document(
            pool,
            regulator=PROVIDER,
            external_id=f"{number}/{filing.transaction_id}",
            stable_url=filing_url(number, filing.transaction_id),
            document_type=filing.kind or filing.category or "filing",
            content=content,
            known_at=now,
            filed_at=filing.filed_on,
            quote=filing.description or None,
            domain_table=DOMAIN_TABLE,
            record_key=f"{ticker}:{number}/{filing.transaction_id}",
        )
        counts["filings"] += 1
    await update_coverage(
        pool,
        CoverageUpdate(
            PROVIDER,
            "filing_history",
            ticker,
            AccessResult(AccessState.AVAILABLE, f"{counts['filings']} filings stored")
            if counts["filings"]
            else AccessResult(AccessState.NOT_COVERED, "no Companies House filings"),
            available_periods=counts["filings"],
        ),
    )
    return counts


def _json(response: httpx.Response | None) -> Any:
    if response is None:
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise FreeSourceError("Companies House sent no JSON") from exc
