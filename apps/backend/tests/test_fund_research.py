"""Phase 3 fund workbench rules, on synthetic data (no database)."""

import datetime as dt
from decimal import Decimal

import pytest

from steward.analytics.returns import Dividend
from steward.fundresearch import analysis, compare, performance
from steward.fundresearch.data import Fact, FundData, FundListing, merge_factsheets
from steward.funds.aggregate import IssuerRef
from steward.lookthrough.read import RowIn, SnapshotIn

D = Decimal
DAY = dt.date(2026, 9, 30)


def row(name, isin, weight, *, asset="equity", sector="Technology", country="US", currency="USD"):
    return RowIn(
        name=name,
        isin=isin,
        ticker=None,
        asset_class=asset,
        weight=weight,
        sector=sector,
        country=country,
        currency=currency,
        currency_derived=False,
    )


def fund(
    isin="IE00AAAAAAA1", rows=None, *, portfolio="P1", sheet=None, as_of=DAY, facts=None
) -> FundData:
    snap = SnapshotIn(
        isin=isin,
        source="ishares_json",
        as_of=as_of,
        fetched_at=dt.datetime(2026, 10, 1, tzinfo=dt.UTC),
        fund_name=f"Fund {isin}",
        country_basis=None,
        currency_basis=None,
        warnings=[],
        rows=rows or [],
    )
    meta = {
        "id": 7,
        "provider": "ishares",
        "portfolio_id": portfolio,
        "as_of": as_of,
        "fund_name": snap.fund_name,
        "source": "ishares_json",
        "fund_currency": "USD",
    }
    return FundData(
        isin=isin,
        factsheet=sheet,
        meta=meta if rows is not None else None,
        snapshot=snap if rows is not None else None,
        listings=[
            FundListing(ticker="FND", currency="USD", price_symbol="fnd.us", has_prices=True)
        ],
        facts=facts or {},
    )


def views(data):
    rows = analysis.holding_rows(data, {}, {})
    return rows, analysis.build_holdings(data, rows)


# --------------------------------------------------------------------------- holdings


def test_residual_weight_is_unknown_not_spread():
    data = fund(
        rows=[
            row("A", "US0000000001", 0.5),
            row("B", "US0000000002", 0.3),
            row("Cash", None, 0.1, asset="cash", sector=None, country=None),
        ]
    )
    _, view = views(data)
    assert view.total_weight == D("0.9")
    assert view.residual_weight == D("0.1")
    assert view.reconciles is False
    assert any(b.kind == "not_accounted" and b.weight == D("0.1") for b in view.asset_classes)
    # the known holdings keep their published weights
    assert [x.weight for x in view.top] == [D("0.5"), D("0.3")]


def test_same_isin_rows_are_one_issuer_and_unclassified_is_visible():
    data = fund(
        rows=[
            row("A", "US0000000001", 0.2),
            row("A (dup line)", "US0000000001", 0.1, sector=None),
            row("B", "US0000000002", 0.7),
        ]
    )
    _, view = views(data)
    assert view.issuer_count == 2
    assert view.top[0].weight == D("0.7") and view.top[1].rows == 2
    assert any(b.kind == "unclassified" and b.weight == D("0.1") for b in view.sectors)


def test_no_snapshot_is_unknown_not_empty():
    data = fund(rows=None)
    _, view = views(data)
    assert view.as_of is None and view.total_weight is None and view.residual_weight is None
    assert "unknown" in view.warnings[0].lower()


def test_exposure_csv_carries_provenance_and_residual():
    data = fund(rows=[row("A", "US0000000001", 0.9)])
    rows, view = views(data)
    text = analysis.exposure_csv(view, rows)
    header, first, last = text.strip().split("\n")
    assert "snapshot_id" in header and "method_version" in header
    assert "7" in first and "ishares_json" in first
    assert "unknown" in last and "0.1" in last


# --------------------------------------------------------------------------- style


