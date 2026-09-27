"""Regression tests for the group E audit fixes: news corroboration and known events, linking,
RSS baselines, notification routing and delivery, and macro surprises."""

import datetime as dt
from decimal import Decimal
from typing import Any

import asyncpg
import httpx
import pytest

from steward.config.event_rules import EventRulesConfig
from steward.config.holding_profiles import HoldingProfile
from steward.config.notifications import NotificationsConfig
from steward.macro.collect import Observation, store_and_evaluate
from steward.news.corroborate import decide
from steward.news.feeds import RawItem
from steward.news.link import Linker, holding_terms
from steward.news.pipeline import StoryState, known_event, other_stories, undated_baseline
from steward.notify.alerts import create_alerts
from steward.notify.channels import ChannelError, EmailChannel, TelegramChannel
from steward.notify.route import AlertRequest, RouteContext, pick_channels, quiet_until, route

UTC = dt.UTC
NOON = dt.datetime(2026, 9, 25, 10, 0, tzinfo=UTC)  # Friday 12:00 in Prague
FRIDAY_NIGHT = dt.datetime(2026, 9, 25, 21, 30, tzinfo=UTC)  # Friday 23:30 in Prague
db = pytest.mark.db


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def test_tier1_rumour_stays_confirmed_by_source_rules() -> None:
    # The source rule is unchanged (route_story and known_event exclude rumours instead).
    assert decide(best_tier=1, outlets=["reuters.com"], rumour=True).status == "confirmed"


def test_watch_topics_and_single_word_names_match_whole_words() -> None:
    target = HoldingProfile(
        ticker="TGT_US_EQ", company_name="Target Corporation", watch_topics=["AI", "tariffs"]
    )
    linker = Linker([holding_terms("TGT_US_EQ", target, None, None)])
    assert linker.link("Analyst raises his price target on Walmart") == []
    assert linker.link("Walmart said sales rose") == []  # "AI" inside "said"
    assert linker.link("Retailers bet on Tariffsville") == []
    assert [x.via for x in linker.link("Target beats estimates")] == ["name"]
    assert [x.via for x in linker.link("TARGET BEATS ESTIMATES")] == ["name"]
    assert [x.via for x in linker.link("Retailers race to adopt AI")] == ["topic"]
    assert [x.via for x in linker.link("New Tariffs hit retailers")] == ["topic"]
    assert linker.link("The ai of the matter") == []  # a short capitals topic keeps its case


def test_rule_falls_back_to_a_configured_channel() -> None:
    assert pick_channels(["telegram"], frozenset({"email"}))[0] == ["email"]
    assert pick_channels(["telegram", "email"], frozenset({"email"}))[0] == ["email"]
    assert pick_channels(["telegram"], frozenset())[0] == ["telegram"]  # fails visibly
    assert pick_channels(["telegram"], None)[0] == ["telegram"]
    request = AlertRequest("news", 3, "AAPL_US_EQ", "guidance_change")
    rows, why = route(
        request, NotificationsConfig(), RouteContext(now=NOON, available=frozenset({"email"}))
    )
    assert [r.channel for r in rows] == ["email"]
    assert "telegram not configured; fell back to email" in why
    test = AlertRequest("test", 3, None, "guidance_change")
    rows, _ = route(
        test, NotificationsConfig(), RouteContext(now=NOON, available=frozenset({"email"}))
    )
    assert [r.channel for r in rows] == ["telegram"]  # a test alert checks the rule as written


def test_friday_night_alert_is_held_past_a_quiet_weekend() -> None:
    config = NotificationsConfig(quiet_all_day_on=["weekends"])
    end = quiet_until(config, FRIDAY_NIGHT, set())
    assert end is not None and end.astimezone(UTC) == dt.datetime(2026, 9, 28, 5, 0, tzinfo=UTC)
    # Without quiet weekends it is Saturday morning, as before.
    end = quiet_until(NotificationsConfig(), FRIDAY_NIGHT, set())
    assert end is not None and end.astimezone(UTC) == dt.datetime(2026, 9, 26, 5, 0, tzinfo=UTC)
    assert quiet_until(config, NOON, set()) is None


