"""Portfolio X-Ray (plan Phase 5): leaves, views, concentration, fundamentals, intersection,
benchmark and proposed-portfolio logic, on a small portfolio worked by hand.

The portfolio is 1000 EUR:
  direct AAPL                     200   (0.20)
  fund CSPX                       500   (0.50)  rows: AAPL .10, MSFT .30, NESN .50, cash .05
  fund VWCE (no stored holdings)  200   (0.20)  not looked through
  cash                            100   (0.10)
so through CSPX: AAPL .05, MSFT .15, NESN .25, the fund's own cash .025, unknown .025.
"""

import datetime as dt
from decimal import Decimal

import pytest

from steward.company.segments import Breakdown, MemberLine, SegmentAnalysis
from steward.config.jobs import CSPX_ISIN, VWCE_ISIN, LookThroughSettings
from steward.funds.aggregate import aggregate_exposure
from steward.lookthrough.read import PortfolioIn, PositionIn, RowIn, SnapshotIn
from steward.xray.benchmark import benchmark_weights, compare
from steward.xray.intersection import intersection
from steward.xray.leaves import IssuerFacts, RevenueSlice, XrayData, build_leaves
from steward.xray.metrics import build_fundamentals, concentration_levels
from steward.xray.scenario import (
    Candidate,
    ProposalError,
    ProposedRequest,
    ProposedWeight,
    analyse,
    apply_proposal,
)
from steward.xray.scenario import (
    compare as compare_proposal,
)
from steward.xray.service import revenue_slices
from steward.xray.views import DIMENSIONS, ViewContext, build_view, build_views

AAPL = "US0378331005"
MSFT = "US5949181045"
NESN = "CH0038863350"
DAY = dt.date(2026, 10, 7)


def row(
    name: str,
    isin: str | None,
    weight: float,
    *,
    asset_class: str = "equity",
    sector: str | None = "Information Technology",
    country: str | None = "US",
    currency: str | None = "USD",
) -> RowIn:
    return RowIn(
        name=name,
        isin=isin,
        ticker=None,
        asset_class=asset_class,
        weight=weight,
        sector=sector,
        country=country,
        currency=currency,
        currency_derived=False,
    )


def snapshot(
    isin: str, rows: list[RowIn], country_basis: str | None = "country_of_risk"
) -> SnapshotIn:
    return SnapshotIn(
        isin=isin,
        source="ishares_json",
        as_of=dt.date(2026, 10, 1),
        fetched_at=dt.datetime(2026, 10, 1, tzinfo=dt.UTC),
        fund_name=None,
        country_basis=country_basis,
        currency_basis="issuer",
        warnings=[],
        rows=rows,
    )


def position(
    ticker: str, isin: str, value: float, *, security_type: str = "STOCK", name: str | None = None
) -> PositionIn:
    return PositionIn(
        ticker=ticker,
        name=name or ticker,
        isin=isin,
        security_type=security_type,
        home_country="US" if security_type == "STOCK" else "IE",
        currency="USD",
        value_base=value,
    )


def portfolio(*, direct: bool = True, vwce: bool = True, cash: float = 100.0) -> PortfolioIn:
    positions = []
    if direct:
        positions.append(position("AAPL_US_EQ", AAPL, 200, name="Apple Inc."))
    positions.append(position("CSPX", CSPX_ISIN, 500, security_type="ETF"))
    if vwce:
        positions.append(position("VWCE", VWCE_ISIN, 200, security_type="ETF"))
    return PortfolioIn(
        snapshot_date=DAY,
        base_currency="EUR",
        account_currency="EUR",
        cash_base=cash,
        positions=positions,
    )


def cspx_snapshot(**kw: object) -> SnapshotIn:
    return snapshot(
        CSPX_ISIN,
        [
            row("Apple Inc.", AAPL, 0.10),
            row("Microsoft Corp", MSFT, 0.30),
            row(
                "Nestle SA",
                NESN,
                0.50,
                sector="Consumer Staples",
                country="CH",
                currency="CHF",
            ),
            row("Cash", None, 0.05, asset_class="cash", sector=None, country=None, currency=None),
        ],
        **kw,  # type: ignore[arg-type]
    )


