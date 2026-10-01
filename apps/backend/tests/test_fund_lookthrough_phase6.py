"""Phase 6 funds: factsheet + reconciliation (D6.1), coverage ledger (D6.5), retention, pins and
point-in-time look-through (D6.8), issuer-level aggregation with unknown fund weight (D6.4) and
the parser-drift audit (D6.9). Offline: recorded issuer fixtures, synthetic snapshots where a
reconciling fund is needed (the recorded holdings fixture is trimmed to 12 rows)."""

import dataclasses
import datetime as dt
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool
from api.main import create_app
from steward.config.jobs import (
    CSPX_ISIN,
    BenchmarkProxy,
    LookThroughFund,
    LookThroughSettings,
)
from steward.funds.aggregate import (
    UNKNOWN_FUND,
    aggregate_exposure,
    aggregate_portfolio,
    compute_fund_overlap,
)
from steward.funds.drift import Prior, classify_observation, record_observation
from steward.lookthrough.collect import collect_fund
from steward.lookthrough.coverage import (
    LedgerEntry,
    backtest_flags,
    derive_ledger,
    read_ledger,
    refresh_ledger,
    save_entry,
)
from steward.lookthrough.factsheet import (
    ManualFactsheet,
    extract_issuer_facts,
    latest_expense_ratios,
    reconcile_rows,
    store_manual_factsheet,
)
from steward.lookthrough.pit import snapshot_ids_as_of
from steward.lookthrough.read import NOT_LOOKED, PortfolioIn, PositionIn, RowIn, SnapshotIn
from steward.lookthrough.retention import (
    RetentionPolicy,
    SnapshotRef,
    pin_snapshot,
    prune_snapshots,
    snapshots_to_keep,
)
from steward.sources.etf_issuers import AssetClass, HoldingRow, parse_ishares_holdings
from steward.sources.fund_characteristics import (
    compare_definitions,
    issuer_definition,
    parse_ishares,
    parse_vanguard,
)
from tests.test_look_through import OWNER, TODAY, _seed_portfolio, issuer_handler, mock

FIX = Path(__file__).parent / "fixtures"
TSM_ADR = "US8740391003"
TSM_ORD = "TW0002330008"
APPLE = "US0378331005"
FUND = "IE00B5BMR087"  # CSPX
OTHER = "IE00BK5BQT80"  # VWCE


def row(
    name: str,
    isin: str | None,
    weight: str,
    asset_class: AssetClass = "equity",
    sector: str | None = "Information Technology",
    country: str | None = "US",
) -> HoldingRow:
    return HoldingRow(
        name=name,
        isin=isin,
        ticker=None,
        sedol=None,
        asset_class=asset_class,
        security_type=None,
        weight=Decimal(weight),
        sector_raw=sector,
        sector=sector,
        country_raw=country,
        country=country,
        currency="USD",
        currency_derived=False,
        exchange=None,
        market_value=None,
    )


# ---------------------------------------------------------------------------
# Factsheet and reconciliation (D6.1)
# ---------------------------------------------------------------------------


def test_published_rows_reconcile_with_cash_derivatives_and_unclassified() -> None:
    rows = [
        row("Apple", APPLE, "0.60"),
        row("Mystery", "US0000000001", "0.30", sector=None, country=None),
        row("USD CASH", None, "0.0995", "cash", None),
        row("FUTURE", None, "0.0005", "derivative", None),
    ]
    rec = reconcile_rows(rows)
    assert rec.total_weight == Decimal("1.0000")
    assert rec.equity_weight == Decimal("0.90") and rec.cash_weight == Decimal("0.0995")
    assert rec.derivative_weight == Decimal("0.0005")
    assert rec.unclassified_sector_weight == Decimal("0.30")
    assert rec.unclassified_country_weight == Decimal("0.30")
    assert rec.reconciles and rec.residual_weight == 0


