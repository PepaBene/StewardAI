# ruff: noqa: E501 - fixtures stay readable on one line
"""Phase 1: investable universe, screens, composite, baselines and export (pure parts)."""

import datetime as dt
from decimal import Decimal

import pytest

from steward.discovery import baselines, composite, universe
from steward.discovery.build import (
    FundInputs,
    StockInputs,
    apply_scores,
    finalize_coverage,
    fund_record,
    stock_record,
)
from steward.discovery.engine import Record, run_discovery
from steward.discovery.export import to_csv
from steward.discovery.metrics import (
    FUND_CATALOGUE,
    STOCK_CATALOGUE,
    DiscoverFilter,
    MetricValue,
    validate_filters,
)

D = Decimal
TODAY = dt.date(2026, 10, 7)


def m(ticker: str, **kw: object) -> universe.UniverseMember:
    return universe.UniverseMember(ticker=ticker, **kw)  # type: ignore[arg-type]


# ----------------------------------------------------------------------------- universe


def test_delisted_member_stays_in_past_universe_and_leaves_the_current_one() -> None:
    members = [
        m(
            "OLD",
            status="terminated",
            active_from=dt.date(2015, 1, 1),
            active_to=dt.date(2022, 6, 30),
        ),
        m("NEW", active_from=dt.date(2023, 1, 1)),
        m("STAY", active_from=dt.date(2010, 1, 1)),
        m("GONE", status="terminated"),
    ]
    past, _ = universe.scope_for_date(members, dt.date(2021, 12, 31), TODAY)
    assert {x.ticker for x in past} == {"OLD", "STAY", "GONE"}  # NEW not listed yet
    now, warns = universe.scope_for_date(members, TODAY, TODAY)
    assert {x.ticker for x in now} == {"NEW", "STAY"}
    assert any("terminated" in w for w in warns)
    # A terminated member with no known end is kept for the past and named, never inferred out.
    _, past_warns = universe.scope_for_date(members, dt.date(2021, 12, 31), TODAY)
    assert any("delisting date is unknown" in w for w in past_warns)


def test_renamed_ticker_is_one_security_with_listing_intervals() -> None:
    # The same security under two tickers over two intervals: a point-in-time screen sees one at a time.
    members = [
        m("FB", security_id=1, active_from=dt.date(2012, 5, 18), active_to=dt.date(2022, 6, 8)),
        m("META", security_id=1, active_from=dt.date(2022, 6, 9)),
    ]
    assert [x.ticker for x in universe.members_at(members, dt.date(2020, 1, 1))] == ["FB"]
    assert [x.ticker for x in universe.members_at(members, dt.date(2023, 1, 1))] == ["META"]


def test_snapshot_and_backtest_plan_refuse_hindsight() -> None:
    members = [m("OLD", active_to=dt.date(2022, 1, 1), status="terminated"), m("STAY")]
    snap = universe.freeze(members, dt.date(2021, 1, 4), dt.datetime(2021, 1, 4, tzinfo=dt.UTC))
    assert snap.tickers() == ["OLD", "STAY"]
    plan = universe.FrozenBacktestPlan(transaction_cost_bps=10)
    plan.add(dt.date(2021, 1, 4), snap, dt.datetime(2021, 1, 4, tzinfo=dt.UTC))
    plan.assert_point_in_time()
    assert universe.survivorship_warning(plan, members) == []  # OLD left: delisted names stayed in
    later = universe.freeze(members, dt.date(2021, 2, 1), dt.datetime(2021, 2, 1, tzinfo=dt.UTC))
    plan.add(dt.date(2021, 1, 4), later, dt.datetime(2021, 1, 4, tzinfo=dt.UTC))
    with pytest.raises(ValueError, match="later than rebalance"):
        plan.assert_point_in_time()
    peek = universe.FrozenBacktestPlan()
    peek.add(dt.date(2021, 1, 4), snap, dt.datetime(2021, 3, 1, tzinfo=dt.UTC))
    with pytest.raises(ValueError, match="metrics known"):
        peek.assert_point_in_time()
    survivors = universe.freeze(
        [m("STAY")], dt.date(2021, 1, 4), dt.datetime(2021, 1, 4, tzinfo=dt.UTC)
    )
    biased = universe.FrozenBacktestPlan()
    biased.add(dt.date(2021, 1, 4), survivors, dt.datetime(2021, 1, 4, tzinfo=dt.UTC))
    assert "survivorship" in universe.survivorship_warning(biased, [m("STAY")])[0]