def test_concentration_and_aggregate_pe_with_coverage():
    lines = [
        analysis.StyleLine(
            "a", "A", D("0.5"), "Technology", D("20e9"), D("0.05"), D("4"), D("0.3"), True
        ),
        analysis.StyleLine(
            "b", "B", D("0.3"), "Financials", D("5e9"), D("0.10"), D("1"), None, True
        ),
        analysis.StyleLine("c", "C", D("0.2"), "Energy", None, None, None, None, False),
    ]
    view = analysis.build_style("IE00AAAAAAA1", DAY, lines, D(0))
    c = view.concentration
    assert c and c.top10_weight == D("1.0")
    # shares .5/.3/.2: HHI = .25+.09+.04 = .38
    assert c.hhi == D("0.38") and round(c.effective_issuers, 4) == D("2.6316")
    by = {m.id: m for m in view.metrics}
    # (0.5*0.05 + 0.3*0.10) / 0.8 = 0.06875 ; coverage 0.8 of 1.0
    assert by["earnings_yield"].value == D("0.06875")
    assert by["earnings_yield"].covered_weight == D("0.8")
    assert by["pe_proxy"].value is not None and round(by["pe_proxy"].value, 3) == D("14.545")
    # profitability excludes the financial and the uncovered holding
    assert by["profitability"].value == D("0.3") and by["profitability"].covered_weight == D("0.5")
    # holding with no market cap is its own bucket, never a size band
    assert next(s for s in view.size if s.kind == "no_market_cap").weight == D("0.2")
    assert view.size_coverage == D("0.8")
    assert "Style Box" in view.note


def test_style_without_equity_is_unknown():
    view = analysis.build_style("IE00AAAAAAA1", DAY, [], D(0))
    assert view.concentration is None and view.metrics == []
    assert view.warnings


# --------------------------------------------------------------------------- bond


def test_bond_figures_are_unavailable_never_inferred():
    data = fund(
        rows=[row("Bond 2030", "XS0000000001", 0.95, asset="other", sector=None)],
        sheet={"equity_weight": D("0.0"), "other_weight": D("0.95"), "as_of": DAY},
    )
    bond = analysis.build_bond(data, [], None)
    assert bond.applicable is True
    for r in (bond.weighted_maturity, bond.duration, bond.credit_quality):
        assert r.value is None and r.status.value == "unavailable"
    assert "name" in bond.duration.warnings[0]


def test_equity_fund_is_not_a_bond_fund():
    data = fund(
        rows=[row("A", "US0000000001", 0.99)],
        sheet={"equity_weight": D("0.99"), "other_weight": D("0"), "as_of": DAY},
    )
    assert analysis.build_bond(data, [], None).applicable is False


# --------------------------------------------------------------------------- checklist


def test_checklist_abstains_when_critical_inputs_missing():
    data = fund(
        rows=[row(f"S{i}", f"US00000000{i:02d}", 0.05) for i in range(20)],
        sheet={
            "expense_ratio": D("0.002"),
            "total_assets": D("5e9"),
            "total_assets_currency": "USD",
            "as_of": DAY,
        },
    )
    rows, hv = views(data)
    st = analysis.build_style(
        data.isin,
        DAY,
        [analysis.StyleLine(r.issuer_key, r.name, r.weight, r.sector) for r in rows],
        D(0),
    )
    cl = analysis.build_checklist(
        data,
        hv,
        st,
        today=DAY + dt.timedelta(days=10),
        tracking_difference=None,
        tracking_note=None,
    )
    by = {c.id: c for c in cl.criteria}
    assert by["costs"].status == "good" and by["freshness"].status == "good"
    assert by["replication"].status == "unknown"
    assert cl.summary_available is False and "Replication method" in (cl.abstain_reason or "")


@pytest.mark.parametrize(
    ("text", "status"),
    [
        ("yes", "watch"),
        ("Yes (up to 10%)", "watch"),
        ("no", "good"),
        ("No lending", "good"),
        ("unknown", "unknown"),
        ("partial", "unknown"),
    ],
)
def test_lending_free_text_is_scored_by_its_meaning(text, status):
    fact = Fact(
        key="securities_lending",
        value=text,
        source_url="https://x.test/l",
        recorded_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
    )
    data = fund(
        rows=[row("A", "US0000000001", 1.0)],
        sheet={"as_of": DAY},
        facts={"securities_lending": fact},
    )
    _, hv = views(data)
    st = analysis.build_style(data.isin, DAY, [], D(0))
    cl = analysis.build_checklist(
        data, hv, st, today=DAY, tracking_difference=None, tracking_note=None
    )
    assert next(c for c in cl.criteria if c.id == "lending").status == status


