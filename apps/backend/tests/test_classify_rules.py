"""Cost lever C7: classification the code can settle without a model call."""

import datetime as dt
import json

import asyncpg
import pytest

from steward.news import classify_rules as rules
from steward.news.pipeline import classify_by_rules

NOW = dt.datetime(2026, 10, 13, 12, 0, tzinfo=dt.UTC)


def links(*pairs: tuple[str, str]) -> str:
    return json.dumps([{"via": via, "term": "tariffs", "ticker": t} for via, t in pairs])


def test_a_watch_topic_hit_linked_only_by_topic_is_other_and_not_relevant() -> None:
    label = rules.topic_only_label(
        "edgar-full-text", "PEPSICO INC 8-K mentions tariffs", links(("topic", "KO_US_EQ"))
    )
    assert label is not None
    assert (label.event_type, label.relevant, label.affected_ticker) == ("other", False, None)


@pytest.mark.parametrize(
    ("source", "headline", "raw"),
    [
        (
            "edgar-full-text",
            "PEPSICO INC 8-K mentions tariffs",
            links(("topic", "A"), ("competitor", "B")),
        ),
        ("edgar-full-text", "PEPSICO INC 8-K mentions tariffs", links(("name", "A"))),
        ("edgar-full-text", "PEPSICO INC 8-K mentions tariffs", "[]"),
        ("edgar-full-text", "Apple raises its outlook", links(("topic", "A"))),
        ("reuters-sitemap", "X mentions tariffs", links(("topic", "A"))),
    ],
)
def test_anything_with_a_peer_signal_or_another_shape_still_goes_to_the_model(
    source: str, headline: str, raw: str
) -> None:
    assert rules.topic_only_label(source, headline, raw) is None


def test_a_repeated_headline_with_the_same_candidates_takes_the_earlier_label() -> None:
    known = rules.known_from_rows(
        [
            {
                "headline": "Apple raises its outlook!",
                "links": links(("name", "AAPL"), ("topic", "MSFT")),
                "event_type": "guidance",
                "affected_ticker": "AAPL",
                "relevant": True,
                "thesis_link": None,
                "rumour": False,
                "event_date": None,
            }
        ]
    )
    same = known.match("apple raises its outlook", links(("topic", "MSFT"), ("name", "AAPL")))
    assert same is not None and same.event_type == "guidance" and same.affected_ticker == "AAPL"
    assert (
        known.match("Apple raises its outlook", links(("name", "AAPL"))) is None
    )  # other holdings
    assert (
        known.match("Apple raises its outlook again", links(("name", "AAPL"), ("topic", "MSFT")))
        is None
    )
    assert known.match("Apple raises its outlook", "[]") is None


async def add_item(
    pool: asyncpg.Pool,
    url: str,
    headline: str,
    raw_links: str,
    source_id: str = "edgar-full-text",
    classified_by: str | None = None,
    event_type: str | None = None,
) -> int:
    await pool.execute(
        "insert into public.source (id, name, tier, type) values ($1, $1, 2, 'rss')"
        " on conflict do nothing",
        source_id,
    )
    return int(
        await pool.fetchval(
            "insert into public.news_item (url, source_id, outlet, tier, published_at, headline,"
            " links, classified_at, classified_by, event_type, relevant, rumour) values"
            " ($1, $2, 'example.com', 2, $3, $4, $5::jsonb, $6, $7, $8, $9, false) returning id",
            url,
            source_id,
            NOW,
            headline,
            raw_links,
            NOW if classified_by else None,
            classified_by,
            event_type,
            True if event_type else None,
        )
    )


@pytest.mark.db
async def test_the_rules_label_what_they_can_and_leave_the_rest_for_the_model(
    backend_pool: asyncpg.Pool,
) -> None:
    topic = await add_item(
        backend_pool, "u1", "ACME CORP 10-Q mentions tariffs", links(("topic", "KO_US_EQ"))
    )
    peer = await add_item(
        backend_pool, "u2", "ACME CORP 8-K mentions tariffs", links(("competitor", "KO_US_EQ"))
    )
    await add_item(
        backend_pool,
        "u3",
        "KO raises outlook",
        links(("name", "KO_US_EQ")),
        source_id="wire-a",
        classified_by="gpt-6-luna",
        event_type="guidance",
    )
    repeat = await add_item(
        backend_pool, "u4", "KO raises outlook.", links(("name", "KO_US_EQ")), source_id="wire-a"
    )
    other = await add_item(
        backend_pool, "u5", "KO falls", links(("name", "KO_US_EQ")), source_id="wire-a"
    )
    rows = await backend_pool.fetch(
        "select id, source_id, headline, links, cluster_id from public.news_item"
        " where classified_at is null order by id"
    )
    touched: set[int] = set()
    detail = {"labelled": 0, "by_rule": 0}
    rest = await classify_by_rules(backend_pool, rows, NOW, touched, detail)
    assert [r["id"] for r in rest] == [peer, other]
    assert detail == {"labelled": 2, "by_rule": 2}
    done = {
        r["id"]: (r["event_type"], r["classified_by"])
        for r in await backend_pool.fetch(
            "select id, event_type, classified_by from public.news_item where id = any($1)",
            [topic, repeat],
        )
    }
    assert done == {
        topic: ("other", "rules:topic_only"),
        repeat: ("guidance", "rules:duplicate"),
    }
