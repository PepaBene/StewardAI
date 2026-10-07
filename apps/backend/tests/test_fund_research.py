"""Phase 3 fund workbench rules, on synthetic data (no database)."""

import datetime as dt
from decimal import Decimal

from steward.analytics.returns import Dividend
from steward.fundresearch import analysis, compare, performance
from steward.fundresearch.data import Fact, FundData, FundListing
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
    v = perf("price")
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