def test_tracking_criterion_stays_unknown_without_an_expense_ratio():
    data = fund(
        rows=[row(f"S{i}", f"US00000000{i:02d}", 0.05) for i in range(20)],
        sheet={"total_assets": D("5e6"), "total_assets_currency": "USD", "as_of": DAY},
    )
    rows, hv = views(data)
    st = analysis.build_style(
        data.isin,
        DAY,
        [analysis.StyleLine(r.issuer_key, r.name, r.weight, r.sector) for r in rows],
        D(0),
    )
    cl = analysis.build_checklist(
        data, hv, st, today=DAY, tracking_difference=D("-0.003"), tracking_note=None
    )
    tracking = {c.id: c for c in cl.criteria}["tracking"]
    assert tracking.status == "unknown" and "expense ratio is not recorded" in tracking.observed


def test_value_growth_bands_vote_on_yield_and_book_and_keep_unknown_apart():
    def line(key, weight, ey, pb, covered=True):
        return analysis.StyleLine(
            key, key, D(weight), "Technology", None, ey, pb, None, covered=covered
        )

    lines = [
        line("cheap", "0.2", D("0.08"), D("1.2")),  # two value votes
        line("rich", "0.3", D("0.02"), D("6")),  # two growth votes
        line("middle", "0.1", D("0.045"), D("2.5")),  # no votes: blend
        line("mixed", "0.1", D("0.07"), D("5")),  # one each: blend
        line("loss", "0.1", D("-0.01"), D("3")),  # negative earnings
        line("unknown", "0.2", None, None, covered=False),
    ]
    view = analysis.build_style("IE00AAAAAAA1", DAY, lines, D(0))
    by = {b.kind: b.weight for b in view.value_growth}
    assert by == {
        "value": D("0.2"),
        "growth": D("0.3"),
        "blend": D("0.2"),
        "no_earnings": D("0.1"),
        "not_covered": D("0.2"),
    }
    assert sum(by.values()) == D(1) and "not Morningstar" in view.value_growth_rule


def test_checklist_summarises_without_a_score_when_inputs_complete():
    fact = Fact(
        key="replication_method",
        value="physical_full",
        source_url="https://example.com/kid",
        recorded_at=dt.datetime(2026, 10, 1, tzinfo=dt.UTC),
    )
    data = fund(
        rows=[row(f"S{i}", f"US00000000{i:02d}", 0.05) for i in range(20)],
        sheet={
            "expense_ratio": D("0.012"),
            "total_assets": D("5e6"),
            "total_assets_currency": "USD",
            "as_of": DAY,
        },
        facts={"replication_method": fact},
    )
    rows, hv = views(data)
    st = analysis.build_style(
        data.isin,
        DAY,
        [analysis.StyleLine(r.issuer_key, r.name, r.weight, r.sector) for r in rows],
        D(0),
    )
    cl = analysis.build_checklist(
        data, hv, st, today=DAY, tracking_difference=D("-0.003"), tracking_note=None
    )
    assert cl.summary_available is True
    by = {c.id: c for c in cl.criteria}
    assert (
        by["costs"].status == "concern"
        and by["liquidity"].status == "concern"
        and by["replication"].source_url
    )
    assert "score" in cl.summary


def test_stale_holdings_are_a_concern():
    data = fund(rows=[row("A", "US0000000001", 1.0)], as_of=DAY - dt.timedelta(days=200))
    _, hv = views(data)
    st = analysis.build_style(data.isin, hv.as_of, [], D(0))
    cl = analysis.build_checklist(
        data, hv, st, today=DAY, tracking_difference=None, tracking_note=None
    )
    assert next(c for c in cl.criteria if c.id == "freshness").status == "concern"


# --------------------------------------------------------------------------- performance


def closes(start, n, daily):
    out, v = [], D(100)
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append((d, v))
            v = v * (1 + daily)
        d += dt.timedelta(days=1)
    return out


