"""N6 analysis techniques: the checks that make model output trustworthy, and the code-only
techniques. No database, no model: every model result is a hand-written fake."""

import datetime as dt
from decimal import Decimal

import pytest
from pydantic import ValidationError

from steward.config.jobs import NewsSettings
from steward.config.thesis import ThesisConfig
from steward.events.paths import event_paths
from steward.news import (
    brief,
    claims,
    extract,
    framing,
    impact,
    market,
    novelty,
    themes,
    thesis_link,
    threads,
)
from steward.news.analysis import ItemText, StoryMaterial, fold, input_hash, verify_span
from steward.xray.supply_chain import SupplyChain, SupplyEdge, SupplyNode

NOW = dt.datetime(2026, 10, 12, 8, 0, tzinfo=dt.UTC)


def item(
    item_id: int,
    headline: str,
    summary: str | None = None,
    *,
    kind: str | None = None,
    outlet: str = "reuters.com",
    key: str | None = None,
    region: str | None = "US",
    body: str | None = None,
    correction: bool = False,
) -> ItemText:
    return ItemText(
        id=item_id,
        url=f"https://{outlet}/{item_id}",
        outlet=outlet,
        outlet_name=outlet,
        tier=1 if kind == "primary_filing" else 2,
        region=region,
        state_media=False,
        evidence_kind=kind,
        independence_key=key or outlet,
        is_correction=correction,
        published_at=NOW,
        language="en",
        headline=headline,
        summary=summary,
        body=body,
    )


def story(*items: ItemText, severity: int = 2, lifecycle: str = "reported") -> StoryMaterial:
    return StoryMaterial(
        cluster_id=1,
        ticker="TSM_US_EQ",
        headline=items[0].headline,
        event_type="guidance",
        severity=severity,
        lifecycle=lifecycle,
        recycled=False,
        event_date=None,
        first_seen=NOW,
        last_seen=NOW,
        thread_id=None,
        items=items,
    )


GUIDANCE = item(
    1,
    "TSMC raises 2026 revenue growth guidance to 30% on AI chip demand",
    "The chipmaker said it now expects revenue to grow about 30% in dollar terms, up from 25%.",
)


def test_spans_are_checked_after_folding_quotes_dashes_and_case() -> None:
    text = "The company’s CEO said “revenue will grow” — about 30%."
    assert fold("It’s") == "it's"
    assert verify_span('the company\'s ceo said "revenue will grow"', [text])
    assert not verify_span("revenue will shrink sharply", [text])
    # A span too short to prove anything fails.
    assert not verify_span("the CEO", [text])


def test_the_input_hash_changes_with_the_input() -> None:
    assert input_hash("a", [1, 2]) == input_hash("a", [1, 2])
    assert input_hash("a", [1, 2]) != input_hash("a", [1, 3])


# --- 6.1 extraction ---------------------------------------------------------------------


def raw_extraction(**kw: object) -> extract.StoryExtraction:
    base: dict[str, object] = {
        "event_type": "guidance",
        "actors": ["TSMC"],
        "action": "raised guidance",
        "magnitude": {"value": 30, "unit": "%", "text": "about 30%", "vs_expectation": "above"},
        "status": "reported",
        "key_claims": [
            {
                "text": "TSMC raised its revenue growth guidance to 30%.",
                "item_id": 1,
                "span": "raises 2026 revenue growth guidance to 30%",
            },
            {
                "text": "Demand is up 80%.",
                "item_id": 1,
                "span": "AI chip demand is up eighty percent",
            },
        ],
    }
    return extract.StoryExtraction.model_validate({**base, **kw})


def test_a_claim_without_a_verifiable_span_is_dropped_and_the_rest_kept() -> None:
    result = extract.verified(story(GUIDANCE), raw_extraction())
    assert result is not None
    assert [c.item_id for c in result.key_claims] == [1]
    assert result.magnitude is not None and result.magnitude.text == "about 30%"


def test_an_extraction_with_no_verified_claim_is_not_saved() -> None:
    raw = raw_extraction(
        key_claims=[{"text": "x", "item_id": 1, "span": "something the model made up here"}]
    )
    assert extract.verified(story(GUIDANCE), raw) is None


