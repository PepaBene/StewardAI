"""Regression tests for the Phase 7 audit items: B42 (classifier schema), B44 (as-of leaks),
B45 (redaction) and the C lows in the read models (claim sentences, claim ids, dedupe keys,
per-provider consensus, deal ordering, coverage ratio, provenance names).

Each test fails on the code the audit described.
"""

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import asyncpg
import pytest
from pydantic import ValidationError

from api.source_routes import redact as route_redact
from steward.config.event_rules import (
    CLASSIFIABLE_EVENT_TYPES,
    EVENT_TYPES,
    SYSTEM_RAISED_EVENT_TYPES,
)
from steward.data_context.common import provenance_of
from steward.data_context.evidence import consensus, deals, dependencies
from steward.data_context.holding import coverage_entries, trade_exposure
from steward.data_context.research import (
    Claim,
    claim_sentence,
    company_context,
    country_context,
)
from steward.free_source_reads import business_network_payload, patent_trend_rows
from steward.jobs.runner import _error_text
from steward.news.classify import ClassificationOutput, ItemLabel
from steward.redact import redact
from steward.sources.base import AccessResult, AccessState
from steward.sources.store import record_access

UTC = dt.UTC
TICKER = "EXM_US_EQ"


def at(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text).replace(tzinfo=UTC)


# ---------------------------------------------------------------------------------------
# B42: the classifier schema enum excludes the system-raised event types
# ---------------------------------------------------------------------------------------


def test_classifier_schema_enum_excludes_system_raised_types() -> None:
    schema = ClassificationOutput.model_json_schema()
    enum = schema["$defs"]["ItemLabel"]["properties"]["event_type"]["enum"]
    assert tuple(enum) == CLASSIFIABLE_EVENT_TYPES
    assert not set(enum) & SYSTEM_RAISED_EVENT_TYPES
    assert set(enum) | SYSTEM_RAISED_EVENT_TYPES == set(EVENT_TYPES)


@pytest.mark.parametrize("event_type", sorted(SYSTEM_RAISED_EVENT_TYPES))
def test_a_model_label_of_a_system_type_is_rejected(event_type: str) -> None:
    with pytest.raises(ValidationError):
        ItemLabel.model_validate(
            {
                "index": 0,
                "event_type": event_type,
                "affected_ticker": None,
                "relevant": True,
                "thesis_link": None,
                "rumour": False,
                "event_date": None,
            }
        )


# ---------------------------------------------------------------------------------------
# B45: redact()
# ---------------------------------------------------------------------------------------

SECRETS = [
    "GET /v1?api_token=tok123ABC&x=1 failed",
    "subscription-key=sub123ABC rejected",
    "Ocp-Apim-Subscription-Key: sub123ABC",
    '{"key": "k1", "other": "fine"}',
    "{'api_token': 'tok123ABC'}",
    '{"token":"t9"}',
    "FRED_API_KEY=fred123ABC missing",
    "ALPHA_VANTAGE_API_KEY: av123ABC",
    "https://host/path?api_key%3Dabc123ABC%26x%3D1",
    "request failed for token%3Dabc123ABC",
    "https://user:pw123ABC@example.com/data",
    "https://tok123ABC@example.com/data",
    "Authorization: Bearer abc.def.ghi",
    "client_secret=cs123ABC password=pw123ABC",
]


@pytest.mark.parametrize("text", SECRETS)
def test_redact_masks_the_credential_forms_the_audit_names(text: str) -> None:
    out = redact(text) or ""
    for leaked in ("tok123ABC", "sub123ABC", "fred123ABC", "av123ABC", "abc123ABC", "pw123ABC"):
        assert leaked not in out, out
    for leaked in ('"k1"', "'t9'", '"t9"', "cs123ABC", "abc.def.ghi"):
        assert leaked not in out, out
    assert "[redacted]" in out
    assert route_redact(text) == out  # the route helper is the same function


