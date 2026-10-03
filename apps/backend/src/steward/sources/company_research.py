"""Opt-in research enrichment for company exposure, identity clues and patents."""

from __future__ import annotations

import datetime as dt
import json
import re
from decimal import Decimal
from urllib.parse import urlsplit

import asyncpg
import httpx

from steward.free_sources import store_company_edge
from steward.graph import provider_gate
from steward.graph.diffbot import parse_diffbot_relations
from steward.graph.evidence import record_evidence
from steward.graph.keys import normalize_object_key
from steward.graph.ops import (
    EpoAuthError,
    LegalEvent,
    OpsFamily,
    derive_legal_status,
    fetch_token,
    parse_ops_legal,
    parse_ops_page,
    search_applicants,
)
from steward.graph.patents import find_subsidiary_path, store_patent_family
from steward.sources.base import AccessResult, AccessState, quota_denial_text, retry_after_at
from steward.sources.credit_group import CreditPart, refund, reserve_group
from steward.sources.free_market import FreeSourceError, parse_alpha_series
from steward.sources.governor import Budget, PostgresRequestGovernor, QuotaExceeded
from steward.sources.store import record_access

__all__ = [
    "collect_alpha_exposure",
    "collect_diffbot_candidates",
    "collect_epo_patents",
    "collect_pdl_company",
    "parse_ops_patent_xml",
    "parse_pdl_company",
    "pdl_skip_reason",
    "store_research_observation",
]

PDL_MATCH_COOLDOWN = dt.timedelta(days=30)
PDL_NO_MATCH_COOLDOWN = dt.timedelta(days=14)
PDL_FAILURE_COOLDOWN = dt.timedelta(hours=24)
_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$")


async def _gate(pool: asyncpg.Pool, provider: str, endpoint: str) -> None:
    """Collectors run only on an endpoint verified free and available, not hard-blocked and
    past any Retry-After / auth-failure cooldown."""
    if not await provider_gate.gate_open(pool, provider, endpoint):
        raise RuntimeError(f"{provider} {endpoint} endpoint is not verified as free and available")


async def _record_http_failure(
    pool: asyncpg.Pool,
    provider: str,
    endpoint: str,
    response: httpx.Response,
    detail: str,
) -> AccessResult:
    """Record a non-success HTTP answer with the right typed state (G12).

    401/403 may be a transient denial: the first failures only cool the endpoint down, and only
    the third consecutive one is recorded as `access_unverified` (which hard-blocks). 402/403
    bodies that talk about a quota are quota states, 429 honours Retry-After, 5xx is transient."""
    status = response.status_code
    retry_at = retry_after_at(response.headers.get("Retry-After"))
    if status in (401, 403) and not quota_denial_text(response.text[:500]):
        result = await provider_gate.note_auth_failure(pool, provider, endpoint, status)
    elif status in (402, 403, 429):
        quota = status == 429 or quota_denial_text(response.text[:500])
        result = AccessResult(
            AccessState.QUOTA_EXHAUSTED if quota else AccessState.PAID_ONLY,
            f"HTTP {status}",
            retry_at,
        )
    elif status >= 500:
        result = AccessResult(AccessState.TEMPORARILY_FAILED, f"HTTP {status}", retry_at)
    else:
        result = AccessResult(AccessState.TEMPORARILY_FAILED, f"HTTP {status}")
    if result.detail is None or detail not in result.detail:
        result = AccessResult(
            result.state, f"{detail}: {result.detail}" if result.detail else detail, result.retry_at
        )
    await record_access(pool, provider, endpoint, result, status_code=status)
    return result