def test_a_short_snapshot_does_not_reconcile_and_the_gap_stays_unknown() -> None:
    snap = parse_ishares_holdings(
        json.loads((FIX / "look_through" / "ishares_holdings.json").read_text()), "253743"
    )
    rec = reconcile_rows(snap.rows)
    assert not rec.reconciles
    assert rec.residual_weight == Decimal(1) - rec.total_weight > Decimal("0.6")


def test_factsheet_fields_the_payload_lacks_stay_null_not_zero() -> None:
    snap = parse_ishares_holdings(
        json.loads((FIX / "look_through" / "ishares_holdings.json").read_text()), "253743"
    )
    facts = extract_issuer_facts(snap)
    assert facts.expense_ratio is None and facts.total_assets is None
    assert set(facts.missing()) >= {"expense_ratio", "total_assets", "benchmark_name"}
    assert facts.fund_ticker == "ID-CSPX" and facts.share_class is None


def test_factsheet_reads_charges_when_the_payload_has_them() -> None:
    # Synthetic: the documented data-point envelope with a candidate expense-ratio point.
    snap = parse_ishares_holdings(
        json.loads((FIX / "look_through" / "ishares_holdings.json").read_text()), "253743"
    )
    assert snap.raw is not None
    body = snap.raw["holdings"]
    body["componentsByNameMap"]["keyFacts"] = {
        "containersByNameMap": {
            "all": {
                "dataPointsByNameMap": {
                    "totalExpenseRatio": {"value": "0.07"},
                    "distributionType": {"value": "Accumulating"},
                }
            }
        }
    }
    facts = extract_issuer_facts(snap)
    assert facts.expense_ratio == Decimal("0.0007")
    assert facts.distribution_policy == "accumulating"


# ---------------------------------------------------------------------------
# Aggregation by canonical issuer (D6.4)
# ---------------------------------------------------------------------------


def rowin(
    name: str, isin: str | None, weight: float, cls: str = "equity", country: str | None = "US"
) -> RowIn:
    return RowIn(
        name=name,
        isin=isin,
        ticker=None,
        asset_class=cls,
        weight=weight,
        sector="Information Technology" if cls == "equity" else None,
        country=country if cls == "equity" else None,
        currency="USD" if cls == "equity" else None,
        currency_derived=False,
    )


def snap(
    isin: str, rows: list[RowIn], as_of: dt.date = dt.date(2026, 9, 25), fetched: str = "2026-09-26"
) -> SnapshotIn:
    return SnapshotIn(
        isin=isin,
        source="ishares_json",
        as_of=as_of,
        fetched_at=dt.datetime.fromisoformat(fetched).replace(tzinfo=dt.UTC),
        fund_name=None,
        country_basis="country_of_risk",
        currency_basis="issuer",
        warnings=[],
        rows=rows,
    )


def position(ticker: str, isin: str, value: float, type_: str = "Common Stock") -> PositionIn:
    return PositionIn(
        ticker=ticker,
        name=ticker,
        isin=isin,
        security_type=type_,
        home_country="TW" if isin == TSM_ADR else "US",
        currency="USD",
        value_base=value,
    )


SETTINGS = LookThroughSettings(
    funds=[
        LookThroughFund(isin=CSPX_ISIN, label="CSPX", provider="ishares", portfolio_id="253743")
    ],
    benchmark_proxies=[],
)


def portfolio(*positions: PositionIn, cash: float = 100.0) -> PortfolioIn:
    return PortfolioIn(
        snapshot_date=dt.date(2026, 9, 28),
        base_currency="USD",
        account_currency="USD",
        cash_base=cash,
        positions=list(positions),
    )