def data(**kw: object) -> XrayData:
    pf = kw.pop("portfolio", None) or portfolio()
    snaps = kw.pop("snapshots", None) or {CSPX_ISIN: cspx_snapshot()}
    return XrayData(
        portfolio=pf,  # type: ignore[arg-type]
        settings=LookThroughSettings(),
        snapshots=snaps,  # type: ignore[arg-type]
        **kw,  # type: ignore[arg-type]
    )


def ctx_for(d: XrayData, facts: dict | None = None) -> ViewContext:  # type: ignore[type-arg]
    return ViewContext(data=d, leafset=build_leaves(d), facts=facts or {})


def bucket(view, key: str):  # type: ignore[no-untyped-def]
    return next(b for b in view.buckets if b.key == key)


# ----------------------------------------------------------------------------- leaves and views


def test_leaves_add_up_to_the_whole_portfolio_and_match_the_aggregate() -> None:
    d = data()
    leafset = build_leaves(d)
    assert sum(x.weight for x in leafset.leaves) == pytest.approx(1.0)
    agg = aggregate_exposure(d.portfolio, d.settings, d.snapshots, as_of=DAY, top=1000)
    by_issuer: dict[str, float] = {}
    for x in leafset.leaves:
        if x.is_equity:
            by_issuer[x.issuer_key] = by_issuer.get(x.issuer_key, 0.0) + x.weight  # type: ignore[index]
    for issuer in agg.issuers:
        assert by_issuer[issuer.key] == pytest.approx(issuer.weight)
    # the ADR-free case: AAPL is merged across the direct holding and the fund row
    assert by_issuer[f"isin:{AAPL}"] == pytest.approx(0.25)


@pytest.mark.parametrize("dimension", DIMENSIONS)
def test_every_view_adds_up_to_the_whole_portfolio(dimension: str) -> None:
    ctx = ctx_for(data())
    view = build_view(dimension, ctx)  # type: ignore[arg-type]
    assert view.total_weight == pytest.approx(1.0)
    assert view.reconciles


def test_views_reconcile_when_a_fund_reports_more_than_100_percent() -> None:
    heavy = snapshot(CSPX_ISIN, [row("Apple Inc.", AAPL, 0.7), row("Microsoft Corp", MSFT, 0.5)])
    ctx = ctx_for(data(snapshots={CSPX_ISIN: heavy}))
    # rows add to 120% of the fund: the extra 10% of 50% is carried, and the view says it adds up
    assert ctx.leafset.over_reported == pytest.approx(0.10)
    for view in build_views(ctx):
        assert view.total_weight == pytest.approx(1.10)
        assert view.reconciles


def test_unknown_weight_is_its_own_bucket_in_every_view() -> None:
    ctx = ctx_for(data())
    for dimension in ("sector", "country", "currency", "asset_class", "industry"):
        view = build_view(dimension, ctx)  # type: ignore[arg-type]
        kinds = {b.kind for b in view.buckets}
        assert {"not_looked_through", "unknown_fund", "cash"} <= kinds
        assert bucket(view, "__not_looked_through__").weight == pytest.approx(0.20)
        assert bucket(view, "__unknown_fund_weight__").weight == pytest.approx(0.025)


def test_sector_view_names_contributors_and_how_each_label_was_obtained() -> None:
    view = build_view("sector", ctx_for(data()))
    it = bucket(view, "Information Technology")
    # direct AAPL has no sector of its own: it takes the fund row for the same ISIN
    assert it.weight == pytest.approx(0.20 + 0.05 + 0.15)
    assert it.basis == {"reported": pytest.approx(0.40)}
    assert [c.key for c in it.contributors][:2] == [f"isin:{AAPL}", f"isin:{MSFT}"]
    assert it.contributors[0].weight == pytest.approx(0.25)
    assert bucket(view, "Consumer Staples").weight == pytest.approx(0.25)


