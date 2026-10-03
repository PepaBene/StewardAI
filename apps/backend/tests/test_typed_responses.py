"""Response models of the routes that used to publish no response schema.

Every route here returned a plain dict through `response_model=None`, so OpenAPI typed its body
as `{}` and the web client had to hand-parse it.  Each now declares its body with
`responses={200: {"model": ...}}` (see `api.typed_responses`).  That documents the body without
putting a pydantic round trip in front of it, so the JSON on the wire is exactly what the handler
returned before: same keys, same order, `Decimal` as a JSON number, `datetime` as `+00:00` ISO.

Each case checks, on seeded data, that

* the body validates against the declared model and the model has no field the body lacks nor the
  reverse (`assert_covers`), so a field added to a handler without the model fails here;
* the bytes on the wire equal what FastAPI produced for the handler's raw return value before the
  change (`legacy_bytes`: the handler is called directly and encoded with `jsonable_encoder`).
"""

from __future__ import annotations

import datetime as dt
import inspect
import os
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import TypeAdapter

from api import typed_responses as tr
from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool, get_pool_or_error
from api.main import create_app
from steward.config.holding_profiles import Exposure, HoldingProfile, HoldingProfilesConfig
from steward.config.store import save_config
from steward.config.thesis import ThesesConfig, ThesisConfig, ThesisKPI
from tests.test_fund_stream_db import evidence, insert_fact
from tests.test_prices_stream import (
    confirmed_listing,
    firds_and_figi,
    instrument,
    put_bars,
    snapshot,
    weekdays,
)

pytestmark = pytest.mark.db

OWNER = "7f0c3d4e-0000-4000-8000-0000000000aa"
CAPTURE_DIR = os.environ.get("STEWARD_CAPTURE_DIR")
ISO_NOW = re.compile(r'"20\d\d-\d\d-\d\dT[0-9:.]+(\+00:00|Z)"')


@pytest.fixture
async def api(backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[get_pool_or_error] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@dataclass
class Case:
    name: str
    path: str
    model: Any
    args: dict[str, Any] = field(default_factory=dict)  # python-typed query values
    template: str | None = None  # route path with `{param}` markers when it differs from `path`
    encoder: Any = None  # legacy encoder: None = jsonable_encoder (response_model=None routes)
    allow_extra: bool = False  # the body may hold keys the model does not name (free-form)


def _http_params(args: dict[str, Any]) -> dict[str, str]:
    return {
        k: v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else str(v)
        for k, v in args.items()
    }


def _route(api: httpx.AsyncClient, case: Case) -> Any:
    app = api._transport.app  # type: ignore[attr-defined]
    template = case.template or case.path
    routers = [getattr(r, "original_router", None) for r in app.routes]
    for route in [*app.routes, *(x for router in routers if router for x in router.routes)]:
        if getattr(route, "path", None) == template and "GET" in getattr(route, "methods", ()):
            return route
    raise AssertionError(f"no GET route {template}")


async def legacy_bytes(
    api: httpx.AsyncClient, pool: asyncpg.Pool, case: Case, path_args: dict[str, str]
) -> bytes:
    """What the route wrote before it was typed: the handler's return value, encoded."""
    route = _route(api, case)
    kwargs: dict[str, Any] = {}
    for name in inspect.signature(route.endpoint).parameters:
        if name == "pool":
            kwargs[name] = pool
        elif name in {"_owner", "owner"}:
            kwargs[name] = AuthenticatedUser(OWNER, None, "aal2")
        elif name in path_args:
            kwargs[name] = path_args[name]
        elif name in case.args:
            kwargs[name] = case.args[name]
    raw = await route.endpoint(**kwargs)
    if case.encoder is not None:
        return bytes(JSONResponse(case.encoder(raw)).body)
    return bytes(JSONResponse(jsonable_encoder(raw)).body)


def assert_covers(model: Any, body: Any, where: str, *, allow_extra: bool = False) -> None:
    """The model names every key the body has (and, for models, none the body lacks), recursively
    through nested models, lists and dicts of models."""
    adapter = TypeAdapter(model)
    adapter.validate_python(body)
    _walk(adapter.core_schema, body, where, allow_extra)


def _walk(schema: Any, value: Any, where: str, allow_extra: bool) -> None:
    kind = schema.get("type")
    if kind == "definitions":
        # Resolve references against the definitions list.
        _DEFS.update({d["ref"]: d for d in schema["definitions"]})
        return _walk(schema["schema"], value, where, allow_extra)
    if kind == "definition-ref":
        return _walk(_DEFS[schema["schema_ref"]], value, where, allow_extra)
    if kind == "nullable":
        if value is None:
            return
        return _walk(schema["schema"], value, where, allow_extra)
    if kind == "default" or kind == "function-after" or kind == "function-before":
        return _walk(schema["schema"], value, where, allow_extra)
    if kind == "model":
        return _walk(schema["schema"], value, where, allow_extra)
    if kind == "model-fields":
        assert isinstance(value, dict), where
        fields = schema["fields"]
        extra = set(value) - set(fields)
        missing = {
            key
            for key, item in fields.items()
            if key not in value and item["schema"]["type"] != "default"
        }
        assert not extra, f"{where}: body keys the model does not declare: {sorted(extra)}"
        assert not missing, f"{where}: model fields the body lacks: {sorted(missing)}"
        for key, item in fields.items():
            if key not in value:
                continue
            _walk(item["schema"], value[key], f"{where}.{key}", allow_extra)
        return
    if kind == "list" and "items_schema" in schema:
        assert isinstance(value, list), where
        for i, item in enumerate(value):
            _walk(schema["items_schema"], item, f"{where}[{i}]", allow_extra)
        return
    if kind == "dict" and "values_schema" in schema:
        assert isinstance(value, dict), where
        for key, item in value.items():
            _walk(schema["values_schema"], item, f"{where}[{key!r}]", allow_extra)
        return
    if kind == "union":
        errors: list[AssertionError] = []
        for choice in schema["choices"]:
            sub = choice[0] if isinstance(choice, tuple) else choice
            try:
                _walk(sub, value, where, allow_extra)
                return
            except (AssertionError, KeyError, TypeError) as exc:
                errors.append(AssertionError(str(exc)))
        raise AssertionError(f"{where}: no union member covers the body: {errors[:2]}")


_DEFS: dict[str, Any] = {}


async def check(
    api: httpx.AsyncClient,
    pool: asyncpg.Pool,
    case: Case,
    *,
    path_args: dict[str, str] | None = None,
) -> Any:
    response = await api.get(case.path, params=_http_params(case.args))
    assert response.status_code == 200, f"{case.name}: {response.status_code} {response.text}"
    body = response.json()
    if case.model is not None:
        assert_covers(case.model, body, case.name, allow_extra=case.allow_extra)
    expected = await legacy_bytes(api, pool, case, path_args or _path_args(case))
    assert response.content == expected, f"{case.name}: the wire changed"
    _capture(case, response.text)
    return body


async def sent(
    api: httpx.AsyncClient, method: str, path: str, model: Any, body: dict[str, Any]
) -> Any:
    """A write route: it succeeds and its acknowledgement matches the declared model."""
    response = await api.request(method, path, json=body)
    assert response.status_code == 200, f"{method} {path}: {response.status_code} {response.text}"
    assert_covers(model, response.json(), f"{method} {path}")
    return response.json()


def _capture(case: Case, text: str) -> None:
    """Dump a body for a before/after diff (set STEWARD_CAPTURE_DIR; timestamps are masked)."""
    if CAPTURE_DIR:
        Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9]+", "_", case.name).strip("_")
        Path(CAPTURE_DIR, f"{slug}.json").write_text(ISO_NOW.sub('"<ts>"', text), encoding="utf-8")