def test_adr_and_ordinary_merge_at_issuer_level_but_stay_traceable() -> None:
    fund_rows = [
        rowin("TAIWAN SEMICONDUCTOR", TSM_ORD, 0.20, country="TW"),
        rowin("APPLE", APPLE, 0.50),
        rowin("USD CASH", None, 0.30, "cash"),
    ]
    report = aggregate_exposure(
        portfolio(position("TSM_US_EQ", TSM_ADR, 100.0), position("CSPX", CSPX_ISIN, 700.0, "ETF")),
        SETTINGS,
        {CSPX_ISIN: snap(CSPX_ISIN, fund_rows)},
        as_of=dt.date(2026, 9, 28),
    )
    tsm = next(i for i in report.issuers if "taiwan" in i.label.lower())
    assert tsm.merged_securities == 2
    assert {s.isin for s in tsm.securities} == {TSM_ADR, TSM_ORD}
    by_isin = {s.isin: s for s in tsm.securities}
    assert by_isin[TSM_ADR].via[0].kind == "direct"
    assert by_isin[TSM_ORD].via[0].kind == "fund" and by_isin[TSM_ORD].via[0].isin == CSPX_ISIN
    # 100 direct + 700 * 0.20 through the fund, out of 900 total.
    assert tsm.weight == pytest.approx((100 + 140) / 900)
    assert tsm.direct_weight == pytest.approx(100 / 900)
    assert tsm.overlap_weight == pytest.approx(100 / 900) and tsm in report.overlap


def test_unknown_fund_weight_is_its_own_bucket_and_never_redistributed() -> None:
    # The published rows cover 80% of the fund; 20% is unaccounted for.
    rows = [rowin("APPLE", APPLE, 0.50), rowin("USD CASH", None, 0.30, "cash")]
    report = aggregate_exposure(
        portfolio(position("CSPX", CSPX_ISIN, 800.0, "ETF"), cash=200.0),
        SETTINGS,
        {CSPX_ISIN: snap(CSPX_ISIN, rows)},
        as_of=dt.date(2026, 9, 28),
    )
    w = 0.8
    assert report.unknown is not None
    assert report.unknown.unknown_fund_weight == pytest.approx(w * 0.2)
    apple = next(i for i in report.issuers if i.label.lower().startswith("apple"))
    assert apple.weight == pytest.approx(w * 0.5)  # unchanged by the unknown part
    fund = report.funds[0]
    assert fund.reconciles is False and fund.residual_weight == pytest.approx(0.2)
    for view in report.exposures:  # every dimension adds up to the whole portfolio
        assert view.reconciles
        unknown = next(b for b in view.buckets if b.key == UNKNOWN_FUND)
        assert unknown.weight == pytest.approx(w * 0.2) and unknown.kind == "unknown_fund"
        classified = sum(b.weight for b in view.buckets if b.kind == "classified")
        assert classified == pytest.approx(w * 0.5)


def test_fully_reconciled_fund_has_no_unknown_weight() -> None:
    rows = [rowin("APPLE", APPLE, 0.7), rowin("USD CASH", None, 0.2995, "cash")]
    rows.append(rowin("FUT", None, 0.0005, "derivative"))
    report = aggregate_exposure(
        portfolio(position("CSPX", CSPX_ISIN, 500.0, "ETF"), cash=0.0),
        SETTINGS,
        {CSPX_ISIN: snap(CSPX_ISIN, rows)},
        as_of=dt.date(2026, 9, 28),
    )
    assert report.funds[0].reconciles is True
    assert report.unknown is not None and report.unknown.unknown_fund_weight == pytest.approx(0)
    fund_cash = next(b for b in report.exposures[0].buckets if b.kind == "fund_cash")
    assert fund_cash.weight == pytest.approx(0.3)


def test_stale_holdings_are_visibly_stale_and_missing_funds_are_unknown() -> None:
    old = snap(CSPX_ISIN, [rowin("APPLE", APPLE, 1.0)], as_of=dt.date(2026, 6, 1))
    report = aggregate_exposure(
        portfolio(
            position("CSPX", CSPX_ISIN, 500.0, "ETF"),
            position("XYZ", "IE0000000009", 300.0, "ETF"),
        ),
        SETTINGS,
        {CSPX_ISIN: old},
        as_of=dt.date(2026, 9, 28),
    )
    cspx = next(f for f in report.funds if f.isin == CSPX_ISIN)
    assert cspx.stale and cspx.status == "stale" and cspx.lag_days == 119
    assert report.stale_funds == ["CSPX"]
    assert any("Stale fund holdings" in n for n in report.notes)
    assert any("holdings are 119 days old" in w for w in cspx.warnings)
    missing = next(f for f in report.funds if f.isin == "IE0000000009")
    assert missing.status == "not_configured" and missing.unknown_weight == missing.weight
    assert report.unknown is not None
    assert report.unknown.not_looked_through_weight == pytest.approx(300 / 900)
    not_looked = next(b for b in report.exposures[0].buckets if b.key == NOT_LOOKED)
    assert not_looked.weight == pytest.approx(300 / 900)


