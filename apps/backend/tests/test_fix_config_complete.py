"""Group CONFIG-COMPLETE: every genuine preference is in the config.

- Optional effort per AI step (unset: not sent; rejected for Haiku 4, skipped defensively).
- The claim verifier's prompt override reaches the digest and escalation verifiers.
- Former constants (breach severities, earnings-check cooldown, digest windows and listing
  floor, 13F holder-drop flag, web-search tiers, Google News edition, Wikidata languages,
  KPI history length, research top holders, price-move news window) are config fields whose
  defaults keep today's behaviour, and bodies saved before them still validate.
"""

import contextlib
import datetime as dt
from collections.abc import AsyncIterator
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest
from pydantic import ValidationError

from steward.budget_guard import BudgetExceeded
from steward.chat.assistant import finish_answer
from steward.config.event_rules import EventRulesConfig
from steward.config.holding_profiles import HoldingProfile, HoldingProfilesConfig
from steward.config.jobs import (
    ChatSettings,
    DigestSettings,
    EarningsCalendarSettings,
    EarningsCheckSettings,
    EscalationSettings,
    JobsConfig,
    NewsSettings,
    ReviewSettings,
    effort_kwargs,
)
from steward.config.news_sources import GoogleNewsLocale, NewsSourcesConfig
from steward.config.notifications import NotificationsConfig
from steward.config.prompts import PromptsConfig
from steward.config.research import ResearchProfile, ResearchProfilesConfig
from steward.config.store import save_config
from steward.config.strategy import StrategyConfig
from steward.config.thesis import ThesesConfig, ThesisConfig, ThesisKPI
from steward.config.verification import ClaimVerifierSettings, VerificationConfig
from steward.documents import fetch as fetch_module
from steward.earnings import calendar as calendar_module
from steward.earnings.calendar import dates_from_ir_page
from steward.earnings.check import LoadedConfig
from steward.earnings.locate import ClaudeSearchLocator, SearchRequest
from steward.earnings.verdicts import FactRow, judge_thesis
from steward.news import digest as digest_module
from steward.news import escalation as escalation_module
from steward.news.classify import (
    Candidate,
    ClassificationOutput,
    ClassifyItem,
    ClaudeClassifier,
)
from steward.news.corroborate import ClaudeCorroborator
from steward.news.corroborate import SearchRequest as CorroborationRequest
from steward.news.digest import ClaudeDigestWriter, gather, period_text
from steward.news.escalation import ClaudeEscalator, EscalationInput, price_move_input
from steward.news.feeds import google_news_url
from steward.news.pipeline import queue_earnings_check
from steward.portfolio.breaches import evaluate, record_breaches
from steward.portfolio.drift import HoldingValue, compute_drift
from steward.research import gather as research_gather
from steward.research.sources import Registry
from steward.review import monthly
from steward.sources.wikidata import WikidataClient
from steward.verify.claims import Claim, ClaudeClaimVerifier

OVERRIDE = "The owner's claim verifier prompt."


def _response(parsed: Any) -> Any:
    usage = SimpleNamespace(
        input_tokens=10,
        output_tokens=1,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        server_tool_use=None,
    )
    return SimpleNamespace(usage=usage, stop_reason="end_turn", parsed_output=parsed, content=[])


class _Messages:
    def __init__(self, parsed: Any) -> None:
        self.parsed = parsed
        self.calls: list[dict[str, Any]] = []

    async def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return _response(self.parsed)


class _Client:
    def __init__(self, parsed: Any = None) -> None:
        self.messages = _Messages(parsed)


# ---------------------------------------------------------------------------
# Bodies saved before these fields still validate, with today's defaults
# ---------------------------------------------------------------------------


NEW_JOBS_FIELDS = {
    "earnings_check": ["locate_effort", "kpi_history_periods"],
    "earnings_calendar_settings": ["effort"],
    "news": ["classification_effort", "corroboration_effort"],
    "digest": ["effort", "min_severity_listed", "daily_window_hours", "weekly_window_days"],
    "escalation": ["effort", "source_tiers", "price_move_news_days", "price_move_news_items"],
    "chat": ["effort", "source_tiers"],
    "review": ["holder_drop_flag_pct", "use_batch", "batch_wait_minutes"],
    "reference": ["wikidata_languages"],
}