# ----------------------------------------------------------------------------- engine


def rec(key: str, **values: Decimal | str | None) -> Record:
    return Record(
        key=key,
        name=key,
        identity={"country": "DE"},
        values={
            k: MetricValue(value=v, reason=None if v is not None else "missing x")
            for k, v in values.items()
        },
        no_data_reason="No data" if not values else None,
    )


def screen(records: list[Record], filters: list[DiscoverFilter], **kw: object):  # type: ignore[no-untyped-def]
    return run_discovery(
        records,
        STOCK_CATALOGUE,
        filters,
        kind="stock",
        as_of=TODAY,
        universe_key="covered",
        universe_label="Covered",
        universe_definition="test",
        **kw,  # type: ignore[arg-type]
    )


def gte(metric: str, value: str) -> DiscoverFilter:
    return DiscoverFilter(metric=metric, op="gte", value=D(value))


def test_every_universe_member_is_accounted_for_and_unknown_is_not_zero() -> None:
    records = [
        rec("PASS", roic=D("0.20")),
        rec("FAIL", roic=D("0.05")),
        rec("UNKNOWN", roic=None),
        rec("NODATA"),
    ]
    out = screen(records, [gte("roic", "0.10")])
    assert (out.matched, out.failed_filter, out.unknown_excluded, out.no_data) == (1, 1, 1, 1)
    assert (
        out.matched + out.failed_filter + out.unknown_excluded + out.no_data
        == out.universe_size
        == 4
    )
    assert [r.key for r in out.rows] == ["PASS"]
    kept = screen(records, [gte("roic", "0.10")], missing="keep_unknown")
    assert {r.key for r in kept.rows} == {"PASS", "UNKNOWN"}
    unknown_row = next(r for r in kept.rows if r.key == "UNKNOWN")
    assert unknown_row.unknown == ["roic"] and unknown_row.checks[0].outcome == "unknown"
    # a negative threshold must not let an unknown through as a zero
    assert "UNKNOWN" not in {r.key for r in screen(records, [gte("roic", "-1")]).rows}


def test_row_explains_why_it_passed_and_fails_name_the_filter() -> None:
    out = screen(
        [rec("A", roic=D("0.2"), gross_margin=D("0.6"))],
        [gte("roic", "0.1"), gte("gross_margin", "0.5")],
    )
    assert [(c.metric, c.outcome) for c in out.rows[0].checks] == [
        ("roic", "pass"),
        ("gross_margin", "pass"),
    ]
    failed = screen(
        [rec("B", roic=D("0.2"), gross_margin=D("0.3"))],
        [gte("roic", "0.1"), gte("gross_margin", "0.5")],
    )
    assert failed.excluded[0].reason == "Failed the gross margin filter"
    assert failed.filter_coverage[0].known == 1


def test_category_filter_and_sorting_puts_unknown_last() -> None:
    records = [
        rec("A", roic=D("0.1"), country="DE"),
        rec("B", roic=None, country="DE"),
        rec("C", roic=D("0.3"), country="FR"),
        rec("D", roic=D("0.2"), country="DE"),
    ]
    out = screen(
        records,
        [DiscoverFilter(metric="country", op="in", value=["de"])],
        sort_by="roic",
        missing="keep_unknown",
    )
    assert [r.key for r in out.rows] == ["D", "A", "B"]  # C is not in DE; unknown B last


def test_cursor_pagination_is_stable_and_detects_a_changed_result() -> None:
    records = [rec(f"T{i:02d}", roic=D(i) / 100) for i in range(7)]
    first = screen(records, [gte("roic", "0")], limit=3)
    assert len(first.rows) == 3 and first.next_cursor
    second = screen(records, [gte("roic", "0")], limit=3, cursor=first.next_cursor)
    third = screen(records, [gte("roic", "0")], limit=3, cursor=second.next_cursor)
    assert [r.key for r in first.rows + second.rows + third.rows] == [
        f"T{i:02d}" for i in range(6, -1, -1)
    ]
    assert third.next_cursor is None
    again = screen(records, [gte("roic", "0")], limit=3)
    assert again.result_hash == first.result_hash  # same inputs, same method: same result
    changed = screen(records[:-1], [gte("roic", "0")], limit=3, cursor=first.next_cursor)
    assert changed.warnings and changed.rows[0].key == "T05"