def test_fee_drag_never_treats_an_unknown_expense_ratio_as_free() -> None:
    second = LookThroughFund(isin=OTHER, label="VWCE", provider="vanguard", portfolio_id="9679")
    settings = LookThroughSettings(funds=[*SETTINGS.funds, second], benchmark_proxies=[])
    rows = [rowin("APPLE", APPLE, 1.0)]
    report = aggregate_exposure(
        portfolio(
            position("CSPX", CSPX_ISIN, 600.0, "ETF"),
            position("VWCE", OTHER, 400.0, "ETF"),
            cash=0.0,
        ),
        settings,
        {CSPX_ISIN: snap(CSPX_ISIN, rows), OTHER: snap(OTHER, rows)},
        as_of=dt.date(2026, 9, 28),
        expense_ratios={CSPX_ISIN: Decimal("0.0007")},
    )
    assert report.fees is not None
    assert report.fees.annual_drag == pytest.approx(0.6 * 0.0007)
    assert report.fees.funds_without_ratio == ["VWCE"]
    assert report.fees.unpriced_weight == pytest.approx(0.4)
    assert next(f for f in report.funds if f.label == "VWCE").fee_drag is None
    assert report.fees.weighted_expense_ratio == pytest.approx(0.0007)


def test_fund_overlap_and_coverage_with_lag() -> None:
    second = LookThroughFund(isin=OTHER, label="VWCE", provider="vanguard", portfolio_id="9679")
    settings = LookThroughSettings(funds=[*SETTINGS.funds, second], benchmark_proxies=[])
    a = snap(CSPX_ISIN, [rowin("APPLE", APPLE, 0.6), rowin("X", "US0000000002", 0.4)])
    b = snap(
        OTHER,
        [rowin("APPLE", APPLE, 0.1), rowin("Y", "US0000000003", 0.7)],
        as_of=dt.date(2026, 8, 31),
    )
    out = compute_fund_overlap(settings, {CSPX_ISIN: a, OTHER: b}, as_of=dt.date(2026, 9, 28))
    pair = out.pairs[0]
    assert pair.overlap == pytest.approx(0.1) and pair.shared_issuers == 1
    assert not pair.same_date
    vw = next(f for f in out.funds if f.isin == OTHER)
    assert vw.lag_days == 28 and vw.unknown_weight == pytest.approx(0.2)
    assert vw.top10_share == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Retention (D6.8)
# ---------------------------------------------------------------------------


def refs(*dates: str) -> list[SnapshotRef]:
    return [
        SnapshotRef(i, dt.date.fromisoformat(d), dt.datetime.fromisoformat(d + "T12:00:00+00:00"))
        for i, d in enumerate(dates, start=1)
    ]


def test_retention_keeps_latest_month_ends_and_pinned() -> None:
    snaps = refs("2026-01-10", "2026-01-30", "2026-02-27", "2026-03-06", "2026-03-13", "2026-03-20")
    keep = snapshots_to_keep(snaps, {1}, RetentionPolicy(keep_latest=2, keep_month_ends=3))
    assert keep == {6: "latest", 5: "latest", 3: "month_end", 2: "month_end", 1: "pinned"}
    only_latest = snapshots_to_keep(snaps, set(), RetentionPolicy(keep_latest=2, keep_month_ends=0))
    assert set(only_latest) == {5, 6}


# ---------------------------------------------------------------------------
# Drift audit (D6.9)
# ---------------------------------------------------------------------------