def test_an_old_jobs_body_validates_with_todays_defaults() -> None:
    body = JobsConfig().model_dump(mode="json")
    for section, fields in NEW_JOBS_FIELDS.items():
        for f in fields:
            del body[section][f]
    loaded = JobsConfig.model_validate(body)
    assert loaded == JobsConfig()
    assert loaded.earnings_check.locate_effort is None
    assert loaded.earnings_check.kpi_history_periods == 8
    assert loaded.earnings_calendar_settings.effort is None
    assert loaded.news.classification_effort is None and loaded.news.corroboration_effort is None
    assert loaded.digest.effort is None and loaded.digest.min_severity_listed == 1
    assert loaded.digest.window("daily") == dt.timedelta(days=1)
    assert loaded.digest.window("weekly") == dt.timedelta(days=7)
    assert loaded.escalation.source_tiers == [1, 2] and loaded.chat.source_tiers == [1, 2]
    assert (loaded.escalation.price_move_news_days, loaded.escalation.price_move_news_items) == (
        3,
        6,
    )
    assert loaded.review.holder_drop_flag_pct == 10
    assert loaded.reference.wikidata_languages == ["en", "de", "cs"]


def test_other_old_bodies_validate_with_todays_defaults() -> None:
    verification = VerificationConfig().model_dump(mode="json")
    del verification["claim_verifier"]["effort"]
    assert VerificationConfig.model_validate(verification).claim_verifier.effort is None

    strategy = StrategyConfig.model_validate({"base_currency": "EUR"})
    assert strategy.hard_limit_severity == "severity_3"
    assert strategy.band_breach_severity == "severity_1"

    sources = NewsSourcesConfig().model_dump(mode="json")
    del sources["google_news_locale"]
    locale = NewsSourcesConfig.model_validate(sources).google_news_locale
    assert (locale.language, locale.country, locale.ceid) == ("en-US", "US", "US:en")

    rules = EventRulesConfig().model_dump(mode="json")
    del rules["earnings_check_cooldown_days"]
    assert EventRulesConfig.model_validate(rules).earnings_check_cooldown_days == 10

    profiles = ResearchProfilesConfig().model_dump(mode="json")
    for p in profiles["profiles"]:
        del p["top_holders"]
    assert all(p.top_holders == 3 for p in ResearchProfilesConfig.model_validate(profiles).profiles)


# ---------------------------------------------------------------------------
# Effort: optional, rejected for Haiku 4, skipped for it at the call site
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "body"),
    [
        (EarningsCheckSettings, {"locate_model": "claude-haiku-4-5", "locate_effort": "low"}),
        (EarningsCalendarSettings, {"effort": "low"}),  # default model is Haiku 4.5
        (NewsSettings, {"classification_effort": "low"}),
        (NewsSettings, {"corroboration_effort": "high"}),
        (DigestSettings, {"model": "claude-haiku-4-5", "effort": "low"}),
        (EscalationSettings, {"model": "claude-haiku-4-5", "effort": "low"}),
        (ChatSettings, {"model": "claude-haiku-4-5", "effort": "max"}),
        (ClaimVerifierSettings, {"effort": "low"}),  # default model is Haiku 4.5
    ],
)
def test_effort_on_haiku_4_is_accepted(model: Any, body: dict[str, Any]) -> None:
    # Haiku 4 maps the level to an extended-thinking budget (see reasoning_kwargs).
    model.model_validate(body)


def test_effort_on_a_model_that_takes_it_is_accepted() -> None:
    assert (
        NewsSettings(corroboration_model="claude-sonnet-5", corroboration_effort="low")
    ).corroboration_effort == "low"
    assert ClaimVerifierSettings(model="claude-sonnet-5", effort="medium").effort == "medium"
    assert EarningsCalendarSettings(model="claude-sonnet-5", effort="xhigh").effort == "xhigh"
    with pytest.raises(ValidationError):
        ChatSettings.model_validate({"effort": "extreme"})


def test_effort_kwargs() -> None:
    assert effort_kwargs("claude-sonnet-5", None, 100) == {"max_tokens": 100}
    assert effort_kwargs("claude-haiku-4-5", "low", 100) == {
        "max_tokens": 2148,
        "thinking": {"type": "enabled", "budget_tokens": 2048},
    }
    assert effort_kwargs("claude-sonnet-5", "low", 100) == {
        "max_tokens": 100,
        "output_config": {"effort": "low"},
    }


