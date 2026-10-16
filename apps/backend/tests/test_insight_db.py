# ruff: noqa: E501 - seed SQL stays readable
"""Insight features against a real database (plan I0-I6): checks cached by input hash, the
multiple-path round trip, the fair-value recorder's idempotency, the fair-value, scorecard,
dividend and coverage routes in their complete / partial / unavailable states, and a paid-only
consensus endpoint recorded as such. Needs STEWARD_TEST_DATABASE_URL (skipped otherwise)."""

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool, get_pool_or_error
from api.main import create_app
from steward.sources import consensus
from steward.valuation import fair_value
from tests.conftest import MigratedDatabase
from tests.test_company_db import OWNER, TICKER, TODAY, seed

pytestmark = pytest.mark.db


async def hold(pool: asyncpg.Pool, price: str = "48", day: dt.date = TODAY) -> None:
    job = await pool.fetchval(
        "insert into public.job (job_type, trigger, status) values ('portfolio_sync', 'schedule', 'succeeded') returning id"
    )
    await pool.execute(
        "insert into public.position_snapshot (snapshot_date, ticker, job_id, captured_at, quantity, avg_price, current_price,"
        " instrument_currency, value, account_currency, value_account, cost_account, ppl, base_currency, fx_source, fx_rate, value_base)"
        " values ($1, $2, $3, now(), 10, 40, $4::numeric, 'USD', 10 * $4::numeric, 'USD', 10 * $4::numeric, 400, 0, 'USD', 'ecb', 1, 10 * $4::numeric)",
        day, TICKER, job, Decimal(price),
    )  # fmt: skip


@pytest.fixture
async def api(
    migrated_db: MigratedDatabase, backend_pool: asyncpg.Pool
) -> AsyncIterator[httpx.AsyncClient]:
    await seed(migrated_db.admin_url)
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