def test_owner_and_provider_sectors_are_labelled_owner_and_inferred() -> None:
    d = data(
        portfolio=portfolio(vwce=False),
        snapshots={CSPX_ISIN: snapshot(CSPX_ISIN, [row("Microsoft Corp", MSFT, 0.5)])},
    )
    d.settings = LookThroughSettings(direct_sectors={"AAPL_US_EQ": "Health Care"})
    owner = build_view("sector", ctx_for(d))
    assert bucket(owner, "Health Care").basis == {"owner": pytest.approx(0.25)}  # 200 of 800
    # no owner label and no fund row for the ISIN: the provider's own sector, mapped to GICS
    d2 = data(
        portfolio=portfolio(vwce=False),
        snapshots={CSPX_ISIN: snapshot(CSPX_ISIN, [row("Microsoft Corp", MSFT, 0.5)])},
    )
    from steward.xray.leaves import Profile

    d2.profiles = {AAPL: Profile(sector="Technology", industry="Consumer Electronics")}
    view = build_view("sector", ctx_for(d2))
    assert bucket(view, "Information Technology").basis["inferred"] == pytest.approx(0.25)
    industry = build_view("industry", ctx_for(d2))
    assert bucket(industry, "Consumer Electronics").basis == {"reported": pytest.approx(0.25)}
    assert bucket(industry, "__unclassified__").weight == pytest.approx(0.3125)  # the MSFT row


def test_country_view_does_not_silently_mix_country_of_risk_and_home_market() -> None:
    view = build_view("country", ctx_for(data()))
    assert view.mixed_basis
    # direct AAPL is the instrument list's home market; the fund rows are country of risk
    assert view.country_kinds is not None
    assert view.country_kinds["home_market"] == pytest.approx(0.20)
    assert view.country_kinds["country_of_risk"] == pytest.approx(0.45)
    assert any("country of risk" in w and "home market" in w for w in view.warnings)
    us = bucket(view, "US")
    assert us.weight == pytest.approx(0.40)
    assert bucket(view, "CH").weight == pytest.approx(0.25)


def test_country_view_is_not_flagged_when_every_label_means_the_same() -> None:
    d = data(portfolio=portfolio(direct=False, vwce=False))
    view = build_view("country", ctx_for(d))
    assert not view.mixed_basis
    assert view.warnings == []


def test_fund_view_keeps_each_funds_unknown_part_inside_the_fund() -> None:
    view = build_view("fund", ctx_for(data()))
    weights = {b.key: b.weight for b in view.buckets}
    assert weights["direct"] == pytest.approx(0.20)
    assert weights[CSPX_ISIN] == pytest.approx(0.50)
    assert weights[VWCE_ISIN] == pytest.approx(0.20)
    assert weights["cash"] == pytest.approx(0.10)


def test_asset_class_view_separates_fund_cash_from_unknown() -> None:
    view = build_view("asset_class", ctx_for(data()))
    assert bucket(view, "equity").weight == pytest.approx(0.65)
    assert bucket(view, "cash").weight == pytest.approx(0.10 + 0.025)
    assert bucket(view, "__not_looked_through__").weight == pytest.approx(0.20)
    assert bucket(view, "__unknown_fund_weight__").weight == pytest.approx(0.025)


def test_fee_view_never_treats_an_unknown_charge_as_free() -> None:
    d = data(expense_ratios={CSPX_ISIN: Decimal("0.0007")})
    view = build_view("fees", ctx_for(d))
    assert bucket(view, "fee:lt0.10").weight == pytest.approx(0.50)
    assert bucket(view, "fee:direct").weight == pytest.approx(0.20)
    # VWCE has no stored ratio
    assert bucket(view, "fee:unknown").weight == pytest.approx(0.20)


