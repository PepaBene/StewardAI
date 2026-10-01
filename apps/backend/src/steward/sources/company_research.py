"""Opt-in research enrichment for company exposure, identity clues and patents."""

from __future__ import annotations

import datetime as dt
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from decimal import Decimal
from typing import cast
from urllib.parse import urlsplit

import asyncpg
import httpx

from steward.free_sources import store_company_edge, store_patent_family
from steward.sources.base import AccessResult, AccessState
from steward.sources.free_market import FreeSourceError, parse_alpha_series
from steward.sources.governor import Budget, PostgresRequestGovernor
from steward.sources.store import record_access


async def _gate(pool: asyncpg.Pool, provider: str, endpoint: str) -> None:
    row = await pool.fetchrow(
        "select state,hard_blocked from public.provider_access where provider=$1 and endpoint=$2",
        provider,
        endpoint,
    )
    if row is None or row["state"] != AccessState.AVAILABLE.value or row["hard_blocked"]:
        raise RuntimeError(f"{provider} {endpoint} endpoint is not verified as free and available")


async def store_research_observation(
    pool: asyncpg.Pool,
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
    return cast(
        int,
        await pool.fetchval(
            """insert into public.company_research_observation
           (ticker,provider,dataset,metric,period_end,value,text_value,unit,currency,source_url,
            published_at,known_at,match_confidence,raw)
           values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,now(),$12,$13::jsonb) returning id""",
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
        ),
    )


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
    governor = PostgresRequestGovernor(pool)
    budget = Budget(daily=Decimal(20))
    await governor.reserve("alpha_vantage", "account_free", Decimal(1), budget)
    await governor.reserve("alpha_vantage", endpoint, Decimal(1), budget)
    params: dict[str, str] = {"function": function, "apikey": api_key}
    if function == "FX_DAILY":
        if not from_currency or not to_currency:
            raise ValueError("FX_DAILY requires from_currency and to_currency")
        params.update(from_symbol=from_currency, to_symbol=to_currency, outputsize="compact")
        currency = to_currency
        unit = f"{from_currency}/{to_currency}"
    else:
        params["interval"] = "daily"
        currency = None
        unit = "provider_reported"
    try:
        response = await http.get("https://www.alphavantage.co/query", params=params)
        if response.status_code != 200:
            raise FreeSourceError(
                f"Alpha Vantage HTTP {response.status_code}", response.status_code
            )
        payload = response.json()
        series = parse_alpha_series(payload)
        if function != "FX_DAILY" and isinstance(payload.get("unit"), str):
            unit = str(payload["unit"])
            currency = "USD"
    except (httpx.HTTPError, ValueError, FreeSourceError):
        await record_access(
            pool,
            "alpha_vantage",
            endpoint,
            AccessResult(AccessState.TEMPORARILY_FAILED, "series fetch failed"),
        )
        raise
    url = f"https://www.alphavantage.co/query?function={function}"
    async with pool.acquire() as conn, conn.transaction():
        for day, value in series:
            await conn.execute(
                """insert into public.company_research_observation
                   (ticker,provider,dataset,metric,period_end,value,unit,currency,source_url,
                    known_at,match_confidence,raw)
                   values($1,'alpha_vantage','exposure_context',$2,$3,$4,$5,$6,$7,now(),1,$8::jsonb)""",
                ticker,
                series_name,
                day,
                value,
                unit,
                currency,
                url,
                json.dumps({"function": function, "provider_series_name": series_name}),
            )
    await record_access(
        pool,
        "alpha_vantage",
        endpoint,
        AccessResult(AccessState.AVAILABLE, "exposure-linked series retrieved"),
        credits=1,
    )
    return len(series)


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


async def collect_pdl_company(
    pool: asyncpg.Pool, http: httpx.AsyncClient, *, ticker: str, domain: str, api_key: str
) -> int:
    await _gate(pool, "people_data_labs", "company_enrich")
    governor = PostgresRequestGovernor(pool)
    await governor.reserve(
        "people_data_labs", "company_enrich", Decimal(1), Budget(monthly=Decimal(100))
    )
    response = await http.get(
        "https://api.peopledatalabs.com/v5/company/enrich",
        params={"website": domain, "min_likelihood": 6, "pretty": "false"},
        headers={"X-API-Key": api_key},
    )
    if response.status_code == 404:
        await record_access(
            pool,
            "people_data_labs",
            "company_enrich",
            AccessResult(AccessState.NOT_COVERED, "no company match"),
            status_code=404,
        )
        return 0
    if response.status_code != 200:
        state = (
            AccessState.ACCESS_UNVERIFIED
            if response.status_code in {401, 403}
            else AccessState.TEMPORARILY_FAILED
        )
        await record_access(
            pool,
            "people_data_labs",
            "company_enrich",
            AccessResult(state, "PDL enrichment request failed"),
            status_code=response.status_code,
        )
        if state == AccessState.ACCESS_UNVERIFIED:
            await governor.hard_block("people_data_labs", "company_enrich")
        raise FreeSourceError(f"PDL enrichment HTTP {response.status_code}", response.status_code)
    payload = response.json()
    if not isinstance(payload, dict):
        raise FreeSourceError("PDL company enrichment schema changed")
    confidence, _ = parse_pdl_company(payload, domain)
    if confidence < Decimal("0.6"):
        raise FreeSourceError("PDL company match confidence is below the publication threshold")
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
    governor = PostgresRequestGovernor(pool)
    # DQL exports are budgeted by entity; cap this one-holding lookup to two KG entities.
    await governor.reserve("diffbot", "organization", Decimal(50), Budget(monthly=Decimal(10_000)))
    query = f'type:Organization homepageUri:"{domain}"'
    response = await http.post(
        "https://kg.diffbot.com/kg/v3/dql",
        params={"token": api_key},
        json={
            "query": query,
            "size": 2,
            "filter": ("$.id;$.name;$.homepageUri;$.nbEmployees;$.relationships"),
        },
    )
    if response.status_code != 200:
        state = (
            AccessState.ACCESS_UNVERIFIED
            if response.status_code in {401, 403}
            else AccessState.TEMPORARILY_FAILED
        )
        await record_access(
            pool,
            "diffbot",
            "organization",
            AccessResult(state, "Diffbot KG query failed"),
            status_code=response.status_code,
        )
        if state == AccessState.ACCESS_UNVERIFIED:
            await governor.hard_block("diffbot", "organization")
        raise FreeSourceError(f"Diffbot KG HTTP {response.status_code}", response.status_code)
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
        expected_domain = (urlsplit(domain).hostname or domain).lower()
        expected_domain = expected_domain.removeprefix("www.").rstrip("/")
        if homepage and homepage != expected_domain:
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
            raw=entity,
        )
        rels = entity.get("relationships")
        if isinstance(rels, list):
            for rel in rels[:100]:
                if not isinstance(rel, dict):
                    continue
                target = rel.get("object") or rel.get("target") or rel.get("entity")
                if not isinstance(target, dict) or not target.get("name"):
                    continue
                raw_type = str(rel.get("type") or rel.get("relationship") or "").casefold()
                edge_type = next(
                    (
                        value
                        for key, value in (
                            ("compet", "competitor"),
                            ("acqui", "acquired"),
                            ("partner", "partner"),
                            ("product", "product"),
                            ("executive", "executive"),
                            ("invest", "investment"),
                            ("parent", "parent"),
                        )
                        if key in raw_type
                    ),
                    None,
                )
                if edge_type:
                    await store_company_edge(
                        pool,
                        subject_key=ticker,
                        object_key=str(target.get("id") or target["name"]),
                        object_name=str(target["name"]),
                        edge_type=edge_type,
                        direction="outgoing",
                        source="diffbot",
                        source_url=str(entity.get("homepageUri") or dql_url),
                        evidence_tier="vendor",
                        status="suggested",
                        detail={"provider_relation": rel},
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


def _xml_children(doc: ET.Element, name: str) -> list[ET.Element]:
    return [node for node in doc.iter() if node.tag.rsplit("}", 1)[-1] == name]


def parse_ops_patent_xml(
    xml_text: str, applicant_aliases: tuple[str, ...]
) -> list[dict[str, object]]:
    if len(xml_text) > 5_000_000:
        raise FreeSourceError("EPO OPS response exceeds the 5 MB parse limit")
    try:
        root = ET.fromstring(xml_text)  # noqa: S314 - Expat disables external entity expansion.
    except ET.ParseError as exc:
        raise FreeSourceError("EPO OPS response was not valid XML") from exc
    aliases = {re.sub(r"[^a-z0-9]", "", x.casefold()) for x in applicant_aliases}
    results: dict[str, dict[str, object]] = {}
    for doc in root.iter():
        if doc.tag.rsplit("}", 1)[-1] != "exchange-document":
            continue

        applicant_names = [
            " ".join((n.text or "").split()) for n in _xml_children(doc, "applicant-name")
        ]
        if not any(
            re.sub(r"[^a-z0-9]", "", name.casefold()) in aliases for name in applicant_names
        ):
            continue
        publication = next(
            (
                n.attrib.get("doc-number")
                for n in _xml_children(doc, "document-id")
                if n.attrib.get("document-id-type") == "docdb"
            ),
            None,
        )
        if not publication:
            publication = doc.attrib.get("doc-number")
        if not publication:
            continue
        titles = [
            " ".join((n.text or "").split())
            for n in _xml_children(doc, "invention-title")
            if n.text
        ]
        priority = next(
            (
                n.attrib.get("date")
                for n in _xml_children(doc, "priority-claim")
                if n.attrib.get("date")
            ),
            None,
        )
        cpcs = sorted(
            {
                "".join((n.text or "").split())
                for n in _xml_children(doc, "classification-symbol")
                if n.text
            }
        )
        try:
            priority_date = dt.datetime.strptime(priority, "%Y%m%d").date() if priority else None
        except ValueError:
            priority_date = None
        results.setdefault(
            str(publication),
            {
                "family_key": str(publication),
                "priority_date": priority_date,
                "title": titles[0] if titles else None,
                "cpc_codes": cpcs,
                "applicant": applicant_names[0],
                "raw": {"publication": publication},
            },
        )
    return list(results.values())


async def collect_epo_patents(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    *,
    ticker: str,
    applicant_aliases: tuple[str, ...],
    client_id: str,
    client_secret: str,
) -> int:
    await _gate(pool, "epo_ops", "patent_search")
    governor = PostgresRequestGovernor(pool)
    await governor.reserve(
        "epo_ops", "patent_search", Decimal(1), Budget(per_minute=Decimal(1), monthly=Decimal(100))
    )
    token_response = await http.post(
        "https://ops.epo.org/3.2/auth/accesstoken",
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if token_response.status_code != 200:
        raise FreeSourceError(
            f"EPO OPS OAuth HTTP {token_response.status_code}", token_response.status_code
        )
    token = token_response.json().get("access_token")
    if not token:
        raise FreeSourceError("EPO OPS OAuth response omitted access token")
    if not applicant_aliases:
        raise ValueError("at least one resolved applicant alias is required")
    cql = f'pa="{applicant_aliases[0].replace(chr(34), "")}"'
    response = await http.get(
        "https://ops.epo.org/3.2/rest-services/published-data/search/biblio",
        params={"q": cql},
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/exchange+xml",
            "Range": "1-25",
        },
    )
    if response.status_code != 200:
        state = (
            AccessState.ACCESS_UNVERIFIED
            if response.status_code in {401, 403}
            else AccessState.TEMPORARILY_FAILED
        )
        await record_access(
            pool,
            "epo_ops",
            "patent_search",
            AccessResult(state, "OPS search failed"),
            status_code=response.status_code,
        )
        if state == AccessState.ACCESS_UNVERIFIED:
            await governor.hard_block("epo_ops", "patent_search")
        raise FreeSourceError(f"EPO OPS search HTTP {response.status_code}", response.status_code)
    patents = parse_ops_patent_xml(response.text, applicant_aliases)
    for item in patents:
        await store_patent_family(
            pool,
            family_key=str(item["family_key"]),
            priority_date=cast("dt.date | None", item["priority_date"]),
            title=cast("str | None", item["title"]),
            cpc_codes=list(cast("Iterable[str]", item["cpc_codes"])),
            jurisdictions=[],
            forward_citations=0,
            backward_citations=0,
            legal_status="unknown",
            source="epo_ops",
            source_url="https://worldwide.espacenet.com/",
            ticker=ticker,
            applicant=str(item["applicant"]),
            ownership_confidence=Decimal("0.5"),
            ownership_basis="suggested",
            raw=cast("dict[str, object] | None", item["raw"]),
        )
    await record_access(
        pool,
        "epo_ops",
        "patent_search",
        AccessResult(AccessState.AVAILABLE, "applicant-filtered family candidates fetched"),
        credits=1,
        response_bytes=len(response.content),
    )
    return len(patents)
