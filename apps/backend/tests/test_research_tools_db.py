"""Phase 1 research tools against a migrated database: bounded, cited, and honest about
what is missing."""

import datetime as dt
import json
from typing import Any

import asyncpg
import pytest

from steward.chat.tools import ChatTools
from steward.config.store import save_config
from steward.config.strategy import PositionWeightRule, StrategyConfig
from steward.config.thesis import ThesesConfig, ThesisConfig, ThesisKPI
from steward.research.sources import Registry

NOW = dt.datetime.now(dt.UTC)


def tools(pool: asyncpg.Pool) -> ChatTools:
    return ChatTools(pool, Registry(), 0, None)


async def save(pool: asyncpg.Pool, domain: str, body: Any) -> int:
    async with pool.acquire() as conn:
        stored = await save_config(conn, domain, body, created_by=None)
    return stored.id


@pytest.mark.db
async def test_every_result_is_stamped_and_traced(backend_pool: asyncpg.Pool) -> None:
    t = tools(backend_pool)
    out = await t.call("data_freshness", {})
    assert out["tool"] == "data_freshness" and "retrieved_at" in out
    bad = await t.call("price_history", {"nope": 1})
    assert "error" in bad and "retrieved_at" not in bad
    assert [e["tool"] for e in t.trace] == ["data_freshness", "price_history"]
    assert t.trace[0]["ok"] and not t.trace[1]["ok"]
    assert {"ms", "args", "sources_added", "bytes"} <= t.trace[0].keys()


@pytest.mark.db
async def test_data_freshness_marks_empty_datasets_missing_not_zero(
    backend_pool: asyncpg.Pool,
) -> None:
    out = await tools(backend_pool).call("data_freshness", {})
    by_name = {d["dataset"]: d for d in out["datasets"]}
    assert by_name["news"]["status"] == "missing" and by_name["news"]["value"] is None
    await backend_pool.execute(
        "insert into public.fx_rate (source, rate_date, currency, home_currency, rate,"
        " published_amount, published_rate) values ('ecb', $1, 'USD', 'EUR', 1.1, 1, 1.1)",
        (NOW - dt.timedelta(days=30)).date(),
    )
    out = await tools(backend_pool).call("data_freshness", {})
    fx = {d["dataset"]: d for d in out["datasets"]}["fx"]
    assert fx["status"] == "stale" and fx["value"] is not None and "older than" in fx["reason"]


@pytest.mark.db
async def test_strategy_rules_returns_the_version_and_a_citable_source(
    backend_pool: asyncpg.Pool,
) -> None:
    t = tools(backend_pool)
    version = await save(
        backend_pool,
        "strategy",
        StrategyConfig(
            base_currency="CZK",
            goals="Beat the core after costs",
            rules=[
                PositionWeightRule(
                    id="max-position", type="position_weight", operator="<=", value=0.1
                )
            ],
        ),
    )
    out = await t.call("strategy_rules", {})
    assert out["config_version"] == version and out["status"] == "ok"
    assert out["strategy"]["rules"][0]["id"] == "max-position"
    source = t.registry.get(out["source_id"])
    assert (
        source is not None
        and source.kind == "strategy"
        and "max-position" in (source.snippet or "")
    )


@pytest.mark.db
async def test_transactions_are_bounded_and_page(backend_pool: asyncpg.Pool) -> None:
    for i in range(7):
        await backend_pool.execute(
            "insert into public.transaction (id, type, subtype, ticker, quantity, price, amount,"
            " currency, occurred_at, raw) values ($1, 'order', 'BUY', 'AAPL_US_EQ', 1, 10, $2,"
            " 'USD', $3, '{}')",
            f"fill:{i}",
            -10.0,
            NOW - dt.timedelta(days=i),
        )
    t = tools(backend_pool)
    first = await t.call("transactions", {"ticker": "AAPL_US_EQ", "limit": 3})
    assert len(first["transactions"]) == 3 and first["total_count"] == 7
    assert first["page"]["truncated"] and first["page"]["next_cursor"]
    second = await t.call(
        "transactions",
        {"ticker": "AAPL_US_EQ", "limit": 50, "cursor": first["page"]["next_cursor"]},
    )
    assert len(second["transactions"]) == 4 and not second["page"]["truncated"]
    assert (await t.call("transactions", {"kind": "bogus"}))["error"]
    assert (await t.call("transactions", {"cursor": "garbage"}))["error"]
    # an absurd limit is clamped, not honoured
    huge = await t.call("transactions", {"limit": 10**6})
    assert huge["page"]["limit"] == 50


@pytest.mark.db
async def test_risk_history_lists_snapshots_with_method_versions(
    backend_pool: asyncpg.Pool,
) -> None:
    for day, vol in ((1, 0.12), (2, 0.15)):
        await backend_pool.execute(
            "insert into public.risk_report_snapshot (kind, scope, as_of, method_version,"
            " payload, payload_hash) values ('ex_ante', '', $1, 'v1', $2::jsonb, $3)",
            dt.date(2026, 9, day),
            json.dumps({"volatility": vol, "nested": {"x": 1}, "label": "text"}),
            f"hash{day}",
        )
    t = tools(backend_pool)
    out = await t.call("risk_history", {"kind": "ex_ante", "limit": 5})
    assert [s["as_of"] for s in out["snapshots"]] == ["2026-09-02", "2026-09-01"]
    assert out["snapshots"][0]["headline"] == {"volatility": 0.15}
    assert out["snapshots"][0]["method_version"] == "v1"
    assert (await t.call("risk_history", {"kind": "shock"}))["status"] == "missing"
    assert "error" in await t.call("risk_history", {"kind": "drop table"})