def test_size_and_style_views_use_stored_facts_and_leave_the_rest_unclassified() -> None:
    facts = {
        f"isin:{AAPL}": IssuerFacts(
            market_cap_eur=Decimal("3000000000000"),
            earnings_yield=Decimal("0.04"),
            price_to_book=Decimal("40"),
            covered=True,
        ),
        f"isin:{NESN}": IssuerFacts(
            market_cap_eur=Decimal("1500000000"),
            earnings_yield=Decimal("0.08"),
            price_to_book=Decimal("1.2"),
            covered=True,
        ),
    }
    ctx = ctx_for(data(), facts)
    size = build_view("size", ctx)
    assert bucket(size, "Large (market cap at least EUR 10bn)").weight == pytest.approx(0.25)
    assert bucket(size, "Small (below EUR 2bn)").weight == pytest.approx(0.25)
    assert bucket(size, "__no_market_cap__").weight == pytest.approx(0.15)  # MSFT: no facts
    style = build_view("style", ctx)
    assert bucket(style, "growth").weight == pytest.approx(0.25)  # AAPL: ey 4%, P/B 40
    assert bucket(style, "value").weight == pytest.approx(0.25)  # NESN: ey 8%, P/B 1.2
    assert bucket(style, "not_covered").weight == pytest.approx(0.15)
    assert style.reconciles and size.reconciles


def test_revenue_geography_only_covers_companies_with_data() -> None:
    d = data(
        revenue={
            AAPL: {
                "revenue_country": [
                    RevenueSlice("US", 0.6, "owner"),
                    RevenueSlice("DE", 0.2, "reported"),
                ]
            }
        }
    )
    view = build_view("revenue_country", ctx_for(d))
    assert bucket(view, "US").weight == pytest.approx(0.25 * 0.6)
    assert bucket(view, "US").basis == {"owner": pytest.approx(0.15)}
    assert bucket(view, "DE").basis == {"reported": pytest.approx(0.05)}
    # the unmapped fifth of AAPL and every company with no data are "no data", not estimated
    assert bucket(view, "__unclassified__").weight == pytest.approx(0.25 * 0.2 + 0.15 + 0.25)
    assert view.reconciles
    assert any("has this data" in w for w in view.warnings)
    assert build_view("commodity", ctx_for(d)).classified_weight == 0


def test_revenue_slices_use_reported_country_names_and_never_regions() -> None:
    def member(label: str, share: str) -> MemberLine:
        return MemberLine(
            key=label,
            label=label,
            role="operating",
            values={},
            latest_value=None,
            latest_share=Decimal(share),
            change=None,
        )

    geo = Breakdown(
        kind="geographic",
        label="Geography",
        periods=["FY2025"],
        latest_period="FY2025",
        members=[
            member("United States", "0.55"),
            member("Europe", "0.30"),
            member("Germany", "0.15"),
        ],
        operating_total=None,
        eliminations=None,
        unallocated=None,
        after_eliminations=None,
        reported_total=None,
        difference=None,
        difference_note=None,
        notes=[],
    )
    analysis = SegmentAnalysis(
        ticker="X",
        basis=None,
        available_bases=[],
        currency=None,
        source_note=None,
        breakdowns=[geo],
        mappings=[],
        exposures=[],
        notes=[],
        method_version="1",
    )
    slices = revenue_slices(analysis)["revenue_country"]
    assert {(s.value, round(s.share, 2), s.basis) for s in slices} == {
        ("US", 0.55, "reported"),
        ("DE", 0.15, "reported"),
    }  # "Europe" is a region, not a country: left as no data


# ----------------------------------------------------------------------------- concentration


def test_issuer_concentration_is_measured_on_identified_weight() -> None:
    levels = {c.level: c for c in concentration_levels(build_leaves(data()))}
    issuer = levels["issuer"]
    assert issuer.known_weight == pytest.approx(0.65)
    assert issuer.unknown_weight == pytest.approx(0.225)  # not looked through + fund remainder
    shares = [0.25 / 0.65, 0.15 / 0.65, 0.25 / 0.65]
    hhi = sum(s * s for s in shares)
    assert issuer.hhi == pytest.approx(hhi)
    assert issuer.effective_n == pytest.approx(1 / hhi)
    assert issuer.top10_weight == pytest.approx(0.65)
    assert issuer.largest is not None and issuer.largest.weight == pytest.approx(0.25)
    # security level keeps AAPL as one ISIN (direct + fund), here identical to the issuer level
    assert levels["security"].hhi == pytest.approx(hhi)
    assert levels["fund"].items == 2
    assert levels["fund"].known_weight == pytest.approx(0.70)


