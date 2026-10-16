# ruff: noqa: E501 - seed SQL stays readable
"""Insight features I11-I14 against a real database: the typed updates feed and its read state,
ownership mix, pay and tenure, positions, risk score, diversification, the insight clocks and
the earnings summary in their empty and seeded states. Needs STEWARD_TEST_DATABASE_URL."""

import datetime as dt
import json

import asyncpg
import httpx
import pytest

from tests.test_company_db import TICKER, TODAY
from tests.test_insight_db import api, hold

__all__ = ["api"]
pytestmark = pytest.mark.db


async def test_updates_feed_and_read_state(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    empty = await api.get("/updates")
    assert empty.status_code == 200, empty.text
    assert empty.json()["items"] == [] and empty.json()["unread"] == 0
    await hold(backend_pool)
    item = await backend_pool.fetchval(
        "insert into public.inbox_item (trigger_key, trigger_kind, trigger_ref, ticker, severity, headline, body)"
        " values ('alert:1', 'alert', 1, $1, 2, 'Price fell 9%', $2::jsonb) returning id",
        TICKER, json.dumps({"summary": "Close 43.10 against 47.40"}),
    )  # fmt: skip
    await backend_pool.execute(
        "insert into public.corporate_action (symbol, action_type, ex_date, payment_date, cash_amount, currency, source, source_url, status)"
        " values ('aaa.us', 'cash_dividend', $1, $2, 0.25, 'USD', 'yahoo', 'https://yahoo.test/1', 'suggested')",
        TODAY + dt.timedelta(days=5), TODAY + dt.timedelta(days=20),
    )  # fmt: skip
    feed = (await api.get("/updates")).json()
    keys = {u["key"]: u for u in feed["items"]}
    inbox = keys[f"inbox:{item}"]
    assert inbox["href"] == f"/inbox?tab=all&item={item}" and inbox["read"] is False
    assert inbox["what_changed"] == "Close 43.10 against 47.40"
    dividend = next(u for u in feed["items"] if u["source"] == "dividends")
    assert dividend["ticker"] == TICKER and "to confirm" in dividend["what_changed"]
    assert feed["unread"] == 2 and sum(c["unread"] for c in feed["counts"]) == 2

    marked = await api.post("/updates/read", json={"keys": [inbox["key"], dividend["key"]]})
    assert marked.status_code == 200, marked.text
    assert marked.json()["marked"] == 2
    # Marking again changes nothing and says so.
    assert (await api.post("/updates/read", json={"keys": [dividend["key"]]})).json()["marked"] == 0
    assert (
        await backend_pool.fetchval("select status from public.inbox_item where id = $1", item)
        == "read"
    )
    after = (await api.get("/updates")).json()
    assert after["unread"] == 0 and all(u["read"] for u in after["items"])
    assert (await api.get("/updates?unread=true")).json()["items"] == []
    assert (await api.get(f"/updates?ticker={TICKER}")).json()["items"]
    assert (await api.get("/updates?ticker=ZZZ_US_EQ")).json()["items"] == []


async def test_ownership_mix_and_pay_tenure_state_their_absence(api: httpx.AsyncClient) -> None:
    mix = await api.get(f"/companies/{TICKER}/ownership-mix")
    assert mix.status_code == 200, mix.text
    body = mix.json()
    # Nothing from 13F or Form 4 is stored: unavailable with a reason, never a 0% slice.
    assert body["status"] == "unavailable" and body["reason"]
    assert body["institutions"] == [] and all(
        m["share"] is None or m["share"] >= 0 for m in body["mix"]
    )
    pay = await api.get(f"/companies/{TICKER}/pay-tenure")
    assert pay.status_code == 200, pay.text
    p = pay.json()
    assert p["status"] in ("unavailable", "partial") and p["reason"]
    assert p["pay"] == [] and p["ceo_tenure_years"] is None


async def test_positions_risk_and_diversification(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    positions = await api.get("/portfolio/performance/positions")
    assert positions.status_code == 200, positions.text
    assert positions.json()["open"] == [] and positions.json()["closed"] == []
    await hold(backend_pool)
    score = await api.get("/portfolio/risk/score")
    assert score.status_code == 200, score.text
    s = score.json()["score"]
    # A score is either given with its inputs or withheld with the reason.
    if s["status"] == "complete":
        assert 0 <= s["score"] <= 100 and s["band"]
    else:
        assert s["score"] is None and s["reason"]
    assert (await api.get("/portfolio/risk/score?days=10")).status_code == 422
    div = await api.get("/portfolio/xray/diversification")
    assert div.status_code in (200, 404), div.text
    if div.status_code == 200:
        d = div.json()
        assert d["concentration"] and isinstance(d["links"], list)


async def test_insight_clocks_before_any_snapshot(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    await hold(backend_pool)
    got = await api.get("/quality/insight-clocks")
    assert got.status_code == 200, got.text
    body = got.json()
    assert [c["clock"] for c in body["clocks"]] == ["dividend_forecast", "consensus", "fair_value"]
    assert all(c["started"] is None for c in body["clocks"])
    assert [c["horizon_months"] for c in body["checkpoints"]] == [3, 6, 12]
    for cp in body["checkpoints"]:
        assert cp["reached"] is False and cp["available_from"] is None
        assert cp["dividend_hit_rate"] is None and cp["consensus_hit_rate"] is None
        assert cp["consensus_note"] == "no consensus snapshot stored yet"
        assert cp["fair_value_note"] == "no fair value recorded yet"


async def test_earnings_summary_without_a_release(
    api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import steward.llm.client as llm_client
    from tests.test_insight_db import FakeLLM

    view = await api.get(f"/companies/{TICKER}/earnings-summary")
    assert view.status_code == 200, view.text
    v = view.json()
    # The seeded facts carry no stored evidence document: nothing to summarise, and it says so.
    assert v["latest_release"] is None and v["note"] is None and v["reason"]
    from api.routes import get_storage

    fake = FakeLLM()
    monkeypatch.setattr(llm_client, "create_llm", lambda _s: fake)
    app = api._transport.app  # type: ignore[attr-defined]
    app.dependency_overrides[get_storage] = lambda: object()
    made = await api.post(f"/companies/{TICKER}/earnings-summary")
    assert made.status_code == 409, made.text
    assert fake.calls == 0