def ishares_page() -> str:
    return (FIX / "valuation" / "ishares_cspx.html").read_text()


def test_issuer_rules_basis_and_dates_are_preserved() -> None:
    fig = parse_ishares(ishares_page(), "u")
    assert fig.rules["pe_cap"] == 60 and fig.rules["pb_cap"] == 25
    assert fig.rules["negative_earnings_excluded"] and fig.rules["negative_book_excluded"]
    assert fig.basis == "fund" and fig.as_of == dt.date(2026, 9, 25) and fig.fingerprint
    van = parse_vanguard(json.loads((FIX / "valuation" / "vanguard_9679.json").read_text()), "u")
    assert van.basis == "fund" and van.index_as_of is not None and van.index_pe is not None
    assert "caps_stated" not in van.rules and "pe_cap" not in van.rules  # not assumed


def prior_of(fig: Any) -> Prior:
    return Prior(
        fingerprint=fig.fingerprint,
        rules=fig.rules,
        as_of=fig.as_of,
        values={"pe": fig.pe, "pb": fig.pb, "index_pe": fig.index_pe, "index_pb": fig.index_pb},
    )


def test_drift_is_told_apart_from_real_change() -> None:
    fig = parse_ishares(ishares_page(), "u")
    assert classify_observation(None, fig).verdict == "first_observation"
    assert classify_observation(prior_of(fig), fig).verdict == "unchanged"

    later = dataclasses.replace(fig, as_of=fig.as_of + dt.timedelta(days=1))
    small = dataclasses.replace(later, pe=fig.pe * Decimal("1.02") if fig.pe else None)
    result = classify_observation(prior_of(fig), small)
    assert result.verdict == "normal_update" and not result.alert

    big = dataclasses.replace(later, pe=(fig.pe or Decimal(1)) * 2)
    result = classify_observation(prior_of(fig), big)
    assert result.verdict == "value_change" and result.alert

    restated = dataclasses.replace(fig, pb=(fig.pb or Decimal(1)) + 1)
    result = classify_observation(prior_of(fig), restated)
    assert result.verdict == "value_change" and result.detail["restated_for_same_as_of"]

    capped = dataclasses.replace(later, rules={**fig.rules, "pe_cap": 50.0})
    assert classify_observation(prior_of(fig), capped).verdict == "methodology_change"

    # The page drops a key the parser reads: the shape moved, whatever the numbers say.
    drifted_page = ishares_page().replace("&quot;formattedValue&quot;:&quot;30.37&quot;,", "")
    assert drifted_page != ishares_page()
    drifted = parse_ishares(drifted_page, "u")
    assert drifted.fingerprint != fig.fingerprint and drifted.pe == fig.pe
    result = classify_observation(prior_of(fig), drifted)
    assert result.verdict == "parser_drift" and not result.alert
    assert result.detail["needs_parser_review"]

    gone = dataclasses.replace(later, pe=None)
    assert classify_observation(prior_of(fig), gone).alert  # same shape, figure removed


def test_constituent_comparison_only_when_definitions_align() -> None:
    fig = parse_ishares(ishares_page(), "u")
    theirs = issuer_definition(fig, "pe")
    # iShares does not state its aggregation, so nothing is comparable yet.
    own = dataclasses.replace(theirs, aggregation="weighted_harmonic")
    verdict = compare_definitions(theirs, own)
    assert not verdict.comparable and "does not state its aggregation" in verdict.reasons[0]

    stated = dataclasses.replace(theirs, aggregation="weighted_average")
    aligned = dataclasses.replace(stated)
    assert compare_definitions(stated, aligned).comparable
    uncapped = dataclasses.replace(stated, cap=None, negatives_excluded=False)
    reasons = compare_definitions(stated, uncapped).reasons
    assert any("capping" in r for r in reasons) and any("negative" in r for r in reasons)
    old = dataclasses.replace(stated, as_of=stated.as_of - dt.timedelta(days=30))
    assert any("effective dates" in r for r in compare_definitions(stated, old).reasons)
    index_basis = dataclasses.replace(stated, basis="index")
    assert any("basis" in r for r in compare_definitions(stated, index_basis).reasons)