async def store_research_observation(
    pool: asyncpg.Pool | asyncpg.Connection,
    *,
    ticker: str,
    provider: str,
    dataset: str,
    metric: str,
    source_url: str,
    value: Decimal | None = None,
    text_value: str | None = None,
    period_end: dt.date | None = None,
    unit: str | None = None,
    currency: str | None = None,
    published_at: dt.datetime | None = None,
    match_confidence: Decimal | None = None,
    raw: dict[str, object] | None = None,
) -> int:
    """Append an observation unless an identical one (same ticker, provider, dataset, metric,
    period, value and unit) is already stored; an unchanged re-fetch only refreshes
    `last_seen_at` and keeps the earliest `known_at`. A changed value is a new row."""
    row_id: int = await pool.fetchval(
        """insert into public.company_research_observation as t
           (ticker,provider,dataset,metric,period_end,value,text_value,unit,currency,source_url,
            published_at,known_at,match_confidence,raw)
           values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,now(),$12,$13::jsonb)
           on conflict (ticker,provider,dataset,metric,coalesce(period_end, date '0001-01-01'),
                        coalesce(trim_scale(value)::text, ''), coalesce(text_value, ''),
                        coalesce(unit, ''))
           do update set last_seen_at=now(),
             match_confidence=greatest(t.match_confidence,excluded.match_confidence),
             currency=coalesce(t.currency,excluded.currency)
           returning id""",
        ticker,
        provider,
        dataset,
        metric,
        period_end,
        value,
        text_value,
        unit,
        currency,
        source_url,
        published_at,
        match_confidence,
        json.dumps(raw or {}, default=str),
    )
    return row_id


def normalize_commodity_unit(unit: str) -> tuple[str, str | None, Decimal]:
    """(unit label, currency, divisor). Alpha Vantage reports some commodities in cents; the
    stored value is converted to the major currency so it is never read as dollars."""
    lowered = unit.casefold()
    if "cent" in lowered:
        return re.sub(r"(?i)cents?", "USD", unit, count=1), "USD", Decimal(100)
    if "dollar" in lowered or "usd" in lowered:
        return unit, "USD", Decimal(1)
    return unit, None, Decimal(1)


async def collect_alpha_exposure(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    *,
    ticker: str,
    function: str,
    series_name: str,
    api_key: str,
    from_currency: str | None = None,
    to_currency: str | None = None,
) -> int:
    endpoint = "fx_daily" if function == "FX_DAILY" else "commodity"
    await _gate(pool, "alpha_vantage", endpoint)
    await _gate(pool, "alpha_vantage", "account_free")
    budget = Budget(daily=Decimal(20))
    # One transaction for both credits: a refusal of the second never burns the first.
    reservation = await reserve_group(
        pool,
        [
            CreditPart("alpha_vantage", "account_free", Decimal(1), budget),
            CreditPart("alpha_vantage", endpoint, Decimal(1), budget),
        ],
    )
    params: dict[str, str] = {"function": function, "apikey": api_key}
    if function == "FX_DAILY":
        if not from_currency or not to_currency:
            await refund(pool, reservation)
            raise ValueError("FX_DAILY requires from_currency and to_currency")
        params.update(from_symbol=from_currency, to_symbol=to_currency, outputsize="compact")
        currency: str | None = to_currency
        unit = f"{from_currency}/{to_currency}"
    else:
        params["interval"] = "daily"
        currency = None
        unit = "provider_reported"
    divisor = Decimal(1)
    try:
        response = await http.get("https://www.alphavantage.co/query", params=params)
    except httpx.HTTPError:
        await refund(pool, reservation)  # the request never reached the provider
        await record_access(
            pool,
            "alpha_vantage",
            endpoint,
            AccessResult(AccessState.TEMPORARILY_FAILED, "network error"),
        )
        raise
    try:
        if response.status_code != 200:
            raise FreeSourceError(
                f"Alpha Vantage HTTP {response.status_code}", response.status_code
            )
        payload = response.json()
        series = parse_alpha_series(payload)
        if function != "FX_DAILY" and isinstance(payload.get("unit"), str):
            unit, currency, divisor = normalize_commodity_unit(str(payload["unit"]))
    except (ValueError, FreeSourceError) as exc:
        state = (
            exc.state
            if isinstance(exc, FreeSourceError) and exc.state is not None
            else AccessState.TEMPORARILY_FAILED
        )
        await record_access(
            pool,
            "alpha_vantage",
            endpoint,
            AccessResult(state, "series fetch failed"),
            status_code=exc.status_code if isinstance(exc, FreeSourceError) else None,
        )
        raise
    url = f"https://www.alphavantage.co/query?function={function}"
    stored = 0
    async with pool.acquire() as conn, conn.transaction():
        for day, value in series:
            before = await conn.fetchval(
                """select count(*) from public.company_research_observation
                   where ticker=$1 and provider='alpha_vantage' and dataset='exposure_context'
                     and metric=$2 and period_end=$3""",
                ticker,
                series_name,
                day,
            )
            await store_research_observation(
                conn,
                ticker=ticker,
                provider="alpha_vantage",
                dataset="exposure_context",
                metric=series_name,
                period_end=day,
                value=value / divisor,
                unit=unit,
                currency=currency,
                source_url=url,
                match_confidence=Decimal(1),
                raw={
                    "function": function,
                    "provider_series_name": series_name,
                    "provider_value": str(value),
                    "provider_unit": payload.get("unit") if function != "FX_DAILY" else unit,
                },
            )
            after = await conn.fetchval(
                """select count(*) from public.company_research_observation
                   where ticker=$1 and provider='alpha_vantage' and dataset='exposure_context'
                     and metric=$2 and period_end=$3""",
                ticker,
                series_name,
                day,
            )
            stored += int(after) - int(before)
    await record_access(
        pool,
        "alpha_vantage",
        endpoint,
        AccessResult(AccessState.AVAILABLE, "exposure-linked series retrieved"),
        credits=1,
    )
    return stored


