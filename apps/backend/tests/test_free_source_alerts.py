"""D7.5 free-source alerts: new-observation-only, dedupe, throttle, revisions, unconfirmed
graph edges, patent status transitions, and the decision trail."""

import datetime as dt
from collections.abc import AsyncIterator
from decimal import Decimal

import asyncpg
import httpx
import pytest

from api.alert_trail_routes import router as trail_router
from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool, get_pool_or_error
from api.main import create_app
from steward.config.event_rules import EventRulesConfig, FreeSourceAlertsConfig
from steward.free_source_alerts import (
    evaluate_free_source_alerts,
    patent_transition_material,
    read_trail,
)
from steward.free_sources import store_company_edge, store_patent_family

OWNER = "7f0c3d4e-0000-4000-8000-000000000001"
NOW = dt.datetime(2026, 10, 4, 12, 0, tzinfo=dt.UTC)
OLD = NOW - dt.timedelta(days=30)
NEW = NOW - dt.timedelta(days=1)
TICKER = "EXM_US_EQ"
BLS = "ea_bls_enterprise_credit_standards"


def test_defaults_and_transitions() -> None:
    cfg = EventRulesConfig()
    assert cfg.severity("credit_stress", None) == 2
    assert cfg.severity("patent_status", TICKER) == 1
    assert cfg.free_source_alerts.credit_stress[0].series_id == BLS
    assert patent_transition_material("granted", "expired")
    assert patent_transition_material("application", "granted")
    assert not patent_transition_material("unknown", "expired")
    assert not patent_transition_material("expired", "unknown")
    assert not patent_transition_material("granted", "active")
    with pytest.raises(ValueError):
        FreeSourceAlertsConfig.model_validate({"lookback_days": 0})


async def seed_holding(pool: asyncpg.Pool) -> None:
    await pool.execute(
        "insert into instrument (t212_ticker, isin, name, currency) values"
        " ($1, 'US30000A1043', 'Example Corp', 'USD')",
        TICKER,
    )
    job = await pool.fetchval(
        "insert into job (job_type, trigger, status) values ('portfolio_sync', 'manual',"
        " 'succeeded') returning id"
    )
    await pool.execute(
        "insert into account_snapshot (snapshot_date, job_id, captured_at, t212_account_id,"
        " account_currency, total_value, cash_available, cash_in_pies, cash_reserved,"
        " invested_value, invested_cost, unrealized_ppl, realized_ppl, base_currency,"
        " fx_source, fx_rate, cash_base, invested_base, total_base, weight_basis) values"
        " ('2026-10-02', $1, now(), 1, 'USD', 1000, 0, 0, 0, 1000, 1000, 0, 0, 'USD', 'ecb',"
        " 1, 0, 1000, 1000, 'total')",
        job,
    )
    await pool.execute(
        "insert into position_snapshot (snapshot_date, ticker, job_id, captured_at, quantity,"
        " avg_price, current_price, instrument_currency, value, account_currency, value_account,"
        " cost_account, ppl, base_currency, fx_source, fx_rate, value_base) values"
        " ('2026-10-02', $2, $1, now(), 1, 1, 1, 'USD', 1, 'USD', 1, 1, 0, 'USD',"
        " 'ecb', 1, 1)",
        job,
        TICKER,
    )


async def seed_macro(
    pool: asyncpg.Pool,
    period: dt.date,
    value: str,
    known_at: dt.datetime,
    vintage: str,
    series: str = BLS,
) -> None:
    await pool.execute(
        "insert into macro_observation (series_id, source, geography, period, value, unit,"
        " frequency, known_at, vintage, source_url) values ($1, 'ecb', 'EA', $2, $3,"
        " 'net % balance', 'quarterly', $4, $5, 'https://data.ecb.europa.eu/x')",
        series,
        period,
        Decimal(value),
        known_at,
        vintage,
    )


async def counts(pool: asyncpg.Pool) -> tuple[int, int]:
    items = await pool.fetchval("select count(*) from news_item where url like '%#alert:%'")
    alerts = await pool.fetchval("select count(*) from alert where kind = 'thesis_breach'")
    return items, alerts