REQUEST = SearchRequest("AAPL", "Apple", dt.date(2025, 7, 31), (2025, "Q3"), ["x.test"], [])


async def test_locate_sends_effort_only_when_set() -> None:
    answer = SimpleNamespace(url=None, reason="none")
    default: Any = _Client(answer)
    await ClaudeSearchLocator(default, "claude-sonnet-5", 3).locate(REQUEST)
    assert "output_config" not in default.messages.calls[0]
    client: Any = _Client(answer)
    await ClaudeSearchLocator(client, "claude-sonnet-5", 3, effort="low").locate(REQUEST)
    assert client.messages.calls[0]["output_config"] == {"effort": "low"}
    assert client.messages.calls[0]["tools"][0]["type"] == "web_search_20260209"


async def test_locate_on_haiku_uses_the_older_search_tool_and_no_effort() -> None:
    client: Any = _Client(SimpleNamespace(url=None, reason="none"))
    await ClaudeSearchLocator(client, "claude-haiku-4-5", 3, effort="low").locate(REQUEST)
    call = client.messages.calls[0]
    assert call["tools"][0]["type"] == "web_search_20250305"
    assert call["tools"][0]["allowed_domains"] == ["x.test"]
    assert "output_config" not in call


async def test_classifier_and_corroborator_send_effort() -> None:
    item = ClassifyItem(
        index=0,
        headline="Apple announces buyback",
        summary=None,
        outlet="reuters.com",
        tier=2,
        published_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
        candidates=(Candidate("AAPL_US_EQ", "name", "Apple"),),
    )
    client: Any = _Client(ClassificationOutput(items=[]))
    classifier = ClaudeClassifier(client, "claude-sonnet-5", effort="medium")
    await classifier.classify([item], [], dt.date(2026, 9, 1))
    assert client.messages.calls[0]["output_config"] == {"effort": "medium"}

    haiku: Any = _Client(ClassificationOutput(items=[]))
    await ClaudeClassifier(haiku, "claude-haiku-4-5", effort="medium").classify(
        [item], [], dt.date(2026, 9, 1)
    )
    assert "output_config" not in haiku.messages.calls[0]

    corro: Any = _Client(None)
    request = CorroborationRequest(
        headline="h",
        summary=None,
        company="Apple",
        published_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
        own_outlets=frozenset(),
        domains=("reuters.com",),
    )
    await ClaudeCorroborator(corro, "claude-sonnet-5", 2, effort="high").search(
        request, lambda _: 2
    )
    assert corro.messages.calls[0]["output_config"] == {"effort": "high"}


async def test_calendar_sends_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(*_: Any, **__: Any) -> fetch_module.Document:
        return fetch_module.Document("https://ir.test/cal", fetch_module.TEXT, b"calendar")

    monkeypatch.setattr("steward.earnings.calendar.fetch_document", fake_fetch)
    profile = HoldingProfile(ticker="X_US_EQ", financial_calendar_url="https://ir.test/cal")
    for settings, expected in (
        (EarningsCalendarSettings(), None),
        (EarningsCalendarSettings(model="claude-sonnet-5", effort="low"), {"effort": "low"}),
    ):
        client: Any = _Client(SimpleNamespace(entries=[]))
        await dates_from_ir_page(client, None, profile, settings, dt.date(2026, 9, 1))  # type: ignore[arg-type]
        assert client.messages.calls[0].get("output_config") == expected


async def test_claim_verifier_and_escalator_send_effort() -> None:
    client: Any = _Client(SimpleNamespace(verdicts=[]))
    claims = [Claim(0, "Sentence.", ("Snippet.",))]
    await ClaudeClaimVerifier(client, "claude-sonnet-5", effort="low").check(
        claims, dt.date(2026, 9, 1)
    )
    assert client.messages.calls[0]["output_config"] == {"effort": "low"}

    esc: Any = _Client(None)
    request = EscalationInput(
        ticker="A",
        name="A",
        trigger="story",
        cluster_id=1,
        event_type="guidance",
        severity=2,
        headline="h",
        sources=[],
        material="m",
        thesis=None,
        domains=(),
    )
    await ClaudeEscalator(esc, EscalationSettings(effort="xhigh")).write(
        request, dt.date(2026, 9, 1)
    )
    assert esc.messages.calls[0]["output_config"] == {"effort": "xhigh"}
    plain: Any = _Client(None)
    await ClaudeEscalator(plain, EscalationSettings()).write(request, dt.date(2026, 9, 1))
    assert "output_config" not in plain.messages.calls[0]