def parse_pdl_company(
    payload: dict[str, object], expected_domain: str
) -> tuple[Decimal, dict[str, object]]:
    likelihood = payload.get("likelihood")
    try:
        confidence = Decimal(str(likelihood)) / Decimal(10)
    except Exception as exc:
        raise FreeSourceError("PDL company response lacks a match likelihood") from exc
    website = str(payload.get("website") or "").lower().removeprefix("www.").rstrip("/")
    expected = expected_domain.lower().removeprefix("www.").rstrip("/")
    if website and website != expected:
        raise FreeSourceError("PDL matched a different company website")
    return max(Decimal(0), min(Decimal(1), confidence)), payload


async def pdl_skip_reason(
    pool: asyncpg.Pool, ticker: str, *, now: dt.datetime | None = None
) -> str | None:
    """Why a PDL lookup should not be spent on this holding right now, or None to proceed.

    PDL's allowance is tiny (100 per month), so it is used only to fill a gap and at most once
    per cooldown: a match is reused for 30 days, a 'no match' for 14, a failed call for one."""
    now = now or dt.datetime.now(dt.UTC)
    log = await pool.fetchrow(
        "select outcome, looked_up_at from public.research_lookup_log"
        " where provider='people_data_labs' and lookup_key=$1",
        ticker,
    )
    if log is not None:
        cooldown = {
            "matched": PDL_MATCH_COOLDOWN,
            "no_match": PDL_NO_MATCH_COOLDOWN,
            "failed": PDL_FAILURE_COOLDOWN,
            "refunded": PDL_FAILURE_COOLDOWN,
        }[log["outcome"]]
        if now - log["looked_up_at"] < cooldown:
            return f"last PDL lookup ({log['outcome']}) is inside its cooldown"
    identity = await pool.fetchrow(
        "select issuer_lei from public.instrument where t212_ticker=$1", ticker
    )
    gaps = await pool.fetch(
        """select distinct metric from public.company_research_observation
           where ticker=$1 and metric in ('employee_count','total_funding_raised')
             and known_at > $2""",
        ticker,
        now - dt.timedelta(days=400),
    )
    have = {r["metric"] for r in gaps}
    identity_unresolved = identity is None or not identity["issuer_lei"]
    if not identity_unresolved and {"employee_count", "total_funding_raised"} <= have:
        return "identity, headcount and funding are already resolved; PDL would add nothing"
    return None


async def _log_lookup(pool: asyncpg.Pool, ticker: str, outcome: str) -> None:
    await pool.execute(
        """insert into public.research_lookup_log(provider,lookup_key,looked_up_at,outcome)
           values('people_data_labs',$1,now(),$2)
           on conflict(provider,lookup_key) do update set
             looked_up_at=excluded.looked_up_at,outcome=excluded.outcome""",
        ticker,
        outcome,
    )


