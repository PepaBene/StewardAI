"""The news pipeline's code-only rules: corroboration, routing, linking, labelling, parsing,
severities and the digest's citation check. No database, no network."""

import datetime as dt
from typing import Any

import pytest

from steward.config import EventRulesConfig, NotificationsConfig
from steward.config.holding_profiles import HoldingProfile
from steward.config.news_sources import NewsSource, NewsSourcesConfig
from steward.config.notifications import QuietHours
from steward.news.classify import Candidate, ClassifyItem, HoldingContext, ItemLabel, check_label
from steward.news.corroborate import OtherStory, SearchFinding, decide, verify_urls
from steward.news.digest import DigestBody, Sentence, keep_cited, render_text
from steward.news.eval import OracleClassifier, evaluate, load_set
from steward.news.feeds import label_filing, page_links, parse_company_news, parse_feed
from steward.news.link import Linker, holding_terms, short_name
from steward.notify.route import AlertRequest, RouteContext, route
from steward.sources.edgar import Filing

UTC = dt.UTC

# ---------------------------------------------------------------------------
# Corroboration (Phase 3 AC)
# ---------------------------------------------------------------------------


def test_a_tier1_item_is_always_confirmed() -> None:
    d = decide(best_tier=1, outlets=["sec.gov"], rumour=False)
    assert (d.status, d.confidence) == ("confirmed", "verified")
    # Even when it reads like a rumour or a search found nothing.
    assert decide(best_tier=1, outlets=[], rumour=True, finding=SearchFinding()).status == (
        "confirmed"
    )


@pytest.mark.parametrize(
    "others",
    [
        [],
        # Another story from the same outlet is not independent.
        [OtherStory(2, 2, frozenset({"reuters.com"}))],
        # Tier-3 stories never corroborate.
        [OtherStory(3, 3, frozenset({"reddit.com"}))],
    ],
)
def test_a_single_tier2_item_is_never_confirmed(others: list[OtherStory]) -> None:
    d = decide(best_tier=2, outlets=["reuters.com"], rumour=False, others=others)
    assert d.status == "unconfirmed"
    assert d.confidence == "unconfirmed"


def test_two_independent_tier2_stories_confirm() -> None:
    d = decide(
        best_tier=2,
        outlets=["reuters.com"],
        rumour=False,
        others=[OtherStory(9, 2, frozenset({"cnbc.com"}))],
    )
    assert (d.status, d.confidence) == ("confirmed", "corroborated")


def test_syndicated_copies_count_once() -> None:
    # One story carried by three outlets (copies) is still one source.
    d = decide(best_tier=2, outlets=["reuters.com", "cnbc.com", "marketwatch.com"], rumour=False)
    assert d.status == "unconfirmed"


def test_rumours_need_a_tier1_source() -> None:
    others = [OtherStory(9, 2, frozenset({"cnbc.com"}))]
    assert decide(best_tier=2, outlets=["reuters.com"], rumour=True, others=others).status == (
        "unconfirmed"
    )
    tier1 = SearchFinding(supporting=(("https://www.globenewswire.com/x", 1),))
    assert decide(best_tier=2, outlets=["reuters.com"], rumour=True, finding=tier1).status == (
        "confirmed"
    )


def test_search_findings_confirm_or_dispute() -> None:
    support = SearchFinding(supporting=(("https://www.cnbc.com/a", 2),))
    assert decide(best_tier=2, outlets=["reuters.com"], rumour=False, finding=support).status == (
        "confirmed"
    )
    against = SearchFinding(contradicting=(("https://www.reuters.com/denial", 2),))
    d = decide(best_tier=2, outlets=["cnbc.com"], rumour=False, finding=against)
    assert (d.status, d.confidence) == ("disputed", "disputed")


def test_tier3_only_is_sentiment() -> None:
    assert decide(best_tier=3, outlets=["reddit.com"], rumour=False).status == "sentiment"