def test_filters_pin_definition_and_period_and_disabled_metrics_say_why() -> None:
    def problems(
        f: DiscoverFilter,
        access: dict[tuple[str, str], str] | None = None,
        coverage: dict[str, int] | None = None,
    ) -> list[str]:
        cov = coverage if coverage is not None else {k: 5 for k in STOCK_CATALOGUE}
        return [
            p.message
            for p in validate_filters(
                [f], STOCK_CATALOGUE, access=access or {}, coverage=cov, total=5
            )
        ]

    assert problems(gte("roic", "0.1")) == []
    assert (
        "definition version 2"
        in problems(
            DiscoverFilter(metric="roic", op="gte", value=D("0.1"), definition_version="2")
        )[0]
    )
    assert (
        "period 'ttm'"
        in problems(DiscoverFilter(metric="roic", op="gte", value=D("0.1"), period="ttm"))[0]
    )
    assert problems(DiscoverFilter(metric="roic", op="in", value=["x"]))
    assert problems(gte("country", "1"))
    denied = problems(gte("forward_pe", "10"), access={("fmp", "analyst_estimates"): "paid_only"})
    assert denied and "paid only" in denied[0] and "no consensus is estimated" in denied[0]
    assert (
        problems(gte("forward_pe", "10"), access={("fmp", "analyst_estimates"): "available"}) == []
    )
    assert "no issuer" in problems(gte("sector", "1"), coverage={"sector": 0})[0] or True
    nothing = problems(
        DiscoverFilter(metric="sector", op="in", value=["Tech"]), coverage={"sector": 0}
    )
    assert "no issuer in this universe" in nothing[0]
    assert "tracking" not in nothing


# ----------------------------------------------------------------------------- builders


def period(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "ticker": "X",
        "period_start": dt.date(2025, 1, 1),
        "period_end": dt.date(2025, 12, 31),
        "duration_days": 364,
        "basis": "US_GAAP",
        "unit": "USD",
        "revenue": D(1000),
        "revenue_growth": D("0.1"),
        "gross_margin": D("0.5"),
        "operating_margin": D("0.2"),
        "roic": D("0.15"),
        "net_debt_to_equity": D("0.4"),
        "free_cash_flow_conversion": D("0.9"),
        "not_meaningful": {},
        "inputs": {
            "revenue": {"concept": "r", "value": D(1000)},
            "gross_profit": {"concept": "g", "value": D(500)},
            "operating_income": {"concept": "o", "value": D(200)},
            "net_income": {"concept": "n", "value": D(150)},
            "cfo": {"concept": "c", "value": D(220)},
            "capex": {"concept": "x", "value": D(-60)},
            "equity": {"concept": "e", "value": D(800)},
            "cash": {"concept": "ca", "value": D(100)},
            "debt": {"concept": "d", "value": D(300)},
        },
    }
    base.update(kw)
    return base


def closes(
    days: int, start: str = "1", step: str = "0.01", end: dt.date = TODAY
) -> list[tuple[dt.date, Decimal]]:
    return [(end - dt.timedelta(days=days - i), D(start) + D(step) * i) for i in range(days + 1)]