def test_sector_and_country_concentration_leave_unlabelled_equity_outside() -> None:
    unlabelled = snapshot(
        CSPX_ISIN,
        [row("Apple Inc.", AAPL, 0.5, sector=None), row("Nestle SA", NESN, 0.5, country=None)],
    )
    levels = {
        c.level: c
        for c in concentration_levels(build_leaves(data(snapshots={CSPX_ISIN: unlabelled})))
    }
    # sector: direct AAPL (.20) takes its sector from the fund row, which has none, plus the fund's
    # own AAPL row (.25). country: only the Nestle row (.25) lacks one; direct AAPL has a home
    # market.
    assert levels["sector"].unclassified_weight == pytest.approx(0.45)
    assert levels["country"].unclassified_weight == pytest.approx(0.25)
    assert any("no label" in w for w in levels["sector"].warnings)


# ----------------------------------------------------------------------------- fundamentals


def covered(ey: str | None, pb: str | None = None, dy: str | None = None, **kw: str) -> IssuerFacts:
    def d(x: str | None) -> Decimal | None:
        return Decimal(x) if x is not None else None

    return IssuerFacts(
        earnings_yield=d(ey),
        price_to_book=d(pb),
        dividend_yield=d(dy),
        profitability=d(kw.get("prof")),
        net_debt_to_equity=d(kw.get("nde")),
        covered=True,
    )


def test_earnings_yield_is_a_weighted_yield_and_pe_is_its_inverse() -> None:
    facts = {
        f"isin:{AAPL}": covered("0.04", "10", "0.01"),
        f"isin:{MSFT}": covered("0.03", "5", "0.02"),
        f"isin:{NESN}": covered("-0.01", "2", "0.03"),
    }
    out = build_fundamentals(build_leaves(data()), facts)
    m = {x.id: x for x in out.metrics}
    # positive-earnings holdings only: (0.25 x 0.04 + 0.15 x 0.03) / 0.40
    assert m["earnings_yield"].value == pytest.approx(0.03625)
    assert m["pe"].value == pytest.approx(1 / 0.03625)
    assert m["earnings_yield"].covered_weight == pytest.approx(0.40)
    assert m["earnings_yield"].coverage == pytest.approx(0.40 / 0.65)
    assert [(e.label, e.reason) for e in m["earnings_yield"].excluded] == [
        ("Nestle SA", "negative or zero earnings")
    ]
    # price to book: harmonic mean over everything covered, 1 / (sum(w/pb) / sum(w))
    expected = 0.65 / (0.25 / 10 + 0.15 / 5 + 0.25 / 2)
    assert m["price_to_book"].value == pytest.approx(expected)
    assert m["dividend_yield"].value == pytest.approx(
        (0.25 * 0.01 + 0.15 * 0.02 + 0.25 * 0.03) / 0.65
    )
    assert any("negative earnings" in n for n in out.notes)
    assert any("unknown fund weight" in n for n in out.notes)


def test_pe_is_withheld_when_the_aggregate_has_no_positive_earnings() -> None:
    facts = {
        f"isin:{AAPL}": covered("-0.04"),
        f"isin:{MSFT}": covered("-0.03"),
        f"isin:{NESN}": covered(None),
    }
    out = build_fundamentals(build_leaves(data()), facts)
    m = {x.id: x for x in out.metrics}
    assert m["earnings_yield"].value is None and m["earnings_yield"].reason
    assert m["pe"].value is None
    assert "no P/E" in (m["pe"].reason or "")


def test_financial_companies_stay_out_of_profitability_and_leverage() -> None:
    bank = snapshot(
        CSPX_ISIN,
        [
            row("Bank", "US0000000001", 0.5, sector="Financials"),
            row("Maker", "US0000000002", 0.5, sector="Industrials"),
        ],
    )
    d = data(portfolio=portfolio(direct=False, vwce=False, cash=0.0), snapshots={CSPX_ISIN: bank})
    facts = {
        "isin:US0000000001": covered("0.10", "1", "0.04", prof="0.05", nde="9"),
        "isin:US0000000002": covered("0.05", "3", "0.02", prof="0.30", nde="1.5"),
    }
    out = build_fundamentals(build_leaves(d), facts)
    m = {x.id: x for x in out.metrics}
    assert m["profitability"].value == pytest.approx(0.30)
    assert m["leverage"].value == pytest.approx(1.5)
    assert m["earnings_yield"].value == pytest.approx(0.075)  # the bank stays in the yield
    assert out.financials_weight == pytest.approx(0.5)
    assert [e.label for e in m["leverage"].excluded] == ["Bank"]
    assert any("Financial companies" in n for n in out.notes)