def test_digest_params_merge_effort_with_the_format() -> None:
    client: Any = object()
    plain = ClaudeDigestWriter(client, DigestSettings())._params("m", dt.date(2026, 9, 1))
    assert set(plain["output_config"]) == {"format"}
    params = ClaudeDigestWriter(client, DigestSettings(effort="medium"))._params(
        "m", dt.date(2026, 9, 1)
    )
    assert params["output_config"]["effort"] == "medium"
    assert params["output_config"]["format"]["type"] == "json_schema"


# ---------------------------------------------------------------------------
# The claim verifier's prompt override and effort reach digest and escalation
# ---------------------------------------------------------------------------


def _patch_handler_deps(monkeypatch: pytest.MonkeyPatch, module: Any, verification: Any) -> None:
    async def fake_load_active(_pool: Any, domain: str, _model: Any) -> Any:
        return SimpleNamespace(id=1, body=verification) if domain == "verification" else None

    async def fake_prompts(_pool: Any) -> PromptsConfig:
        return PromptsConfig(overrides={"claim_verifier": OVERRIDE})

    monkeypatch.setattr(module, "create_client", lambda _settings: object())
    monkeypatch.setattr(module, "load_active", fake_load_active)
    monkeypatch.setattr(module, "load_prompts", fake_prompts)


VERIFICATION = VerificationConfig.model_validate(
    {"claim_verifier": {"model": "claude-sonnet-5", "effort": "low"}}
)