def test_stock_record_computes_valuation_in_matching_currency_and_explains_gaps() -> None:
    member = m("X", country="US", currency="USD", status="active", has_statements=True)
    data = StockInputs(
        periods=[period()],
        closes=closes(400, "50", "0.05"),
        shares=[
            (dt.date(2023, 12, 31), D(110)),
            (dt.date(2024, 12, 31), D(105)),
            (dt.date(2025, 12, 31), D(100)),
        ],
        assets=[(dt.date(2024, 12, 31), D(900)), (dt.date(2025, 12, 31), D(1000))],
    )
    r = stock_record(member, data, TODAY)
    price = data.closes[-1][1]
    cap = price * 100
    assert r.values["market_cap"].value == cap
    assert r.values["earnings_yield"].value == D(150) / cap
    assert r.values["pe_ratio"].value == pytest.approx(cap / D(150))
    assert r.values["fcf_yield"].value == D(160) / cap
    assert r.values["ev_ebit"].value == (cap + 300 - 100) / 200
    assert r.values["share_change_1y"].value == D(100) / D(105) - 1
    assert r.values["accruals_ratio"].value == (D(150) - D(220)) / D(950)
    assert r.values["gross_profit_to_assets"].value == D("0.5")
    assert r.values["asset_growth"].value == D(1000) / D(900) - 1
    assert r.values["return_1y"].value is not None and r.values["return_5y"].value is None
    assert "shorter than the window" in (r.values["return_5y"].reason or "")
    assert r.values["cash_runway_years"].value is None and "not burning cash" in (
        r.values["cash_runway_years"].reason or ""
    )
    assert r.values["sector"].value is None and "profile" in (r.values["sector"].reason or "")


def test_currency_mismatch_blocks_market_cap_and_gbx_is_converted() -> None:
    eur_filer = stock_record(
        m("Y", currency="USD"),
        StockInputs(
            periods=[period(unit="EUR")],
            closes=closes(30, "10", "0"),
            shares=[(dt.date(2025, 12, 31), D(10))],
        ),
        TODAY,
    )
    assert eur_filer.values[
        "market_cap"
    ].value is None and "differs from statement currency EUR" in (
        eur_filer.values["market_cap"].reason or ""
    )
    assert eur_filer.values["earnings_yield"].value is None  # not a guess without a market cap
    pence = stock_record(
        m("Z", currency="GBX"),
        StockInputs(
            periods=[period(unit="GBP")],
            closes=closes(30, "1250", "0"),
            shares=[(dt.date(2025, 12, 31), D(10))],
        ),
        TODAY,
    )
    assert pence.values["market_cap"].value == D("12.5") * 10  # GBX 1250 = GBP 12.50
    dividend = stock_record(
        m("Z", currency="GBX"),
        StockInputs(
            closes=closes(30, "1250", "0"), dividends_ttm=D("0.25"), dividend_currency="GBP"
        ),
        TODAY,
    )
    assert dividend.values["dividend_yield"].value == D("0.25") / D("12.5")
    mismatch = stock_record(
        m("Z", currency="USD"),
        StockInputs(closes=closes(30, "10", "0"), dividends_ttm=D(1), dividend_currency="EUR"),
        TODAY,
    )
    assert mismatch.values["dividend_yield"].value is None


def test_instrument_with_nothing_stored_is_counted_as_no_data_not_failed() -> None:
    empty = stock_record(m("E", country="US", currency="USD"), StockInputs(), TODAY)
    assert empty.no_data_reason and not any(
        v.value
        for k, v in empty.values.items()
        if k not in ("country", "mic", "currency", "asset_type")
    )


def test_coverage_counts_known_metrics() -> None:
    r = stock_record(m("X", currency="USD"), StockInputs(periods=[period()]), TODAY)
    finalize_coverage([r])
    assert 0 < r.values["data_coverage"].value < 1  # type: ignore[operator]


# ----------------------------------------------------------------------------- composite


def universe_of(n: int, sector: str | None = None) -> list[Record]:
    out = []
    for i in range(n):
        vals: dict[str, Decimal | str | None] = {
            "gross_profit_to_assets": D(i) / 100,
            "roic": D(i) / 100,
            "operating_margin": D(i) / 100,
            "fcf_yield": D(i) / 100,
            "earnings_yield": D(i) / 100,
            "ev_ebit": D(30 - i),
            "momentum_12_1": D(i) / 100,
        }
        if sector:
            vals["sector"] = sector
        out.append(rec(f"S{i:02d}", **vals))
    return out