def test_a_magnitude_the_text_does_not_state_is_cleared() -> None:
    raw = raw_extraction(
        magnitude={"value": 45, "unit": "%", "text": "45%", "vs_expectation": "above"}
    )
    result = extract.verified(story(GUIDANCE), raw)
    assert result is not None and result.magnitude is None


def test_event_type_and_dates_are_normalised() -> None:
    result = extract.verified(
        story(GUIDANCE), raw_extraction(event_type="astrology", occurred="last week")
    )
    assert result is not None and result.event_type == "other" and result.occurred is None
    ok = extract.verified(
        story(GUIDANCE), raw_extraction(event_type="cyber", occurred="2026-10-01")
    )
    assert ok is not None and ok.event_type == "cyber" and ok.occurred == "2026-10-01"


# --- 6.2 novelty ------------------------------------------------------------------------


def test_novelty_is_new_update_repeat_or_recycled() -> None:
    own = [1.0, 0.0]
    earlier = {7: [0.95, 0.312249], 8: [0.0, 1.0]}
    kw: dict[str, object] = {
        "recycled": False,
        "event_date": None,
        "first_seen": NOW,
        "repeat_at": 0.97,
        "update_at": 0.8,
    }
    repeat = novelty.classify_novelty(own, {7: [0.999, 0.0447]}, **kw)  # type: ignore[arg-type]
    assert (repeat.novelty, repeat.similar_cluster_id) == ("repeat", 7)
    assert novelty.classify_novelty(own, earlier, **kw).novelty == "update"  # type: ignore[arg-type]
    assert novelty.classify_novelty(own, {8: [0.0, 1.0]}, **kw).novelty == "new"  # type: ignore[arg-type]
    assert novelty.classify_novelty([], earlier, **kw).basis == "no_embedding"  # type: ignore[arg-type]
    recycled = novelty.classify_novelty(
        own,
        earlier,
        **{**kw, "event_date": dt.date(2026, 9, 1)},  # type: ignore[arg-type]
    )
    assert recycled.novelty == "recycled"


def test_surprise_comes_from_the_extraction_only() -> None:
    assert novelty.surprise_from(None) == "unknown"
    assert novelty.surprise_from(raw_extraction()) == "above"
    assert novelty.surprise_from(raw_extraction(magnitude=None)) == "unknown"


# --- 6.3 impact chains ------------------------------------------------------------------


def graph() -> SupplyChain:
    edge = SupplyEdge(
        id=None,
        supplier="TSM",
        customer="NVDA",
        product="wafers",
        customer_share=0.2,
        source_url="https://sec.gov/x",
        as_of=dt.date.today(),
        origin="filing",
    )
    return SupplyChain(
        nodes=[
            SupplyNode(
                symbol="TSM", name="TSMC", weight=0.03, held=True, direct_weight=0.03, level=0
            ),
            SupplyNode(
                symbol="NVDA", name="Nvidia", weight=0.08, held=True, direct_weight=0.05, level=1
            ),
            SupplyNode(
                symbol="MSFT", name="Microsoft", weight=0.05, held=True, direct_weight=0.05, level=1
            ),
        ],
        edges=[edge],
        linked=[],
        held_in_graph=0.16,
        notes=[],
    )


def test_chains_come_from_the_graph_and_the_model_only_adds_sentences_and_hypotheses() -> None:
    g = graph()
    result, paths = impact.graph_chains(g, story(GUIDANCE), raw_extraction())
    relations = [(c.relation, c.holding) for c in result.chains]
    assert relations == [("direct", "TSM"), ("second_order", "NVDA")]
    assert result.reached_weight == pytest.approx(0.08)
    assert result.chains[1].chain == ["TSM", "NVDA"]

    notes = impact.ImpactNotes(
        paths=[
            impact.PathNote(path_index=0, text="TSMC wafers go into Nvidia chips."),
            impact.PathNote(path_index=9, text="no such path"),
        ],
        hypotheses=[
            impact.Hypothesis(
                holding="MSFT", via=["Azure"], relation="buys GPUs", text="Maybe.", confidence="low"
            ),
            impact.Hypothesis(holding="NVDA", via=[], relation="x", text="duplicate of a path"),
            impact.Hypothesis(holding="ZZZZ", via=[], relation="x", text="not held"),
        ],
    )
    out = impact.apply_notes(result, paths, notes, g)
    assert [(c.relation, c.holding) for c in out.chains] == [
        ("direct", "TSM"),
        ("second_order", "NVDA"),
        ("hypothesis", "MSFT"),
    ]
    assert out.chains[1].note == "TSMC wafers go into Nvidia chips."
    hyp = out.chains[2]
    assert hyp.weight is None and hyp.confidence == "low"