# ---------------------------------------------------------------------------
# Coverage ledger (D6.5)
# ---------------------------------------------------------------------------


def test_ledger_flags_proxy_unknown_total_return_and_unmapped_funds() -> None:
    settings = LookThroughSettings(
        funds=SETTINGS.funds,
        benchmark_proxies=[BenchmarkProxy(symbol="^spx", fund_isin=CSPX_ISIN, label="S&P 500")],
    )
    entries = derive_ledger(
        settings,
        ["^spx", "^ndq"],
        portfolio(position("ZZZ", "IE0000000009", 10.0, "ETF")),
        {CSPX_ISIN: dt.date(2026, 9, 25)},
        {CSPX_ISIN: 3},
    )
    by_key = {(e.subject_kind, e.subject_key): e for e in entries}
    assert by_key[("fund", CSPX_ISIN)].tier == "issuer_downloadable"
    assert by_key[("fund", CSPX_ISIN)].historical_constituents is True
    assert by_key[("fund", "IE0000000009")].tier == "unavailable"
    spx = by_key[("index", "^spx")]
    assert spx.tier == "proxy_fund" and "not the official index history" in spx.reason
    assert by_key[("index", "^ndq")].tier == "unavailable"
    flags = backtest_flags("index", "proxy_fund", None, None)
    assert len(flags) == 3
    assert backtest_flags("index", "official_holdings", True, True) == []


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