async def test_digest_claim_verifier_gets_the_override(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_handler_deps(monkeypatch, digest_module, VERIFICATION)
    seen: dict[str, Any] = {}

    async def fake_run(_ctx: Any, _kind: str, **kwargs: Any) -> None:
        seen.update(kwargs)

    monkeypatch.setattr(digest_module, "run_digest", fake_run)
    await digest_module._handler("daily")(SimpleNamespace(pool=None))  # type: ignore[arg-type]
    verifier = seen["claim_verifier"]
    assert verifier._system == OVERRIDE and verifier._effort == "low"


async def test_escalation_claim_verifier_gets_the_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_handler_deps(monkeypatch, escalation_module, VERIFICATION)
    seen: list[Any] = []

    async def fake_run(_ctx: Any, deps: Any) -> None:
        seen.append(deps)

    monkeypatch.setattr(escalation_module, "run_escalation", fake_run)
    await escalation_module.event_escalation_handler(SimpleNamespace(pool=None))  # type: ignore[arg-type]
    verifier = seen[0].claim_verifier
    assert verifier._system == OVERRIDE and verifier._effort == "low"
    assert seen[0].settings == EscalationSettings()


# ---------------------------------------------------------------------------
# A budget cap stops the calendar refresh instead of being swallowed per ticker
# ---------------------------------------------------------------------------


class _Ctx:
    def __init__(self) -> None:
        self.pool = None
        self.params = {"all": True}

    @contextlib.asynccontextmanager
    async def step(self, _name: str) -> AsyncIterator[Any]:
        yield SimpleNamespace(detail={}, usage=None)


async def test_calendar_refresh_stops_on_a_budget_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    config = LoadedConfig(
        theses=ThesesConfig(),
        theses_version_id=None,
        profiles=HoldingProfilesConfig(),
        verification=VerificationConfig(),
        jobs=JobsConfig(),
    )
    calls: list[str] = []

    async def fake_load_config(_ctx: Any) -> LoadedConfig:
        return config

    async def fake_prompts(_pool: Any) -> PromptsConfig:
        return PromptsConfig()

    async def fake_tickers(_pool: Any, _theses: Any) -> list[str]:
        return ["A_EQ", "B_EQ"]

    async def fake_find(_http: Any, _client: Any, _key: Any, profile: Any, *_: Any) -> Any:
        calls.append(profile.ticker)
        raise BudgetExceeded("monthly cap reached")

    monkeypatch.setattr(calendar_module, "load_config", fake_load_config)
    monkeypatch.setattr(calendar_module, "load_prompts", fake_prompts)
    monkeypatch.setattr(calendar_module, "_holding_tickers", fake_tickers)
    monkeypatch.setattr(calendar_module, "_find_date", fake_find)
    with pytest.raises(BudgetExceeded):
        await calendar_module.refresh_calendar(_Ctx(), full=True)  # type: ignore[arg-type]
    assert calls == ["A_EQ"]


# ---------------------------------------------------------------------------
# Portfolio breach severities
# ---------------------------------------------------------------------------


def _strategy(**extra: Any) -> StrategyConfig:
    return StrategyConfig.model_validate(
        {
            "base_currency": "EUR",
            "target_positions": [
                {"ticker": "A_EQ", "target_weight": 0.3, "band": 0.05, "hard_limit": 0.4}
            ],
            **extra,
        }
    )


def _drift(strategy: StrategyConfig, weight_a: float) -> Any:
    total = Decimal(1000)
    a = Decimal(str(weight_a)) * total
    rest = (total - a) / 2
    return compute_drift(
        [
            HoldingValue("A_EQ", "EUR", a),
            HoldingValue("B_EQ", "EUR", rest),
            HoldingValue("C_EQ", "EUR", rest),
        ],
        Decimal(0),
        strategy,
    )


def test_breach_severities_follow_the_strategy() -> None:
    default = _strategy()
    found = {f.kind: f.severity for f in evaluate(_drift(default, 0.5), default)}
    assert found == {"hard_limit": 3, "band": 1}
    custom = _strategy(hard_limit_severity="severity_2", band_breach_severity="severity_2")
    found = {f.kind: f.severity for f in evaluate(_drift(custom, 0.5), custom)}
    assert found == {"hard_limit": 2, "band": 2}


@pytest.mark.db
async def test_a_band_breach_at_severity_2_alerts(backend_pool: asyncpg.Pool) -> None:
    async with backend_pool.acquire() as conn:
        await save_config(
            conn,
            "notifications",
            NotificationsConfig.model_validate(
                {
                    "quiet_hours": {"enabled": False},
                    "rules": [
                        {"id": "all", "match": {"severities": [2, 3]}, "channels": ["telegram"]}
                    ],
                    "repeat_throttle_hours": 0,
                }
            ),
            created_by=None,
        )
    job = await backend_pool.fetchval(
        "insert into job (job_type, trigger) values ('portfolio_sync', 'manual') returning id"
    )
    now = dt.datetime(2026, 9, 25, 10, tzinfo=dt.UTC)
    default = _strategy()
    # Above the band, below the hard limit: severity 1 by default, never alerted.
    await record_breaches(
        backend_pool,
        evaluate(_drift(default, 0.37), default),
        job_id=job,
        config_version_id=None,
        now=now,
    )
    assert await backend_pool.fetchval("select count(*) from alert") == 0
    # Back in band: the open flag clears, so the next one opens a new breach.
    await record_breaches(backend_pool, [], job_id=job, config_version_id=None, now=now)
    loud = _strategy(band_breach_severity="severity_2")
    summary = await record_breaches(
        backend_pool,
        evaluate(_drift(loud, 0.37), loud),
        job_id=job,
        config_version_id=None,
        now=now,
    )
    assert summary.opened == ["band:position:A_EQ"] and len(summary.alerts) == 1
    alert = await backend_pool.fetchrow("select severity, message from alert")
    assert alert["severity"] == 2 and "target band" in alert["message"]


# ---------------------------------------------------------------------------
# Former constants
# ---------------------------------------------------------------------------


class _Pool:
    def __init__(self, fetch_rows: list[list[Any]] | None = None, value: Any = None) -> None:
        self.fetches: list[tuple[str, tuple[Any, ...]]] = []
        self.values: list[tuple[str, tuple[Any, ...]]] = []
        self._rows = list(fetch_rows or [])
        self._value = value

    async def fetch(self, query: str, *args: Any) -> list[Any]:
        self.fetches.append((query, args))
        return self._rows.pop(0) if self._rows else []

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.values.append((query, args))
        return self._value


async def test_earnings_check_cooldown_comes_from_the_event_rules() -> None:
    pool = _Pool(value=True)
    now = dt.datetime(2026, 9, 25, 10, tzinfo=dt.UTC)
    story = SimpleNamespace(
        status="confirmed", ticker="A_EQ", event_type="earnings_release", published_at=now
    )
    config = SimpleNamespace(event_rules=EventRulesConfig(earnings_check_cooldown_days=3))
    assert await queue_earnings_check(SimpleNamespace(pool=pool), story, config, now) is None  # type: ignore[arg-type]
    assert pool.values[0][1][1] == now - dt.timedelta(days=3)
    with pytest.raises(ValidationError):
        EventRulesConfig(earnings_check_cooldown_days=0)


async def test_digest_listing_floor_and_windows() -> None:
    pool = _Pool()
    start = dt.datetime(2026, 9, 24, tzinfo=dt.UTC)
    await gather(pool, start, start + dt.timedelta(days=1), HoldingProfilesConfig(), 2, 2)  # type: ignore[arg-type]
    query, args = pool.fetches[0]
    assert "severity >= $3" in query and args[2] == 2
    assert period_text(dt.timedelta(hours=24)) == "24 hours"
    assert period_text(dt.timedelta(days=7)) == "7 days"
    assert period_text(dt.timedelta(hours=12)) == "12 hours"
    settings = DigestSettings(daily_window_hours=12, weekly_window_days=14)
    assert settings.window("daily") == dt.timedelta(hours=12)
    assert settings.window("weekly") == dt.timedelta(days=14)
    with pytest.raises(ValidationError, match="min_severity_listed"):
        DigestSettings(min_severity_listed=3, min_severity_summarised=2)


async def test_review_holder_drop_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    periods = [
        SimpleNamespace(period="2026-06-30", holders=92),
        SimpleNamespace(period="2026-03-31", holders=100),
    ]

    async def fake_ownership(*_: Any, **__: Any) -> Any:
        return SimpleNamespace(periods=periods, cusips=["X"])

    monkeypatch.setattr(monthly, "holding_ownership", fake_ownership)
    position = {"ticker": "A_EQ", "avg_daily_turnover": None, "weight": 0.1}

    async def flagged(settings: ReviewSettings) -> list[str]:
        pool = _Pool([[position], []])
        out = await monthly.flags(pool, Registry(), settings)  # type: ignore[arg-type]
        return [s.text for s in out if "13F holders" in s.text]

    assert await flagged(ReviewSettings()) == []  # an 8 % fall is under the default 10 %
    assert len(await flagged(ReviewSettings(holder_drop_flag_pct=5))) == 1


def test_chat_citations_follow_the_configured_tiers() -> None:
    draft = SimpleNamespace(
        sentences=[SimpleNamespace(text="Said.", source_ids=[], urls=["https://blog.test/a"])]
    )
    titles = {"https://blog.test/a": "A blog post"}

    def tier_of(_host: Any) -> int:
        return 3

    [default] = finish_answer(draft, Registry(), titles, tier_of)  # type: ignore[arg-type]
    assert default.no_source
    [wide] = finish_answer(draft, Registry(), titles, tier_of, [1, 2, 3])  # type: ignore[arg-type]
    assert not wide.no_source
    with pytest.raises(ValidationError):
        ChatSettings(source_tiers=[])
    assert ChatSettings(source_tiers=[2, 1, 2]).source_tiers == [1, 2]


async def test_price_move_news_window() -> None:
    pool = _Pool(value="Apple")
    params = {"ticker": "A_EQ", "move_pct": 9.0, "date": "2026-09-24"}
    await price_move_input(pool, params, ThesesConfig(), (), 5, 10)  # type: ignore[arg-type]
    query, args = pool.fetches[0]
    assert "- $3::int" in query and "limit $4" in query and args[2:] == (5, 10)
    await price_move_input(pool, params, ThesesConfig(), ())  # type: ignore[arg-type]
    assert pool.fetches[1][1][2:] == (3, 6)


def test_google_news_locale() -> None:
    old = (
        "https://news.google.com/rss/search?q=%28%22Apple%22%29+when%3A3d&hl=en-US&gl=US&ceid=US:en"
    )
    assert google_news_url(["Apple"], 3) == old
    cz = GoogleNewsLocale(language="cs", country="CZ")
    assert google_news_url(["Apple"], 3, cz) == old.replace(
        "hl=en-US&gl=US&ceid=US:en", "hl=cs&gl=CZ&ceid=CZ:cs"
    )
    for bad in ({"language": "english"}, {"country": "usa"}, {"language": "en_us"}):
        with pytest.raises(ValidationError):
            GoogleNewsLocale.model_validate(bad)


async def test_wikidata_alias_languages() -> None:
    queries: list[str] = []

    class _Http:
        async def get(self, _url: str, params: dict[str, str], **_: Any) -> Any:
            queries.append(params["query"])
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"results": {"bindings": []}},
                raise_for_status=lambda: None,
            )

    client = WikidataClient(_Http(), "test")  # type: ignore[arg-type]
    await client.aliases("Q1")
    assert 'IN ("en", "de", "cs")' in queries[0]
    await client.aliases("Q1", ["fr", "it"])
    assert 'IN ("fr", "it")' in queries[1]
    from steward.config.jobs import ReferenceSettings

    with pytest.raises(ValidationError):
        ReferenceSettings(wikidata_languages=["EN"])
    with pytest.raises(ValidationError):
        ReferenceSettings(wikidata_languages=['en") || true || ("'])