def test_an_event_that_is_not_confirmed_is_not_called_high_severity() -> None:
    g = graph()
    _, paths = impact.graph_chains(g, story(GUIDANCE, lifecycle="reported"), raw_extraction())
    assert all(p.severity != "high" for p in paths)
    assert event_paths(g, "TSM", "downstream", "confirmed").paths  # sanity: the path exists
    assert impact.event_state(raw_extraction(status="official"), "reported") == "confirmed"
    assert impact.event_state(None, "primary_confirmed") == "confirmed"
    assert impact.event_state(raw_extraction(status="rumour"), "reported") == "rumoured"


# --- 6.4 threads ------------------------------------------------------------------------


def test_the_thread_diff_separates_new_changed_and_corrected() -> None:
    prior = ["TSMC raised full-year revenue growth guidance to 25%", "Capex stays at 30 billion"]
    now = [
        "TSMC raised full-year revenue growth guidance to 30%",
        "Capex stays at 30 billion",
        "TSMC will build a second plant in Arizona",
    ]
    diff = threads.claim_diff(prior, now, corrections=["Correction: capex"], unknowns=["timing"])
    assert [c["now"] for c in diff.changed] == [now[0]]
    assert diff.changed[0]["was"] == prior[0]
    assert diff.new == [now[2]]
    assert diff.retracted == ["Correction: capex"] and diff.unknown == ["timing"]


def test_a_story_joins_the_closest_thread_above_the_threshold() -> None:
    own = [1.0, 0.0]
    found = threads.best_thread(own, {1: [0.0, 1.0], 2: [0.9, 0.435889]}, 0.8)
    assert found == 2
    assert threads.best_thread(own, {1: [0.0, 1.0]}, 0.8) is None


# --- 6.5 framing ------------------------------------------------------------------------


def test_framing_keeps_only_checkable_quotes_and_numbers() -> None:
    a = item(1, "Fed holds rates", 'Chair says inflation "remains somewhat elevated" at 3.2%.')
    b = item(2, "US rate decision", "The Fed left rates unchanged on Wednesday.", outlet="ft.com")
    c = item(3, "Fed pauses", "Markets had priced a hold.", outlet="nhk.or.jp", region="JP")
    s = story(a, b, c)
    assert framing.eligible(s, 3) and not framing.eligible(s, 4)
    raw = framing.FramingComparison(
        outlets=[
            framing.OutletFraming(
                item_id=1,
                headline_claim="Fed holds",
                numbers_cited=["3.2%", "9.9%"],
                quotes=["remains somewhat elevated", "an invented quote of some length"],
            ),
            framing.OutletFraming(item_id=2, headline_claim="Fed unchanged"),
            framing.OutletFraming(item_id=99, headline_claim="not an outlet of the story"),
        ],
        agreement="conflict",
        conflicts=[],
    )
    out = framing.verified(s, raw)
    assert out is not None
    assert [o.item_id for o in out.outlets] == [1, 2]
    assert out.outlets[0].quotes == ["remains somewhat elevated"]
    assert out.outlets[0].numbers_cited == ["3.2%"]
    # A "conflict" with no stated conflict is not one.
    assert out.agreement == "agree"


# --- 6.6 claim check --------------------------------------------------------------------