@pytest.mark.parametrize(
    "text",
    [
        "token expired",
        "invalid key length",
        "primary key violated on provider_access",
        "rate limit: 5 tokens per minute",
        "the retry_after_seconds_value_is_long_but_plain_words_only",
        "quota exhausted until tomorrow",
        "secretary of state filing",
    ],
)
def test_redact_leaves_plain_words_alone(text: str) -> None:
    assert redact(text) == text


def test_redact_still_masks_long_opaque_tokens_and_none() -> None:
    assert redact("a" * 40) == "[redacted]"
    assert redact("key 0123456789abcdef0123456789abcdef0123") == "key [redacted]"
    assert redact(None) is None


def test_job_errors_are_redacted_before_they_are_stored() -> None:
    err = _error_text(RuntimeError("fetch failed: https://x/y?api_token=tok123ABC"))
    assert "tok123ABC" not in err and "[redacted]" in err


@pytest.mark.db
async def test_provider_attempt_errors_are_stored_redacted(backend_pool: asyncpg.Pool) -> None:
    await record_access(
        backend_pool,
        "finnhub",
        "peers",
        AccessResult(AccessState.TEMPORARILY_FAILED, "boom subscription-key=sub123ABC"),
        status_code=500,
    )
    stored = await backend_pool.fetchval(
        "select error_redacted from provider_fetch_audit where provider = 'finnhub' limit 1"
    )
    detail = await backend_pool.fetchval(
        "select detail from provider_access where provider = 'finnhub' and endpoint = 'peers'"
    )
    assert "sub123ABC" not in stored and "sub123ABC" not in detail


# ---------------------------------------------------------------------------------------
# C lows (pure)
# ---------------------------------------------------------------------------------------


def _claim(value: str | None, **kw: object) -> Claim:
    base: dict[str, object] = {
        "claim_id": "x",
        "domain": "macro",
        "subject": "US",
        "metric": "m",
        "label": "Series",
        "value": None if value is None else Decimal(value),
        "unit": "%",
        "period": dt.date(2026, 6, 1),
        "known_as_of": dt.date(2026, 7, 1),
        "source": "fred",
        "provenance": "primary",
        "source_url": None,
    }
    return Claim(**{**base, **kw})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        ("0.004", "0.004"),
        ("0.00456789", "0.004568"),
        ("-0.0042", "-0.0042"),
        ("0.0123456", "0.0123"),
        ("0.5", "0.5"),
        ("1234.5", "1,234.5"),
        ("12", "12"),
        ("0", "0"),
    ],
)
def test_claim_sentence_does_not_round_small_values_to_zero(value: str, shown: str) -> None:
    assert f": {shown} %" in claim_sentence(_claim(value))


def test_provenance_is_by_exact_source_name() -> None:
    assert provenance_of("sec_xbrl") == "primary"
    assert provenance_of("un_comtrade") == "primary"
    assert provenance_of("epo_ops") == "primary"
    assert provenance_of("Alpha-Vantage") == "vendor"
    # Substring matches used to classify these as primary (sec, bis, issuer) or vendor.
    assert provenance_of("sector_feed") == "unclassified"
    assert provenance_of("bistro_data") == "unclassified"
    assert provenance_of("tissuer_blog") == "unclassified"
    assert provenance_of("fmpx_research") == "unclassified"
    assert provenance_of("secondary_news") == "unclassified"


# ---------------------------------------------------------------------------------------
# A18 in the read models
# ---------------------------------------------------------------------------------------


async def seed_trade(pool: asyncpg.Pool) -> None:
    for reporter, value in (("842", "100"), ("276", "50")):
        await pool.execute(
            "insert into trade_observation (reporter, partner, flow, hs_revision, hs_code,"
            " period, trade_value, known_at, source_url) values ($1, '156', 'import', 'HS2017',"
            " '7403', '2025-01-01', $2, '2026-06-01', 'https://comtradeplus.un.org/x')",
            reporter,
            Decimal(value),
        )


