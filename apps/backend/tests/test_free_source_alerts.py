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


def open_rules() -> EventRulesConfig:
    """Production defaults with the Phase 8 gates switched off: these tests exercise the alert
    logic itself (the gates have their own tests below)."""
    return EventRulesConfig(
        free_source_alerts=FreeSourceAlertsConfig(enforce_verification_gates=False)
    )


def test_defaults_and_transitions() -> None:
    cfg = EventRulesConfig()
    assert cfg.severity("credit_stress", None) == 2
    assert cfg.severity("patent_status", TICKER) == 1
    assert cfg.free_source_alerts.credit_stress[0].series_id == BLS
    assert cfg.free_source_alerts.enforce_verification_gates is True
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
    geography: str = "EA",
    source: str = "ecb",
) -> None:
    await pool.execute(
        "insert into macro_observation (series_id, source, geography, period, value, unit,"
        " frequency, known_at, vintage, source_url) values ($1, $6, $7, $2, $3,"
        " 'net % balance', 'quarterly', $4, $5, 'https://data.ecb.europa.eu/x')",
        series,
        period,
        Decimal(value),
        known_at,
        vintage,
        source,
        geography,
    )


async def seed_evidence(pool: asyncpg.Pool, tag: str = "a") -> int:
    doc = await pool.fetchval(
        "insert into public.source_document (regulator, external_id, stable_url)"
        " values ('sec', $1, 'https://example.test/' || $1) returning id",
        f"0000111111-26-{tag}",
    )
    version = await pool.fetchval(
        "insert into public.source_document_version (document_id, content_hash, known_at)"
        " values ($1, 'h-' || $2, '2026-08-01') returning id",
        doc,
        tag,
    )
    return int(
        await pool.fetchval(
            "insert into public.source_evidence (document_version_id, quote)"
            " values ($1, 'Example Corp announced the event.') returning id",
            version,
        )
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
    rules = open_rules()
    first = await evaluate_free_source_alerts(pool, rules, now=NOW, held=set())
    assert first.raised == [f"credit:ea_bls_tightening:ecb:EA:{dt.date(2026, 7, 1)}"]
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
    summary = await evaluate_free_source_alerts(pool, open_rules(), now=NOW, held=set())
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
    rules = open_rules()
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
        "insert into exposure_mapping (issuer_id, segment, hs_code, hs_revision, basis, weight,"
        " valid_from) values ($1, 'Copper', '7403', 'HS2017', 'reported', 0.30, '2020-01-01'),"
        " ($1, 'Tiny', '9999', 'HS2017', 'estimated', 0.01, '2020-01-01')",
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
                " period, trade_value, known_at, source_url) values ('842', $1, 'import', 'HS2017',"
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
    summary = await evaluate_free_source_alerts(pool, open_rules(), now=NOW)
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
    again = await evaluate_free_source_alerts(pool, open_rules(), now=NOW)
    assert again.raised == []


@pytest.mark.db
async def test_throttle_off_raises_every_distinct_alert(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    await seed_holding(pool)
    await seed_trade(pool)
    rules = EventRulesConfig(
        free_source_alerts=FreeSourceAlertsConfig(
            throttle_hours=0, enforce_verification_gates=False
        )
    )
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
    summary = await evaluate_free_source_alerts(pool, open_rules(), now=NOW)
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
        " source_url, status, evidence_id) values ($1, 'leadership', 'closed', $2, 'sec', $3,"
        " 'confirmed', $4)",
        TICKER,
        NEW,
        "https://www.sec.gov/8k-ceo",
        await seed_evidence(pool),
    )
    summary = await evaluate_free_source_alerts(pool, open_rules(), now=NOW)
    assert len(summary.raised) == 2
    types = {
        r["event_type"]
        for r in await pool.fetch("select event_type from news_item where url like '%#alert:%'")
    }
    assert types == {"m_and_a", "management_change"}
    again = await evaluate_free_source_alerts(pool, open_rules(), now=NOW)
    assert again.raised == [] and again.counts["deduplicated"] == 2


@pytest.mark.db
async def test_severity_zero_is_recorded_not_raised(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    await seed_holding(pool)
    await pool.execute(
        "insert into company_event (ticker, event_type, event_state, known_at, source,"
        " source_url, status, evidence_id) values ($1, 'leadership', 'closed', $2, 'sec', $3,"
        " 'confirmed', $4)",
        TICKER,
        NEW,
        "https://www.sec.gov/8k-ceo",
        await seed_evidence(pool),
    )
    quiet = EventRulesConfig(
        severities={"management_change": 0},
        free_source_alerts=FreeSourceAlertsConfig(enforce_verification_gates=False),
    )
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
    rules = open_rules()
    await add_family(pool, "granted")
    first = await evaluate_free_source_alerts(pool, rules, now=NOW)
    assert first.raised == [] and first.counts["patent_baseline"] == 1
    await add_family(pool, "unknown")  # an unreadable status keeps the last known one
    await evaluate_free_source_alerts(pool, rules, now=NOW)
    assert await pool.fetchval("select legal_status from patent_family") == "granted"
    assert (await counts(pool))[0] == 0
    await add_family(pool, "expired")
    summary = await evaluate_free_source_alerts(pool, rules, now=NOW)
    assert summary.raised == [f"patent:{TICKER}:FAM-1:granted>expired:1"]
    await evaluate_free_source_alerts(pool, rules, now=NOW)
    assert (await counts(pool))[0] == 1


@pytest.mark.db
async def test_suggested_patent_ownership_is_not_attributed(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    await seed_holding(pool)
    await add_family(pool, "granted", basis="suggested")
    await evaluate_free_source_alerts(pool, open_rules(), now=NOW)
    await add_family(pool, "expired", basis="suggested")
    summary = await evaluate_free_source_alerts(pool, open_rules(), now=NOW)
    assert summary.raised == [] and not summary.counts


@pytest.mark.db
async def test_disabled_config_and_run_cap(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    await seed_holding(pool)
    await seed_trade(pool)
    off = EventRulesConfig(
        free_source_alerts=FreeSourceAlertsConfig(enabled=False, enforce_verification_gates=False)
    )
    assert (await evaluate_free_source_alerts(pool, off, now=NOW)).raised == []
    capped = EventRulesConfig(
        free_source_alerts=FreeSourceAlertsConfig(
            throttle_hours=0, max_alerts_per_run=1, enforce_verification_gates=False
        )
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
    await evaluate_free_source_alerts(pool, open_rules(), now=NOW, held=set())
    body = (await api.get("/alerts/free-source-trail", params={"outcome": "raised"})).json()
    assert [r["rule"] for r in body["rows"]] == ["credit_stress"]
    assert body["rows"][0]["reason"] and body["next_before_id"] is None
    page = (await api.get("/alerts/free-source-trail", params={"limit": 1})).json()
    assert len(page["rows"]) == 1
    gated = await api.get("/alerts/free-source-trail", params={"outcome": "blocked_by_gate"})
    assert gated.status_code == 200 and gated.json()["rows"] == []
    bad = await api.get("/alerts/free-source-trail", params={"outcome": "nope"})
    assert bad.status_code == 422


# ---------------------------------------------------------------------------------------
# Audit fixes: A18, A19, B41, B43, gates, watermark, idempotence, patent flaps
# ---------------------------------------------------------------------------------------


async def seed_geo_trade(pool: asyncpg.Pool, geography: str | None, weight: str = "0.30") -> None:
    """A holding exposed to HS 7403 in `geography` (ISO) and Comtrade rows of two reporters:
    the US (M49 842) and Germany (276), both down 40% year on year."""
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
        "insert into exposure_mapping (issuer_id, segment, geography, hs_code, hs_revision, basis,"
        " weight, valid_from) values ($1, 'Copper', $2, '7403', 'HS2017', 'reported', $3,"
        " '2020-01-01')",
        issuer,
        geography,
        Decimal(weight),
    )
    for reporter in ("842", "276"):
        for period, value, known in (
            (dt.date(2024, 1, 1), "100", OLD),
            (dt.date(2025, 1, 1), "60", NEW),
        ):
            await pool.execute(
                "insert into trade_observation (reporter, partner, flow, hs_revision, hs_code,"
                " period, trade_value, known_at, source_url) values ($1, '156', 'import',"
                " 'HS2017', '7403', $2, $3, $4, 'https://comtradeplus.un.org/x')",
                reporter,
                period,
                Decimal(value),
                known,
            )


@pytest.mark.db
async def test_geography_qualified_trade_exposure_matches_comtrade_m49_reporters(
    backend_pool: asyncpg.Pool,
) -> None:
    """A18: the exposure says `US`, Comtrade stores 842. The old `reporter = geography` join
    matched nothing, so a geography-qualified exposure never alerted."""
    pool = backend_pool
    await seed_holding(pool)
    await seed_geo_trade(pool, "US")
    # What the old query did: no row at all.
    assert await pool.fetchval("select count(*) from trade_observation where reporter = 'US'") == 0
    summary = await evaluate_free_source_alerts(pool, open_rules(), now=NOW)
    assert len(summary.raised) == 1 and "842" in summary.raised[0]
    trail = (await read_trail(pool, rule="trade_exposure")).rows
    raised = next(r for r in trail if r.outcome == "raised")
    assert raised.evidence["reporter"] == "842" and raised.evidence["reporter_iso"] == "US"
    assert raised.evidence["exposure_geography"] == "US"
    # Germany's rows are for another geography: no alert, not even a baseline for them.
    assert not any(r.evidence.get("reporter") == "276" for r in trail)


@pytest.mark.db
async def test_trade_exposure_uses_reporter_iso_when_the_column_is_filled(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await seed_holding(pool)
    await seed_geo_trade(pool, "DE")
    await pool.execute("update trade_observation set reporter_iso = 'DE' where reporter = '276'")
    await pool.execute("update trade_observation set reporter_iso = 'US' where reporter = '842'")
    summary = await evaluate_free_source_alerts(pool, open_rules(), now=NOW)
    assert len(summary.raised) == 1 and ":276:" in summary.raised[0]


@pytest.mark.db
async def test_unmapped_geography_alerts_nothing_and_null_geography_keeps_all_reporters(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await seed_holding(pool)
    await seed_geo_trade(pool, "JP")
    assert (await evaluate_free_source_alerts(pool, open_rules(), now=NOW)).raised == []
    await pool.execute("update exposure_mapping set geography = null")
    rules = EventRulesConfig(
        free_source_alerts=FreeSourceAlertsConfig(
            throttle_hours=0, enforce_verification_gates=False
        )
    )
    assert len((await evaluate_free_source_alerts(pool, rules, now=NOW)).raised) == 2


@pytest.mark.db
async def test_patent_state_does_not_advance_when_the_alert_is_dropped_by_the_run_cap(
    backend_pool: asyncpg.Pool,
) -> None:
    """A19: at the run cap nothing was recorded but the state advanced to `expired`, so the
    lapse was never evaluated again."""
    pool = backend_pool
    await seed_holding(pool)
    await add_family(pool, "granted")
    rules = EventRulesConfig(
        free_source_alerts=FreeSourceAlertsConfig(
            max_alerts_per_run=1, throttle_hours=0, enforce_verification_gates=False
        )
    )
    await evaluate_free_source_alerts(pool, rules, now=NOW)  # baseline
    await add_family(pool, "expired")
    await seed_macro(pool, dt.date(2026, 1, 1), "2", OLD, "v1")
    await seed_macro(pool, dt.date(2026, 7, 1), "14", NEW, "v2")
    first = await evaluate_free_source_alerts(pool, rules, now=NOW)
    # The credit alert took the only slot; the patent lapse is deferred, not lost.
    assert len(first.raised) == 1 and first.counts["run_cap"] == 1
    state = await pool.fetchval(
        "select state from free_source_alert_state where rule = 'patent_status'"
    )
    assert state == "granted"
    second = await evaluate_free_source_alerts(pool, rules, now=NOW)
    assert second.raised == [f"patent:{TICKER}:FAM-1:granted>expired:1"]
    assert (
        await pool.fetchval(
            "select state from free_source_alert_state where rule = 'patent_status'"
        )
        == "expired"
    )


@pytest.mark.db
async def test_patent_status_that_flaps_alerts_every_lapse(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    await seed_holding(pool)
    rules = EventRulesConfig(
        free_source_alerts=FreeSourceAlertsConfig(
            throttle_hours=0, enforce_verification_gates=False
        )
    )
    raised: list[str] = []
    for status in ("granted", "expired", "granted", "expired"):
        await add_family(pool, status)
        raised += (await evaluate_free_source_alerts(pool, rules, now=NOW)).raised
    assert raised == [
        f"patent:{TICKER}:FAM-1:granted>expired:1",
        f"patent:{TICKER}:FAM-1:granted>expired:3",
    ]


@pytest.mark.db
async def test_patent_owner_outside_its_validity_window_is_not_attributed(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await seed_holding(pool)
    await add_family(pool, "granted")
    await evaluate_free_source_alerts(pool, open_rules(), now=NOW)
    await add_family(pool, "expired")
    await pool.execute("update patent_family_owner set valid_to = '2020-01-01'")
    summary = await evaluate_free_source_alerts(pool, open_rules(), now=NOW)
    assert summary.raised == []


@pytest.mark.db
async def test_credit_stress_dedupe_key_includes_source_and_geography(
    backend_pool: asyncpg.Pool,
) -> None:
    """B41: a second geography crossing in the same period alerted nothing and left no trail
    row, because the key was only rule + period."""
    pool = backend_pool
    rules = EventRulesConfig(
        free_source_alerts=FreeSourceAlertsConfig(
            throttle_hours=0, enforce_verification_gates=False
        )
    )
    for geography in ("EA", "DE"):
        await seed_macro(pool, dt.date(2026, 1, 1), "2", OLD, "v1", geography=geography)
        await seed_macro(pool, dt.date(2026, 7, 1), "14", NEW, "v2", geography=geography)
    summary = await evaluate_free_source_alerts(pool, rules, now=NOW, held=set())
    assert sorted(summary.raised) == [
        f"credit:ea_bls_tightening:ecb:DE:{dt.date(2026, 7, 1)}",
        f"credit:ea_bls_tightening:ecb:EA:{dt.date(2026, 7, 1)}",
    ]
    trail = (await read_trail(pool, rule="credit_stress", outcome="raised")).rows
    assert len(trail) == 2


@pytest.mark.db
async def test_late_confirmed_event_alerts_on_its_decision_time(
    backend_pool: asyncpg.Pool,
) -> None:
    """B43: an event first seen long ago and confirmed yesterday is a new decision."""
    pool = backend_pool
    await seed_holding(pool)
    evidence = await seed_evidence(pool)
    await pool.execute(
        "insert into company_event (ticker, event_type, event_state, known_at, decided_at, source,"
        " source_url, status, evidence_id) values ($1, 'deal', 'announced', $2, $3, 'sec', $4,"
        " 'confirmed', $5)",
        TICKER,
        OLD,
        NEW,
        "https://www.sec.gov/8k-deal",
        evidence,
    )
    summary = await evaluate_free_source_alerts(pool, open_rules(), now=NOW)
    assert len(summary.raised) == 1


@pytest.mark.db
async def test_confirmed_event_from_a_non_primary_source_is_review_only(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await seed_holding(pool)
    evidence = await seed_evidence(pool, "b")
    await pool.execute(
        "insert into company_event (ticker, event_type, event_state, known_at, source,"
        " source_url, status, evidence_id) values ($1, 'deal', 'announced', $2, 'rumour_blog', $3,"
        " 'confirmed', $4)",
        TICKER,
        NEW,
        "https://blog.example/deal",
        evidence,
    )
    summary = await evaluate_free_source_alerts(pool, open_rules(), now=NOW)
    assert summary.raised == []
    trail = (await read_trail(pool, rule="company_graph")).rows
    assert [r.outcome for r in trail] == ["review_needed"]


@pytest.mark.db
async def test_gates_default_deny_then_open_with_audit_data(backend_pool: asyncpg.Pool) -> None:
    """Phase 8 enforcement: with no audit data the alert is recorded as blocked_by_gate and
    raises nothing; once the macro domain has reconciliation samples the next run raises it and
    the provisional row is replaced."""
    pool = backend_pool
    await seed_macro(pool, dt.date(2026, 1, 1), "2", OLD, "v1")
    await seed_macro(pool, dt.date(2026, 7, 1), "14", NEW, "v2")
    rules = EventRulesConfig()  # production defaults: gates enforced
    assert rules.free_source_alerts.enforce_verification_gates
    blocked = await evaluate_free_source_alerts(pool, rules, now=NOW, held=set())
    assert blocked.raised == [] and blocked.counts["blocked_by_gate"] == 1
    trail = (await read_trail(pool, rule="credit_stress", outcome="blocked_by_gate")).rows
    assert len(trail) == 1 and "macro" in trail[0].reason
    assert (await counts(pool))[0] == 0
    # The same run again does not pile up rows, and the watermark stays back.
    again = await evaluate_free_source_alerts(pool, rules, now=NOW, held=set())
    assert again.counts["blocked_by_gate"] == 0
    await pool.execute(
        "insert into reconciliation_sample (domain, subject_key, metric, stored_value,"
        " primary_value, primary_source, primary_url, abs_error, rel_error, matched,"
        " threshold_version, reviewer, sampled_at) select 'macro', 'series-' || g, 'value', 1, 1,"
        " 'ecb', 'https://data.ecb.europa.eu/t', 0, 0, true, '2026.10-defaults', 'owner',"
        " now() - interval '1 day' from generate_series(1, 25) g"
    )
    opened = await evaluate_free_source_alerts(pool, rules, now=NOW, held=set())
    assert len(opened.raised) == 1
    rows = (await read_trail(pool, rule="credit_stress")).rows
    assert [r.outcome for r in rows if r.outcome != "baseline"] == ["raised"]
    assert (await counts(pool))[0] == 1


@pytest.mark.db
async def test_signal_gate_blocks_a_confirmed_deal_edge(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    await seed_holding(pool)
    evidence = await seed_evidence(pool, "c")
    await store_company_edge(
        pool, subject_key=TICKER, object_key="TGT", object_name="Target Inc",
        edge_type="acquirer", direction="outgoing", source="sec",
        source_url="https://www.sec.gov/8k", evidence_tier="primary", status="confirmed",
        evidence_id=evidence, passage="Example Corp agreed to acquire Target Inc.",
    )  # fmt: skip
    await pool.execute("update company_graph_edge set known_at = $1", NEW)
    rules = EventRulesConfig()
    summary = await evaluate_free_source_alerts(pool, rules, now=NOW)
    assert summary.raised == [] and summary.counts["blocked_by_gate"] == 1
    await pool.execute(
        "insert into signal_audit_result (signal_name, sample_size, evidence_correct, pit_checked,"
        " pit_violations, reviewer, audited_at) values ('deal_state', 40, 40, 30, 0, 'owner',"
        " now() - interval '2 days')"
    )
    summary = await evaluate_free_source_alerts(pool, rules, now=NOW)
    assert len(summary.raised) == 1


@pytest.mark.db
async def test_a_job_outage_does_not_lose_alerts(backend_pool: asyncpg.Pool) -> None:
    """The window starts at the last evaluated run, not a fixed three days."""
    pool = backend_pool
    await seed_macro(pool, dt.date(2026, 1, 1), "2", OLD, "v1")
    earlier = NOW - dt.timedelta(days=10)
    await evaluate_free_source_alerts(pool, open_rules(), now=earlier, held=set())
    await seed_macro(pool, dt.date(2026, 7, 1), "14", NOW - dt.timedelta(days=8), "v2")
    summary = await evaluate_free_source_alerts(pool, open_rules(), now=NOW, held=set())
    assert len(summary.raised) == 1


@pytest.mark.db
async def test_a_crash_between_raising_and_recording_is_recovered(
    backend_pool: asyncpg.Pool,
) -> None:
    from steward.free_source_alerts import MACRO_ALERT_SOURCE
    from steward.news.synthetic import SyntheticEvent, raise_events

    pool = backend_pool
    await seed_macro(pool, dt.date(2026, 1, 1), "2", OLD, "v1")
    await seed_macro(pool, dt.date(2026, 7, 1), "14", NEW, "v2")
    url = "https://data.ecb.europa.eu/x#alert:credit:ea_bls_tightening:ecb:EA:2026-07-01"
    await raise_events(
        pool, MACRO_ALERT_SOURCE,
        [SyntheticEvent(None, "credit_stress", "h", "s", url, None, "ecb.europa.eu", "ECB")],
        NOW, None,
    )  # fmt: skip
    summary = await evaluate_free_source_alerts(pool, open_rules(), now=NOW, held=set())
    assert summary.counts["recovered"] == 1
    row = (await read_trail(pool, rule="credit_stress", outcome="raised")).rows[0]
    assert row.news_item_id is not None and "recovered" in row.reason
    assert (await counts(pool))[0] == 1
