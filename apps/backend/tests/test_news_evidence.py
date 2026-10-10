"""N5: provenance kinds, lifecycle states, correction notices, link confidence, portfolio topics."""

from steward.config.news_sources import NewsSource, NewsSourcesConfig
from steward.news.evidence import (
    PRIMARY_KINDS,
    Basis,
    evidence_kind,
    indirect_only,
    is_correction,
    lifecycle_for,
    matching_topics,
)
from steward.news.link import Link

SOURCES = NewsSourcesConfig()
IR = {"investor.apple.com"}


def source(**kw: object) -> NewsSource:
    base: dict[str, object] = {"id": "s", "name": "S", "tier": 2, "type": "rss"}
    return NewsSource.model_validate({**base, **kw})


def kind(src: NewsSource, tier: int, host: str | None) -> str:
    return evidence_kind(src, tier, host, IR, SOURCES)


def test_provenance_kind_follows_the_source_and_the_outlet() -> None:
    assert kind(source(type="tdnet", tier=1), 1, "release.tdnet.info") == "regulator"
    assert kind(source(type="edgar", tier=1), 1, "sec.gov") == "primary_filing"
    assert (
        kind(source(type="rss", tier=1, tickers=["AAPL_US_EQ"]), 1, "x.test") == "company_release"
    )
    assert kind(source(type="rss", tier=2), 2, "investor.apple.com") == "company_release"
    assert kind(source(type="rss", tier=1, macro=True), 1, "ecb.europa.eu") == "central_bank"
    assert kind(source(type="rss", tier=2), 2, "www.reuters.com") == "wire"
    assert kind(source(type="rss", tier=2), 2, "ft.com") == "outlet"
    assert kind(source(type="google_news", tier=2), 2, "ft.com") == "aggregator"
    assert kind(source(type="ai_search", tier=3), 3, "ft.com") == "lead"
    assert {"regulator", "primary_filing", "company_release", "central_bank"} == PRIMARY_KINDS


def test_correction_notices_are_found_in_four_languages_but_revisions_are_not() -> None:
    assert is_correction("CORRECTION: Apple cuts guidance")
    assert is_correction("Berichtigung: Ad-hoc-Mitteilung")
    assert is_correction("（訂正）2027年3月期 決算短信")
    assert is_correction("[기재정정]분기보고서")
    assert not is_correction("業績予想の修正に関するお知らせ")  # a forecast revision is news
    assert not is_correction("Apple corrects course on pricing")


def test_lifecycle_states_in_order_of_precedence() -> None:
    def state(**kw: object) -> str:
        base = {"status": "unconfirmed", "confidence": "unconfirmed", "kinds": frozenset()}
        return lifecycle_for(Basis(**{**base, **kw}))[0]  # type: ignore[arg-type]

    assert state() == "reported"
    assert state(status="confirmed", confidence="corroborated") == "corroborated"
    assert state(status="confirmed", confidence="corroborated",
                 kinds=frozenset({"outlet", "regulator"})) == "primary_confirmed"  # fmt: skip
    assert state(status="confirmed", confidence="verified") == "primary_confirmed"
    assert state(status="confirmed", confidence="corroborated", tier1_support=True) == (
        "primary_confirmed"
    )
    assert state(status="confirmed", confidence="verified", corrected=True) == "corrected"
    assert state(status="disputed", confidence="disputed", corrected=True) == "disputed"
    assert state(status="disputed", confidence="disputed", closed=True) == "resolved"


def test_link_confidence_and_indirect_exposure() -> None:
    assert Link("AAPL", "isin", "US0378331005").confidence == 1.0
    assert (
        Link("AAPL", "name", "Apple").confidence > Link("AAPL", "alias", "iPhone maker").confidence
    )
    assert Link("AAPL", "competitor", "Samsung").confidence < 0.6
    assert Link("AAPL", "name", "Apple").as_json()["confidence"] == 0.85
    assert indirect_only(["competitor", "topic"])
    assert not indirect_only(["competitor", "name"])
    assert not indirect_only([])


def test_portfolio_topics_tag_wire_items_by_whole_word() -> None:
    topics = SOURCES.portfolio_topics
    assert matching_topics("Fed signals an interest rate cut in March", topics) == ["rates"]
    assert matching_topics("New tariffs and sanctions on chips", topics) == ["trade-policy"]
    assert matching_topics("Bananas are cheap", topics) == []
    assert "fx" not in matching_topics("The eurozone said nothing", topics)  # whole words only