async def test_checks_are_cached_by_input_hash(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    first = await api.get(f"/companies/{TICKER}/checks")
    assert first.status_code == 200, first.text
    body = first.json()
    assert [a["axis"] for a in body["axes"]] == ["value", "future", "past", "health", "dividend"]
    assert all(
        c["state"] in ("pass", "fail", "na", "unknown") and c["sentence"]
        for c in body["checks"] + body["risks"]
    )
    # No estimates are stored: the future axis is unknown, never zero.
    future = next(a for a in body["axes"] if a["axis"] == "future")
    assert future["score"] is None and future["unknown"] == 6
    again = (await api.get(f"/companies/{TICKER}/checks")).json()
    assert again["input_hash"] == body["input_hash"] and again["computed_at"] == body["computed_at"]
    assert await backend_pool.fetchval("select count(*) from public.company_check_snapshot") == 1
    assert (await api.get("/companies/NOPE_US_EQ/checks")).status_code == 404


async def test_checks_rules_are_versioned(api: httpx.AsyncClient) -> None:
    assert (await api.get("/config/checks")).status_code == 404
    defaults = (await api.get("/config/checks/defaults")).json()
    defaults["limits"]["roe_floor"] = 0.2
    saved = await api.put("/config/checks", json=defaults)
    assert saved.status_code == 200, saved.text
    assert (await api.get("/config/checks")).json()["body"]["limits"]["roe_floor"] == 0.2
    bad = dict(defaults, dividends={**defaults["dividends"], "tier_cuts": [0.8, 0.2]})
    assert (await api.post("/config/checks/validate", json=bad)).status_code == 422


async def test_multiple_path_round_trip_and_drift(api: httpx.AsyncClient) -> None:
    defaults = (await api.get(f"/companies/{TICKER}/valuation/multiple/defaults")).json()
    assert [d["name"] for d in defaults] == ["bear", "base", "bull"]
    run = (
        await api.post(
            f"/companies/{TICKER}/valuation/multiple",
            json={"scenarios": defaults, "selected": "base"},
        )
    ).json()
    base = next(s for s in run["scenarios"] if s["name"] == "base")
    assert base["value_per_share"]["value"] is not None
    assert base["chain"][0]["label"] == "Starting revenue"
    assert run["reverse"]["exit_pe"] is not None
    assert (await api.get(f"/companies/{TICKER}/valuation/multiple/drift")).json() is None
    saved = await api.post(
        f"/companies/{TICKER}/valuation/multiple/saved",
        json={"name": "Main", "request": {"scenarios": defaults, "selected": "base"}},
    )
    assert saved.status_code == 201, saved.text
    listed = (await api.get(f"/companies/{TICKER}/valuation/multiple/saved")).json()
    assert listed[0]["version"] == 1 and listed[0]["assumptions"] == saved.json()["assumptions"]
    rerun = (
        await api.post(
            f"/companies/{TICKER}/valuation/multiple",
            json={"scenarios": listed[0]["assumptions"], "selected": "base"},
        )
    ).json()
    assert (
        next(s for s in rerun["scenarios"] if s["name"] == "base")["value_per_share"]["value"]
        == base["value_per_share"]["value"]
    )
    drift = (await api.get(f"/companies/{TICKER}/valuation/multiple/drift")).json()
    assert drift["stale"] is False
    # The DCF list does not show multiple-path sets.
    assert (await api.get(f"/companies/{TICKER}/valuation/saved")).json() == []


async def test_fair_value_recorder_is_idempotent_and_feeds_the_view(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    owner = uuid.UUID(OWNER)
    view = (await api.get("/portfolio/fair-value")).json()
    assert view["status"] == "unavailable" and view["total"] == 0
    await hold(backend_pool)
    first = await fair_value.record(backend_pool, owner)
    assert "dcf_default" in first[TICKER]["bases"]
    n = await backend_pool.fetchval("select count(*) from public.fair_value_point")
    again = await fair_value.record(backend_pool, owner)
    assert again[TICKER]["written"] == 0
    assert await backend_pool.fetchval("select count(*) from public.fair_value_point") == n
    with pytest.raises((asyncpg.RaiseError, asyncpg.InsufficientPrivilegeError)):
        await backend_pool.execute("update public.fair_value_point set value = 1")
    view = (await api.get("/portfolio/fair-value?basis=dcf_default")).json()
    assert view["status"] == "complete" and view["covered"] == 1
    h = view["holdings"][0]
    assert Decimal(h["fair_value_account"]) == (
        Decimal(h["value_account"]) * Decimal(h["fair_value"]) / Decimal(h["price"])
    ).quantize(Decimal("0.01"))
    assert view["history_starts"] == dt.date.today().isoformat()
    thesis = (await api.get("/portfolio/fair-value?basis=thesis_target")).json()
    assert thesis["status"] == "unavailable" and thesis["uncovered"] == [TICKER]
    assert thesis["holdings"][0]["reason"].startswith("no thesis target")


async def test_scorecard_states(api: httpx.AsyncClient, backend_pool: asyncpg.Pool) -> None:
    empty = (await api.get("/portfolio/scorecard")).json()
    assert empty["status"] == "unavailable"
    await hold(backend_pool)
    unchecked = (await api.get("/portfolio/scorecard")).json()
    assert unchecked["unchecked"] == [TICKER] and unchecked["status"] == "unavailable"
    await api.get(f"/companies/{TICKER}/checks")
    card = (await api.get("/portfolio/scorecard")).json()
    assert card["status"] in ("complete", "partial") and card["unchecked"] == []
    past = next(a for a in card["axes"] if a["axis"] == "past")
    assert past["coverage_text"].startswith("1 of 1 holdings")
    future = next(a for a in card["axes"] if a["axis"] == "future")
    assert future["score"] is None and future["holdings_covered"] == 0
    marks = (await api.post("/checks/marks", json={"tickers": [TICKER, "ZZZ_US_EQ"]})).json()
    assert TICKER in marks["marks"] and "ZZZ_US_EQ" not in marks["marks"]


async def test_dividend_routes(api: httpx.AsyncClient, backend_pool: asyncpg.Pool) -> None:
    empty = (await api.get("/portfolio/dividends")).json()
    assert empty["status"] == "unavailable"
    await hold(backend_pool)
    # Two sources agree on five quarterly payments: corroborated history, so a schedule exists.
    for k in range(5):
        ex = TODAY - dt.timedelta(days=91 * (5 - k))
        for source in ("twelve_data", "yahoo"):
            await backend_pool.execute(
                "insert into public.corporate_action (symbol, action_type, ex_date, payment_date, cash_amount, currency, source, source_url, status)"
                " values ('aaa.us', 'cash_dividend', $1, $2, 0.25, 'USD', $3, $4, 'suggested')",
                ex, ex + dt.timedelta(days=14), source, f"https://{source}.test/{k}",
            )  # fmt: skip
    div = await api.get("/portfolio/dividends")
    assert div.status_code == 200, div.text
    assert div.json()["status"] in ("partial", "complete")
    cal = await api.get(f"/portfolio/dividends/calendar?month={TODAY:%Y-%m}")
    assert cal.status_code == 200
    assert (await api.get("/portfolio/dividends/calendar?month=2026-13")).status_code == 422
    company = (await api.get(f"/companies/{TICKER}/dividends")).json()
    assert company["ticker"] == TICKER and len(company["checks"]) == 6
    assert company["payout"]["limit_earnings"] == "0.75"
    confirm = (
        await api.post(
            f"/companies/{TICKER}/dividends/confirm",
            json={"event_ids": [company["events"][0]["id"]]},
        )
    ).json()
    assert confirm["confirmed"] == [company["events"][0]["id"]]
    status = await backend_pool.fetchval(
        "select status from public.corporate_action where id = $1", confirm["confirmed"][0]
    )
    assert status == "confirmed"


async def test_coverage_and_access_routes(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    await hold(backend_pool)
    cov = (await api.get("/quality/insight-coverage")).json()
    assert cov["holdings"][0]["ticker"] == TICKER
    states = {c["area"]: c["state"] for c in cov["holdings"][0]["cells"]}
    assert states["estimates"] == "unavailable" and states["consensus"] == "unavailable"
    verdicts = (await api.get("/sources/insight-access")).json()
    assert {(v["provider"], v["endpoint"]) for v in verdicts} >= {
        ("yahoo", "chart_dividends"),
        ("fmp", "price_target_consensus"),
    }


async def test_paid_only_consensus_is_recorded_not_zero(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    h = consensus.Holding(TICKER, "AAA", "AAA", None, "USD", Decimal(48), "USD")
    snap = consensus.Snapshot(TICKER, "finnhub", "paid_only", "plan does not include price targets")
    assert await consensus.store_snapshot(backend_pool, snap, h, TODAY)
    assert not await consensus.store_snapshot(backend_pool, snap, h, TODAY)
    row = await backend_pool.fetchrow(
        "select access_state, target_mean, analyst_count from public.consensus_snapshot"
    )
    assert (row["access_state"], row["target_mean"], row["analyst_count"]) == (
        "paid_only",
        None,
        None,
    )


async def test_portfolio_strips(api: httpx.AsyncClient, backend_pool: asyncpg.Pool) -> None:
    empty = await api.get("/portfolio/strips")
    assert empty.status_code == 200, empty.text
    assert all(s["dots"] == [] for s in empty.json()["strips"])
    await hold(backend_pool)
    await api.get(f"/companies/{TICKER}/checks")
    body = (await api.get("/portfolio/strips?basis=dcf_default")).json()
    by_metric = {s["metric"]: s for s in body["strips"]}
    assert set(by_metric) == {
        "valuation",
        "forecast_growth",
        "roe",
        "debt_to_equity",
        "forecast_yield",
    }
    assert all(d["ticker"] == TICKER for s in body["strips"] for d in s["dots"])
    # No benchmark universe is stored: each strip says so instead of drawing a zero.
    assert all(s["benchmark"] is None and s["benchmark_note"] for s in body["strips"])
    assert (await api.get("/portfolio/strips?basis=nope")).status_code == 422


async def test_screen_summary_reports_new_matches(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    saved = await api.post(
        "/discover/screens", json={"name": "Quality", "kind": "stock", "stock": {"filters": []}}
    )
    assert saved.status_code == 201, saved.text
    screen_id = saved.json()["id"]
    first = (await api.get("/discover/screens")).json()[0]
    assert first["last_run_at"] is None and first["new_basis"] == "not run yet"

    async def run(tickers: list[str], days_ago: int) -> None:
        await backend_pool.execute(
            "insert into public.screen_run (owner_id, screen_id, screen_version, kind, definition_hash, method_version,"
            " universe_key, universe_size, result_count, matched_tickers, result_hash, run_at)"
            " values ($1, $2, 1, 'stock', 'h', 'v1', 'covered', 10, $3, $4, 'r', now() - make_interval(days => $5))",
            OWNER, screen_id, len(tickers), tickers, days_ago,
        )  # fmt: skip

    await run(["OLD_US_EQ"], 9)
    one = (await api.get("/discover/screens")).json()[0]
    assert one["last_matched"] == 1 and one["new_last_7d"] is None and "7 days" in one["new_basis"]
    await run([TICKER, "OLD_US_EQ"], 0)
    two = (await api.get("/discover/screens")).json()[0]
    assert two["new_last_7d"] == 1 and two["new_tickers"] == [TICKER]
    assert two["new_basis"].startswith("against the run of")


async def test_valuation_pictures_state_their_absence(api: httpx.AsyncClient) -> None:
    got = await api.get(f"/companies/{TICKER}/valuation/pictures")
    assert got.status_code == 200, got.text
    body = got.json()
    # No sector is stored for the seeded company: no peers, no distribution, each with a reason.
    assert body["histogram"]["available"] is False and body["histogram"]["reason"]
    assert body["peers"]["median"] is None
    assert body["fair_multiple"]["available"] is False and body["fair_multiple"]["reason"]
    assert body["fair_multiple"]["label"] == "Indicative"
    assert {w["years"] for w in body["history"]["windows"]} <= {1, 3, 5, 10}
    assert (await api.get("/companies/NOPE_US_EQ/valuation/pictures")).status_code == 404


async def test_consensus_views(api: httpx.AsyncClient, backend_pool: asyncpg.Pool) -> None:
    empty = (await api.get(f"/companies/{TICKER}/consensus")).json()
    assert empty["coverage"] == "unavailable" and empty["latest"] is None
    assert empty["track"]["available"] is False and empty["chart_available"] is False
    await hold(backend_pool)
    await backend_pool.execute(
        "insert into public.consensus_snapshot (ticker, as_of, source, analyst_count, target_mean, target_high,"
        " target_low, target_currency, price_at_snapshot, access_state) values ($1, $2, 'finnhub', 9, 60, 75, 45,"
        " 'USD', 48, 'complete')",
        TICKER, TODAY,
    )  # fmt: skip
    view = (await api.get(f"/companies/{TICKER}/consensus")).json()
    assert view["coverage"] == "complete" and view["latest"]["agreement"] == "wide"
    assert Decimal(view["latest"]["upside"]) == Decimal("0.25") and view["snapshots"] == 1
    assert view["chart_reason"].endswith("needs 8")
    port = (await api.get("/portfolio/consensus")).json()
    assert port["complete"] == 1 and Decimal(port["holdings"][0]["mean"]) == 60


async def test_holdings_insight_and_notes(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    empty = await api.get("/portfolio/holdings/insight")
    assert empty.status_code == 200, empty.text
    assert empty.json()["holdings"] == []
    await hold(backend_pool)
    await api.get(f"/companies/{TICKER}/checks")
    body = (await api.get("/portfolio/holdings/insight?basis=dcf_default")).json()
    [row] = body["holdings"]
    assert row["ticker"] == TICKER and row["notes"] == 0
    # Every preset field is present; a missing figure is null with a reason, never zero.
    assert {"roe", "pe_ratio", "net_margin", "market_cap"} <= set(row["metrics"])
    assert all(row["metric_reasons"].get(k) for k, v in row["metrics"].items() if v is None)
    assert set(row["mark"]) == {"value", "future", "past", "health", "dividend"}
    assert (await api.get("/portfolio/holdings/insight?basis=nope")).status_code == 422

    made = await api.post(f"/holdings/{TICKER}/notes", json={"body": "  Watch the capex guide.  "})
    assert made.status_code == 201, made.text
    note = made.json()
    assert note["body"] == "Watch the capex guide."
    assert (await api.post(f"/holdings/{TICKER}/notes", json={"body": ""})).status_code == 422
    edited = await api.put(
        f"/holdings/{TICKER}/notes/{note['id']}", json={"body": "Capex guide raised."}
    )
    assert edited.json()["body"] == "Capex guide raised."
    listed = (await api.get(f"/holdings/{TICKER}/notes")).json()
    assert [n["id"] for n in listed] == [note["id"]]
    assert (await api.get("/portfolio/holdings/insight")).json()["holdings"][0]["notes"] == 1

    from steward.portfolio.holding_insight import search_notes

    found = await search_notes(backend_pool, OWNER, "capex")
    assert [n.id for n in found] == [note["id"]]
    assert await search_notes(backend_pool, OWNER, "dividend") == []
    assert (
        await api.put(f"/holdings/{TICKER}/notes/999999", json={"body": "x"})
    ).status_code == 404
    assert (await api.delete(f"/holdings/{TICKER}/notes/{note['id']}")).status_code == 204
    assert (await api.delete(f"/holdings/{TICKER}/notes/{note['id']}")).status_code == 404


async def test_holdings_views_are_versioned(api: httpx.AsyncClient) -> None:
    first = (await api.get("/config/holdings-views")).json()
    assert first["id"] is None and first["body"]["selected"] == "holding"
    bad = await api.put(
        "/config/holdings-views", json={"selected": "custom", "custom_columns": ["price", "nope"]}
    )
    assert bad.status_code == 422
    saved = await api.put(
        "/config/holdings-views",
        json={"selected": "custom", "custom_columns": ["holding", "notes", "price"]},
    )
    assert saved.status_code == 200, saved.text
    got = (await api.get("/config/holdings-views")).json()
    assert got["id"] == saved.json()["id"] and got["body"]["custom_columns"] == [
        "holding",
        "notes",
        "price",
    ]


class FakeLLM:
    """Answers a screen request with a fixed draft and counts the calls."""

    def __init__(self) -> None:
        self.calls = 0

    def configured(self, _model: str) -> bool:
        return True

    async def complete(self, req: object) -> object:
        from steward.discovery.parse import ModelDraft, ModelFilter
        from steward.llm.types import Completion

        self.calls += 1
        parsed = ModelDraft(
            refused=False,
            refusal_reason=None,
            markets=["US"],
            industries=[],
            keywords=[],
            filters=[
                ModelFilter(
                    metric_id="dividend_yield",
                    op="gte",
                    number=0.04,
                    values=[],
                    phrase="yielding over 4%",
                ),
                ModelFilter(
                    metric_id="net_debt_to_equity",
                    op="lte",
                    number=0.5,
                    values=[],
                    phrase="low debt",
                ),
                ModelFilter(
                    metric_id="forward_eps_growth",
                    op="gte",
                    number=0,
                    values=[],
                    phrase="positive earnings growth",
                ),
            ],
            sort=None,
            unmatched_phrases=["run by founders"],
        )
        return Completion(
            model="fake-model",
            provider="openai",
            text=parsed.model_dump_json(),
            parsed=parsed,
            stop_reason="end_turn",
            usage=None,
        )


async def test_parse_screen_drafts_but_never_runs(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    import steward.llm.client as llm_client

    fake = FakeLLM()
    monkeypatch.setattr(llm_client, "create_llm", lambda _s: fake)
    text = "US dividend payers yielding over 4% with low debt and positive earnings growth, run by founders"
    got = await api.post("/discover/parse", json={"text": text})
    assert got.status_code == 200, got.text
    body = got.json()
    draft = body["draft"]
    assert draft["markets"] == ["US"] and not draft["refused"]
    assert [(f["metric_id"], f["op"]) for f in draft["filters"]] == [
        ("dividend_yield", "gte"),
        ("net_debt_to_equity", "lte"),
        ("forward_eps_growth", "gte"),
    ]
    assert draft["unmatched_phrases"] == ["run by founders"]
    assert body["cached"] is False and body["evaluation"]
    again = (await api.post("/discover/parse", json={"text": text})).json()
    assert again["cached"] is True and fake.calls == 1
    assert await backend_pool.fetchval("select count(*) from public.screen_parse_log") == 2
    assert await backend_pool.fetchval("select count(*) from public.screen_run") == 0
    assert (
        await backend_pool.fetchval(
            "select count(*) from public.job where job_type = 'screen_parse'"
        )
        == 1
    )
    assert (await api.post("/discover/parse", json={"text": "x"})).status_code == 422


async def test_screen_templates_list_and_version(api: httpx.AsyncClient) -> None:
    got = await api.get("/discover/templates")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["version_id"] is None and len(body["templates"]) == 12
    assert any(t["id"] == "quality_payers" for t in body["templates"])
    for t in body["templates"]:
        # Either it ran (a count and a stated basis) or each disabled filter says why.
        assert (t["matched"] is not None and t["mark_basis"]) or t["disabled"]
    bad = await api.put(
        "/config/screen-templates",
        json={
            "templates": [
                {
                    "id": "x1",
                    "title": "X",
                    "rationale": "Ten chars at least.",
                    "filters": [{"metric": "nope", "op": "gte", "value": 1}],
                }
            ]
        },
    )
    assert bad.status_code == 422
    one = {
        "id": "mine",
        "title": "Mine",
        "rationale": "My own starter screen.",
        "filters": [{"metric": "pe_ratio", "op": "lte", "value": "15"}],
    }
    saved = await api.put("/config/screen-templates", json={"templates": [one]})
    assert saved.status_code == 200, saved.text
    after = (await api.get("/discover/templates")).json()
    assert after["version_id"] == saved.json()["id"] and [t["id"] for t in after["templates"]] == [
        "mine"
    ]