def test_a_figure_covering_under_half_the_equity_is_marked_thin() -> None:
    facts = {f"isin:{NESN}": covered("0.04", "2")}
    out = build_fundamentals(build_leaves(data()), facts)
    m = {x.id: x for x in out.metrics}
    assert m["earnings_yield"].coverage == pytest.approx(0.25 / 0.65)
    assert m["earnings_yield"].thin


def test_fee_drag_lists_funds_without_a_known_charge() -> None:
    out = build_fundamentals(build_leaves(data(expense_ratios={CSPX_ISIN: Decimal("0.0007")})), {})
    assert out.fees is not None
    assert out.fees.annual_drag == pytest.approx(0.5 * 0.0007)
    assert out.fees.annual_cost_base == pytest.approx(0.5 * 0.0007 * 1000)
    assert out.fees.funds_without_ratio == ["VWCE"]
    assert out.fees.unpriced_weight == pytest.approx(0.20)


# ----------------------------------------------------------------------------- intersection


def test_intersection_keeps_the_fund_to_underlying_trail() -> None:
    d = data()
    report = aggregate_exposure(d.portfolio, d.settings, d.snapshots, as_of=DAY, top=1000)
    out = intersection(report)
    assert out.shared_count == 1
    apple = out.issuers[0]  # shared issuers come first
    assert apple.key == f"isin:{AAPL}"
    assert apple.weight == pytest.approx(0.25)
    assert apple.direct_weight == pytest.approx(0.20)
    assert apple.fund_weight == pytest.approx(0.05)
    assert {(c.kind, c.label) for c in apple.channels} == {("direct", "Direct"), ("fund", "CSPX")}
    assert apple.securities[0].isin == AAPL
    assert out.redundant_weight == pytest.approx(0.05)


def test_removing_a_fund_recomputes_concentration_with_proceeds_in_cash() -> None:
    d = data()
    report = aggregate_exposure(d.portfolio, d.settings, d.snapshots, as_of=DAY, top=1000)
    out = intersection(report)
    cspx = next(r for r in out.removal if r.isin == CSPX_ISIN)
    assert cspx.issuers_touched == 3
    # only direct AAPL (0.20) remains, so the identified issuer weight is one name
    assert cspx.hhi_after == pytest.approx(1.0)
    assert cspx.effective_after == pytest.approx(1.0)
    assert cspx.top10_after == pytest.approx(0.20)
    assert cspx.largest_after is not None and cspx.largest_after.label.startswith("Apple")
    assert cspx.shared_after == 0
    assert cspx.unknown_removed == pytest.approx(0.025)
    drops = {x.key: (x.before, x.after) for x in cspx.biggest_drops}
    assert drops[f"isin:{MSFT}"] == (pytest.approx(0.15), pytest.approx(0.0))
    vwce = next(r for r in out.removal if r.isin == VWCE_ISIN)
    assert vwce.issuers_touched == 0  # nothing known about it, nothing to remove from the issuers


# ----------------------------------------------------------------------------- benchmark


def proxy_snapshot(**kw: object) -> SnapshotIn:
    return snapshot(
        CSPX_ISIN,
        [
            row("Apple Inc.", AAPL, 0.5),
            row("Microsoft Corp", MSFT, 0.3),
            row("Exxon", "US30231G1022", 0.2, sector="Energy"),
        ],
        **kw,  # type: ignore[arg-type]
    )