@pytest.mark.db
async def test_holding_trade_exposure_matches_the_geography_to_the_comtrade_reporter(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_trade(backend_pool)
    effect = SimpleNamespace(
        geography="US", hs_code="7403", weight=Decimal("0.3"), basis="reported"
    )
    out = await trade_exposure(backend_pool, [effect], at("2026-07-01"))
    assert [r.reporter for r in out[0].rows] == ["842"]
    assert out[0].rows[0].reporter_iso == "US" and out[0].gap is None
    other = SimpleNamespace(geography="JP", hs_code="7403", weight=Decimal("0.3"), basis="reported")
    assert (await trade_exposure(backend_pool, [other], at("2026-07-01")))[0].gap


@pytest.mark.db
async def test_country_context_trade_claims_match_iso_geography(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_trade(backend_pool)
    context = await country_context(backend_pool, "US", at("2026-07-01"))
    trade = [c for c in context.claims if c.domain == "trade"]
    assert len(trade) == 1 and trade[0].value == Decimal("100")
    assert not any(g.domain == "trade" for g in context.gaps)


# ---------------------------------------------------------------------------------------
# B44: as-of leaks
# ---------------------------------------------------------------------------------------


async def edge(pool: asyncpg.Pool, status: str, decided: str | None, known: str) -> int:
    evidence = None
    if status == "confirmed":
        doc = await pool.fetchval(
            "insert into source_document (regulator, external_id, stable_url)"
            " values ('sec', 'e-1', 'https://example.test/e1') returning id"
        )
        version = await pool.fetchval(
            "insert into source_document_version (document_id, content_hash, known_at)"
            " values ($1, 'h', '2026-01-01') returning id",
            doc,
        )
        evidence = await pool.fetchval(
            "insert into source_evidence (document_version_id, quote) values ($1, 'q')"
            " returning id",
            version,
        )
    return int(
        await pool.fetchval(
            "insert into company_graph_edge (subject_key, object_key, object_name, edge_type,"
            " direction, status, evidence_tier, source, source_url, evidence_id, passage,"
            " known_at, decided_at) values ($1, 'ACME', 'Acme', 'supplier', 'incoming', $2,"
            " 'primary', 'sec', 'https://example.test/e', $3, 'Acme supplies parts.', $4, $5)"
            " returning id",
            TICKER,
            status,
            evidence,
            at(known),
            at(decided) if decided else None,
        )
    )


@pytest.mark.db
async def test_edge_status_is_the_status_in_force_at_the_cutoff(
    backend_pool: asyncpg.Pool,
) -> None:
    """A confirmation made after the cutoff must not make the edge look confirmed then."""
    await edge(backend_pool, "confirmed", "2026-09-01", "2026-03-01")
    early = await business_network_payload(backend_pool, TICKER, at("2026-06-01"))
    assert [e["status"] for e in early["edges"]] == ["suggested"]
    late = await business_network_payload(backend_pool, TICKER, at("2026-10-01"))
    assert [e["status"] for e in late["edges"]] == ["confirmed"]
    current = await business_network_payload(backend_pool, TICKER, None)
    assert [e["status"] for e in current["edges"]] == ["confirmed"]
    # The dependency read model lists confirmed relationships only: none yet at the early cutoff.
    deps = await dependencies(backend_pool, TICKER, at("2026-06-01"))
    assert deps.dependencies == [] and deps.excluded_unconfirmed == 1


@pytest.mark.db
async def test_legacy_relationships_without_a_date_are_not_part_of_a_historical_view(
    backend_pool: asyncpg.Pool,
) -> None:
    await backend_pool.execute(
        "insert into entity_relationship (ticker, subject, object_key, object_name, type, source,"
        " status, as_of) values ($1, 'S', 'P1', 'Parent', 'parent', 'gleif', 'confirmed', null),"
        " ($1, 'S', 'P2', 'Dated', 'owner', 'gleif', 'confirmed', '2026-01-01')",
        TICKER,
    )
    historical = await business_network_payload(backend_pool, TICKER, at("2026-06-01"))
    assert [e["object_key"] for e in historical["edges"]] == ["P2"]
    current = await business_network_payload(backend_pool, TICKER, None)
    assert sorted(e["object_key"] for e in current["edges"]) == ["P1", "P2"]
    # `historical=False` keeps the undated row for a "now" view that carries a timestamp.
    now_view = await business_network_payload(
        backend_pool, TICKER, at("2026-06-01"), historical=False
    )
    assert sorted(e["object_key"] for e in now_view["edges"]) == ["P1", "P2"]


async def family(
    pool: asyncpg.Pool, key: str, valid_from: str | None, valid_to: str | None
) -> None:
    fid = await pool.fetchval(
        "insert into patent_family (family_key, priority_date, cpc_codes, legal_status, source,"
        " source_url, known_at) values ($1, '2015-03-01', '{H01L}', 'granted', 'epo_ops',"
        " 'https://example.test/p', '2026-01-01') returning id",
        key,
    )
    await pool.execute(
        "insert into patent_family_owner (family_id, ticker, applicant, ownership_confidence,"
        " ownership_basis, valid_from, valid_to) values"
        " ($1, $2, 'Example', 0.95, 'direct', $3, $4)",
        fid,
        TICKER,
        dt.date.fromisoformat(valid_from) if valid_from else None,
        dt.date.fromisoformat(valid_to) if valid_to else None,
    )


@pytest.mark.db
async def test_patent_ownership_validity_and_the_cutoff_reach_the_trend(
    backend_pool: asyncpg.Pool,
) -> None:
    await family(backend_pool, "OLD-OWNER", "2010-01-01", "2020-12-31")  # sold before the cutoff
    await family(backend_pool, "NEW-OWNER", "2024-01-01", None)  # acquired after a 2022 cutoff
    await family(backend_pool, "ALWAYS", None, None)
    families = {
        p["family_key"]
        for p in (await business_network_payload(backend_pool, TICKER, at("2022-06-01")))["patents"]
    }
    assert families == set()  # none of the families was known before 2026-01-01
    cutoff = at("2026-06-01")
    now = await business_network_payload(backend_pool, TICKER, cutoff)
    assert {p["family_key"] for p in now["patents"]} == {"NEW-OWNER", "ALWAYS"}
    trend = await patent_trend_rows(backend_pool, TICKER, cutoff)
    assert sum(int(r["families"]) for r in trend) == 2
    # Before any of the families was known the trend is empty (the old call passed no cutoff).
    assert await patent_trend_rows(backend_pool, TICKER, at("2025-01-01")) == []


@pytest.mark.db
async def test_coverage_is_the_ledger_as_checked_by_the_cutoff(
    backend_pool: asyncpg.Pool,
) -> None:
    await backend_pool.execute(
        "insert into coverage_status (provider, dataset, entity_key, state, checked_at)"
        " values ('sec', 'facts', $1, 'available', '2026-08-01'),"
        " ('epo_ops', 'patents', $1, 'available', '2026-10-01')",
        TICKER,
    )
    assert len(await coverage_entries(backend_pool, [TICKER], at("2026-09-01"))) == 1
    assert len(await coverage_entries(backend_pool, [TICKER], at("2026-11-01"))) == 2
    assert len(await coverage_entries(backend_pool, [TICKER])) == 2


# ---------------------------------------------------------------------------------------
# C lows: company_context keys and ids, consensus per provider, deal ordering
# ---------------------------------------------------------------------------------------


async def fact(
    pool: asyncpg.Pool,
    start: str,
    end: str,
    value: str,
    *,
    unit: str = "USD",
    concept: str = "Revenues",
    known: str = "2026-08-01",
) -> int:
    return int(
        await pool.fetchval(
            "insert into financial_statement_fact (ticker, concept, statement, period_start,"
            " period_end, basis, value, unit, currency, source, source_url, known_at)"
            " values ($1, $2, 'income', $3, $4, 'us-gaap', $5, $6, $6, 'sec_xbrl',"
            " 'https://example.test/f', $7) returning id",
            TICKER,
            concept,
            dt.date.fromisoformat(start),
            dt.date.fromisoformat(end),
            Decimal(value),
            unit,
            at(known),
        )
    )


@pytest.mark.db
async def test_company_context_keeps_facts_that_differ_in_period_start_or_unit(
    backend_pool: asyncpg.Pool,
) -> None:
    q = await fact(backend_pool, "2026-04-01", "2026-06-30", "10")
    h = await fact(backend_pool, "2026-01-01", "2026-06-30", "25")
    e = await fact(backend_pool, "2026-04-01", "2026-06-30", "9", unit="EUR")
    context = await company_context(backend_pool, TICKER, at("2026-09-01"))
    values = sorted(c.value for c in context.claims if c.domain == "financial" and c.value)
    assert values == [Decimal("9"), Decimal("10"), Decimal("25")]
    ids = [c.claim_id for c in context.claims if c.domain == "financial"]
    assert len(set(ids)) == 3
    assert not any(str(row) == i.rsplit(":", 1)[-1] for row in (q, h, e) for i in ids)


@pytest.mark.db
async def test_claim_ids_do_not_change_when_the_same_value_is_fetched_again(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool

    async def seed_macro_row(known: str, vintage: str) -> None:
        await pool.execute(
            "insert into macro_observation (series_id, source, geography, period, value, unit,"
            " frequency, known_at, vintage, source_url) values ('cpi', 'fred', 'US',"
            " '2026-06-01', 3.1, '%', 'monthly', $1, $2, 'https://fred.example/cpi')",
            at(known),
            vintage,
        )

    await seed_macro_row("2026-07-01", "v1")
    first = await country_context(pool, "US", at("2026-07-15"))
    await seed_macro_row("2026-07-10", "v2")  # a re-fetch of the same value: a new row id
    second = await country_context(pool, "US", at("2026-07-15"))
    macro_first = [c.claim_id for c in first.claims if c.domain == "macro"]
    macro_second = [c.claim_id for c in second.claims if c.domain == "macro"]
    assert macro_first == macro_second and macro_first
    assert macro_first[0] == "macro:cpi:fred:%:US:2026-06-01"


async def forward(pool: asyncpg.Pool, provider: str, vintage: str, value: str, known: str) -> None:
    await pool.execute(
        "insert into forward_observation (ticker, kind, provider, metric, fiscal_period, currency,"
        " value, vintage, known_at, source_url, observation_key) values ($1,"
        " 'published_analyst_consensus', $2, 'eps', 'FY2026', 'USD', $3, $4, $5,"
        " 'https://example.test/c', md5($2 || $4))",
        TICKER,
        provider,
        Decimal(value),
        vintage,
        at(known),
    )


@pytest.mark.db
async def test_consensus_revisions_are_computed_per_provider(backend_pool: asyncpg.Pool) -> None:
    await forward(backend_pool, "issuer_ir", "2026-Q1", "1.00", "2026-04-01")
    await forward(backend_pool, "issuer_ir", "2026-Q2", "1.10", "2026-07-01")
    await forward(backend_pool, "other_ir", "2026-Q1", "5.00", "2026-05-01")
    result = await consensus(backend_pool, TICKER, at("2026-09-01"))
    by_provider = {s.provider: s for s in result.series}
    assert set(by_provider) == {"issuer_ir", "other_ir"}
    assert by_provider["issuer_ir"].revision_since_previous == Decimal("0.10")
    # One vintage of its own: no revision, instead of 5.00 - 1.10 across providers.
    assert by_provider["other_ir"].revision_since_previous is None
    ctx = await company_context(backend_pool, TICKER, at("2026-09-01"), enforce_gates=False)
    forward_claims = [c for c in ctx.claims if c.domain == "forward"]
    assert {c.value for c in forward_claims} == {Decimal("1.10"), Decimal("5.00")}


async def deal_evidence(pool: asyncpg.Pool) -> int:
    doc = await pool.fetchval(
        "insert into source_document (regulator, external_id, stable_url)"
        " values ('sec', 'deal-1', 'https://example.test/d') returning id"
    )
    version = await pool.fetchval(
        "insert into source_document_version (document_id, content_hash, known_at)"
        " values ($1, 'dh', '2026-01-01') returning id",
        doc,
    )
    return int(
        await pool.fetchval(
            "insert into source_evidence (document_version_id, quote) values ($1, 'q')"
            " returning id",
            version,
        )
    )


@pytest.mark.db
async def test_deal_current_state_follows_event_time_not_row_order(
    backend_pool: asyncpg.Pool,
) -> None:
    """An undated 'closed' event learned last was ordered before a dated 'announced' one (SQL
    `nulls first`), so the older state won."""
    evidence = await deal_evidence(backend_pool)
    for state, announced, known in (
        ("announced", "2026-05-01", "2026-05-02"),
        ("closed", None, "2026-08-15"),
    ):
        await backend_pool.execute(
            "insert into company_event (ticker, event_type, event_state, announced_at, known_at,"
            " source, source_url, counterparty_key, status, evidence_id) values ($1, 'deal', $2,"
            " $3, $4, 'sec_xbrl', $5, 'target', 'confirmed', $6)",
            TICKER,
            state,
            dt.date.fromisoformat(announced) if announced else None,
            at(known),
            f"https://example.test/{state}",
            evidence,
        )
    result = await deals(backend_pool, TICKER, at("2026-09-01"))
    assert result.deals[0].current_state == "closed"
    assert [e.event_state for e in result.deals[0].events] == ["announced", "closed"]
    # At an earlier cutoff only the announcement is known.
    early = await deals(backend_pool, TICKER, at("2026-06-01"))
    assert early.deals[0].current_state == "announced"


# ---------------------------------------------------------------------------------------
# C lows: coverage summary ratio
# ---------------------------------------------------------------------------------------


@pytest.mark.db
async def test_coverage_summary_ratio_uses_only_entries_with_an_expected_count(
    backend_pool: asyncpg.Pool,
) -> None:
    import httpx

    from api.auth import AuthenticatedUser, require_owner
    from api.db import get_pool, get_pool_or_error
    from api.main import create_app

    await backend_pool.execute(
        "insert into coverage_status (provider, dataset, entity_key, state, expected_periods,"
        " available_periods) values ('fred', 'cpi', 'US', 'available', 10, 5),"
        " ('fred', 'cpi', 'DE', 'available', null, 40)"
    )
    app = create_app()

    async def override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = override
    app.dependency_overrides[get_pool_or_error] = override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser("o", None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        body = (await client.get("/data/coverage/summary", params={"group_by": "source"})).json()
    group = next(g for g in body["groups"] if g["key"] == "fred")
    # DE states no expected count: its 40 available periods are in neither term.
    assert group["expected_periods"] == 10 and group["available_periods"] == 5
    assert Decimal(str(group["coverage_ratio"])) == Decimal("0.5")
    assert group["entries"] == 2 and group["entries_with_expected"] == 1


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("sec%2Dtoken%3Dabc123def456", "abc123def456"),
        ("GET /x?my%5Fapi%5Fkey%3Dabc123def456&a=1", "abc123def456"),
        ("Cookie: session=abc123", "abc123"),
        ("Set-Cookie: sid=abc123def; Path=/; HttpOnly", "abc123def"),
        ("Cookie: a=one1; b=two2", "two2"),
        ("req headers\r\ncookie: auth=zz99\r\nAccept: */*", "zz99"),
    ],
)
def test_redact_masks_encoded_prefixed_names_and_cookie_headers(text: str, secret: str) -> None:
    """N12: the prefix group took only `_`/`-`, and Cookie headers were not credential names."""
    out = redact(text) or ""
    assert secret not in out, out
    assert "[redacted]" in out


def test_redact_keeps_the_rest_of_a_request_around_a_cookie_header() -> None:
    out = redact("GET /x\nCookie: s=1\nAccept: */*") or ""
    assert "Accept: */*" in out and "GET /x" in out