def test_fed_funds_band_ignores_daily_drift() -> None:
    assert EventRulesConfig().macro_surprise_bands["fed_funds"] == pytest.approx(0.1)


async def test_email_error_carries_resend_message_and_idempotency_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            403,
            json={
                "statusCode": 403,
                "name": "validation_error",
                "message": "The gmail.com domain is not verified. key re_secret123",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        channel = EmailChannel(http, "re_secret123", "me@gmail.com", "me@example.com")
        with pytest.raises(ChannelError) as info:
            await channel.send("s", "t", idempotency_key="steward-alert-7")
    message = str(info.value)
    assert "HTTP 403: The gmail.com domain is not verified" in message
    assert "re_secret123" not in message
    assert seen[0].headers["Idempotency-Key"] == "steward-alert-7"


async def test_telegram_error_carries_description_without_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        channel = TelegramChannel(http, "123:tok", "42")
        with pytest.raises(ChannelError) as info:
            await channel.send("s", "t")
    assert str(info.value) == "Telegram returned HTTP 400: Bad Request: chat not found"


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


async def _cluster(
    pool: asyncpg.Pool,
    *,
    seen: dt.datetime,
    event_type: str = "m_and_a",
    status: str = "confirmed",
    rumour: bool = False,
    severity: int = 3,
    resolution: str | None = None,
    outlet: str = "wire.test",
) -> int:
    await pool.execute(
        "insert into source (id, name, tier, type) values ('wire', 'Wire', 2, 'rss')"
        " on conflict do nothing"
    )
    cluster: int = await pool.fetchval(
        "insert into story_cluster (headline, headline_key, best_tier, ticker, event_type,"
        " severity, status, rumour, resolution, first_seen, last_seen)"
        " values ($1, $1, 2, 'AAPL_US_EQ', $2, $3, $4, $5, $6, $7, $7) returning id",
        f"{event_type} {seen.isoformat()} {status} {rumour}",
        event_type,
        severity,
        status,
        rumour,
        resolution,
        seen,
    )
    await pool.execute(
        "insert into news_item (url, source_id, outlet, tier, published_at, headline,"
        " cluster_id) values ($1, 'wire', $2, 2, $3, 'h', $4)",
        f"https://{outlet}/{cluster}",
        outlet,
        seen,
        cluster,
    )
    return cluster


def _story(cluster_id: int, seen: dt.datetime) -> StoryState:
    return StoryState(
        id=cluster_id,
        headline="h",
        summary=None,
        ticker="AAPL_US_EQ",
        event_type="m_and_a",
        severity=3,
        rumour=False,
        recycled=False,
        relevant=True,
        thesis_link=None,
        event_date=None,
        best_tier=2,
        outlets=frozenset({"wire.test"}),
        published_at=seen,
        first_seen=seen,
        status="confirmed",
        search_rounds=0,
        last_searched_at=None,
        finding=None,
        url="https://wire.test/x",
        classified=True,
        resolution=None,
        duplicate_of=None,
    )


@db
async def test_an_earlier_rumour_is_not_the_known_event(backend_pool: asyncpg.Pool) -> None:
    earlier = NOON - dt.timedelta(hours=5)
    await _cluster(backend_pool, seen=earlier, rumour=True)  # tier-1 rumour, "confirmed"
    await _cluster(backend_pool, seen=earlier, severity=0)  # irrelevant
    await _cluster(backend_pool, seen=earlier, resolution="false")
    story = await _cluster(backend_pool, seen=NOON)
    assert await known_event(backend_pool, _story(story, NOON)) is None
    real = await _cluster(backend_pool, seen=earlier + dt.timedelta(hours=1))
    assert await known_event(backend_pool, _story(story, NOON)) == real


@db
async def test_disputed_and_false_stories_never_corroborate(backend_pool: asyncpg.Pool) -> None:
    await _cluster(backend_pool, seen=NOON, status="disputed", outlet="a.test")
    await _cluster(backend_pool, seen=NOON, resolution="false", outlet="b.test")
    good = await _cluster(backend_pool, seen=NOON, outlet="c.test")
    story = await _cluster(backend_pool, seen=NOON, status="unconfirmed", outlet="d.test")
    others = await other_stories(backend_pool, _story(story, NOON), dt.timedelta(hours=48))
    assert [o.cluster_id for o in others] == [good]


@db
async def test_undated_feed_items_start_as_a_baseline(backend_pool: asyncpg.Pool) -> None:
    def item(url: str, dated: bool) -> RawItem:
        return RawItem("wire", url, "h", NOON if dated else None)

    first = [item("https://w.test/old", False), item("https://w.test/dated", True)]
    kept, notes = await undated_baseline(backend_pool, "wire", first)
    assert [i.url for i in kept] == ["https://w.test/dated"] and notes
    second = [*first, item("https://w.test/new", False)]
    kept, notes = await undated_baseline(backend_pool, "wire", second)
    assert [i.url for i in kept] == ["https://w.test/dated", "https://w.test/new"]
    assert notes == []


@db
async def test_daily_cap_counts_events_not_channel_rows(backend_pool: asyncpg.Pool) -> None:
    config = NotificationsConfig.model_validate(
        {
            "rules": [
                {"id": "all", "match": {"severities": [3]}, "channels": ["telegram", "email"]}
            ],
            "quiet_hours": {"enabled": False},
            "daily_alert_cap": 2,
            "repeat_throttle_hours": 0,
        }
    )
    both = frozenset({"telegram", "email"})
    for n in range(2):
        created = await create_alerts(
            backend_pool,
            AlertRequest("system", 3, None, None),
            f"event {n}",
            config,
            now=NOON + dt.timedelta(minutes=n),
            dedupe_key=f"k{n}",
            available_channels=both,
        )
        assert len(created.channels) == 2  # two rows each, one event
    third = await create_alerts(
        backend_pool,
        AlertRequest("system", 3, None, None),
        "event 3",
        config,
        now=NOON + dt.timedelta(minutes=3),
        dedupe_key="k3",
        available_channels=both,
    )
    assert third.suppressed_reason and "cap" in third.suppressed_reason


@db
async def test_breach_throttle_is_per_kpi(backend_pool: asyncpg.Pool) -> None:
    version = await backend_pool.fetchval(
        "insert into config_version (domain, body, schema_version) values"
        " ('theses', '{}'::jsonb, 1) returning id"
    )

    async def verdict(kpi: str) -> int:
        vid: int = await backend_pool.fetchval(
            "insert into verdict (ticker, kpi_id, metric, basis, kpi_period, status, operator,"
            " threshold, threshold_unit, rationale, config_version_id) values ('AAPL_US_EQ',"
            " $1, 'm', 'reported', 'quarter', 'breach', '>', 1, '%', 'r', $2) returning id",
            kpi,
            version,
        )
        return vid

    config = NotificationsConfig.model_validate(
        {"quiet_hours": {"enabled": False}, "daily_alert_cap": None}
    )
    request = AlertRequest("thesis_breach", 3, "AAPL_US_EQ", "thesis_breach")
    email = frozenset({"email"})

    async def alert(vid: int) -> Any:
        return await create_alerts(
            backend_pool, request, "m", config, now=NOON, verdict_id=vid, available_channels=email
        )

    assert (await alert(await verdict("margin"))).channels == ["email"]
    assert (await alert(await verdict("growth"))).channels == ["email"]  # another KPI
    repeat = await alert(await verdict("margin"))  # the same KPI again within 24 h
    assert repeat.suppressed_reason and "repeat" in repeat.suppressed_reason


@db
async def test_macro_first_run_is_a_baseline(backend_pool: asyncpg.Pool) -> None:
    def obs(month: int, value: str) -> Observation:
        period = dt.date(2026, month, 1)
        return Observation(
            "us_cpi", "us_cpi", "fred", period, period.strftime("%Y-%m"), Decimal(value),
            "US CPI", "%", period, "https://fred.stlouisfed.org/series/X", False,
        )  # fmt: skip

    bands = EventRulesConfig().macro_surprise_bands
    count, events = await store_and_evaluate(
        backend_pool, [obs(5, "2.0"), obs(6, "3.0"), obs(7, "4.0")], bands
    )
    assert (count, events) == (3, [])  # backfilled history raises nothing
    _, events = await store_and_evaluate(backend_pool, [obs(8, "5.0")], bands)
    assert len(events) == 1