@pytest.mark.db
async def test_macro_series_lists_then_reads_the_newest_vintage(
    backend_pool: asyncpg.Pool,
) -> None:
    for period, vintage, rev, value in (
        (dt.date(2026, 7, 1), "a", 0, 2.0),
        (dt.date(2026, 8, 1), "a", 0, 2.1),
        (dt.date(2026, 8, 1), "b", 1, 2.3),
    ):
        await backend_pool.execute(
            "insert into public.macro_observation (series_id, source, geography, period, value,"
            " unit, frequency, known_at, vintage, revision, source_url) values ('CPI_YOY',"
            " 'eurostat', 'EA', $1, $2, 'percent', 'monthly', $3, $4, $5, 'https://e.example/cpi')",
            period,
            value,
            NOW - dt.timedelta(days=10 if rev == 0 else 1),
            vintage,
            rev,
        )
    t = tools(backend_pool)
    listing = await t.call("macro_series", {"query": "cpi"})
    assert listing["series"][0]["series_id"] == "CPI_YOY" and listing["series"][0]["periods"] == 2
    assert (await t.call("macro_series", {"query": "%"}))["series"] == []  # `%` is literal
    out = await t.call("macro_series", {"series_id": "CPI_YOY", "geography": "ea"})
    assert [o["period"] for o in out["observations"]] == ["2026-08-01", "2026-07-01"]
    assert out["observations"][0]["value"]["value"] == 2.3  # the revised vintage wins
    source = t.registry.get(out["source_id"])
    assert source is not None and source.url == "https://e.example/cpi"
    missing = await t.call("macro_series", {"series_id": "NOPE"})
    assert missing["status"] == "missing"


@pytest.mark.db
async def test_thesis_and_reports_joins_thesis_verdicts_and_last_two_reports(
    backend_pool: asyncpg.Pool,
) -> None:
    thesis = ThesisConfig(
        ticker="AAPL_US_EQ",
        why_owned="Services growth funds buybacks",
        kpis=[
            ThesisKPI(
                id="services",
                metric="services_revenue_growth",
                operator=">=",
                threshold=10,
                unit="%",
            )
        ],
    )
    version = await save(backend_pool, "theses", ThesesConfig(theses=[thesis]))
    await backend_pool.execute(
        "insert into public.verdict (ticker, kpi_id, metric, basis, kpi_period, status,"
        " observed_value, operator, threshold, threshold_unit, rationale, config_version_id)"
        " values ('AAPL_US_EQ', 'services', 'services_revenue_growth', 'reported', 'quarter',"
        " 'breach', 8.5, '>=', 10, '%', 'growth 8.5% is below 10%', $1)",
        version,
    )
    for i in range(3):
        await backend_pool.execute(
            "insert into public.report (type, ticker, title, body, body_md, created_at)"
            " values ('research', 'AAPL_US_EQ', $1, '{}', $2, $3)",
            f"Report {i}",
            f"body {i}",
            NOW - dt.timedelta(days=10 - i),
        )
    t = tools(backend_pool)
    out = await t.call("thesis_and_reports", {"ticker": "AAPL_US_EQ"})
    assert out["thesis"]["why_owned"].startswith("Services") and out["thesis"]["status"] == "ok"
    assert out["kpi_verdicts"][0]["status"] == "breach"
    assert out["kpi_verdicts"][0]["observed"]["value"] == 8.5
    assert [r["title"] for r in out["latest_reports"]] == ["Report 2", "Report 1"]
    assert all(t.registry.get(r["source_id"]) for r in out["latest_reports"])
    none = await t.call("thesis_and_reports", {"ticker": "ZZZ"})
    assert none["thesis"]["status"] == "missing" and none["kpi_verdicts"]["status"] == "missing"


@pytest.mark.db
async def test_filing_evidence_reports_coverage_and_rejects_bad_input(
    backend_pool: asyncpg.Pool,
) -> None:
    t = tools(backend_pool)
    out = await t.call("filing_evidence", {"ticker": "AAPL_US_EQ", "section": "consensus"})
    assert out["status"] == "missing" and out["coverage"]["state"] == "not_covered"
    assert out["data"]["series"] == []
    assert "error" in await t.call("filing_evidence", {"ticker": "A", "section": "everything"})
    assert "error" in await t.call(
        "filing_evidence", {"ticker": "A", "section": "insider", "as_of": "yesterday"}
    )
    for section in ("filed_notes", "control_stakes", "institutional", "fund_positions"):
        got = await t.call("filing_evidence", {"ticker": "AAPL_US_EQ", "section": section})
        assert "error" not in got and got["coverage"]["state"] in (
            "covered", "partial", "not_covered"
        ), section  # fmt: skip
    for section in ("insider", "dependencies", "deals"):
        got = await t.call("filing_evidence", {"ticker": "AAPL_US_EQ", "section": section})
        assert "error" not in got, section


@pytest.mark.db
async def test_company_tools_say_when_a_ticker_is_not_covered(
    backend_pool: asyncpg.Pool,
) -> None:
    t = tools(backend_pool)
    for name, args in (
        ("fundamentals", {"ticker": "ZZZ"}),
        ("valuation_scenarios", {"ticker": "ZZZ"}),
        ("price_history", {"ticker": "ZZZ"}),
    ):
        out = await t.call(name, args)
        assert "not in the covered universe" in out["error"], name
