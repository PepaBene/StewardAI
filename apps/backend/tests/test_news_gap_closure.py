"""Gap-closure checks that need no database and no model: triage, sentiment, GDELT themes,
theme narratives, the rank nudges and the owner-confirmed breach."""

import datetime as dt

from steward.news import brief, gdelt_theme, sentiment, theme_narrative, thesis_link, triage
from tests.test_news_analysis import item, raw_extraction, story, thesis

GUIDANCE = item(1, "TSMC raises 2026 revenue guidance to 30%", "Revenue growth guided to 30%")


# --- triage ------------------------------------------------------------------------------


def triage_item() -> triage.TriageItem:
    return triage.TriageItem(0, 5, "Chipmaker lifts guidance to 30%", None, "en", "US")


def test_triage_keeps_a_gloss_that_uses_only_numbers_of_the_item() -> None:
    row = triage.TriageRow(
        index=0, relevance=9, topic=" chips ", entities=["TSMC", " "], gloss="Guidance is 30%."
    )
    out = triage.check(triage_item(), row)
    assert out is not None
    assert out["relevance"] == 3 and out["topic"] == "chips" and out["entities"] == ["TSMC"]


def test_triage_drops_a_gloss_with_an_invented_number_or_no_text() -> None:
    item_ = triage_item()
    invented = triage.TriageRow(index=0, relevance=2, topic="t", gloss="Guidance is 45%.")
    empty = triage.TriageRow(index=0, relevance=2, topic="t", gloss="  ")
    assert triage.check(item_, invented) is None
    assert triage.check(item_, empty) is None


# --- sentiment ---------------------------------------------------------------------------


def aspect(span: str, strength: float, direction: str = "positive") -> sentiment.Aspect:
    return sentiment.Aspect.model_validate(
        {"entity": "TSMC", "direction": direction, "strength": strength, "item_id": 1, "span": span}
    )


def test_sentiment_keeps_only_verified_spans_and_clamps_strength() -> None:
    s = story(GUIDANCE)
    raw = sentiment.RawSentiment(
        aspects=[
            aspect("TSMC raises 2026 revenue guidance to 30%", 4.0),
            aspect("This sentence is not in the item", 0.9),
            aspect("TSMC raises 2026 revenue guidance to 30%", 0.9, "neutral"),
        ]
    )
    out = sentiment.bounded(s, raw)
    assert [(a.direction, a.strength) for a in out.aspects] == [("positive", 1.0), ("neutral", 0.0)]
    assert out.intensity == 1.0


# --- GDELT -------------------------------------------------------------------------------


def test_gdelt_query_quotes_phrases_and_caps_terms() -> None:
    assert gdelt_theme.query_for(["chip export controls"]) == '"chip export controls"'
    assert gdelt_theme.query_for(["a", "b c"]) == '(a OR "b c")'
    assert gdelt_theme.query_for([str(i) for i in range(12)]).count(" OR ") == 7


def test_gdelt_timeline_is_averaged_per_utc_day() -> None:
    doc = {
        "timeline": [
            {
                "data": [
                    {"date": "20261010T000000Z", "value": 1.0},
                    {"date": "20261010T120000Z", "value": -3.0},
                    {"date": "20261011T000000Z", "value": 2.0},
                ]
            }
        ]
    }
    assert gdelt_theme.parse_daily(doc) == {
        dt.date(2026, 10, 10): -1.0,
        dt.date(2026, 10, 11): 2.0,
    }
    assert gdelt_theme.parse_daily({"timeline": [{"data": [{"date": "x"}]}]}) == {}
    assert gdelt_theme.parse_daily(None) == {}


# --- theme narrative ---------------------------------------------------------------------


def theme_items() -> list[theme_narrative.ThemeItem]:
    return [
        theme_narrative.ThemeItem(1, "Export curbs widen to 14 chip tools", None, "US", 2),
        theme_narrative.ThemeItem(2, "Tokyo objects to the curbs", "Talks continue", "JP", 2),
        theme_narrative.ThemeItem(3, "Unrelated 99 item", None, None, 3),
    ]


def test_a_narrative_must_cite_two_theme_items_and_invent_no_number() -> None:
    items = theme_items()
    good = theme_narrative.Narrative(text="Curbs cover 14 tools.", item_ids=[1, 2])
    assert theme_narrative.accepted(items, good)
    assert not theme_narrative.accepted(
        items, theme_narrative.Narrative(text="Curbs cover 14 tools.", item_ids=[1])
    )
    assert not theme_narrative.accepted(
        items, theme_narrative.Narrative(text="Curbs cover 99 tools.", item_ids=[1, 2])
    )
    assert not theme_narrative.accepted(
        items, theme_narrative.Narrative(text="Curbs.", item_ids=[1, 42])
    )
    body = theme_narrative.as_body("Chips", good)
    assert body.sections[0].lines[0].item_ids == [1, 2]


# --- rank nudges and the owner-confirmed breach ------------------------------------------


def test_rank_nudges_are_bounded_and_never_revive_a_repeat() -> None:
    base = brief.rank_score(2, 0.1, "new")
    assert brief.rank_score(2, 0.1, "new", 1.0, 0.2, 0) > base
    assert brief.rank_score(2, 0.1, "new", 5.0, 0.2, 0) == brief.rank_score(2, 0.1, "new", 1.0, 0.2)
    assert brief.rank_score(2, 0.1, "new", 0.0, 0.0, 10) <= base * 1.15 + 1e-9
    assert brief.rank_score(2, 0.1, "repeat", 1.0, 0.2, 3) == 0.0


def test_an_owner_confirmed_mark_lets_a_reported_story_breach() -> None:
    ex = raw_extraction()
    raw = thesis_link.ThesisLinks(
        links=[
            thesis_link.ThesisLinkOut(
                kind="exit_condition",
                ref="Loses Apple as a customer",
                effect="weakens",
                reason="Apple is dual-sourcing.",
                claim_index=0,
            )
        ]
    )
    reported = story(GUIDANCE)
    assert thesis_link.checked(reported, ex, thesis(), raw)[0].status == "review"
    assert thesis_link.checked(reported, ex, thesis(), raw, True)[0].status == "breach"