def test_a_verdict_stands_only_with_a_real_quote_from_a_primary_document() -> None:
    filing = item(
        5,
        "Form 8-K: guidance update",
        "The company now expects 2026 revenue growth of approximately 30 percent.",
        kind="company_release",
        outlet="ir.tsmc.com",
    )
    s = story(GUIDANCE, filing)
    texts = [
        "Revenue growth guidance is 30 percent",
        "Revenue growth guidance is 35 percent",
        "Demand doubled",
        "A claim the model marked contradicted without a quote",
    ]
    raw = claims.ClaimVerdicts(
        verdicts=[
            claims.ClaimVerdict(
                claim_index=0,
                verdict="supported",
                primary_item_id=5,
                quote="expects 2026 revenue growth of approximately 30 percent",
            ),
            # Supported, but 35 is nowhere in the document.
            claims.ClaimVerdict(
                claim_index=1,
                verdict="supported",
                primary_item_id=5,
                quote="expects 2026 revenue growth of approximately 30 percent",
            ),
            # The quote is not in the cited document.
            claims.ClaimVerdict(
                claim_index=2, verdict="supported", primary_item_id=5, quote="demand doubled in Q3"
            ),
            claims.ClaimVerdict(claim_index=3, verdict="contradicted", primary_item_id=5),
        ]
    )
    out = claims.checked(s, texts, raw)
    assert [c.verdict for c in out] == ["supported", "unverified", "unverified", "unverified"]
    assert out[0].primary_url == "https://ir.tsmc.com/5"
    assert [i.id for i in claims.primaries(s)] == [5]


# --- 6.7 market context -----------------------------------------------------------------


def closes(start: dt.date, values: list[str]) -> list[tuple[dt.date, Decimal]]:
    return [(start + dt.timedelta(days=i), Decimal(v)) for i, v in enumerate(values)]


def test_window_moves_are_measured_from_the_last_close_before_the_story() -> None:
    day = dt.date(2026, 10, 12)
    stock = closes(day - dt.timedelta(days=2), ["100", "100", "102", "104", "106", "108", "110"])
    bench = closes(
        day - dt.timedelta(days=2), ["1000", "1000", "1010", "1020", "1030", "1040", "1050"]
    )
    base, moves = market.window_moves(stock, bench, day)
    assert base == day - dt.timedelta(days=1)
    by_days = {m.days: m for m in moves}
    assert by_days[1].stock_pct == 2.0 and by_days[1].benchmark_pct == 1.0
    assert by_days[1].abnormal_pct == 1.0
    assert by_days[5].stock_pct == 10.0 and by_days[5].abnormal_pct == pytest.approx(5.0)
    # Too few bars after the story: the window is open, not zero.
    _, short = market.window_moves(stock[:4], bench[:4], day)
    assert {m.days: m.stock_pct for m in short}[5] is None


def test_the_event_study_hides_groups_too_small_to_mean_anything() -> None:
    samples = [("guidance", "US", 1, v) for v in (1.0, 2.0, 3.0, 4.0, 10.0)] + [
        ("m_and_a", "US", 1, 5.0)
    ]
    rows = market.study_rows(samples)
    assert len(rows) == 1
    row = rows[0]
    assert (row.event_type, row.n, row.median_pct) == ("guidance", 5, 3.0)
    assert row.p25_pct <= row.median_pct <= row.p75_pct
    assert market.pick_benchmark("DE", ["^spx", "^dax"]) == "^dax"
    assert market.pick_benchmark("FR", ["^spx", "^dax"]) == "^spx"
    assert market.pick_benchmark("FR", []) is None


# --- 6.8 themes -------------------------------------------------------------------------


def test_theme_flags_need_a_baseline_and_a_real_change() -> None:
    calm = [2, 3, 2, 2, 3, 2, 3]
    assert themes.flag_for({"US": 12}, calm, [1] * 7) == "spike"
    assert themes.flag_for({"US": 4}, calm, [1] * 7) is None
    # No baseline yet: nothing is called a spike.
    assert themes.flag_for({"US": 12}, [2, 2], [1, 1]) is None
    assert themes.flag_for({"US": 2, "JP": 3}, calm, [1] * 7) == "spread"
    assert themes.flag_for({"US": 2, "JP": 3}, calm, [1, 3, 1, 1, 1, 1, 1]) is None


# --- 6.9 thesis -------------------------------------------------------------------------


def thesis() -> ThesisConfig:
    return ThesisConfig.model_validate(
        {
            "ticker": "TSM_US_EQ",
            "why_owned": "Leading-edge foundry share",
            "kpis": [
                {
                    "id": "rev-growth",
                    "metric": "revenue_growth",
                    "operator": ">=",
                    "threshold": 20,
                    "unit": "%",
                }
            ],
            "exit_conditions": ["Loses Apple as a customer"],
        }
    )