def perf(bench_basis, *, policy="accumulating", bench_ccy="USD", divs=(), n=800):
    data = fund(
        rows=[row("A", "US0000000001", 1.0)], sheet={"distribution_policy": policy, "as_of": DAY}
    )
    listing = data.listings[0]
    start = DAY - dt.timedelta(days=n * 7 // 5)
    f = closes(start, n, D("0.0004"))
    b = closes(start, n, D("0.0005"))
    info = performance.BenchmarkInfo(
        symbol="^idx", label="Idx", basis=bench_basis, currency=bench_ccy, note=""
    )
    end = f[-1][0]
    return performance.build_performance(data, listing, f, list(divs), True, info, b, [], None, end)


def test_matched_price_basis_gives_a_difference_and_tracking_error():
    v = perf("price", policy="distributing")
    one = next(p for p in v.periods if p.label == "1 year")
    assert (
        one.compared_on == "price" and one.difference is not None and one.difference < 0
    )  # fund drifts slower
    assert one.difference_annualised is not None
    assert v.tracking_error is not None and v.tracking_error.value is not None
    assert v.nav_available is False


def test_price_against_total_return_index_is_withheld_for_distributing_fund_without_distributions():
    v = perf("total", policy="distributing")
    one = next(p for p in v.periods if p.label == "1 year")
    assert one.compared_on == "total" and one.difference is None
    assert "distribution history" in (one.note or "")
    assert v.tracking_error is not None and v.tracking_error.value is None


def test_total_basis_works_for_accumulating_fund():
    v = perf("total", policy="accumulating")
    one = next(p for p in v.periods if p.label == "1 year")
    assert one.difference is not None and one.compared_on == "total"
    assert (
        v.tracking_error is not None and v.tracking_error.value is None
    )  # daily TE only on a price basis here


def test_unknown_basis_and_currency_mismatch_withhold_the_difference():
    unknown = perf("unknown")
    assert all(p.difference is None for p in unknown.periods)
    assert any("basis" in (p.note or "") for p in unknown.periods if p.start)
    mism = perf("price", bench_ccy="EUR")
    assert all(p.difference is None for p in mism.periods)
    assert any("currencies" in w for w in mism.warnings)


def test_accumulating_fund_is_not_compared_with_a_price_return_index():
    v = perf("price", policy="accumulating")
    assert all(p.difference is None for p in v.periods)
    assert any("accumulating" in (p.note or "") for p in v.periods if p.start)
    assert v.tracking_error is not None and v.tracking_error.value is None


def test_unrecorded_benchmark_or_listing_currency_withholds_the_difference():
    none_ccy = perf("price", policy="distributing", bench_ccy=None)
    assert all(p.difference is None for p in none_ccy.periods)
    assert any("currency is not recorded" in w for w in none_ccy.warnings)
    assert none_ccy.tracking_error is not None and none_ccy.tracking_error.value is None


def test_stale_last_bar_withholds_period_returns():
    data = fund(
        rows=[row("A", "US0000000001", 1.0)],
        sheet={"distribution_policy": "distributing", "as_of": DAY},
    )
    f = closes(DAY - dt.timedelta(days=1200), 600, D("0.0004"))
    v = performance.build_performance(
        data, data.listings[0], f, [], True, None, [], [], None, f[-1][0] + dt.timedelta(days=60)
    )
    assert all(p.fund_price.value is None for p in v.periods)
    assert any("days before" in w for w in v.warnings)
    assert "days before" in (v.periods[0].fund_price.warnings[0])


def test_fund_volatility_does_not_depend_on_the_benchmark_dates():
    data = fund(
        rows=[row("A", "US0000000001", 1.0)],
        sheet={"distribution_policy": "distributing", "as_of": DAY},
    )
    f = closes(DAY - dt.timedelta(days=900), 600, D("0.0004"))
    f = [(d, c * (1 + D("0.01") * (i % 3))) for i, (d, c) in enumerate(f)]
    sparse = [(d, c) for i, (d, c) in enumerate(f) if i % 2 == 0]
    info = performance.BenchmarkInfo(symbol="^i", label="I", basis="price", currency="USD", note="")
    end = f[-1][0]
    alone = performance.build_performance(
        data, data.listings[0], f, [], True, None, [], [], None, end
    )
    with_bench = performance.build_performance(
        data, data.listings[0], f, [], True, info, sparse, [], None, end
    )
    vol = lambda v: next(r for r in v.risk if r.id == "volatility").fund.value  # noqa: E731
    assert vol(alone) == vol(with_bench)


def test_distributions_make_total_return_exceed_price_return():
    d = DAY - dt.timedelta(days=100)
    d = d - dt.timedelta(days=d.weekday() - 0) if d.weekday() > 4 else d
    v = perf("price", policy="distributing", divs=[Dividend(ex_date=d, amount=D("1.5"))])
    one = next(p for p in v.periods if p.label == "1 year")
    assert one.fund_total and one.fund_total.value is not None and one.fund_price.value is not None
    assert one.fund_total.value > one.fund_price.value
    assert any(p.fund_total is not None for p in v.periods)


def test_no_prices_is_unknown():
    data = fund(rows=[row("A", "US0000000001", 1.0)])
    v = performance.build_performance(data, data.listings[0], [], [], True, None, [], [], None, DAY)
    assert v.periods == [] and v.warnings


def test_benchmark_is_suggested_only_on_exact_label_match():
    opts = [performance.BenchmarkOption(symbol="^spx", label="S&P 500", basis="price")]
    assert performance.suggest(opts, "s&p 500") == "^spx"
    assert performance.suggest(opts, "S&P 500 Index (USD)") is None


# --------------------------------------------------------------------------- compare


def prepared(isin, rows, *, portfolio, as_of=DAY):
    data = fund(isin, rows, portfolio=portfolio, as_of=as_of)
    groups = {}
    rws = analysis.holding_rows(data, groups, {})
    hv = analysis.build_holdings(data, rws)
    st = analysis.build_style(
        isin,
        hv.as_of,
        [analysis.StyleLine(r.issuer_key, r.name, r.weight, r.sector) for r in rws if r.issuer_key],
        D(0),
    )
    f = compare.CompareFund(
        isin=isin,
        name=data.name,
        provider="ishares",
        currency="USD",
        distribution_policy=None,
        expense_ratio=None,
        total_assets=None,
        total_assets_currency=None,
        holdings_as_of=hv.as_of,
        holdings_lag_days=(DAY - hv.as_of).days if hv.as_of else None,
        stale=False,
        portfolio_id=portfolio,
        duplicate_of=None,
        equity_weight=None,
        residual_weight=hv.residual_weight,
        unidentified_equity_weight=D(0),
        issuers=None,
        top10_weight=None,
        effective_issuers=None,
        returns=[],
    )
    return compare.Prepared(fund=f, rows=rws, view=hv, style=st)


def test_overlap_is_sum_of_min_and_lists_shared_holdings():
    a = prepared(
        "IE00AAAAAAA1",
        [row("A", "US0000000001", 0.6), row("B", "US0000000002", 0.4)],
        portfolio="P1",
    )
    b = prepared(
        "IE00BBBBBBB2",
        [row("A", "US0000000001", 0.3), row("C", "US0000000003", 0.7)],
        portfolio="P2",
    )
    out = compare.build_comparison([a, b])
    pair = out.pairs[0]
    assert pair.overlap.value == D("0.3") and pair.shared_issuers == 1
    assert pair.shared[0].issuer == "A" and pair.shared[0].overlap == D("0.3")


def test_fund_without_holdings_gives_unavailable_overlap_not_zero():
    a = prepared("IE00AAAAAAA1", [row("A", "US0000000001", 1.0)], portfolio="P1")
    data = fund("IE00BBBBBBB2", rows=None, portfolio="P2")
    empty_view = analysis.build_holdings(data, [])
    b = compare.Prepared(
        fund=a.fund.model_copy(
            update={"isin": "IE00BBBBBBB2", "holdings_as_of": None, "portfolio_id": "P2"}
        ),
        rows=[],
        view=empty_view,
        style=analysis.build_style("IE00BBBBBBB2", None, [], D(0)),
    )
    out = compare.build_comparison([a, b])
    assert out.pairs[0].overlap.value is None and out.pairs[0].overlap.status.value == "unavailable"
    assert any("unknown, not zero" in w for w in out.warnings)


def test_two_share_classes_of_one_portfolio_are_not_compared():
    rows = [row("A", "US0000000001", 0.6), row("B", "US0000000002", 0.4)]
    a = prepared("IE00AAAAAAA1", rows, portfolio="P1")
    b = prepared("IE00BBBBBBB2", rows, portfolio="P1")
    c = prepared("IE00CCCCCCC3", [row("A", "US0000000001", 0.5)], portfolio="P3")
    out = compare.build_comparison([a, b, c])
    dup = next(f for f in out.funds if f.isin == "IE00BBBBBBB2")
    assert dup.duplicate_of == "IE00AAAAAAA1"
    skipped = [p for p in out.pairs if p.skipped]
    assert len(skipped) == 1 and skipped[0].overlap.value is None
    assert any("share class" in w for w in out.warnings)


def test_different_providers_on_one_index_are_not_inferred_as_one_portfolio():
    rows = [row("A", "US0000000001", 0.6), row("B", "US0000000002", 0.4)]
    a = prepared("IE00AAAAAAA1", rows, portfolio=None)
    b = prepared("LU00BBBBBBB2", rows, portfolio=None)
    b = compare.Prepared(
        fund=b.fund.model_copy(update={"provider": "spdr"}), rows=b.rows, view=b.view, style=b.style
    )
    out = compare.build_comparison([a, b])
    assert all(f.duplicate_of is None for f in out.funds)
    assert not any(p.skipped for p in out.pairs)


def test_identical_holdings_without_a_portfolio_id_are_inferred_as_one_portfolio():
    rows = [row("A", "US0000000001", 0.6), row("B", "US0000000002", 0.4)]
    a = prepared("IE00AAAAAAA1", rows, portfolio=None)
    b = prepared("LU00BBBBBBB2", rows, portfolio=None)
    other = [row("A", "US0000000001", 0.6), row("B", "US0000000002", 0.3)]
    c = prepared("IE00CCCCCCC3", other, portfolio=None)
    out = compare.build_comparison([a, b, c])
    by = {f.isin: f for f in out.funds}
    assert by["LU00BBBBBBB2"].duplicate_of == "IE00AAAAAAA1"
    assert by["LU00BBBBBBB2"].duplicate_basis == "identical_holdings"
    assert by["IE00CCCCCCC3"].duplicate_of is None  # a different weight is a different portfolio
    assert any("inferred from the holdings" in w for w in out.warnings)


def test_issuer_register_groups_two_isins_into_one_issuer():
    data = fund(rows=[row("Share A", "US0000000001", 0.2), row("Share B", "US0000000002", 0.3)])
    register = {
        "US0000000001": IssuerRef("issuer:1", "Acme"),
        "US0000000002": IssuerRef("issuer:1", "Acme"),
    }
    rows = analysis.holding_rows(data, {}, register)
    view = analysis.build_holdings(data, rows)
    assert view.issuer_count == 1 and view.top[0].weight == D("0.5") and view.top[0].rows == 2


# --------------------------------------------------------------------------- bond risk and NAV


def test_dv01_duration_sums_key_rates_over_net_assets():
    risk = analysis.NportRisk(
        source="N-PORT x",
        period_end=DAY,
        net_assets=D(1_000_000),
        metrics={
            "interest_rate_dv01": {
                "USD": {
                    "period3Mon": "10",
                    "period1Yr": "20",
                    "period5Yr": "30",
                    "period10Yr": "20",
                    "period30Yr": "20",
                }
            }
        },
    )
    buckets, total, notes = analysis.dv01_duration(risk)
    assert total == D(1) and len(buckets) == 5 and notes == []


def test_dv01_withheld_when_a_bucket_is_missing_and_other_currency_said():
    risk = analysis.NportRisk(
        source="N-PORT x",
        period_end=DAY,
        net_assets=D(1_000_000),
        metrics={"interest_rate_dv01": {"USD": {"period1Yr": "5"}, "EUR": {"period1Yr": "5"}}},
    )
    buckets, total, notes = analysis.dv01_duration(risk)
    assert total is None and len(buckets) == 1
    assert any("EUR" in n for n in notes) and any("withheld" in n for n in notes)


def test_bond_prefers_owner_duration_and_never_invents_credit_quality():
    sheet = {"equity_weight": D("0.0"), "other_weight": D("0.9")}
    f = Fact(
        key="effective_duration_years",
        value="6.4",
        source_url="https://x.test/kid",
        recorded_at=dt.datetime(2026, 10, 1, tzinfo=dt.UTC),
    )
    view = analysis.build_bond(fund(sheet=sheet, facts={f.key: f}), [], None, None)
    assert view.duration.value == D("6.40") and view.duration.sources[0].kind == "owner_input"
    assert view.credit_quality.value is None


def test_nav_premium_needs_matching_currency_and_a_near_close():
    from steward.fundresearch.data import NavPoint

    closes = [(dt.date(2026, 9, 28), D("101")), (dt.date(2026, 9, 29), D("102"))]
    url = "https://x.test/n"
    pts = [
        NavPoint(as_of=dt.date(2026, 1, 5), nav=D("90"), currency="USD", source_url=url),
        NavPoint(as_of=dt.date(2026, 9, 29), nav=D("100"), currency="USD", source_url=url),
        NavPoint(as_of=dt.date(2026, 9, 30), nav=D("100"), currency="EUR", source_url=url),
    ]
    view = performance.build_nav(pts, closes, "USD")
    by = {p.as_of: p for p in view.points}
    assert by[dt.date(2026, 9, 29)].premium == D("0.02")
    assert "EUR" in by[dt.date(2026, 9, 30)].reason
    assert "no listing close" in by[dt.date(2026, 1, 5)].reason


def test_nav_premium_is_withheld_when_the_listing_currency_is_unknown():
    from steward.fundresearch.data import NavPoint

    pts = [
        NavPoint(
            as_of=dt.date(2026, 9, 29), nav=D("100"), currency="USD", source_url="https://x.test/n"
        )
    ]
    view = performance.build_nav_for_listing(pts, [(dt.date(2026, 9, 29), D("102"))], None)
    assert view.points[0].premium is None and "not recorded" in view.points[0].reason


def test_nav_premium_scales_pence_listing_closes_to_the_major_unit():
    from steward.fundresearch.data import NavPoint

    pts = [
        NavPoint(
            as_of=dt.date(2026, 9, 29), nav=D("10"), currency="GBP", source_url="https://x.test/n"
        )
    ]
    view = performance.build_nav_for_listing(pts, [(dt.date(2026, 9, 29), D("1020"))], "GBX")
    assert view.points[0].premium == D("0.02")  # GBX 1020 = GBP 10.20, not 102x the NAV
    assert view.points[0].listing_close == D("10.20")


def test_a_hand_entered_factsheet_is_not_hidden_by_a_later_empty_scrape():
    # Production, 17 Oct 2026: the weekly iShares scrape (holdings only) was dated after the
    # factsheet the owner entered, so the fund page showed the charge and benchmark as missing.
    scrape = {
        "source": "ishares_json",
        "provider": "ishares",
        "as_of": dt.date(2026, 10, 15),
        "fund_name": "iShares Diversified Commodity",
        "share_class": None,
        "expense_ratio": None,
        "benchmark_name": None,
        "total_assets": None,
        "charges": {},
    }
    owner = {
        "source": "manual",
        "provider": None,
        "as_of": dt.date(2026, 9, 30),
        "share_class": "USD (Acc)",
        "expense_ratio": D("0.0019"),
        "benchmark_name": "Bloomberg Commodity Index TR",
        "total_assets": D("2430430000"),
        "total_assets_currency": "USD",
        "total_assets_as_of": None,
        "charges": {},
    }
    merged = merge_factsheets([scrape, owner])
    assert merged is not None and merged["fund_name"] == "iShares Diversified Commodity"
    assert merged["expense_ratio"] == D("0.0019") and merged["total_assets_currency"] == "USD"
    assert set(merged["filled_from"]) == {
        "share_class",
        "expense_ratio",
        "benchmark_name",
        "total_assets",
    }
    by_key = {f.key: f for f in analysis.build_profile(fund(sheet=merged)).fields}
    assert by_key["expense_ratio"].value == "0.0019"
    assert by_key["expense_ratio"].source == "owner-entered factsheet"
    assert by_key["expense_ratio"].as_of == dt.date(2026, 9, 30)
    assert by_key["total_assets"].as_of == dt.date(2026, 9, 30)
    assert by_key["issuer"].source == "ishares factsheet (ishares_json)"
    assert merge_factsheets([]) is None
