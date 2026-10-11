"""Fixes from the news plan audit: pure-code checks (no database)."""

import datetime as dt
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from steward.news import analysis_job, market
from steward.news.analysis_job import alert_brief, notification_text
from steward.news.brief import BriefBody, BriefLine, BriefSection
from steward.news.framing import FramingComparison, OutletFraming, verified
from steward.news.novelty import centroid
from steward.news.read_analysis import like_pattern
from steward.news.translate import invents_numbers


def test_like_pattern_escapes_wildcards() -> None:
    assert like_pattern("50%_off") == "%50\\%\\_off%"
    assert like_pattern("a\\b") == "%a\\\\b%"


def test_invented_numbers_are_found_against_sources() -> None:
    assert not invents_numbers("Profit rose 12bn", ["Profit of $12bn"])
    assert invents_numbers("Profit rose 15bn", ["Profit of $1.5bn"])
    assert not invents_numbers("No figures here", [])


def test_event_day_rolls_to_the_next_day_after_the_close() -> None:
    noon = dt.datetime(2026, 9, 29, 12, 0, tzinfo=dt.UTC)
    late = dt.datetime(2026, 9, 29, 21, 30, tzinfo=dt.UTC)
    assert market.event_day_of(noon, None, "US") == dt.date(2026, 9, 29)
    assert market.event_day_of(late, None, "US") == dt.date(2026, 9, 30)
    assert market.event_day_of(dt.datetime(2026, 9, 29, 16, 0, tzinfo=dt.UTC), None, "DE") == (
        dt.date(2026, 9, 30)
    )
    # An unknown country uses the latest close; an earlier event date is kept as it is.
    assert market.event_day_of(noon, None, None) == dt.date(2026, 9, 29)
    assert market.event_day_of(late, dt.date(2026, 9, 25), "US") == dt.date(2026, 9, 25)


def test_a_centroid_skips_embeddings_of_another_dimension() -> None:
    assert centroid([[1.0, 0.0], [1.0, 0.0], [0.0, 0.0, 1.0]]) == pytest.approx([1.0, 0.0])
    assert centroid([[1.0, 0.0], [0.0, 1.0, 0.0]]) != []


def test_notification_text_is_bounded_and_clean() -> None:
    dirty = "Line one\n\x07<script>" + "x" * 500
    cleaned = notification_text(dirty)
    assert "\n" not in cleaned and "<" not in cleaned and "\x07" not in cleaned
    assert len(cleaned) <= analysis_job.ALERT_LINE_CHARS


class Story:
    headline = "Alpha 12bn deal"

    def __init__(self) -> None:
        self.items = [
            SimpleNamespace(id=1, text="Alpha agreed a $12bn deal after 3 rounds."),
            SimpleNamespace(id=2, text="Alpha deal worth 12bn confirmed."),
        ]

    def item(self, item_id: int) -> Any:
        return next((i for i in self.items if i.id == item_id), None)


def test_framing_free_text_with_invented_numbers_is_dropped() -> None:
    raw = FramingComparison(
        outlets=[
            OutletFraming(
                item_id=1, headline_claim="Alpha agrees a 12bn deal", emphasised_cause="7 rounds"
            ),
            OutletFraming(item_id=2, headline_claim="Alpha deal confirmed"),
            OutletFraming(item_id=2, headline_claim="dup"),
        ],
        agreement="conflict",
        conflicts=["Outlet A says 12bn, outlet B says 99bn."],
    )
    out = verified(Story(), raw)  # type: ignore[arg-type]
    assert out is not None
    assert out.outlets[0].emphasised_cause is None
    assert out.conflicts == [] and out.agreement == "agree"


def line(story_id: int, score: float, alert_score: float | None, text: str) -> BriefLine:
    return BriefLine(
        story_id=story_id,
        ticker="ABC_US_EQ",
        text=text,
        item_ids=[story_id],
        severity=2,
        lifecycle="unconfirmed",
        score=score,
        alert_score=alert_score,
    )


class FakePool:
    def __init__(self) -> None:
        self.keys: list[str] = []

    async def fetchval(self, sql: str, *args: Any) -> bool:
        return False


async def test_alert_brief_ranks_without_the_sentiment_nudge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: dict[str, Any] = {}

    async def fake_load_active(pool: Any, name: str, model: Any) -> None:
        return None

    async def fake_create_alerts(pool: Any, request: Any, message: str, *a: Any, **k: Any) -> Any:
        sent["message"] = message
        return SimpleNamespace(explanation="sent")

    monkeypatch.setattr(analysis_job, "load_active", fake_load_active)
    monkeypatch.setattr(analysis_job, "create_alerts", fake_create_alerts)
    # Story 9 has the highest score only because of its sentiment nudge.
    lines = [
        line(9, 1.30, 0.50, "nudged by sentiment"),
        line(1, 1.0, 1.0, "first on merit"),
        line(2, 0.9, 0.9, "second on merit"),
        line(3, 0.8, 0.8, "third on merit"),
    ]
    body = BriefBody(sections=[BriefSection(title="Americas", lines=lines)])
    ctx = SimpleNamespace(pool=FakePool(), job_id=1)
    result = await alert_brief(ctx, body, dt.datetime(2026, 10, 10, 6, tzinfo=dt.UTC))  # type: ignore[arg-type]
    assert result == "sent"
    assert "nudged by sentiment" not in sent["message"]
    assert "third on merit" in sent["message"]


async def test_alert_brief_with_no_lines_sends_nothing() -> None:
    ctx = SimpleNamespace(pool=FakePool(), job_id=1)
    body = BriefBody(sections=[])
    assert await alert_brief(ctx, body, dt.datetime(2026, 10, 10, tzinfo=dt.UTC)) == "no lines"  # type: ignore[arg-type]


async def test_fetch_gdelt_stops_at_the_first_failure_and_stores_nothing_for_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Pool:
        async def fetch(self, *a: Any, **k: Any) -> list[Any]:
            return []

    async def fake_load_active(pool: Any, name: str, model: Any) -> Any:
        from steward.config.news_sources import NewsSourcesConfig

        topics = NewsSourcesConfig().portfolio_topics
        return SimpleNamespace(body=NewsSourcesConfig(portfolio_topics=topics))

    async def fake_stale(pool: Any, themes: list[str], today: dt.date) -> list[str]:
        return themes[:2]

    stored: list[str] = []

    async def fake_store(pool: Any, theme: str, tone: Any, attention: Any) -> None:
        stored.append(theme)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="slow down")

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(
        analysis_job.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw)
    )
    monkeypatch.setattr(analysis_job, "load_active", fake_load_active)
    monkeypatch.setattr(analysis_job.gdelt_theme, "stale_themes", fake_stale)
    monkeypatch.setattr(analysis_job.gdelt_theme, "store_series", fake_store)
    out = await analysis_job.fetch_gdelt(
        Pool(),  # type: ignore[arg-type]
        5,
        dt.datetime(2026, 10, 10, tzinfo=dt.UTC),
    )
    assert out["themes"] == [] and len(out["errors"]) == 1 and stored == []
    assert json.dumps(out)


def test_canonical_url_keeps_numeric_article_ids_but_strips_word_tracking() -> None:
    from steward.news.normalize import canonical_url

    assert canonical_url("https://x.test/a?cid=1&utm_source=t") != canonical_url(
        "https://x.test/a?cid=2&utm_source=t"
    )
    assert canonical_url("https://x.test/a?ref=rss") == canonical_url("https://x.test/a")
    assert canonical_url("https://x.test/a?mod=twitter&id=7") == canonical_url(
        "https://x.test/a?id=7"
    )