def _path_args(case: Case) -> dict[str, str]:
    if not case.template:
        return {}
    names = re.findall(r"\{(\w+)\}", case.template)
    parts = re.split(r"\{\w+\}", case.template)
    # Match the concrete path against the template to recover the parameter values.
    pattern = "^" + "(.+?)".join(re.escape(p) for p in parts) + "$"
    match = re.match(pattern, case.path)
    assert match, (case.template, case.path)
    return dict(zip(names, match.groups(), strict=True))


# ------------------------------------------------------------------------------------ seeds
async def seed_holding_aaa(pool: asyncpg.Pool, api: httpx.AsyncClient) -> int:
    """Statements, forward data, peers, IR, graph, patents and relationships of `AAA`."""
    evidence_id = await evidence(pool)
    kpi = ThesisKPI(id="growth", metric="revenue", operator=">=", threshold=1, unit="USD")
    async with pool.acquire() as conn:
        version_id = await save_config(
            conn,
            "theses",
            ThesesConfig(theses=[ThesisConfig(ticker="AAA", why_owned="x", kpis=[kpi])]),
            created_by=None,
        )
    for ticker, name in (("AAA", "Alpha Corp"), ("BBB", "Bravo Corp")):
        await pool.execute(
            "insert into instrument (t212_ticker, isin, name, short_name, currency, security_type)"
            " values ($1,$2,$3,$1,'USD','Common Stock')",
            ticker,
            "US" + ticker.ljust(9, "0") + "1",
            name,
        )
    await snapshot(pool, dt.date(2026, 9, 25), [("AAA", "USD"), ("BBB", "USD")])
    async with pool.acquire() as conn:
        await save_config(
            conn,
            "holding_profiles",
            HoldingProfilesConfig(
                profiles=[
                    HoldingProfile(
                        ticker="AAA",
                        exposures=[
                            Exposure(kind="revenue_currency", name="EUR", share=0.4),
                            Exposure(kind="commodity_input", name="Copper"),
                        ],
                    )
                ]
            ),
            created_by=None,
        )
    await pool.execute(
        "insert into public.company_research_observation (ticker, provider, dataset, metric,"
        " period_end, value, text_value, unit, currency, source_url, published_at, known_at,"
        " match_confidence, raw) values ('AAA','diffbot','org','employees','2025-12-31',1200,"
        " null,'persons',null,'https://x','2026-01-02','2026-01-03',0.9,'{\"k\": [1, 2]}'),"
        " ('AAA','pdl','org','industry',null,null,'software',null,null,'https://y',null,"
        "'2026-01-04',null,'{}'),"
        " ('AAA','alpha_vantage','exposure_context','COPPER','2026-08-31',9100.5,null,'USD/t',"
        "'USD','https://z','2026-09-02','2026-09-03',null,'{\"function\": \"COPPER\"}')"
    )
    # Statements: AAA twice (a restatement), two peers, one segment fact.
    await insert_fact(pool, "AAA", "us-gaap:Revenues", 200)
    await insert_fact(pool, "AAA", "us-gaap:GrossProfit", 100)
    await insert_fact(
        pool,
        "AAA",
        "us-gaap:Revenues",
        120,
        dims={"members": [{"axis": "Seg", "member": "co:A"}]},
        validation={"reconciled": True, "checks": [{"name": "sum"}]},
    )
    await insert_fact(pool, "AAA", "us-gaap:Revenues", 150, start=dt.date(2024, 1, 1),
                      end=dt.date(2024, 12, 31), validation={"reconciled": False})  # fmt: skip
    for peer, revenue, gross in (("BBB", 100, 30), ("CCC", 100, 60)):
        await insert_fact(pool, peer, "us-gaap:Revenues", revenue)
        await insert_fact(pool, peer, "us-gaap:GrossProfit", gross)
    reported = dt.datetime(2026, 7, 30, 20, 0, tzinfo=dt.UTC)
    for period, validation in (("FY2026Q2", "{}"), ("FY2026Q3", '{"reconciled": false}'),
                               ("FY2026Q4", '{"reconciled": true}')):  # fmt: skip
        await pool.execute(
            "insert into public.financial_statement_fact (ticker, concept, statement, line_item,"
            " period_start, period_end, fiscal_period, basis, value, unit, currency, dimensions,"
            " source, source_url, known_at, published, validation) values ('AAA',"
            " 'us-gaap:Revenues','income','revenue','2026-04-01','2026-06-30',$2,'US_GAAP',1050,"
            " 'USD','USD','{\"members\": []}'::jsonb,'test','https://x',$1,true,$3::jsonb)",
            reported,
            period,
            validation,
        )
        for vintage, age in (("2026-07-01", 30), ("2026-07-10", 5)):
            await pool.execute(
                "insert into public.forward_observation (ticker, kind, provider, metric,"
                " fiscal_period, currency, value, low, high, analyst_count, vintage, known_at,"
                " source_url, observation_key) values ('AAA','published_analyst_consensus','p',"
                "'revenue',$1,'USD',$4,null,null,7,$2,$3,'https://x', md5($1||$2))",
                period,
                vintage,
                reported - dt.timedelta(days=age),
                Decimal(1000) + age,
            )
    # (the FY2026Q3 consensus is in another currency: currency_mismatch)
    await pool.execute(
        "update public.forward_observation set currency='EUR' where fiscal_period='FY2026Q3'"
    )
    posted = await api.post(
        "/holdings/AAA/forward-data",
        json={
            "kind": "company_guidance", "metric": "revenue", "fiscal_period": "FY2027",
            "currency": "USD", "low": "1000.5", "high": "1100", "source_url": "https://example.test/g",
            "raw": {"note": "guided"},
        },
    )  # fmt: skip
    assert posted.status_code == 200, posted.text
    for peer, source in (("BBB", "finnhub"), ("BBB", "owner"), ("CCC", "owner")):
        response = await api.post(
            "/holdings/AAA/peer-set",
            json={"peer_ticker": peer, "basis": "owner review", "source": source,
                  "status": "confirmed", "evidence_id": evidence_id,
                  "detail": {"why": "same market"}},
        )  # fmt: skip
        assert response.status_code == 200, response.text
    assert (
        await api.post(
            "/holdings/AAA/peer-set",
            json={"peer_ticker": "DDD", "basis": "vendor", "source": "finnhub"},
        )
    ).status_code == 200
    # Issuer IR.
    assert (
        await api.post(
            "/holdings/AAA/issuer-ir-sources",
            json={"url": "https://ir.example.com/results", "release_type": "guidance",
                  "label": "Results"},
        )
    ).status_code == 200  # fmt: skip
    assert (
        await api.post(
            "/holdings/AAA/issuer-ir-releases",
            json={"release_type": "guidance", "source_url": "https://ir.example.com/r1",
                  "content_hash": "a" * 64, "published_at": "2026-07-29T10:00:00+00:00",
                  "analyst_count": 5, "basis": "adjusted", "metadata": {"pages": 3}},
        )
    ).status_code == 200  # fmt: skip
    # Company graph.
    for body in (
        {"subject_key": "AAA", "object_key": "BBB", "object_name": "Bravo Corp",
         "edge_type": "customer", "direction": "outgoing", "source": "owner",
         "source_url": "https://example.test/e1", "evidence_id": evidence_id,
         "valid_from": "2024-01-01", "passage": "Bravo Corp buys from Alpha Corp.",
         "detail": {"k": "v"}},
        {"subject_key": "AAA", "object_key": "CCC", "object_name": "Charlie Corp",
         "edge_type": "competitor", "direction": "symmetric", "source": "owner",
         "source_url": "https://example.test/e2"},
    ):  # fmt: skip
        response = await api.post("/company-graph/edges", json=body)
        assert response.status_code == 200, response.text
    await pool.execute(
        "update public.company_graph_edge set status='confirmed', evidence_tier='primary',"
        " decided_at='2026-04-01', decision_reason='checked against the filing'"
        " where edge_type='customer'"
    )
    await pool.execute(
        "insert into public.entity_relationship (ticker, subject, object_key, object_name, type,"
        " source, source_ref, as_of, status, detail) values ('AAA','AAA','dddd','Delta Ltd',"
        "'subsidiary','gleif','https://g','2025-01-01','confirmed','{\"lei\": \"X\"}'::jsonb)"
    )
    await pool.execute(
        "insert into public.verdict (ticker, kpi_id, metric, basis, kpi_period, fiscal_year,"
        " fiscal_period, status, observed_value, operator, threshold, threshold_unit, rationale,"
        " config_version_id) values ('AAA','growth','revenue','reported','annual',2025,'FY',"
        "'pass',1200.5,'>=',1,'USD','ok',$1)",
        version_id.id if hasattr(version_id, "id") else version_id,
    )
    await pool.execute(
        "insert into public.story_cluster (ticker, event_type, severity, status, confidence,"
        " kpi_id, headline, headline_key, best_tier, source_count, first_seen, event_date)"
        " values ('AAA','deal',2,'confirmed','corroborated','growth','Alpha buys Bravo',"
        "'alpha-buys-bravo',1,3,'2026-03-05','2026-03-01')"
    )
    event_bodies: list[dict[str, Any]] = [
        {"event_type": "deal", "event_state": "announced", "announced_at": "2026-03-01",
         "source": "owner", "source_url": "https://example.test/d1",
         "counterparty_key": "Bravo Corp",
         "terms": {"total_value": "500", "stock_pct": "40", "cash_pct": "60", "currency": "USD"},
         "thesis_kpi": "growth"},
        {"event_type": "deal", "event_state": "closed", "effective_at": "2026-06-01",
         "announced_at": "2026-03-01", "source": "owner", "source_url": "https://example.test/d2",
         "evidence_id": evidence_id, "counterparty_key": "Bravo Corp",
         "terms": {"total_value": "500", "stock_pct": "40", "cash_pct": "60", "currency": "USD",
                   "shares_to_issue": "10"}},
        {"event_type": "leadership", "event_state": "unknown", "source": "owner",
         "source_url": "https://example.test/l1", "detail": {"who": "CEO"}},
    ]  # fmt: skip
    for body in event_bodies:
        response = await api.post("/holdings/AAA/company-events", json=body)
        assert response.status_code == 200, response.text
    await pool.execute(
        "update public.company_event set status='confirmed', evidence_tier='primary',"
        " decided_at='2026-06-02', decision_reason='closing confirmed' where event_state='closed'"
    )
    for key, cpc, year, status in (("F1", ["H01L21/02", "G06F1/00"], 2018, "granted"),
                                   ("F2", ["H01L29/10"], 2020, "application")):  # fmt: skip
        response = await api.post(
            "/holdings/AAA/patent-families",
            json={"family_key": key, "priority_date": f"{year}-05-01", "title": f"Family {key}",
                  "cpc_codes": cpc, "jurisdictions": ["US", "EP"], "forward_citations": 4,
                  "legal_status": status, "source": "owner", "source_url": "https://example.test/p",
                  "applicant": "Alpha Corp", "ownership_confidence": "1",
                  "ownership_basis": "direct", "evidence_id": evidence_id},
        )  # fmt: skip
        assert response.status_code == 200, response.text
    response = await api.post(
        "/holdings/CCC/patent-families",
        json={"family_key": "C1", "priority_date": "2019-02-01", "cpc_codes": ["H01L21/00"],
              "jurisdictions": ["US"], "legal_status": "active", "source": "owner",
              "source_url": "https://example.test/pc", "applicant": "Charlie Corp",
              "ownership_confidence": "1", "ownership_basis": "direct", "evidence_id": evidence_id},
    )  # fmt: skip
    assert response.status_code == 200, response.text
    counterparties: list[tuple[str, str | None, str | None, bool]] = [
        ("customer", "Bravo Corp", "0.31", False),
        ("customer", None, "0.12", True),
        ("supplier", "Charlie Corp", None, False),
    ]
    for relationship, cp_name, cp_share, anonymous in counterparties:
        response = await api.post(
            "/holdings/AAA/relationship-disclosures",
            json={"relationship": relationship,
                  "direction": "issuer_sells_to"
                  if relationship == "customer" else "issuer_buys_from",
                  "counterparty_name": cp_name, "anonymous_counterparty": anonymous,
                  "reported_share": cp_share, "share_basis": "issuer_revenue"
                  if relationship == "customer" else "issuer_purchases",
                  "fiscal_period": "FY2025", "valid_from": "2025-01-01",
                  "evidence_id": evidence_id,
                  "passage": "A customer accounted for a share of revenue in the year.",
                  "source_url": "https://example.test/10k", "source": "sec_10k"},
        )  # fmt: skip
        assert response.status_code == 200, response.text
    await pool.execute(
        "update public.relationship_disclosure set status='confirmed', decided_at='2026-04-01',"
        " decision_reason='read the filing' where fiscal_period='FY2025'"
    )
    response = await api.post(
        "/holdings/AAA/relationship-disclosures",
        json={"relationship": "supplier", "direction": "unknown", "counterparty_name": "Echo Ltd",
              "passage": "Echo Ltd was mentioned among suppliers of components.",
              "source_url": "https://example.test/10q", "source": "sec_10q"},
    )  # fmt: skip
    assert response.status_code == 200, response.text
    await pool.execute(
        "insert into public.filing_hit (ticker, kind, query, matched_term, accession, cik,"
        " filer_name, form, filed_at, url, review_state, attempts) values ('AAA',"
        "'relationship_candidate','q','Alpha Corp','0001-26-1','0000000009','Zeta Inc','10-K',"
        "'2026-02-01','https://sec.example/a','pending',0)"
    )
    disclosure = await pool.fetchval(
        "select id from public.relationship_disclosure where status='confirmed' order by id limit 1"
    )
    other = await pool.fetchval(
        "select id from public.relationship_disclosure where status='confirmed' order by id desc"
        " limit 1"
    )
    await pool.execute(
        "insert into public.relationship_contradiction (ticker, relationship, fiscal_period, kind,"
        " disclosure_id, other_disclosure_id, detail, status) values ('AAA','customer','FY2025',"
        "'share_total_exceeds_one',$1,$2,'{\"total\": \"1.2\"}','open')",
        disclosure,
        other,
    )
    await pool.execute(
        "insert into public.graph_reconciliation (ticker, change_kind, from_key, to_key,"
        " observed_source, observed_at, outcome, detail, status) values ('AAA','parent_changed',"
        "'old','new','gleif','2026-05-01','unexplained','{\"n\": 1}','open')"
    )
    await pool.execute(
        "insert into public.identity_match_decision (instrument_ticker, decision, score, reasons,"
        " decided_at) values ('AAA','confirmed',1,'[\"isin\"]'::jsonb,'2026-05-01')"
    )
    return evidence_id