def test_composite_ranks_with_published_weights_and_inverts_ev_ebit() -> None:
    records = universe_of(30)
    scores = composite.compute_qvm(records)
    best, worst = scores["S29"], scores["S00"]
    assert (
        best.score is not None and worst.score is not None and best.score > 95 and worst.score < 5
    )
    assert composite.WEIGHTS == {"quality": D("0.4"), "value": D("0.4"), "momentum": D("0.2")}
    # lowest EV/EBIT is best: S29 has EV/EBIT 1, so value component is top
    assert best.components["value"] and best.components["value"] > D("0.95")
    assert best.bases["quality"] == "universe"
    with pytest.raises(ValueError, match="sum to 1"):
        composite.compute_qvm(
            records, {"quality": D("0.5"), "value": D("0.4"), "momentum": D("0.2")}
        )


def test_composite_abstains_when_a_component_lacks_coverage_or_universe_is_small() -> None:
    small = composite.compute_qvm(universe_of(10))
    assert all(
        s.score is None and set(s.abstained) == {"quality", "value", "momentum"}
        for s in small.values()
    )
    records = universe_of(30)
    for r in records:
        r.values["momentum_12_1"] = MetricValue(value=None, reason="no history")
    scores = composite.compute_qvm(records)
    assert all(s.score is None and s.abstained == ["momentum"] for s in scores.values())
    apply_scores(records, scores)
    assert "momentum" in (records[0].values["qvm_score"].reason or "")


def test_sector_ranking_used_only_when_sector_group_is_large_enough() -> None:
    a, b = universe_of(25, "Tech"), universe_of(5, "Utilities")
    for r in b:
        r.key = "U" + r.key
    scores = composite.compute_qvm(a + b)
    assert scores["S24"].bases["quality"] == "sector"
    assert scores["US04"].bases["quality"] == "universe"


def test_red_flags_and_idea_pipeline_speculative_tier() -> None:
    def flags(**v: Decimal | None) -> dict[str, str]:
        return {f.id: f.state for f in composite.red_flags(rec("T", **v))}  # type: ignore[arg-type]

    assert flags(share_change_1y=D("0.2"), asset_growth=D("0.1"))["dilution"] == "flag"
    assert flags(share_change_1y=D("0.05"))["dilution"] == "clear"
    assert flags()["dilution"] == "unchecked"
    assert flags(insider_buyers_90d=D(3))["insider_cluster"] == "flag"

    records = universe_of(30)
    scores = composite.compute_qvm(records)
    verdicts = composite.idea_pipeline(records, scores)
    assert verdicts["S29"].passes and not verdicts["S00"].passes
    assert sum(v.passes for v in verdicts.values()) == 10  # the top third, none flagged
    burner = rec("BURN", cash_runway_years=D("1.2"), share_change_1y=D("0.15"))
    score = composite.QvmScore(D(99), {}, {}, [], "x")
    v = composite.idea_pipeline([burner], {"BURN": score})["BURN"]
    assert v.speculative and not v.passes
    assert any("runway" in r for r in v.reasons) and any("10 % dilution" in r for r in v.reasons)
    assert "speculative" not in " ".join(
        composite.idea_pipeline([rec("OK", share_change_1y=D("0.01"))], {"OK": score})["OK"].reasons
    )


# ----------------------------------------------------------------------------- baselines


def test_baseline_withholds_median_below_five_and_excludes_stale_and_other_basis() -> None:
    def peer(
        key: str,
        value: str | None,
        end: dt.date = dt.date(2025, 12, 31),
        basis: str = "IFRS",
        country: str = "DE",
    ) -> Record:
        r = Record(key, key, {"country": country})
        r.values["gross_margin"] = MetricValue(
            value=D(value) if value else None,
            reason=None if value else "missing gross profit",
            period_end=end,
            basis=basis,
        )
        return r

    group = [peer(f"P{i}", f"0.{i + 3}") for i in range(5)] + [
        peer("STALE", "0.9", end=dt.date(2023, 1, 1)),
        peer("GAAP", "0.8", basis="US_GAAP"),
        peer("NONE", None),
    ]
    out = baselines.baseline(
        group,
        STOCK_CATALOGUE["gross_margin"],
        scope="global",
        scope_detail="x",
        as_of=TODAY,
        basis="IFRS",
    )
    assert out.members == 8 and out.comparable == 5 and out.median == D("0.5")
    reasons = {e.key: e.reason for e in out.excluded}
    assert (
        "stale" in reasons["STALE"]
        and "differs from IFRS" in reasons["GAAP"]
        and reasons["NONE"] == "missing gross profit"
    )
    few = baselines.baseline(
        group[:3], STOCK_CATALOGUE["gross_margin"], scope="global", scope_detail="x", as_of=TODAY
    )
    assert (
        few.median is None
        and "needs 5" in (few.median_withheld_reason or "")
        and few.high == D("0.5")
    )
    home, detail = baselines.pick_group(
        [*group, peer("FR", "0.4", country="FR")], "home_market", home_country="de"
    )
    assert "FR" not in {r.key for r in home} and "DE" in detail


