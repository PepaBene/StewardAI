"""Thesis upkeep: which holdings get a draft, when a review date may move, and the notices."""

import datetime as dt
import json

import asyncpg
import pytest

from steward.autopilot import thesis_upkeep as tu
from steward.config.store import load_active, save_config
from steward.config.thesis import ThesesConfig, ThesisConfig, ThesisKPI
from steward.ops import tasks

TODAY = dt.date(2026, 11, 10)


def kpi(kpi_id: str = "k1") -> ThesisKPI:
    return ThesisKPI(
        id=kpi_id, metric="revenue_growth", operator=">=", threshold=10, unit="%", period="annual"
    )


def thesis(review: dt.date | None, kpis: list[ThesisKPI] | None = None) -> ThesisConfig:
    return ThesisConfig(
        ticker="AAPL_US_EQ", why_owned="services", review_date=review, kpis=kpis or []
    )


# ---------------------------------------------------------------------------------- pure


def test_drafts_go_to_the_heaviest_uncovered_holdings() -> None:
    weights = {"A": 0.1, "B": 0.3, "C": 0.2, "D": 0.25, "E": 0.15}
    out = tu.drafts_due(
        weights,
        thesis_tickers={"D"},
        core={"E"},
        recently_researched={"B"},
        in_flight=set(),
        limit=5,
    )
    assert out == ["C", "A"]  # D has a thesis, E is core, B was just researched
    assert tu.drafts_due(weights, set(), set(), set(), {"B"}, 1) == ["D"]


def test_review_date_moves_only_on_clean_evidence() -> None:
    t = thesis(dt.date(2026, 11, 1), [kpi()])
    ok = {"k1": "pass"}
    assert tu.review_extension(t, ok, True, False, TODAY, 90) == dt.date(2027, 2, 8)
    assert tu.review_extension(t, {"k1": "breach"}, True, False, TODAY, 90) is None
    assert tu.review_extension(t, {}, True, False, TODAY, 90) is None  # no verdict yet
    assert tu.review_extension(t, ok, False, False, TODAY, 90) is None  # no research since
    assert tu.review_extension(t, ok, True, True, TODAY, 90) is None  # research wants a change
    assert (
        tu.review_extension(thesis(dt.date(2026, 12, 1), [kpi()]), ok, True, False, TODAY, 90)
        is None
    )
    assert tu.review_extension(thesis(dt.date(2026, 11, 1)), ok, True, False, TODAY, 90) is None
    assert tu.review_extension(thesis(None, [kpi()]), ok, True, False, TODAY, 90) is None


# ------------------------------------------------------------------------------ database


def hold(monkeypatch: pytest.MonkeyPatch, weights: dict[str, float]) -> None:
    """Pretend the latest snapshot holds these tickers (a real snapshot needs a job, an
    instrument and a dozen more columns that have nothing to do with these tests)."""

    async def fake(_pool: asyncpg.Pool) -> dict[str, float]:
        return weights

    monkeypatch.setattr("steward.autopilot.ledger.holding_weights", fake)


async def seed_report(pool: asyncpg.Pool, ticker: str, changes: list[dict[str, str]]) -> int:
    body = {"thesis_proposal": {"changes": changes}}
    return int(
        await pool.fetchval(
            "insert into public.report (type, ticker, title, body, body_md)"
            " values ('research', $1, 't', $2::jsonb, '') returning id",
            ticker,
            json.dumps(body),
        )
    )


@pytest.mark.db
async def test_a_draft_notice_opens_for_a_held_stock_without_a_thesis_and_clears(
    backend_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    hold(monkeypatch, {"NVDA_US_EQ": 0.12})
    report = await seed_report(backend_pool, "NVDA_US_EQ", [{"field": "why_owned"}])
    out = await tu.announce_drafts(backend_pool)
    assert out["ready"] == ["NVDA_US_EQ"]
    notice = (await tasks.needs_owner(backend_pool))[0]
    assert notice.kind == "thesis_draft" and notice.href == f"/research/{report}"

    async with backend_pool.acquire() as conn:
        await save_config(
            conn,
            "theses",
            ThesesConfig(theses=[ThesisConfig(ticker="NVDA_US_EQ", why_owned="ai demand")]),
            created_by=None,
        )
    again = await tu.announce_drafts(backend_pool)
    assert again["ready"] == [] and again["resolved"] == 1


@pytest.mark.db
async def test_review_date_moves_and_is_a_config_version(backend_pool: asyncpg.Pool) -> None:
    review = TODAY - dt.timedelta(days=5)
    async with backend_pool.acquire() as conn:
        saved = await save_config(
            conn,
            "theses",
            ThesesConfig(theses=[thesis(review, [kpi()])]),
            created_by=None,
        )
    await backend_pool.execute(
        "insert into public.verdict (ticker, kpi_id, metric, basis, kpi_period, status,"
        " operator, threshold, threshold_unit, rationale, config_version_id)"
        " values ('AAPL_US_EQ', 'k1', 'revenue_growth', 'reported', 'annual', 'pass',"
        " '>=', 10, '%', 'ok', $1)",
        saved.id,
    )
    await backend_pool.execute(
        "insert into public.report (type, ticker, title, body, body_md, created_at)"
        " values ('research', 'AAPL_US_EQ', 't', '{}'::jsonb, '', $1)",
        dt.datetime.combine(TODAY, dt.time.min, tzinfo=dt.UTC),
    )
    out = await tu.refresh_review_dates(backend_pool, days=90, today=TODAY)
    assert out["moved"] == {"AAPL_US_EQ": "2027-02-08"}
    after = await load_active(backend_pool, "theses", ThesesConfig)
    assert after is not None and after.id != saved.id
    assert after.body.theses[0].review_date == dt.date(2027, 2, 8)
    assert (await tu.refresh_review_dates(backend_pool, days=90, today=TODAY))["moved"] == []