def test_search_urls_are_checked_not_trusted() -> None:
    config = NewsSourcesConfig()
    seen = {"https://www.cnbc.com/a", "https://www.reuters.com/b", "https://blog.example.com/c"}
    kept, rejected = verify_urls(
        [
            "https://www.cnbc.com/a",  # kept
            "https://www.reuters.com/b",  # the story's own outlet
            "https://blog.example.com/c",  # no tier
            "https://www.apnews.com/made-up",  # never in the results
        ],
        seen,
        frozenset({"reuters.com"}),
        config.domain_tier,
    )
    assert kept == [("https://www.cnbc.com/a", 2)]
    assert len(rejected) == 3


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

NOON = dt.datetime(2026, 9, 25, 10, 0, tzinfo=UTC)  # 12:00 in Prague
NIGHT = dt.datetime(2026, 9, 25, 21, 30, tzinfo=UTC)  # 23:30 in Prague


def _request(severity: int = 3, event: str = "guidance_change", ticker: str = "AAPL_US_EQ") -> Any:
    return AlertRequest("news", severity, ticker, event)


def test_severity3_goes_to_telegram_now_outside_quiet_hours() -> None:
    rows, why = route(_request(), NotificationsConfig(), RouteContext(now=NOON))
    assert [(r.channel, r.status, r.send_after) for r in rows] == [("telegram", "pending", NOON)]
    assert "instant-severity-3" in why


def test_quiet_hours_hold_until_they_end() -> None:
    rows, _ = route(_request(), NotificationsConfig(), RouteContext(now=NIGHT))
    assert rows[0].status == "pending"
    assert rows[0].send_after == dt.datetime(2026, 9, 26, 5, 0, tzinfo=UTC)  # 07:00 Prague


def test_quiet_hours_modes() -> None:
    config = NotificationsConfig.model_validate(
        {
            "rules": [
                {"id": "a", "match": {"severities": [3]}, "channels": ["email"],
                 "during_quiet_hours": "send"},
                {"id": "b", "match": {"severities": [2]}, "channels": ["email"],
                 "during_quiet_hours": "digest_only"},
            ]
        }
    )  # fmt: skip
    rows, _ = route(_request(3), config, RouteContext(now=NIGHT))
    assert rows[0].send_after == NIGHT and rows[0].status == "pending"
    rows, _ = route(_request(2), config, RouteContext(now=NIGHT))
    assert rows[0].status == "suppressed"


def test_first_matching_rule_wins_and_unmatched_waits_for_digest() -> None:
    config = NotificationsConfig.model_validate(
        {
            "rules": [
                {"id": "apple", "match": {"severities": [3], "tickers": ["AAPL_US_EQ"]},
                 "channels": ["email"]},
                {"id": "all", "match": {"severities": [3]}, "channels": ["telegram"]},
            ]
        }
    )  # fmt: skip
    assert route(_request(), config, RouteContext(now=NOON))[0][0].channel == "email"
    assert route(_request(ticker="MSFT_US_EQ"), config, RouteContext(now=NOON))[0][0].channel == (
        "telegram"
    )
    assert route(_request(2), config, RouteContext(now=NOON))[0] == []


def test_mutes_cap_and_throttle_suppress() -> None:
    config = NotificationsConfig(muted_tickers=["AAPL_US_EQ"], daily_alert_cap=2)
    assert route(_request(), config, RouteContext(now=NOON))[0][0].status == "suppressed"
    config = NotificationsConfig(daily_alert_cap=2)
    capped = route(_request(), config, RouteContext(now=NOON, alerts_today=2))[0][0]
    assert capped.suppressed_reason and "cap" in capped.suppressed_reason
    repeat = route(_request(), config, RouteContext(now=NOON, recent_alert_id=7))[0][0]
    assert repeat.suppressed_reason and "#7" in repeat.suppressed_reason
    # A test alert is never capped or throttled.
    test = AlertRequest("test", 3, None, "guidance_change")
    assert route(test, config, RouteContext(now=NOON, alerts_today=9))[0][0].status == "pending"


def test_quiet_hours_window() -> None:
    q = QuietHours(start="22:00", end="07:00")
    assert q.end_if_quiet(NOON) is None
    end = q.end_if_quiet(dt.datetime(2026, 9, 26, 3, 0, tzinfo=UTC))  # 05:00 Prague
    assert end is not None and end.hour == 7 and end.day == 26
    daytime = QuietHours(start="12:00", end="14:00")
    assert daytime.end_if_quiet(NOON) is not None