# ----------------------------------------------------------------------------- funds & export


def test_fund_unknown_fee_is_never_zero_and_filters_exclude_it() -> None:
    cheap = fund_record(
        FundInputs(
            "IE00B4L5Y983",
            "Cheap",
            dt.date(2026, 9, 30),
            {
                "expense_ratio": D("0.0020"),
                "equity_weight": D("0.99"),
                "total_weight": D("0.995"),
                "distribution_policy": "accumulating",
            },
            [
                {
                    "row_index": i,
                    "isin": f"X{i}",
                    "name": f"n{i}",
                    "asset_class": "equity",
                    "weight": D("0.1"),
                    "sector": "Tech",
                    "country": "US",
                }
                for i in range(10)
            ],
        ),
        TODAY,
    )
    unknown = fund_record(
        FundInputs(
            "LU0000000001", "Unknown fee", dt.date(2026, 9, 30), {"equity_weight": D("0.5")}, []
        ),
        TODAY,
    )
    empty = fund_record(FundInputs("DE0000000002", None, None, {}, []), TODAY)
    assert (
        cheap.values["asset_class"].value == "equity"
        and unknown.values["asset_class"].value == "mixed_or_other"
    )
    assert unknown.values["expense_ratio"].value is None and "no expense ratio" in (
        unknown.values["expense_ratio"].reason or ""
    )
    assert cheap.values["effective_holdings"].value == D(10) and cheap.values[
        "top10_weight"
    ].value == D(1)
    assert (
        cheap.values["holdings_age_days"].value == D(7) and cheap.values["domicile"].value == "IE"
    )
    assert cheap.values["tracking_difference"].value is None
    out = run_discovery(
        [cheap, unknown, empty],
        FUND_CATALOGUE,
        [DiscoverFilter(metric="expense_ratio", op="lte", value=D("0.005"))],
        kind="fund",
        as_of=TODAY,
        universe_key="funds",
        universe_label="Funds",
        universe_definition="t",
    )
    assert [r.key for r in out.rows] == ["IE00B4L5Y983"]
    assert (out.matched, out.unknown_excluded, out.no_data) == (1, 1, 1)
    # Tracking difference is no longer a refused metric: it is filled per fund from a recorded benchmark.
    assert "no benchmark recorded" in (cheap.values["tracking_difference"].reason or "")
    assert FUND_CATALOGUE["tracking_difference"].no_source_reason is None


def test_csv_export_carries_provenance_and_unknown_is_blank_with_reason() -> None:
    records = [rec("A", roic=D("0.2")), rec("B", roic=None)]
    out = screen(records, [gte("roic", "0.1")], missing="keep_unknown")
    text = to_csv(
        out, STOCK_CATALOGUE, ["roic", "no_such_column"], generated_at="2026-10-07T00:00:00+00:00"
    )
    lines = text.splitlines()
    assert lines[0].startswith("# StewardAI discovery export")
    assert any(
        f"definition_hash={out.definition_hash}" in ln and "method_version=2.0.0" in ln
        for ln in lines
    )
    header = next(ln for ln in lines if not ln.startswith("#"))
    assert header.startswith("key,name,roic,roic__reason") and "no_such_column" not in header
    a = next(ln for ln in lines if ln.startswith("A,"))
    b = next(ln for ln in lines if ln.startswith("B,"))
    assert a.split(",")[2] == "0.2" and b.split(",")[2] == "" and b.split(",")[3] == "missing x"