async def collect_pdl_company(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    *,
    ticker: str,
    domain: str,
    api_key: str,
    force: bool = False,
) -> int:
    await _gate(pool, "people_data_labs", "company_enrich")
    if not force:
        reason = await pdl_skip_reason(pool, ticker)
        if reason is not None:
            return 0
    clean_domain = (urlsplit(domain).hostname or domain).lower().removeprefix("www.")
    if not _DOMAIN_RE.match(clean_domain):
        raise ValueError("PDL lookups need a plain company domain")
    budget = Budget(monthly=Decimal(100))
    reservation = await reserve_group(
        pool, [CreditPart("people_data_labs", "company_enrich", Decimal(1), budget)]
    )
    try:
        response = await http.get(
            "https://api.peopledatalabs.com/v5/company/enrich",
            params={"website": clean_domain, "min_likelihood": 6, "pretty": "false"},
            headers={"X-API-Key": api_key},
        )
    except httpx.HTTPError:
        await refund(pool, reservation)  # never reached PDL: do not spend the monthly credit
        await _log_lookup(pool, ticker, "refunded")
        await record_access(
            pool,
            "people_data_labs",
            "company_enrich",
            AccessResult(AccessState.TEMPORARILY_FAILED, "network error"),
        )
        raise
    if response.status_code == 404:
        # PDL bills only matches; a 404 is free, so give the reserved credit back.
        await refund(pool, reservation)
        await _log_lookup(pool, ticker, "no_match")
        await record_access(
            pool,
            "people_data_labs",
            "company_enrich",
            AccessResult(AccessState.NOT_COVERED, "no company match"),
            status_code=404,
        )
        return 0
    if response.status_code != 200:
        if response.status_code >= 500 or response.status_code == 429:
            await refund(pool, reservation)  # PDL does not bill failed calls
        await _log_lookup(pool, ticker, "failed")
        await _record_http_failure(
            pool, "people_data_labs", "company_enrich", response, "PDL enrichment request failed"
        )
        raise FreeSourceError(f"PDL enrichment HTTP {response.status_code}", response.status_code)
    payload = response.json()
    if not isinstance(payload, dict):
        await _log_lookup(pool, ticker, "failed")
        raise FreeSourceError("PDL company enrichment schema changed")
    try:
        confidence, _ = parse_pdl_company(payload, clean_domain)
    except FreeSourceError:
        await _log_lookup(pool, ticker, "no_match")
        raise
    if confidence < Decimal("0.6"):
        await _log_lookup(pool, ticker, "no_match")
        raise FreeSourceError("PDL company match confidence is below the publication threshold")
    await provider_gate.note_success(pool, "people_data_labs", "company_enrich")
    source = "https://api.peopledatalabs.com/v5/company/enrich"
    # Keep only business-level matching and workforce/funding fields; discard any
    # unrequested person-level or contact fields if the response schema expands.
    safe_raw = {
        key: payload[key]
        for key in (
            "id",
            "name",
            "website",
            "likelihood",
            "employee_count",
            "employee_count_range",
            "total_funding_raised",
            "latest_funding_stage",
            "founded",
            "industry",
        )
        if key in payload
    }
    async with pool.acquire() as conn, conn.transaction():
        for key in ("employee_count", "total_funding_raised"):
            value = payload.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                await store_research_observation(
                    conn,
                    ticker=ticker,
                    provider="pdl",
                    dataset="company_enrichment",
                    metric=key,
                    value=Decimal(str(value)),
                    unit="people" if key == "employee_count" else "USD",
                    currency="USD" if key == "total_funding_raised" else None,
                    source_url=source,
                    match_confidence=confidence,
                    raw=safe_raw,
                )
        for key in ("employee_count_range", "latest_funding_stage", "name"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                await store_research_observation(
                    conn,
                    ticker=ticker,
                    provider="pdl",
                    dataset="company_enrichment",
                    metric=key,
                    text_value=value,
                    source_url=source,
                    match_confidence=confidence,
                    raw=safe_raw,
                )
    await _log_lookup(pool, ticker, "matched")
    await record_access(
        pool,
        "people_data_labs",
        "company_enrich",
        AccessResult(AccessState.AVAILABLE, "company match returned"),
        credits=1,
    )
    return 1


async def collect_diffbot_candidates(
    pool: asyncpg.Pool, http: httpx.AsyncClient, *, ticker: str, domain: str, api_key: str
) -> int:
    await _gate(pool, "diffbot", "organization")
    clean_domain = (urlsplit(domain).hostname or domain).lower().removeprefix("www.").rstrip("/")
    if not _DOMAIN_RE.match(clean_domain):
        raise ValueError("Diffbot lookups need a plain company domain")
    # DQL exports are budgeted by entity; cap this one-holding lookup to two KG entities.
    reservation = await reserve_group(
        pool,
        [CreditPart("diffbot", "organization", Decimal(50), Budget(monthly=Decimal(10_000)))],
    )
    query = f'type:Organization homepageUri:"{clean_domain}"'
    fields = "$.id;$.name;$.homepageUri;$.nbEmployees;" + ";".join(
        f"${'.' + f}"
        for f in (
            "parentCompany",
            "subsidiaries",
            "acquisitions",
            "acquiredBy",
            "investments",
            "investors",
            "competitors",
            "partnerships",
            "customers",
            "suppliers",
            "products",
            "ceo",
        )
    )
    try:
        response = await http.post(
            "https://kg.diffbot.com/kg/v3/dql",
            params={"token": api_key},
            json={"query": query, "size": 2, "filter": fields},
        )
    except httpx.HTTPError:
        await refund(pool, reservation)
        await record_access(
            pool,
            "diffbot",
            "organization",
            AccessResult(AccessState.TEMPORARILY_FAILED, "network error"),
        )
        raise
    if response.status_code != 200:
        if response.status_code >= 500:
            await refund(pool, reservation)
        await _record_http_failure(
            pool, "diffbot", "organization", response, "Diffbot KG query failed"
        )
        raise FreeSourceError(f"Diffbot KG HTTP {response.status_code}", response.status_code)
    await provider_gate.note_success(pool, "diffbot", "organization")
    payload = response.json()
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise FreeSourceError("Diffbot KG response schema changed")
    stored = 0
    dql_url = "https://kg.diffbot.com/kg/v3/dql"
    for row in rows[:2]:
        entity = row.get("entity") if isinstance(row, dict) else None
        if not isinstance(entity, dict) or not entity.get("id") or not entity.get("name"):
            continue
        homepage_uri = str(entity.get("homepageUri") or "")
        homepage = (urlsplit(homepage_uri).hostname or homepage_uri).lower()
        homepage = homepage.removeprefix("www.").rstrip("/")
        if homepage and homepage != clean_domain:
            continue
        await store_research_observation(
            pool,
            ticker=ticker,
            provider="diffbot",
            dataset="organization_match",
            metric="entity_name",
            text_value=str(entity["name"]),
            source_url=dql_url,
            match_confidence=Decimal("0.8"),
            raw={k: entity[k] for k in ("id", "name", "homepageUri", "nbEmployees") if k in entity},
        )
        for rel in parse_diffbot_relations(entity)[:200]:
            await store_company_edge(
                pool,
                subject_key=ticker,
                object_key=rel.object_key,
                object_name=rel.object_name,
                edge_type=rel.edge_type,
                direction=rel.direction,
                source="diffbot",
                source_url=f"{dql_url}#{entity['id']}/{rel.provider_field}",
                status="suggested",
                valid_from=rel.valid_from,
                valid_to=rel.valid_to,
                detail=rel.detail,
            )
            stored += 1
    await record_access(
        pool,
        "diffbot",
        "organization",
        AccessResult(AccessState.AVAILABLE, "KG candidates fetched"),
        credits=50,
    )
    return stored


# --------------------------------------------------------------------------------------- EPO


def parse_ops_patent_xml(
    xml_text: str, applicant_aliases: tuple[str, ...]
) -> list[dict[str, object]]:
    """Patent families (one per OPS family-id) whose applicants include an alias.

    Kept as the dict-shaped view of `graph.ops.parse_ops_page` for callers and tests that want
    plain values; `family_key` is the OPS family id."""
    page = parse_ops_page(xml_text, applicant_aliases)
    out: list[dict[str, object]] = []
    for family in page.families:
        out.append(_family_dict(family))
    return out


def _family_dict(family: OpsFamily) -> dict[str, object]:
    return {
        "family_key": family.family_id,
        "priority_date": family.priority_date,
        "title": family.title,
        "cpc_codes": sorted(family.cpc_codes),
        "applicant": family.applicants[0] if family.applicants else "",
        "applicants": list(family.applicants),
        "jurisdictions": family.jurisdictions,
        "backward_citations": len(family.cited),
        "first_publication_date": family.first_publication_date,
        "grant_date": family.grant_date,
        "publications": [(p.country, p.doc_number, p.kind, p.date) for p in family.publications],
        "raw": {
            "ops_family_id": family.family_id,
            "forward_citations": "not provided by OPS bibliographic data",
        },
    }


async def patent_search_aliases(
    pool: asyncpg.Pool, ticker: str, base_aliases: tuple[str, ...]
) -> tuple[tuple[str, ...], set[str]]:
    """(all search aliases, normalised subsidiary names).

    Aliases are the profile's names, the resolved legal/other names and every confirmed or
    GLEIF-reported subsidiary, so a family filed by a subsidiary is found too."""
    rows = await pool.fetch(
        "select alias, kind from public.entity_alias where ticker=$1"
        " and kind in ('legal_name','other_name','alias','subsidiary')",
        ticker,
    )
    edges = await pool.fetch(
        "select object_name from public.company_graph_edge where subject_key=$1"
        " and edge_type='subsidiary' and status='confirmed'",
        ticker,
    )
    subsidiaries = {normalize_object_key(r["alias"]) for r in rows if r["kind"] == "subsidiary"}
    subsidiaries |= {normalize_object_key(r["object_name"]) for r in edges}
    names = [
        *base_aliases,
        *(r["alias"] for r in rows if r["kind"] != "listing_ticker"),
        *(r["object_name"] for r in edges),
    ]
    unique = tuple(dict.fromkeys(n.strip() for n in names if n and n.strip()))
    return unique, subsidiaries


async def collect_epo_patents(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    *,
    ticker: str,
    applicant_aliases: tuple[str, ...],
    client_id: str,
    client_secret: str,
    legal_lookups: int = 10,
    today: dt.date | None = None,
) -> int:
    await _gate(pool, "epo_ops", "patent_search")
    if not applicant_aliases:
        raise ValueError("at least one resolved applicant alias is required")
    today = today or dt.datetime.now(dt.UTC).date()
    aliases, subsidiaries = await patent_search_aliases(pool, ticker, applicant_aliases)
    own_names = {normalize_object_key(a) for a in applicant_aliases} - subsidiaries
    governor = PostgresRequestGovernor(pool)
    budget = Budget(per_minute=Decimal(20), monthly=Decimal(500))

    async def reserve() -> None:
        await governor.reserve("epo_ops", "patent_search", Decimal(1), budget)

    await reserve()  # the OAuth call and the first page share one credit
    try:
        token = await fetch_token(http, client_id, client_secret)
    except EpoAuthError as exc:
        # Typed: bad credentials, a quota/throttle denial and an outage are different states.
        if exc.kind == "forbidden" or exc.kind == "invalid_credentials":
            result = await provider_gate.note_auth_failure(
                pool, "epo_ops", "patent_search", exc.status_code or 403
            )
            if exc.kind == "invalid_credentials" and result.state == AccessState.TEMPORARILY_FAILED:
                # Rejected credentials are not transient, but one 400 may still be a typo that
                # was just fixed: keep the cooldown and block on the repeat.
                pass
        else:
            result = AccessResult(exc.state or AccessState.TEMPORARILY_FAILED, str(exc))
        await record_access(pool, "epo_ops", "patent_search", result, status_code=exc.status_code)
        raise
    try:
        search = await search_applicants(http, token, aliases, reserve_request=reserve)
    except FreeSourceError as exc:
        if exc.status_code in (401, 403):
            result = await provider_gate.note_auth_failure(
                pool, "epo_ops", "patent_search", exc.status_code
            )
        else:
            result = AccessResult(AccessState.TEMPORARILY_FAILED, "OPS search failed")
        await record_access(pool, "epo_ops", "patent_search", result, status_code=exc.status_code)
        raise
    except httpx.HTTPError:
        await record_access(
            pool,
            "epo_ops",
            "patent_search",
            AccessResult(AccessState.TEMPORARILY_FAILED, "network error"),
        )
        raise
    await provider_gate.note_success(pool, "epo_ops", "patent_search")
    legal: dict[str, list[LegalEvent]] = {}
    lookups = 0
    for family in search.families.values():
        if lookups >= legal_lookups or family.grant_date is None:
            continue
        representative = next((p for p in family.publications if p.kind[:1] in {"B", "C"}), None)
        if representative is None:
            continue
        try:
            await reserve()
            lookups += 1
            response = await http.get(
                "https://ops.epo.org/3.2/rest-services/legal/publication/docdb/"
                f"{representative.country}.{representative.doc_number}.{representative.kind}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/xml"},
            )
            if response.status_code == 200:
                events = parse_ops_legal(response.text).get(family.family_id, [])
                legal[family.family_id] = list(events)
        except (QuotaExceeded, httpx.HTTPError, FreeSourceError):
            break  # legal status is a refinement: keep what the biblio data proves
    stored = 0
    for family in search.families.values():
        events = legal.get(family.family_id, [])
        status, expiry = derive_legal_status(family, events, today=today)
        matched = [
            a
            for a in family.applicants
            if normalize_object_key(a) in {normalize_object_key(x) for x in aliases}
        ]
        applicant = matched[0] if matched else (family.applicants[0] if family.applicants else "")
        applicant_key = normalize_object_key(applicant) if applicant else ""
        basis, confidence, evidence_id, valid_from = "suggested", Decimal("0.5"), None, None
        if applicant_key in own_names and applicant_key not in subsidiaries:
            evidence_id = await record_evidence(
                pool,
                regulator="epo_ops",
                external_id=f"family:{family.family_id}",
                url=f"https://worldwide.espacenet.com/patent/search/family/{family.family_id}/",
                document_type="patent_family",
                content=None,
                quote=f"applicant: {applicant}",
            )
            basis, confidence = "direct", Decimal("0.8")
        elif applicant_key in subsidiaries and family.priority_date is not None:
            path = await find_subsidiary_path(
                pool, ticker=ticker, applicant=applicant, priority_date=family.priority_date
            )
            if path is not None:
                basis, confidence, valid_from = (
                    "dated_subsidiary_path",
                    Decimal("0.7"),
                    path["valid_from"],
                )
        await store_patent_family(
            pool,
            family_key=family.family_id,
            priority_date=family.priority_date,
            title=family.title,
            cpc_codes=sorted(family.cpc_codes),
            jurisdictions=family.jurisdictions,
            forward_citations=None,  # OPS bibliographic data cannot supply it
            backward_citations=len(family.cited),
            legal_status=status,
            source="epo_ops",
            source_url=f"https://worldwide.espacenet.com/patent/search/family/{family.family_id}/",
            ticker=ticker,
            applicant=applicant,
            ownership_confidence=confidence,
            ownership_basis=basis,  # type: ignore[arg-type]
            ownership_valid_from=valid_from,
            evidence_id=evidence_id,
            raw={
                "ops_family_id": family.family_id,
                "forward_citations": "not provided by OPS bibliographic data",
                "applicants": family.applicants,
            },
            family_id_source="ops_family_id",
            first_publication_date=family.first_publication_date,
            grant_date=family.grant_date,
            expiry_date=expiry,
            legal_events=events,
            publications=[(p.country, p.doc_number, p.kind, p.date) for p in family.publications],
        )
        stored += 1
    detail = f"{stored} families over {search.pages} pages"
    if search.truncated:
        detail += f" (stopped: {search.stopped_reason})"
    await record_access(
        pool,
        "epo_ops",
        "patent_search",
        AccessResult(AccessState.AVAILABLE, detail),
        credits=max(search.pages, 1),
    )
    return stored