@pytest.fixture
async def api(backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    from api.fund_routes import router

    app = create_app()
    app.include_router(router)

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def insert_snapshot(pool: asyncpg.Pool, isin: str, as_of: str, fetched: str) -> int:
    value: int = await pool.fetchval(
        "insert into public.etf_snapshot (fund_isin, provider, portfolio_id, source, as_of,"
        " raw, fetched_at) values ($1, 'ishares', '253743', 'ishares_json', $2, '{}'::jsonb, $3)"
        " returning id",
        isin,
        dt.date.fromisoformat(as_of),
        dt.datetime.fromisoformat(fetched).replace(tzinfo=dt.UTC),
    )
    return value


async def test_collect_stores_factsheet_and_retention_keeps_dated_history(
    backend_pool: asyncpg.Pool,
) -> None:
    fund = LookThroughFund(isin=CSPX_ISIN, label="CSPX", provider="ishares", portfolio_id="253743")
    calls: list[str] = []
    async with mock(issuer_handler(calls)) as http:
        from steward.lookthrough.collect import LookThroughDeps

        detail = await collect_fund(
            backend_pool,
            LookThroughDeps(http=http, today=dt.date(2026, 9, 26), retry_delay=0),
            fund,
            SETTINGS,
            None,
        )
    assert detail["outcome"] == "stored" and detail["factsheet_id"]
    sheet = await backend_pool.fetchrow("select * from public.fund_factsheet")
    assert sheet["fund_isin"] == CSPX_ISIN and sheet["as_of"] == dt.date(2026, 9, 25)
    assert sheet["expense_ratio"] is None and "expense_ratio" in sheet["missing_fields"]
    assert sheet["reconciles"] is False and sheet["residual_weight"] > Decimal("0.6")
    assert sheet["lookthrough_basis"] == "issuer_holdings"
    assert sheet["holdings_count"] == 12 and "do NOT reconcile" in sheet["coverage_note"]

    # Older dated snapshots survive beyond keep_latest through month ends and pins.
    a = await insert_snapshot(backend_pool, CSPX_ISIN, "2026-05-29", "2026-05-30T08:00:00")
    b = await insert_snapshot(backend_pool, CSPX_ISIN, "2026-06-12", "2026-06-13T08:00:00")
    c = await insert_snapshot(backend_pool, CSPX_ISIN, "2026-06-26", "2026-06-27T08:00:00")
    assert await pin_snapshot(backend_pool, b, "report", "weekly-7", "cited in report")
    assert not await pin_snapshot(backend_pool, 999999, "report", "x")
    result = await prune_snapshots(
        backend_pool, CSPX_ISIN, RetentionPolicy(keep_latest=1, keep_month_ends=0)
    )
    remaining = {r["id"] for r in await backend_pool.fetch("select id from public.etf_snapshot")}
    assert a not in remaining and c not in remaining and b in remaining
    assert result["deleted"] == 2 and result["pinned"] == 1
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await backend_pool.execute("delete from public.etf_snapshot where id = $1", b)

    # The default policy also keeps each recent month's last snapshot.
    d = await insert_snapshot(backend_pool, CSPX_ISIN, "2026-07-10", "2026-07-11T08:00:00")
    e = await insert_snapshot(backend_pool, CSPX_ISIN, "2026-07-24", "2026-07-25T08:00:00")
    await prune_snapshots(backend_pool, CSPX_ISIN, RetentionPolicy(keep_latest=1))
    remaining = {r["id"] for r in await backend_pool.fetch("select id from public.etf_snapshot")}
    assert e in remaining and d not in remaining


async def test_point_in_time_uses_only_what_was_known(backend_pool: asyncpg.Pool) -> None:
    early = await insert_snapshot(backend_pool, CSPX_ISIN, "2026-09-01", "2026-09-02T06:00:00")
    late = await insert_snapshot(backend_pool, CSPX_ISIN, "2026-09-15", "2026-09-16T06:00:00")
    await insert_snapshot(backend_pool, CSPX_ISIN, "2026-09-08", "2026-09-30T06:00:00")

    async def at(day: str) -> int | None:
        return (
            await snapshot_ids_as_of(backend_pool, [CSPX_ISIN], dt.date.fromisoformat(day))
        ).get(CSPX_ISIN)

    assert await at("2026-09-01") is None  # fetched on the 2nd
    assert await at("2026-09-10") == early  # the 8th snapshot was only backfilled on the 30th
    assert await at("2026-09-15") == early  # the 15th snapshot became known on the 16th
    assert await at("2026-09-16") == late
    assert await at("2026-09-30") == late  # the backfilled 8th is older than the 15th
    assert await at("2026-10-05") == late  # newest holdings date wins


async def test_ledger_refresh_never_overwrites_a_manual_row(backend_pool: asyncpg.Pool) -> None:
    settings = LookThroughSettings(
        funds=SETTINGS.funds,
        benchmark_proxies=[BenchmarkProxy(symbol="^spx", fund_isin=CSPX_ISIN, label="S&P 500")],
    )
    await refresh_ledger(backend_pool, settings, ["^spx"], None)
    ledger = await read_ledger(backend_pool)
    spx = next(e for e in ledger.entries if e.subject_key == "^spx")
    assert spx.tier == "proxy_fund" and not spx.usable_for_backtest
    assert ledger.flagged_benchmarks == ["^spx"]
    await save_entry(
        backend_pool,
        LedgerEntry(
            "index",
            "^spx",
            "S&P 500",
            "official_holdings",
            "S&P DJI constituent files, licensed total-return series verified",
            origin="manual",
            total_return_available=True,
            historical_constituents=True,
        ),
    )
    await refresh_ledger(backend_pool, settings, ["^spx"], None)
    ledger = await read_ledger(backend_pool)
    spx = next(e for e in ledger.entries if e.subject_key == "^spx")
    assert spx.origin == "manual" and spx.tier == "official_holdings" and spx.usable_for_backtest
    assert ledger.flagged_benchmarks == []


async def test_record_observation_audits_value_change_and_drift(
    backend_pool: asyncpg.Pool,
) -> None:
    fig = parse_ishares(ishares_page(), "u")
    assert (await record_observation(backend_pool, "CSPX", fig)).verdict == "first_observation"
    drifted = parse_ishares(
        ishares_page().replace("&quot;formattedValue&quot;:&quot;30.37&quot;,", ""), "u"
    )
    assert (await record_observation(backend_pool, "CSPX", drifted)).verdict == "parser_drift"
    rows = await backend_pool.fetch(
        "select verdict, alert from public.fund_characteristic_audit order by id"
    )
    assert [(r["verdict"], r["alert"]) for r in rows] == [
        ("first_observation", False),
        ("parser_drift", False),
    ]


async def test_expense_ratio_lookup_skips_nulls_and_respects_as_of(
    backend_pool: asyncpg.Pool,
) -> None:
    await store_manual_factsheet(
        backend_pool,
        ManualFactsheet(CSPX_ISIN, dt.date(2026, 3, 1), expense_ratio=Decimal("0.0007")),
    )
    await store_manual_factsheet(
        backend_pool,
        ManualFactsheet(CSPX_ISIN, dt.date(2026, 9, 1), expense_ratio=Decimal("0.0005")),
    )
    await store_manual_factsheet(backend_pool, ManualFactsheet(CSPX_ISIN, dt.date(2026, 9, 20)))
    assert (await latest_expense_ratios(backend_pool, [CSPX_ISIN]))[CSPX_ISIN] == Decimal("0.0005")
    early = await latest_expense_ratios(backend_pool, [CSPX_ISIN], dt.date(2026, 6, 1))
    assert early[CSPX_ISIN] == Decimal("0.0007")
    assert await latest_expense_ratios(backend_pool, [CSPX_ISIN], dt.date(2026, 1, 1)) == {}


async def test_api_aggregate_shows_unknown_weight_and_dates(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await _seed_portfolio(backend_pool)
    fund = LookThroughFund(isin=CSPX_ISIN, label="CSPX", provider="ishares", portfolio_id="253743")
    from steward.lookthrough.collect import LookThroughDeps

    calls: list[str] = []
    async with mock(issuer_handler(calls)) as http:
        await collect_fund(
            backend_pool,
            LookThroughDeps(http=http, today=TODAY, retry_delay=0),
            fund,
            SETTINGS,
            None,
        )
    await store_manual_factsheet(
        backend_pool,
        ManualFactsheet(CSPX_ISIN, dt.date(2026, 9, 25), expense_ratio=Decimal("0.0007")),
    )
    body = (await api.get("/funds/aggregate")).json()
    assert body["portfolio_snapshot_date"] == TODAY.isoformat()
    cspx = next(f for f in body["funds"] if f["isin"] == CSPX_ISIN)
    assert cspx["holdings_as_of"] == "2026-09-25" and cspx["reconciles"] is False
    assert cspx["unknown_weight"] > 0 and cspx["expense_ratio"] == pytest.approx(0.0007)
    assert body["unknown"]["unknown_fund_weight"] == pytest.approx(cspx["unknown_weight"])
    assert body["fees"]["annual_drag"] > 0
    nvda = next(i for i in body["issuers"] if i["key"] == "isin:US67066G1040")
    assert {c["kind"] for c in nvda["channels"]} == {"direct", "fund"}  # held both ways
    direct_report = await aggregate_portfolio(backend_pool)
    assert direct_report.total_value == pytest.approx(400)

    assert (await api.get("/funds/overlap")).status_code == 200
    assert (await api.get("/funds/coverage-ledger")).status_code == 200
    assert (await api.get("/funds/characteristics-audit")).json() == []
    sheets = (await api.get(f"/funds/{CSPX_ISIN}/factsheet")).json()
    assert {s["source"] for s in sheets} == {"ishares_json", "manual"}
    past = (await api.get("/funds/aggregate", params={"as_of": "2026-01-01"})).json()
    assert past["available"] is False and past["portfolio_snapshot_date"] is None
    assert (await api.get("/funds/aggregate", params={"as_of": "2999-01-01"})).status_code == 422
    pinned = await api.post(
        "/funds/snapshots/pin",
        json={"ref_kind": "report", "ref_id": "r-1", "as_of": "2026-12-31"},
    )
    assert pinned.status_code == 200 and len(pinned.json()["pinned"]) == 1
    assert (
        await api.post("/funds/snapshots/pin", json={"ref_kind": "manual", "ref_id": "x"})
    ).status_code == 422