@pytest.mark.db
async def test_new_observation_alerts_once_and_replay_is_deduplicated(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await seed_macro(pool, dt.date(2026, 1, 1), "2", OLD, "v1")
    await seed_macro(pool, dt.date(2026, 4, 1), "4", OLD, "v1")
    await seed_macro(pool, dt.date(2026, 7, 1), "14", NEW, "v2")  # crosses 10
    rules = EventRulesConfig()
    first = await evaluate_free_source_alerts(pool, rules, now=NOW, held=set())
    assert first.raised == [f"credit:ea_bls_tightening:{dt.date(2026, 7, 1)}"]
    again = await evaluate_free_source_alerts(pool, rules, now=NOW, held=set())
    assert again.raised == [] and again.counts["deduplicated"] == 1
    assert (await counts(pool))[0] == 1
    trail = await read_trail(pool, rule="credit_stress")
    raised = [r for r in trail.rows if r.outcome == "raised"]
    assert len(raised) == 1 and raised[0].news_item_id and raised[0].severity == 2
    assert raised[0].evidence["observation_id"]
    item = await pool.fetchrow(
        "select event_type, affected_ticker, tier from news_item where id = $1",
        raised[0].news_item_id,
    )
    assert item is not None and item["event_type"] == "credit_stress" and item["tier"] == 1


@pytest.mark.db
async def test_revision_of_an_old_period_and_identical_refetch_never_alert(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await seed_macro(pool, dt.date(2026, 1, 1), "2", OLD, "v1")
    await seed_macro(pool, dt.date(2026, 4, 1), "4", OLD, "v1")
    # Within the lookback window: the 2026-04 value is revised beyond the threshold, and the
    # 2026-01 value is fetched again unchanged under a new vintage.
    await seed_macro(pool, dt.date(2026, 4, 1), "25", NEW, "v2")
    await seed_macro(pool, dt.date(2026, 1, 1), "2", NEW, "v3")
    summary = await evaluate_free_source_alerts(pool, EventRulesConfig(), now=NOW, held=set())
    assert summary.raised == []
    assert (await counts(pool))[0] == 0
    trail = await read_trail(pool)
    assert [r.outcome for r in trail.rows] == ["revision_ignored"]
    assert trail.rows[0].evidence["previous_value"] == "4"
    assert summary.counts["refetch_identical"] == 1


@pytest.mark.db
async def test_first_load_is_baseline_and_persisting_stress_does_not_repeat(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    for month, value in ((1, "30"), (4, "31")):
        await seed_macro(pool, dt.date(2026, month, 1), value, NEW, "v1")
    rules = EventRulesConfig()
    await evaluate_free_source_alerts(pool, rules, now=NOW, held=set())
    assert (await counts(pool))[0] == 0
    assert [r.outcome for r in (await read_trail(pool)).rows] == ["baseline"]
    # A later period, still beyond the threshold: the crossing was already there.
    await seed_macro(pool, dt.date(2026, 7, 1), "32", NOW - dt.timedelta(hours=1), "v2")
    await evaluate_free_source_alerts(pool, rules, now=NOW, held=set())
    assert (await counts(pool))[0] == 0
    assert "persisting" in {r.outcome for r in (await read_trail(pool)).rows}


async def seed_trade(pool: asyncpg.Pool) -> None:
    issuer = await pool.fetchval(
        "insert into issuer (legal_name) values ('Example Corp') returning id"
    )
    security = await pool.fetchval(
        "insert into security (issuer_id, kind, name) values ($1, 'equity', 'Example')"
        " returning id",
        issuer,
    )
    await pool.execute(
        "insert into instrument_security_link (ticker, security_id, status) values"
        " ($1, $2, 'confirmed')",
        TICKER,
        security,
    )
    await pool.execute(
        "insert into exposure_mapping (issuer_id, segment, hs_code, basis, weight, valid_from)"
        " values ($1, 'Copper', '7403', 'reported', 0.30, '2020-01-01'),"
        " ($1, 'Tiny', '9999', 'estimated', 0.01, '2020-01-01')",
        issuer,
    )
    for partner, prior, latest, hs in (
        ("156", "100", "60", "7403"),
        ("276", "100", "50", "7403"),
        ("156", "100", "10", "9999"),
    ):
        for period, value, known in (
            (dt.date(2024, 1, 1), prior, OLD),
            (dt.date(2025, 1, 1), latest, NEW),
        ):
            await pool.execute(
                "insert into trade_observation (reporter, partner, flow, hs_revision, hs_code,"
                " period, trade_value, known_at, source_url) values ('842', $1, 'import', 'HS',"
                " $2, $3, $4, $5, 'https://comtradeplus.un.org/x')",
                partner,
                hs,
                period,
                Decimal(value),
                known,
            )


@pytest.mark.db
async def test_trade_exposure_alert_is_throttled_per_scope_and_skips_unmapped_weight(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await seed_holding(pool)
    await seed_trade(pool)
    summary = await evaluate_free_source_alerts(pool, EventRulesConfig(), now=NOW)
    # Two partners moved by -40% and -50% for the same holding and product: one alert, the
    # other recorded as throttled. The 1% exposure is below the weight floor.
    assert len(summary.raised) == 1
    trail = (await read_trail(pool, rule="trade_exposure")).rows
    outcomes = sorted(r.outcome for r in trail if r.outcome != "baseline")
    assert outcomes == ["raised", "throttled"]
    assert all(r.ticker == TICKER for r in trail)
    assert not any(r.evidence.get("hs_code") == "9999" for r in trail)
    raised = next(r for r in trail if r.outcome == "raised")
    assert Decimal(str(raised.evidence["exposure_weight"])) == Decimal("0.30")
    # Outside the throttle window the next scope alert is allowed again.
    again = await evaluate_free_source_alerts(pool, EventRulesConfig(), now=NOW)
    assert again.raised == []


@pytest.mark.db
async def test_throttle_off_raises_every_distinct_alert(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    await seed_holding(pool)
    await seed_trade(pool)
    rules = EventRulesConfig(free_source_alerts=FreeSourceAlertsConfig(throttle_hours=0))
    summary = await evaluate_free_source_alerts(pool, rules, now=NOW)
    assert len(summary.raised) == 2


@pytest.mark.db
async def test_suggested_and_disputed_edges_prompt_review_but_never_alert_or_breach(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await seed_holding(pool)
    url = "https://www.sec.gov/Archives/edgar/data/1/10k.htm"
    for status in ("suggested", "disputed"):
        edge = await store_company_edge(
            pool, subject_key=TICKER, object_key=f"OBJ_{status}", object_name=f"Obj {status}",
            edge_type="customer", direction="outgoing", source="sec", source_url=url + status,
            evidence_tier="primary",
        )  # fmt: skip
        if status == "disputed":
            await pool.execute(
                "update company_graph_edge set status = 'disputed' where id = $1", edge
            )
    await pool.execute(
        "insert into company_event (ticker, event_type, event_state, known_at, source,"
        " source_url, status) values ($1, 'deal', 'announced', $2, 'sec', $3, 'suggested')",
        TICKER,
        NEW,
        url + "deal",
    )
    # A "confirmed" row lacking primary evidence is not high-confidence either.
    await pool.execute(
        "insert into company_graph_edge (subject_key, object_key, object_name, edge_type,"
        " direction, status, evidence_tier, source, source_url, known_at) values ($1, 'X',"
        " 'X', 'acquirer', 'outgoing', 'confirmed', 'secondary', 'news', $2, $3)",
        TICKER,
        url + "x",
        NEW,
    )
    summary = await evaluate_free_source_alerts(pool, EventRulesConfig(), now=NOW)
    assert summary.raised == []
    assert await counts(pool) == (0, 0)
    assert await pool.fetchval("select count(*) from alert") == 0
    trail = (await read_trail(pool, rule="company_graph")).rows
    assert len(trail) == 4 and {r.outcome for r in trail} == {"review_needed"}
    assert all(r.news_item_id is None for r in trail)


@pytest.mark.db
async def test_confirmed_edge_and_event_raise_with_event_rule_severity(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await seed_holding(pool)
    await store_company_edge(
        pool,
        subject_key=TICKER,
        object_key="TGT",
        object_name="Target Inc",
        edge_type="acquirer",
        direction="outgoing",
        source="sec",
        source_url="https://www.sec.gov/8k",
        evidence_tier="primary",
        status="confirmed",
        passage="Example Corp agreed to acquire Target Inc.",
    )
    await pool.execute("update company_graph_edge set known_at = $1", NEW)
    await pool.execute(
        "insert into company_event (ticker, event_type, event_state, known_at, source,"
        " source_url, status) values ($1, 'leadership', 'closed', $2, 'sec', $3, 'confirmed')",
        TICKER,
        NEW,
        "https://www.sec.gov/8k-ceo",
    )
    summary = await evaluate_free_source_alerts(pool, EventRulesConfig(), now=NOW)
    assert len(summary.raised) == 2
    types = {
        r["event_type"]
        for r in await pool.fetch("select event_type from news_item where url like '%#alert:%'")
    }
    assert types == {"m_and_a", "management_change"}
    again = await evaluate_free_source_alerts(pool, EventRulesConfig(), now=NOW)
    assert again.raised == [] and again.counts["deduplicated"] == 2


@pytest.mark.db
async def test_severity_zero_is_recorded_not_raised(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    await seed_holding(pool)
    await pool.execute(
        "insert into company_event (ticker, event_type, event_state, known_at, source,"
        " source_url, status) values ($1, 'leadership', 'closed', $2, 'sec', $3, 'confirmed')",
        TICKER,
        NEW,
        "https://www.sec.gov/8k-ceo",
    )
    quiet = EventRulesConfig(severities={"management_change": 0})
    summary = await evaluate_free_source_alerts(pool, quiet, now=NOW)
    assert summary.raised == []
    assert [r.outcome for r in (await read_trail(pool)).rows] == ["below_severity"]


async def add_family(pool: asyncpg.Pool, status: str, basis: str = "direct") -> None:
    await store_patent_family(
        pool, family_key="FAM-1", priority_date=dt.date(2015, 1, 1), title="Widget",
        cpc_codes=["H01L"], jurisdictions=["US"], forward_citations=1, backward_citations=1,
        legal_status=status, source="epo_ops", source_url="https://worldwide.espacenet.com/fam1",
        ticker=TICKER, applicant="Example Corp", ownership_confidence=Decimal("0.95"),
        ownership_basis="direct" if basis == "direct" else "suggested",
    )  # fmt: skip


@pytest.mark.db
async def test_patent_status_baseline_then_material_change_then_unknown_is_ignored(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await seed_holding(pool)
    rules = EventRulesConfig()
    await add_family(pool, "granted")
    first = await evaluate_free_source_alerts(pool, rules, now=NOW)
    assert first.raised == [] and first.counts["patent_baseline"] == 1
    await add_family(pool, "unknown")  # an unreadable status keeps the last known one
    await evaluate_free_source_alerts(pool, rules, now=NOW)
    assert await pool.fetchval("select legal_status from patent_family") == "granted"
    assert (await counts(pool))[0] == 0
    await add_family(pool, "expired")
    summary = await evaluate_free_source_alerts(pool, rules, now=NOW)
    assert summary.raised == [f"patent:{TICKER}:FAM-1:granted>expired"]
    await evaluate_free_source_alerts(pool, rules, now=NOW)
    assert (await counts(pool))[0] == 1


@pytest.mark.db
async def test_suggested_patent_ownership_is_not_attributed(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    await seed_holding(pool)
    await add_family(pool, "granted", basis="suggested")
    await evaluate_free_source_alerts(pool, EventRulesConfig(), now=NOW)
    await add_family(pool, "expired", basis="suggested")
    summary = await evaluate_free_source_alerts(pool, EventRulesConfig(), now=NOW)
    assert summary.raised == [] and not summary.counts


@pytest.mark.db
async def test_disabled_config_and_run_cap(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    await seed_holding(pool)
    await seed_trade(pool)
    off = EventRulesConfig(free_source_alerts=FreeSourceAlertsConfig(enabled=False))
    assert (await evaluate_free_source_alerts(pool, off, now=NOW)).raised == []
    capped = EventRulesConfig(
        free_source_alerts=FreeSourceAlertsConfig(throttle_hours=0, max_alerts_per_run=1)
    )
    run1 = await evaluate_free_source_alerts(pool, capped, now=NOW)
    assert len(run1.raised) == 1 and run1.counts["run_cap"] == 1
    run2 = await evaluate_free_source_alerts(pool, capped, now=NOW)  # the rest next run
    assert len(run2.raised) == 1


@pytest.fixture
async def api(backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    app.include_router(trail_router)

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[get_pool_or_error] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.db
async def test_trail_endpoint_pages_and_filters(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    pool = backend_pool
    await seed_macro(pool, dt.date(2026, 1, 1), "2", OLD, "v1")
    await seed_macro(pool, dt.date(2026, 7, 1), "14", NEW, "v2")
    await evaluate_free_source_alerts(pool, EventRulesConfig(), now=NOW, held=set())
    body = (await api.get("/alerts/free-source-trail", params={"outcome": "raised"})).json()
    assert [r["rule"] for r in body["rows"]] == ["credit_stress"]
    assert body["rows"][0]["reason"] and body["next_before_id"] is None
    page = (await api.get("/alerts/free-source-trail", params={"limit": 1})).json()
    assert len(page["rows"]) == 1
    bad = await api.get("/alerts/free-source-trail", params={"outcome": "nope"})
    assert bad.status_code == 422