def M(name: str) -> Any:
    """The model called `name` in `api.typed_responses` (None while it is not written yet)."""
    return getattr(tr, name, None)


AS_OF = dt.datetime(2026, 8, 15, tzinfo=dt.UTC)


async def test_holding_routes_are_typed_and_the_wire_is_unchanged(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await seed_holding_aaa(backend_pool, api)
    h = "/holdings/AAA"
    cases = [
        Case(
            "research_obs", f"{h}/company-research-observations", M("CompanyResearchObservations")
        ),
        Case(
            "research_obs_asof",
            f"{h}/company-research-observations",
            M("CompanyResearchObservations"),
            {"as_of": AS_OF},
        ),
        Case("exposure_context", f"{h}/exposure-context", M("ExposureContext")),
        Case("fin_history", f"{h}/financial-history", M("FinancialHistoryRows")),
        Case(
            "fin_history_filtered",
            f"{h}/financial-history",
            M("FinancialHistoryRows"),
            {"statement": "income", "reconciled_only": True},
        ),
        Case("forward", f"{h}/forward-data", M("ForwardObservations")),
        Case("forward_consensus", f"{h}/forward-data/consensus", M("ForwardConsensus")),
        Case(
            "forward_consensus_asof",
            f"{h}/forward-data/consensus",
            M("ForwardConsensus"),
            {"as_of": AS_OF},
        ),
        Case("peer_set", f"{h}/peer-set", M("PeerSet")),
        Case("peer_set_asof", f"{h}/peer-set", M("PeerSet"), {"as_of": AS_OF}),
        Case("peer_metrics", f"{h}/peer-metrics", M("PeerMetrics")),
        Case(
            "peer_metrics_reconciled",
            f"{h}/peer-metrics",
            M("PeerMetrics"),
            {"reconciled_only": True},
        ),
        Case("ir_releases", f"{h}/issuer-ir-releases", M("IssuerIrReleases")),
        Case("ir_sources", f"{h}/issuer-ir-sources", M("IssuerIrSources")),
        Case("business_network", f"{h}/business-network", M("BusinessNetwork")),
        Case(
            "business_network_asof",
            f"{h}/business-network",
            M("BusinessNetwork"),
            {"as_of": dt.datetime(2099, 1, 1, tzinfo=dt.UTC)},
        ),
        Case(
            "concentration",
            f"{h}/customer-supplier-concentration",
            M("CustomerSupplierConcentration"),
        ),
        Case("patent_trends", f"{h}/patent-trends", M("PatentTrends")),
        Case("patent_overlap", f"{h}/patent-competitor-overlap", M("PatentCompetitorOverlap")),
        Case("deals", f"{h}/deals", M("HoldingDeals")),
        Case("thesis_kpi_links", f"{h}/thesis-kpi-links", M("ThesisKpiLinks")),
        Case("knock_on", "/portfolio/relationship-knock-on", M("RelationshipKnockOn")),
        Case("review_queue", "/relationship-review/queue", M("RelationshipReviewQueue")),
        Case(
            "review_queue_ticker",
            "/relationship-review/queue",
            M("RelationshipReviewQueue"),
            {"ticker": "AAA", "limit": 5},
        ),
        Case("reconciliations", "/company-graph/reconciliations", M("GraphReconciliations")),
        Case(
            "identity_decisions",
            "/reference/identity-decisions",
            M("IdentityDecisions"),
            encoder=lambda raw: TypeAdapter(list[dict[str, object]]).dump_python(raw, mode="json"),
        ),
    ]
    for period in ("FY2026Q2", "FY2026Q3", "FY2026Q4", "FY2099"):
        cases.append(
            Case(
                f"actual_vs_consensus_{period}",
                f"{h}/actual-vs-consensus",
                M("ActualVsConsensus"),
                {"metric": "revenue", "fiscal_period": period},
            )
        )
    cases.append(
        Case(
            "actual_vs_consensus_unsupported",
            f"{h}/actual-vs-consensus",
            M("ActualVsConsensus"),
            {"metric": "zzz", "fiscal_period": "FY2026Q2"},
        )
    )
    for domain in ("filed-notes", "positions", "insider-activity", "dependencies", "deals",
                   "consensus"):  # fmt: skip
        cases.append(
            Case(
                f"evidence_{domain}",
                f"{h}/evidence/{domain}",
                M("EvidenceDomainBody"),
                template="/holdings/{ticker}/evidence/{domain}",
            )
        )
    bodies = {}
    for case in cases:
        if case.template is None:
            case.template = case.path.replace("/holdings/AAA", "/holdings/{ticker}")
        if "{ticker}" in (case.template or "") and "ticker" not in _path_args(case):
            pass
        bodies[case.name] = await check(api, backend_pool, case, path_args=_holding_args(case))
    await write_routes_of_aaa(backend_pool, api)
    # The seeded data reaches the interesting branches (a green run of empty lists proves little).
    assert bodies["business_network"]["edges"] and bodies["business_network"]["events"]
    assert bodies["business_network"]["patents"] and bodies["business_network"]["disclosures"]
    assert bodies["deals"]["deals"] and bodies["concentration"]["periods"]
    assert bodies["patent_trends"] and bodies["patent_overlap"]["peers"]
    assert bodies["peer_metrics"]["confirmed_peers"] and bodies["knock_on"]["links"]
    assert bodies["review_queue"]["filing_hits"] and bodies["review_queue"]["contradictions"]
    assert bodies["reconciliations"]["open"] and bodies["identity_decisions"]
    assert bodies["actual_vs_consensus_FY2026Q2"]["state"] == "available"
    assert bodies["actual_vs_consensus_FY2026Q3"]["state"] == "currency_mismatch"
    assert bodies["actual_vs_consensus_FY2099"]["state"] == "no_actual"
    assert bodies["actual_vs_consensus_unsupported"]["state"] == "unsupported_metric"


def _holding_args(case: Case) -> dict[str, str]:
    args = {}
    if case.template and "{ticker}" in case.template:
        args["ticker"] = "AAA"
    if case.template and "{domain}" in case.template:
        args["domain"] = case.path.rsplit("/", 1)[1]
    return args


async def test_price_routes_are_typed_and_the_wire_is_unchanged(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    """Returns, listing, corporate actions and the exchange calendar of a held CEZ line."""
    await instrument(backend_pool, "CEZ_EQ")
    await firds_and_figi(backend_pool, "CEZ_EQ", "CZ0005112300", "XPRA", "CZK", "CEZ", "CP")
    await confirmed_listing(backend_pool, "CEZ_EQ")
    dates = weekdays(dt.date(2026, 9, 14), dt.date(2026, 9, 25))
    await put_bars(
        backend_pool,
        "cez.cz",
        "stooq",
        {d: str(1300 + 10 * i) for i, d in enumerate(dates)},
        currency="CZK",
        bar_type="raw",
    )
    await put_bars(
        backend_pool,
        "cez.cz",
        "stooq",
        {d: str(1290 + 10 * i) for i, d in enumerate(dates)},
        currency="CZK",
    )
    await snapshot(backend_pool, dt.date(2026, 9, 25), [("CEZ_EQ", "CZK")])
    evidence_id = await evidence(backend_pool)
    posted = await api.post(
        "/holdings/CEZ_EQ/corporate-actions",
        json={"action_type": "cash_dividend", "symbol": "cez.cz", "ex_date": "2026-09-10",
              "cash_amount": "42.5", "currency": "CZK", "source": "issuer",
              "source_url": "https://example.test/cez-dividend", "raw": {"note": "d"}},
    )  # fmt: skip
    assert posted.status_code == 200, posted.text
    posted = await api.post(
        "/holdings/CEZ_EQ/corporate-actions",
        json={"action_type": "split", "symbol": "cez.cz", "ex_date": "2026-09-16", "ratio": "2",
              "source": "issuer", "source_url": "https://example.test/split"},
    )  # fmt: skip
    assert posted.status_code == 200, posted.text
    decided = await api.post(
        f"/holdings/CEZ_EQ/corporate-actions/{posted.json()['id']}/decision",
        json={"decision": "confirmed", "reason": "split announced by the issuer",
              "evidence_id": evidence_id},
    )  # fmt: skip
    assert decided.status_code == 200, decided.text
    for body in (
        {"mic": "XPRA", "session_date": "2026-09-16", "state": "closed", "source": "owner",
         "evidence_url": "https://example.test/closed", "reason": "ad hoc closure"},
        {"mic": "XPRA", "session_date": "2026-09-17", "state": "half_day", "source": "owner",
         "evidence_url": "https://example.test/half",
         "open_at": "2026-09-17T07:00:00+00:00", "close_at": "2026-09-17T11:00:00+00:00"},
    ):  # fmt: skip
        assert (await api.post("/exchange-sessions", json=body)).status_code == 200
    window = {"start": dt.date(2026, 9, 14), "end": dt.date(2026, 9, 25)}
    c = "/holdings/CEZ_EQ"
    cases = [
        Case(
            "returns",
            f"{c}/returns",
            M("HoldingReturns"),
            window,
            template="/holdings/{ticker}/returns",
        ),
        Case(
            "returns_withholding",
            f"{c}/returns",
            M("HoldingReturns"),
            {**window, "withholding_rate": Decimal("0.15")},
            template="/holdings/{ticker}/returns",
        ),
        Case(
            "returns_no_series",
            "/holdings/NOPE/returns",
            M("HoldingReturns"),
            window,
            template="/holdings/{ticker}/returns",
        ),
        Case("listing", f"{c}/listing", M("HoldingListing"), template="/holdings/{ticker}/listing"),
        Case(
            "corporate_actions",
            f"{c}/corporate-actions",
            M("CorporateActions"),
            template="/holdings/{ticker}/corporate-actions",
        ),
        Case(
            "exchange_sessions",
            "/exchange-sessions/XPRA",
            M("ExchangeSessions"),
            window,
            template="/exchange-sessions/{mic}",
        ),
        Case(
            "exchange_sessions_expected",
            "/exchange-sessions/XPRA/expected",
            M("ExpectedExchangeSessions"),
            window,
            template="/exchange-sessions/{mic}/expected",
        ),
    ]
    bodies = {}
    for case in cases:
        args = {}
        if "{ticker}" in (case.template or ""):
            args["ticker"] = case.path.split("/")[2]
        if "{mic}" in (case.template or ""):
            args["mic"] = "XPRA"
        bodies[case.name] = await check(api, backend_pool, case, path_args=args)
    await sent(
        api, "POST", "/exchange-sessions", M("ExchangeSessionRecorded"),
        {"mic": "XPRA", "session_date": "2026-09-18", "state": "closed", "source": "owner",
         "evidence_url": "https://example.test/c2"},
    )  # fmt: skip
    await sent(
        api, "POST", "/holdings/CEZ_EQ/listing/derive", M("ListingDerived"), {}
    )  # the listing is already confirmed: `unchanged`
    await sent(
        api, "POST", "/holdings/CEZ_EQ/listing/confirm", M("ListingConfirmed"),
        {"decision": "confirmed", "reason": "ISIN and exchange ticker checked by hand"},
    )  # fmt: skip
    await sent(
        api, "POST", "/holdings/CEZ_EQ/corporate-actions", M("CorporateActionRecorded"),
        {"action_type": "split", "symbol": "cez.cz", "ex_date": "2026-09-22", "ratio": "3",
         "source": "issuer", "source_url": "https://example.test/split3"},
    )  # fmt: skip
    await sent(
        api, "POST", f"/holdings/CEZ_EQ/corporate-actions/{posted.json()['id']}/decision",
        M("CorporateActionDecided"),
        {"decision": "suggested", "reason": "issuer withdrew the announcement"},
    )  # fmt: skip
    await sent(
        api, "POST", "/holdings/CEZ_EQ/corporate-actions/coverage",
        M("CorporateActionCoverageConfirmed"),
        {"through": "2026-09-25", "source_url": "https://example.test/coverage",
         "reason": "read the issuer's action list"},
    )  # fmt: skip
    assert bodies["returns"]["results"] and bodies["returns_no_series"]["results"] == []
    assert len(bodies["corporate_actions"]) == 2 and bodies["exchange_sessions"]
    assert bodies["listing"]["listing"] is not None
    missing = await api.get("/holdings/NOPE/listing")
    assert missing.status_code == 404


async def test_fund_and_verification_routes_are_typed_and_the_wire_is_unchanged(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    from steward.funds.drift import record_observation
    from steward.lookthrough.collect import LookThroughDeps, collect_fund
    from steward.lookthrough.factsheet import ManualFactsheet, store_manual_factsheet
    from steward.sources.fund_characteristics import parse_ishares
    from tests.test_fund_lookthrough_phase6 import SETTINGS, ishares_page
    from tests.test_funds_ownership_fixpass import CSPX_ISIN
    from tests.test_look_through import TODAY, _seed_portfolio, issuer_handler, mock

    await _seed_portfolio(backend_pool)
    fund = SETTINGS.funds[0]
    async with mock(issuer_handler([])) as http:
        await collect_fund(
            backend_pool,
            LookThroughDeps(http=http, today=TODAY, retry_delay=0),
            fund,
            SETTINGS,
            None,
        )
    await store_manual_factsheet(
        backend_pool,
        ManualFactsheet(
            CSPX_ISIN, dt.date(2026, 9, 25), share_class="Acc", total_assets=Decimal("1500000000"),
            total_assets_currency="USD", expense_ratio=Decimal("0.0007"),
            benchmark_name="S&P 500", distribution_policy="accumulating",
            nav_basis="close", charges={"ongoing": "0.07%"}, note="kid",
        ),
    )  # fmt: skip
    fig = parse_ishares(ishares_page(), "u")
    await record_observation(backend_pool, "CSPX", fig)
    await record_observation(backend_pool, "CSPX", fig)
    await backend_pool.execute(
        "insert into public.macro_observation (series_id, source, geography, period, value, unit,"
        " frequency, known_at, vintage, source_url) values ('CPI','fred','US','2026-08-01',3.1,"
        "'pct','monthly','2026-09-10','v1','https://x')"
    )
    await backend_pool.execute(
        "insert into public.source_shadow_report (window_start, window_end, days_observed,"
        " sign_off_ready, report, markdown) values ('2026-09-01','2026-09-28',20,false,"
        ' \'{"sources": [{"name": "a", "agreement": 0.99}]}\'::jsonb, \'# report\')'
    )
    cases = [
        Case(
            "factsheet",
            f"/funds/{CSPX_ISIN}/factsheet",
            M("FundFactsheets"),
            template="/funds/{isin}/factsheet",
        ),
        Case(
            "factsheet_limit",
            f"/funds/{CSPX_ISIN}/factsheet",
            M("FundFactsheets"),
            {"limit": 1},
            template="/funds/{isin}/factsheet",
        ),
        Case("characteristics_audit", "/funds/characteristics-audit", M("CharacteristicsAudit")),
        Case(
            "characteristics_audit_alerts",
            "/funds/characteristics-audit",
            M("CharacteristicsAudit"),
            {"alerts_only": True, "limit": 5},
        ),
        Case(
            "fund_look_through", "/funds/look-through", M("FundLookThroughAsOf"), {"as_of": TODAY}
        ),
        Case(
            "fund_look_through_early",
            "/funds/look-through",
            M("FundLookThroughAsOf"),
            {"as_of": dt.date(2020, 1, 1), "top": 3},
        ),
        Case(
            "zero_cost_gate",
            "/verification/zero-cost-gate",
            M("ZeroCostGate"),
            encoder=_pydantic_dict,
        ),
        Case(
            "domain_gates", "/verification/domain-gates", M("DomainGates"), encoder=_pydantic_dict
        ),
        Case("signal_gates", "/verification/signals", M("SignalGates"), encoder=_pydantic_dict),
        Case(
            "signal_gate",
            "/verification/signals/segment_revenue",
            M("SignalGate"),
            template="/verification/signals/{signal_name}",
            encoder=_pydantic_dict,
        ),
        Case(
            "measured_coverage",
            "/verification/coverage",
            M("MeasuredCoverage"),
            encoder=_pydantic_list,
        ),
        Case(
            "shadow_report",
            "/verification/shadow-report",
            M("ShadowReport"),
            encoder=_pydantic_dict,
        ),
    ]
    bodies = {}
    for case in cases:
        args = {}
        if "{isin}" in (case.template or ""):
            args["isin"] = CSPX_ISIN
        if "{signal_name}" in (case.template or ""):
            args["signal_name"] = "segment_revenue"
        bodies[case.name] = await check(api, backend_pool, case, path_args=args)
    await sent(
        api, "PUT", f"/funds/{CSPX_ISIN}/factsheet", M("FactsheetStored"),
        {"as_of": "2026-09-26", "expense_ratio": "0.0007"},
    )  # fmt: skip
    await sent(
        api, "POST", "/funds/snapshots/pin", M("SnapshotsPinned"),
        {"ref_kind": "report", "ref_id": "r-1", "as_of": "2026-12-31"},
    )  # fmt: skip
    assert len(bodies["factsheet"]) == 2 and bodies["characteristics_audit"]
    assert bodies["fund_look_through"]["report"] and bodies["shadow_report"]["markdown"]


def _pydantic_dict(raw: Any) -> Any:
    return TypeAdapter(dict[str, Any]).dump_python(raw, mode="json")


def _pydantic_list(raw: Any) -> Any:
    return TypeAdapter(list[dict[str, Any]]).dump_python(raw, mode="json")


async def write_routes_of_aaa(pool: asyncpg.Pool, api: httpx.AsyncClient) -> None:
    """Acknowledgements of the write routes of the holding family."""
    evidence_id = int(await pool.fetchval("select id from public.source_evidence limit 1"))
    forward = {"kind": "owner_scenario", "metric": "revenue", "fiscal_period": "FY2028",
               "value": "1200", "source_url": "https://example.test/s"}  # fmt: skip
    await sent(api, "POST", "/holdings/AAA/forward-data", M("RecordedId"), forward)
    peer = {"peer_ticker": "EEE", "basis": "vendor", "source": "finnhub"}
    peer_id = (await sent(api, "POST", "/holdings/AAA/peer-set", M("RecordedId"), peer))["id"]
    changed = await sent(
        api, "PUT", f"/holdings/AAA/peer-set/{peer_id}/status", M("PeerStatusChanged"),
        {"status": "rejected", "reason": "not a peer"},
    )  # fmt: skip
    assert changed["peer"]["status"] == "rejected"
    await sent(
        api, "POST", "/holdings/AAA/issuer-ir-sources", M("RecordedId"),
        {"url": "https://ir.example.com/other", "release_type": "consensus"},
    )  # fmt: skip
    await sent(
        api, "POST", "/holdings/AAA/issuer-ir-releases", M("RecordedId"),
        {"release_type": "consensus", "source_url": "https://ir.example.com/r2",
         "content_hash": "b" * 64},
    )  # fmt: skip
    edge = await sent(
        api, "POST", "/company-graph/edges", M("CompanyEdgeRecorded"),
        {"subject_key": "AAA", "object_key": "ZZZ", "object_name": "Zed Corp",
         "edge_type": "partner", "direction": "symmetric", "source": "owner",
         "source_url": "https://example.test/z"},
    )  # fmt: skip
    await sent(
        api, "POST", f"/company-graph/edges/{edge['id']}/decision", M("GraphDecisionResult"),
        {"decision": "reject", "reason": "not a real partnership"},
    )  # fmt: skip
    event = await sent(
        api, "POST", "/holdings/AAA/company-events", M("RecordedId"),
        {"event_type": "product", "event_state": "announced", "source": "owner",
         "source_url": "https://example.test/prod"},
    )  # fmt: skip
    await sent(
        api, "POST", f"/holdings/AAA/company-events/{event['id']}/decision",
        M("GraphDecisionResult"), {"decision": "reject", "reason": "a rumour, not an event"},
    )  # fmt: skip
    await sent(
        api, "POST", "/holdings/AAA/patent-families", M("PatentFamilyRecorded"),
        {"family_key": "F9", "cpc_codes": ["G06F1/00"], "source": "owner",
         "source_url": "https://example.test/p9", "applicant": "Alpha Corp",
         "ownership_confidence": "0.5"},
    )  # fmt: skip
    disclosure = await pool.fetchval(
        "select id from public.relationship_disclosure where status='suggested'"
    )
    await sent(
        api, "POST", f"/holdings/AAA/relationship-disclosures/{disclosure}/decision",
        M("DisclosureDecisionResult"), {"decision": "reject", "reason": "not a supplier at all"},
    )  # fmt: skip
    contradiction = await pool.fetchval("select id from public.relationship_contradiction")
    await sent(
        api, "POST", f"/relationship-review/contradictions/{contradiction}/resolve",
        M("ContradictionResolved"), {"resolution": "dismissed", "note": "checked by hand"},
    )  # fmt: skip
    await sent(
        api, "POST", "/provider-access/alpha_vantage/account_free/reset-block",
        M("ProviderBlockReset"), {},
    )  # fmt: skip
    assert evidence_id