# ---------------------------------------------------------------------------
# Linking and labelling
# ---------------------------------------------------------------------------


def _linker() -> Linker:
    apple = HoldingProfile(
        ticker="AAPL_US_EQ",
        company_name="Apple Inc.",
        suppliers=["TSMC"],
        watch_topics=["App Store fees"],
    )
    sap = HoldingProfile(ticker="SAPd_EQ", company_name="SAP SE", sec_ticker="SAP")
    return Linker(
        [
            holding_terms("AAPL_US_EQ", apple, "Apple", "US0378331005"),
            holding_terms("SAPd_EQ", sap, None, None),
        ]
    )


def test_links_by_name_ticker_relation_and_topic() -> None:
    linker = _linker()
    assert [(x.ticker, x.via) for x in linker.link("Apple unveils a new iPhone")] == [
        ("AAPL_US_EQ", "name")
    ]
    assert linker.link("Shares of $AAPL slipped")[0].via == "ticker"
    assert linker.link("TSMC raises capex")[0].via == "supplier"
    assert linker.link("EU probes app store fees")[0].via == "topic"
    assert linker.link("Maple sap season starts early") == []  # case matters for short names
    assert [x.via for x in linker.link("SAP beats estimates")] == ["name"]
    assert linker.link("Unrelated headline", direct=["SAPd_EQ"])[0].via == "source"
    assert linker.link("Unrelated headline") == []


def test_short_names() -> None:
    assert short_name("Apple Inc.") == "Apple"
    assert short_name("ASML Holding N.V.") == "ASML"
    assert short_name("Novo Nordisk A/S") == "Novo Nordisk"


def test_labels_are_checked_against_the_item() -> None:
    item = ClassifyItem(
        0, "Apple is reportedly in talks to buy X", None, "reuters.com", 2, NOON,
        (Candidate("AAPL_US_EQ", "name", "Apple"),),
    )  # fmt: skip
    context = {"AAPL_US_EQ": HoldingContext("AAPL_US_EQ", "Apple", kpis=(("growth", "growth"),))}
    raw = ItemLabel(
        index=0, event_type="m_and_a", affected_ticker="MSFT_US_EQ", relevant=True,
        thesis_link="growth", rumour=False, event_date="2026-13-01",
    )  # fmt: skip
    label = check_label(raw, item, context)
    assert label.affected_ticker is None and label.relevant is False
    assert label.thesis_link is None
    assert label.rumour is True  # rumour language found in code even though the model missed it
    assert label.event_date is None
    ok = check_label(raw.model_copy(update={"affected_ticker": "AAPL_US_EQ"}), item, context)
    assert ok.affected_ticker == "AAPL_US_EQ" and ok.thesis_link == "growth"