def test_a_breach_needs_a_primary_confirmed_story_otherwise_it_is_a_review() -> None:
    ex = raw_extraction()
    raw = thesis_link.ThesisLinks(
        links=[
            thesis_link.ThesisLinkOut(
                kind="exit_condition",
                ref="Loses Apple as a customer",
                effect="weakens",
                reason="Report says Apple is dual-sourcing.",
                claim_index=0,
            ),
            thesis_link.ThesisLinkOut(
                kind="kpi",
                ref="rev-growth",
                effect="strengthens",
                reason="Guidance up.",
                claim_index=0,
            ),
            thesis_link.ThesisLinkOut(
                kind="kpi", ref="not_a_kpi", effect="weakens", reason="x", claim_index=0
            ),
            thesis_link.ThesisLinkOut(
                kind="kpi", ref="rev-growth", effect="weakens", reason="x", claim_index=7
            ),
        ]
    )
    reported = thesis_link.checked(story(GUIDANCE), ex, thesis(), raw)
    assert [(a.ref, a.status) for a in reported] == [
        ("Loses Apple as a customer", "review"),
        ("rev-growth", "supports"),
    ]
    confirmed = thesis_link.checked(
        story(GUIDANCE, lifecycle="primary_confirmed"), ex, thesis(), raw
    )
    assert confirmed[0].status == "breach"


# --- 6.10 brief -------------------------------------------------------------------------


def candidate(
    sid: int, headline: str, sev: int, exposure: float, nov: str | None
) -> brief.Candidate:
    base = story(item(sid * 10, headline, "Net income rose 12%"), severity=sev)
    s = StoryMaterial(**{**base.__dict__, "cluster_id": sid})
    return brief.Candidate(
        s, exposure, nov, ("Net income rose 12%",), brief.rank_score(sev, exposure, nov)
    )


def test_ranking_is_exposure_times_materiality_times_novelty() -> None:
    assert brief.rank_score(3, 0.1, "new") > brief.rank_score(2, 0.1, "new")
    assert brief.rank_score(2, 0.1, "new") > brief.rank_score(2, 0.1, "update")
    assert brief.rank_score(3, 0.5, "repeat") == 0
    assert brief.rank_score(3, 0.5, "recycled") == 0
    # A holding with no known exposure still ranks above zero.
    assert brief.rank_score(2, 0.0, None) > 0


def test_a_written_line_must_cite_its_own_items_and_invent_no_number() -> None:
    c = candidate(1, "Acme reports results", 2, 0.05, "new")
    own_id = c.story.items[0].id
    good = brief.WrittenLine(
        story_id=1, text="Acme's net income rose 12%.", why="A 5% holding.", item_ids=[own_id]
    )
    wrong_item = good.model_copy(update={"item_ids": [999]})
    no_cite = good.model_copy(update={"item_ids": []})
    invented = good.model_copy(update={"text": "Acme's net income rose 15%."})
    assert brief.accepted(c, good)
    assert not any(brief.accepted(c, w) for w in (wrong_item, no_cite, invented))

    lines = brief.build_lines([c], {1: invented})
    assert lines[0].text == "Acme reports results" and not lines[0].written_by_model
    assert brief.build_lines([c], {1: good})[0].written_by_model


def test_sections_follow_market_open_order_by_holding_region() -> None:
    def line(sid: int, ticker: str) -> brief.BriefLine:
        return brief.BriefLine(
            story_id=sid, ticker=ticker, text="t", item_ids=[1], severity=2,
            lifecycle="reported", score=1.0,
        )  # fmt: skip

    out = brief.sections(
        "morning", [line(1, "AAPL_US_EQ"), line(2, "7203_JP_EQ"), line(3, "SIE_DE_EQ")]
    )
    assert [s.title for s in out] == ["Asia", "Europe", "Americas"]
    assert [s.title for s in brief.sections("holding", [line(1, "AAPL_US_EQ")])] == ["What changed"]
    assert brief.sections("morning", []) == []


# --- config -----------------------------------------------------------------------------


def test_news_settings_defaults_run_only_the_free_techniques() -> None:
    news = NewsSettings()
    assert set(news.analysis_techniques) == {"novelty", "thread", "market", "themes"}
    with pytest.raises(ValidationError):
        NewsSettings(novelty_update_similarity=0.9, novelty_repeat_similarity=0.88)
    with pytest.raises(ValidationError):
        NewsSettings(analysis_techniques=["novelty", "novelty"])