def test_kpi_history_periods() -> None:
    ids = iter(range(1, 1000))
    facts = [
        FactRow(next(ids), "revenue", "reported", fy, fp, Decimal(100 + fy), "currency", "USD")
        for fy in range(2018, 2026)
        for fp in ("Q1", "Q2", "Q3", "Q4")
    ]
    kpi = ThesisKPI(id="k", metric="revenue", operator=">=", threshold=1, unit="USD")
    thesis = ThesisConfig(ticker="AAPL_US_EQ", why_owned="x", kpis=[kpi])
    assert len(judge_thesis(thesis, facts)) == 8
    assert len(judge_thesis(thesis, facts, 3)) == 3


async def test_research_top_holders(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []

    async def fake_ownership(*_: Any, top: int, **__: Any) -> Any:
        seen.append(top)
        return SimpleNamespace(periods=[], top_holders=[], cusips=[])

    monkeypatch.setattr(research_gather, "holding_ownership", fake_ownership)
    await research_gather.ownership(_Pool(), Registry(), "A_EQ")  # type: ignore[arg-type]
    await research_gather.ownership(_Pool(), Registry(), "A_EQ", 7)  # type: ignore[arg-type]
    assert seen == [3, 7]
    profile = ResearchProfile(id="p", name="P", sections=["ownership"], top_holders=5)
    assert profile.top_holders == 5


def test_effort_maps_to_the_model() -> None:
    from steward.config.jobs import THINKING_BUDGET, reasoning_kwargs

    fmt = {"format": {"type": "json_schema"}}
    # Models that take effort get it in output_config, merged with the format.
    assert reasoning_kwargs("claude-sonnet-5", "high", 8000, fmt) == {
        "max_tokens": 8000,
        "output_config": {"format": {"type": "json_schema"}, "effort": "high"},
    }
    # Haiku 4 gets the equivalent: extended thinking, with max_tokens grown by the budget.
    out = reasoning_kwargs("claude-haiku-4-5", "medium", 4000, fmt)
    assert out == {
        "max_tokens": 4000 + THINKING_BUDGET["medium"],
        "thinking": {"type": "enabled", "budget_tokens": THINKING_BUDGET["medium"]},
        "output_config": {"format": {"type": "json_schema"}},
    }
    assert out["thinking"]["budget_tokens"] >= 1024
    # xhigh is not a 4.6 level.
    assert reasoning_kwargs("claude-sonnet-4-6", "xhigh", 100)["output_config"] == {
        "effort": "high"
    }
    # No effort: nothing but max_tokens (the model's default).
    assert reasoning_kwargs("claude-haiku-4-5", None, 4000) == {"max_tokens": 4000}
    assert reasoning_kwargs("claude-opus-5-5", None, 4000, fmt) == {
        "max_tokens": 4000,
        "output_config": fmt,
    }