def test_event_rule_severities() -> None:
    rules = EventRulesConfig.model_validate(
        {"overrides": [{"ticker": "AAPL_US_EQ", "event_type": "product_business", "severity": 3}]}
    )
    assert rules.severity("guidance_change", "X") == 3
    assert rules.severity("product_business", "AAPL_US_EQ") == 3
    assert rules.severity("product_business", "MSFT_US_EQ") == 1
    assert rules.severity("regulatory_legal", "X", thesis_linked=True) == 3
    assert rules.severity("insider_transaction", "X", insider_cluster=True) == 2
    assert rules.severity("analyst_rating", "X") == 0


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Wire</title><language>en-us</language>
<item><title>Apple raises dividend</title><link>https://example.com/a?utm_source=rss</link>
<pubDate>Thu, 24 Sep 2026 12:00:00 GMT</pubDate><description>&lt;p&gt;Board approves.&lt;/p&gt;</description></item>
<item><title>Apple buys startup - Reuters</title><link>https://news.google.com/rss/articles/x</link>
<pubDate>Thu, 24 Sep 2026 13:00:00 +0200</pubDate><source url="https://www.reuters.com">Reuters</source></item>
</channel></rss>"""  # noqa: E501


def test_rss_parsing() -> None:
    items = parse_feed(RSS, "wire")
    assert [i.headline for i in items] == ["Apple raises dividend", "Apple buys startup - Reuters"]
    assert items[0].published_at == dt.datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
    assert items[1].published_at == dt.datetime(2026, 9, 24, 11, 0, tzinfo=UTC)
    assert items[0].summary == "Board approves."
    assert items[1].outlet_host == "reuters.com" and items[1].outlet_name == "Reuters"
    assert items[0].language == "en"


def test_page_links_keep_site_articles() -> None:
    html = (
        '<nav><a href="/about">About the company and its long history</a></nav>'
        '<a href="/news/q3-results">Third-quarter results published today</a>'
        '<a href="https://other.com/x">An external link with a long title</a>'
        '<a href="/news/short">Short</a>'
    )
    assert page_links(html, "https://ir.example.com/news") == [
        ("https://ir.example.com/news/q3-results", "Third-quarter results published today")
    ]


def test_finnhub_outlets() -> None:
    rows = [
        {"url": "https://finnhub.io/api/news?id=1", "headline": "H", "source": "Reuters",
         "datetime": 1790000000, "summary": ""},
        {"url": "https://www.cnbc.com/x", "headline": "H2", "source": "CNBC",
         "datetime": 1790000000},
    ]  # fmt: skip
    items = parse_company_news(rows, "finnhub", "AAPL_US_EQ")
    assert [i.outlet_host for i in items] == ["reuters.com", "cnbc.com"]
    assert items[0].tickers == ("AAPL_US_EQ",)


def test_edgar_filings_are_labelled_by_rule() -> None:
    def filing(form: str, items: str = "") -> Filing:
        return Filing("0000320193", form, "0000320193-26-000001", dt.date(2026, 9, 1), None,
                      "a.htm", items)  # fmt: skip

    assert label_filing(filing("8-K", "2.02,9.01"))[0] == "earnings_release"  # type: ignore[index]
    assert label_filing(filing("8-K", "5.02"))[0] == "management_change"  # type: ignore[index]
    assert label_filing(filing("4"))[0] == "insider_transaction"  # type: ignore[index]
    assert label_filing(filing("S-8")) is None


def test_source_config_validation() -> None:
    with pytest.raises(ValueError, match="needs a url"):
        NewsSource(id="x", name="X", tier=2, type="rss")
    with pytest.raises(ValueError, match="takes no url"):
        NewsSource.model_validate(
            {"id": "x", "name": "X", "tier": 1, "type": "edgar", "url": "https://sec.gov"}
        )
    config = NewsSourcesConfig()
    assert config.domain_tier("www.reuters.com") == 2
    assert config.domain_tier("efts.sec.gov") == 1
    assert config.domain_tier("example.org") is None


# ---------------------------------------------------------------------------
# Digest citations
# ---------------------------------------------------------------------------


def test_uncited_or_miscited_sentences_are_dropped() -> None:
    kept, dropped = keep_cited(
        [
            Sentence(text="Apple cut guidance.", news_item_ids=[1]),
            Sentence(text="No citation."),
            Sentence(text="Cites something it was not given.", news_item_ids=[1, 99]),
            Sentence(text="Cites a fact.", fact_ids=[5]),
        ],
        item_ids={1, 2},
        fact_ids={5},
    )
    assert [s.text for s in kept] == ["Apple cut guidance.", "Cites a fact."]
    assert dropped == 2


def test_nothing_material_digest_renders() -> None:
    body = DigestBody(
        kind="daily",
        window_start=NOON - dt.timedelta(days=1),
        window_end=NOON,
        nothing_material=True,
        headline="Nothing material: no severity-2 or higher events for your holdings.",
    )
    text = render_text(body, "https://app.example.com", markdown=False)
    assert "Nothing material" in text


# ---------------------------------------------------------------------------
# The labelled classification set
# ---------------------------------------------------------------------------


async def test_classification_set_and_harness() -> None:
    labelled = load_set()
    assert len(labelled) == 100
    types = {x.expected["event_type"] for x in labelled}
    assert len(types) == 16
    for x in labelled:
        candidates = {c.ticker for c in x.item.candidates}
        assert x.expected["affected_ticker"] in candidates | {None}
    score = await evaluate(OracleClassifier(labelled), labelled)
    assert score.accuracy == 1.0 and score.missing == 0