def test_benchmark_compares_sector_and_issuer_and_reports_missing_constituents() -> None:
    d = data(portfolio=portfolio(direct=False, vwce=False))
    bw = benchmark_weights(
        proxy_snapshot(), label="S&P 500", symbol="^spx", fund_label="CSPX", groups={}, register={}
    )
    result, actives = compare(build_leaves(d), bw, today=DAY)
    assert result.available
    # total 600: CSPX is 5/6 of it, so AAPL .0833, MSFT .25, NESN .4167 of the total; equity .75
    assert result.equity_weight == pytest.approx(0.75)
    assert result.outside_weight == pytest.approx(0.25)  # cash, the fund's cash, unknown
    sector = next(x for x in result.dimensions if x.dimension == "sector")
    assert sector.like_for_like
    it = next(x for x in sector.lines if x.key == "Information Technology")
    assert it.portfolio_weight == pytest.approx((0.0833333 + 0.25) / 0.75, rel=1e-5)
    assert it.benchmark_weight == pytest.approx(0.8)
    assert it.active == pytest.approx(it.portfolio_weight - 0.8)
    # issuer: NESN is held but not in the proxy; Exxon is in the proxy but not held
    assert [x.label for x in result.portfolio_only] == ["Nestle SA"]
    assert result.portfolio_only[0].weight == pytest.approx(0.4166667 / 0.75, rel=1e-5)
    assert [x.label for x in result.benchmark_only] == ["Exxon"]
    assert result.benchmark_only_weight == pytest.approx(0.2)
    # overlap: min(AAPL .1111, .5) + min(MSFT .3333, .3)
    assert result.overlap == pytest.approx(0.0833333 / 0.75 + 0.3, rel=1e-5)
    assert "issuer" in actives and "sector" in actives
    over, under, _ = actives["sector"]
    assert under[0].key == "Information Technology"
    assert over[0].key == "Consumer Staples"
    assert "not the index's membership" in result.definition


def test_country_is_compared_only_against_the_proxys_own_basis() -> None:
    bw = benchmark_weights(
        proxy_snapshot(country_basis="country_of_risk"),
        label="S&P 500",
        symbol="^spx",
        fund_label="CSPX",
        groups={},
        register={},
    )
    like = compare(
        build_leaves(data(portfolio=portfolio(direct=False, vwce=False))), bw, today=DAY
    )[0]
    country = next(x for x in like.dimensions if x.dimension == "country")
    assert country.like_for_like and country.lines
    # a direct stock's country is the instrument list's home market: not the same thing
    mixed, actives = compare(build_leaves(data()), bw, today=DAY)
    country = next(x for x in mixed.dimensions if x.dimension == "country")
    assert not country.like_for_like and country.lines == []
    assert "not compared" in (country.reason or "")
    assert "country" not in actives


def test_sector_is_withheld_when_the_labels_are_not_the_same_scheme() -> None:
    icb = snapshot(CSPX_ISIN, [row("Apple Inc.", AAPL, 1.0, sector="Technology")])
    bw = benchmark_weights(
        icb, label="FTSE", symbol=None, fund_label="VWCE", groups={}, register={}
    )
    result, _ = compare(
        build_leaves(data(portfolio=portfolio(direct=False, vwce=False))), bw, today=DAY
    )
    sector = next(x for x in result.dimensions if x.dimension == "sector")
    assert not sector.like_for_like
    assert "GICS" in (sector.reason or "")


def test_benchmark_notes_that_it_is_a_proxy_and_how_old_it_is() -> None:
    old = proxy_snapshot()
    object.__setattr__(old, "as_of", DAY - dt.timedelta(days=90))
    bw = benchmark_weights(
        old, label="S&P 500", symbol=None, fund_label="CSPX", groups={}, register={}
    )
    result, _ = compare(build_leaves(data()), bw, today=DAY)
    assert result.age_days == 90
    assert any("90 days old" in n for n in result.notes)
    assert "today" in result.definition


# ----------------------------------------------------------------------------- proposed portfolio


CANDIDATES = {
    "NESN_EQ": Candidate("NESN_EQ", "Nestle SA", NESN, "STOCK", "CH", "CHF"),
    "SXR8": Candidate("SXR8", "iShares Core MSCI World", "IE00B4L5Y983", "ETF", "IE", "EUR"),
}


def test_proposed_weights_change_a_copy_and_leave_the_saved_portfolio_alone() -> None:
    pf = portfolio()
    before_values = [p.value_base for p in pf.positions]
    req = ProposedRequest(weights=[ProposedWeight(ticker="AAPL_US_EQ", weight=0.10)])
    new_pf, notes = apply_proposal(pf, req, CANDIDATES)
    assert [p.value_base for p in pf.positions] == before_values  # the original is untouched
    assert pf.cash_base == 100
    by = {p.ticker: p.value_base for p in new_pf.positions}
    assert by["AAPL_US_EQ"] == pytest.approx(100)
    assert new_pf.cash_base == pytest.approx(200)  # the 100 freed sits in cash
    assert sum(by.values()) + new_pf.cash_base == pytest.approx(1000)
    assert any("cash is the remainder" in n for n in notes)


def test_proposal_cannot_exceed_the_whole_portfolio_or_name_unknown_securities() -> None:
    pf = portfolio()
    too_much = ProposedRequest(weights=[ProposedWeight(ticker="AAPL_US_EQ", weight=0.9)])
    with pytest.raises(ProposalError, match="cannot exceed 100%"):
        apply_proposal(pf, too_much, CANDIDATES)
    with pytest.raises(ProposalError, match="not a held position"):
        apply_proposal(
            pf, ProposedRequest(weights=[ProposedWeight(ticker="ZZZ", weight=0.1)]), CANDIDATES
        )
    with pytest.raises(ProposalError, match="not in the investable universe"):
        apply_proposal(
            pf, ProposedRequest(additions=[ProposedWeight(ticker="NOPE", weight=0.1)]), CANDIDATES
        )
    with pytest.raises(ProposalError, match="already held"):
        apply_proposal(
            pf,
            ProposedRequest(additions=[ProposedWeight(ticker="AAPL_US_EQ", weight=0.1)]),
            CANDIDATES,
        )


def test_before_and_after_show_overlap_fees_and_coverage_differences() -> None:
    d = data(expense_ratios={CSPX_ISIN: Decimal("0.0007")})
    pf = d.portfolio
    assert pf is not None
    req = ProposedRequest(
        weights=[ProposedWeight(ticker="AAPL_US_EQ", weight=0.0)],
        additions=[
            ProposedWeight(ticker="NESN_EQ", weight=0.10),
            ProposedWeight(ticker="SXR8", weight=0.10),
        ],
    )
    new_pf, notes = apply_proposal(pf, req, CANDIDATES)
    import dataclasses

    after_data = dataclasses.replace(d, portfolio=new_pf)
    before, after = analyse(d, {}), analyse(after_data, {})
    out = compare_proposal(
        before,
        after,
        pf,
        new_pf,
        as_of=DAY.isoformat(),
        notes=notes,
        held_funds_looked_through={"CSPX": True, "VWCE": False, "SXR8": False},
    )
    assert out.saved_holdings_changed is False
    assert out.before.shared_issuers == 1 and out.after.shared_issuers == 1
    # AAPL left the direct sleeve, so the redundancy with CSPX disappears; Nestle is now both
    assert out.before.redundant_weight == pytest.approx(0.05)
    assert out.after.redundant_weight == pytest.approx(0.10)
    # SXR8 is a fund with no stored holdings: its weight is unknown, not zero
    assert out.after.not_looked_through_weight == pytest.approx(0.30)
    assert any("Funds not looked through go from 20.0% to 30.0%" in c for c in out.coverage)
    assert any("SXR8" in c and "unknown" in c for c in out.coverage)
    assert out.after.fee_drag == pytest.approx(out.before.fee_drag)  # CSPX unchanged
    positions = {p.ticker: p for p in out.positions}
    assert positions["AAPL_US_EQ"].delta == pytest.approx(-0.20)
    assert positions["NESN_EQ"].kind == "added"
    assert positions["SXR8"].looked_through is False
    for view in out.views:
        assert view.reconciles_before and view.reconciles_after
        assert sum(r.before for r in view.rows) == pytest.approx(1.0)
        assert sum(r.after for r in view.rows) == pytest.approx(1.0)
