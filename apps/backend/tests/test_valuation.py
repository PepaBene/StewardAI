"""Valuation: fundamentals from XBRL (companyfacts, a filing's instance, ESEF), the ETF
providers' fund figures, per-holding ratios and the portfolio against its benchmark.

Fixtures in tests/fixtures/valuation are trimmed, verbatim copies of live responses fetched
on 2026-09-28 (SEC companyfacts, submissions and TSMC's FY2025 20-F instance; Novo
Nordisk's Q2 2026 ESEF report; the iShares CSPX page's characteristics component; the
Vanguard GraphQL response for VWCE). No test touches the network."""

import datetime as dt
import itertools
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from pydantic import ValidationError

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool
from api.main import create_app
from steward.config.holding_profiles import (
    HoldingProfile,
    HoldingProfilesConfig,
    ManualFundamentals,
)
from steward.config.jobs import FundValuationSource, JobsConfig
from steward.config.store import save_config
from steward.fx.convert import FxRate, FxTable
from steward.jobs.runner import JobContext, run_job
from steward.sources.edgar import EdgarClient
from steward.sources.esef import EsefFiling, parse_filings, parse_xbrl_json
from steward.sources.fund_characteristics import FundDataError, parse_ishares, parse_vanguard
from steward.sources.xbrl import XbrlError, parse_instance, plain_facts
from steward.valuation.collect import (
    ValuationDeps,
    pick_esef,
    preferred_currencies,
    run_valuation,
)
from steward.valuation.fundamentals import (
    ALL_CONCEPTS,
    EpsStep,
    Fact,
    companyfacts_facts,
    compute,
    eps_steps,
    ttm,
)
from steward.valuation.read import (
    PE_CAP,
    FundamentalsSource,
    PerShareFigure,
    ValuationContribution,
    ValuationPrice,
    _Chosen,
    aggregate,
    merged_steps,
    ratio,
)

FIXTURES = Path(__file__).parent / "fixtures" / "valuation"
TODAY = dt.date(2026, 9, 28)
OWNER = "00000000-0000-0000-0000-00000000000c"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def fact(
    start: str | None,
    end: str,
    value: str,
    concept: str = "us-gaap:EarningsPerShareDiluted",
    unit: str = "USD/shares",
    filed: str = "2026-08-01",
    ref: str = "a",
) -> Fact:
    return Fact(
        concept=concept,
        start=dt.date.fromisoformat(start) if start else None,
        end=dt.date.fromisoformat(end),
        value=Decimal(value),
        unit=unit,
        source="sec",
        ref=ref,
        filed=dt.date.fromisoformat(filed),
        form="10-Q",
    )


# ---------------------------------------------------------------------------
# Trailing twelve months
# ---------------------------------------------------------------------------


def test_ttm_is_the_full_year_when_the_latest_period_is_one() -> None:
    result = ttm([fact("2025-07-01", "2026-06-30", "17.95"), fact("2026-01-01", "2026-03-31", "4")])
    assert result is not None
    assert result.value == Decimal("17.95")
    assert result.method.startswith("full year")
    assert not result.annual_only  # interim figures exist: a genuine TTM


def test_ttm_rolls_the_year_to_date_forward() -> None:
    result = ttm(
        [
            fact("2025-01-01", "2025-12-31", "23.03"),
            fact("2026-01-01", "2026-06-30", "15.66"),
            fact("2025-01-01", "2025-06-30", "12.49"),
            fact("2026-04-01", "2026-06-30", "4.75"),
        ]
    )
    assert result is not None
    assert result.value == Decimal("26.20")
    assert "FY to 2025-12-31 + 6M to 2026-06-30 - 6M to 2025-06-30" == result.method
    assert result.end == dt.date(2026, 6, 30)


def test_ttm_sums_four_quarters_when_no_year_to_date_lines_up() -> None:
    quarters = [
        fact("2025-07-01", "2025-09-30", "1"),
        fact("2025-10-01", "2025-12-31", "2"),
        fact("2026-01-01", "2026-03-31", "3"),
        fact("2026-04-01", "2026-06-30", "4"),
    ]
    result = ttm(quarters)
    assert result is not None
    assert result.value == Decimal(10)
    assert result.method == "sum of the last four quarters"


def test_ttm_falls_back_to_the_latest_year_flagged_annual() -> None:
    result = ttm(
        [fact("2025-01-01", "2025-12-31", "2.09"), fact("2024-01-01", "2024-12-31", "1.36")]
    )
    assert result is not None
    assert result.value == Decimal("2.09")
    assert result.annual_only


def test_restated_values_win_per_period() -> None:
    old = fact("2025-01-01", "2025-12-31", "10", filed="2026-02-01", ref="old")
    new = fact("2025-01-01", "2025-12-31", "1", filed="2026-05-01", ref="new")
    result = ttm([old, new])
    assert result is not None and result.value == Decimal(1)


def test_nvidia_companyfacts_give_a_rolled_forward_ttm_and_steps() -> None:
    facts = companyfacts_facts(fixture("companyfacts_0001045810.json"))
    figures = compute("sec", facts, ["USD"], TODAY)
    assert figures is not None
    assert figures.basis == "US GAAP"
    eps = figures.eps_ttm
    assert eps is not None
    assert eps.value == pytest.approx(7.91)
    assert eps.period_end == dt.date(2026, 7, 26)
    assert eps.method == "FY to 2026-01-25 + 6M to 2026-07-26 - 6M to 2025-07-27"
    assert [i.ref for i in eps.inputs] == [
        "0001045810-26-000021",
        "0001045810-26-000075",
        "0001045810-26-000075",
    ]
    # Book value per share: equity at the balance-sheet date over the nearest share count
    # (the cover page's, 26 days later; the balance-sheet count is from January).
    bvps = figures.book_value_per_share
    assert bvps is not None and bvps.currency == "USD"
    assert "shares outstanding at 2026-08-21" in bvps.method
    dps = figures.dividend_per_share
    assert dps is not None and dps.value == pytest.approx(0.28)
    steps = figures.eps_steps
    assert steps[-1].eps == pytest.approx(7.91)
    assert steps[-1].effective == dt.date(2026, 8, 26)  # the 10-Q's filing date
    assert all(a.effective <= b.effective for a, b in itertools.pairwise(steps))


def test_eps_steps_take_effect_on_the_first_filing() -> None:
    facts = [
        fact("2024-01-01", "2024-12-31", "1.36", filed="2025-04-17"),
        fact("2025-01-01", "2025-12-31", "2.09", filed="2026-04-16"),
    ]
    steps = eps_steps(facts, TODAY)
    assert [(s.effective, s.eps) for s in steps] == [
        (dt.date(2025, 4, 17), 1.36),
        (dt.date(2026, 4, 16), 2.09),
    ]


# ---------------------------------------------------------------------------
# TSMC: companyfacts lags, the instance has the FY2025 figures (USD convenience)
# ---------------------------------------------------------------------------


def test_tsmc_companyfacts_stop_at_fy2024() -> None:
    facts = companyfacts_facts(fixture("companyfacts_0001046179.json"))
    figures = compute("sec", facts, ["USD"], TODAY)
    assert figures is not None and figures.eps_ttm is not None
    assert figures.eps_ttm.period_end == dt.date(2024, 12, 31)
    assert "0001628280-26-025362" not in {f.ref for f in facts if "Earnings" in f.concept}


def test_instance_parser_reads_non_dimensional_facts() -> None:
    facts = plain_facts(
        parse_instance((FIXTURES / "tsm-20251231_htm.xml").read_bytes(), ALL_CONCEPTS)
    )
    eps = [f for f in facts if f.concept == "ifrs-full:DilutedEarningsLossPerShare"]
    assert ("USD/shares", Decimal("2.09")) in {(f.unit, f.value) for f in eps if f.end.year == 2025}
    assert ("TWD/shares", Decimal("65.47")) in {
        (f.unit, f.value) for f in eps if f.end.year == 2025
    }
    shares = [f for f in facts if f.concept == "dei:EntityCommonStockSharesOutstanding"]
    assert shares and shares[0].start is None and shares[0].value == Decimal(25932524521)
    # The dimensional context (c-2) is never read as a total.
    assert all(f.fact.context.id != "c-2" for f in facts)


def test_instance_with_a_dtd_is_refused() -> None:
    with pytest.raises(XbrlError):
        parse_instance(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><xbrl/>')


def test_currency_choice_prefers_a_convertible_reporting_currency() -> None:
    # No TWD reference rate: the USD convenience figures (the ADR's price currency) win.
    assert preferred_currencies("TWD", "USD", {"USD", "EUR", "CZK"}) == ["USD"]
    assert preferred_currencies("EUR", "EUR", {"CZK"}) == ["EUR"]
    assert preferred_currencies("DKK", "USD", {"USD", "DKK", "CZK"}) == ["DKK", "USD"]
    assert preferred_currencies(None, "GBX", {"GBP"}) == ["GBP"]


# ---------------------------------------------------------------------------
# ESEF (filings.xbrl.org)
# ---------------------------------------------------------------------------


def test_xbrl_json_periods_and_units() -> None:
    facts = parse_xbrl_json(fixture("novo-2026-06-30.json"), ALL_CONCEPTS)
    h1 = [f for f in facts if f.start == dt.date(2026, 1, 1) and f.end == dt.date(2026, 6, 30)]
    assert [(f.value, f.unit) for f in h1] == [(Decimal("15.66"), "DKK/shares")]
    equity = [f for f in facts if f.concept == "ifrs-full:Equity" and f.end == dt.date(2026, 6, 30)]
    assert equity and equity[0].start is None
    # Facts on the components-of-equity axis are breakdowns, not totals.
    assert all(f.raw["dimensions"].keys() <= {"concept", "entity", "period", "unit"} for f in facts)


def test_xbrl_json_maps_document_prefixes() -> None:
    doc = {
        "documentInfo": {
            "namespaces": {"ifrs": "http://xbrl.ifrs.org/taxonomy/2020-03-16/ifrs-full"}
        },
        "facts": {
            "f1": {
                "value": "52.4",
                "dimensions": {
                    "concept": "ifrs:DilutedEarningsLossPerShare",
                    "entity": "scheme:x",
                    "period": "2025-01-01T00:00:00/2026-01-01T00:00:00",
                    "unit": "iso4217:CZK/xbrli:shares",
                },
            }
        },
    }
    (only,) = parse_xbrl_json(doc, ALL_CONCEPTS)
    assert only.concept == "ifrs-full:DilutedEarningsLossPerShare"
    assert (only.start, only.end) == (dt.date(2025, 1, 1), dt.date(2025, 12, 31))


def _esef(period: str, fid: str) -> EsefFiling:
    return EsefFiling(
        fid, dt.date.fromisoformat(period), None, "DK", f"https://x/{fid}.json", None, {}
    )


def test_pick_esef_adds_the_annual_report_to_an_interim_one() -> None:
    filings = [_esef("2026-06-30", "q2"), _esef("2026-03-31", "q1"), _esef("2025-12-31", "fy")]
    assert [f.id for f in pick_esef(filings, 12, TODAY, 18)] == ["q2", "fy"]
    # CEZ's and KB's collections stop at FY2020: too old to use.
    assert pick_esef([_esef("2020-12-31", "old")], 12, TODAY, 18) == []


def test_parse_filings_newest_first() -> None:
    data = {
        "data": [
            {"id": "1", "attributes": {"period_end": "2025-12-31", "json_url": "/a.json"}},
            {
                "id": "2",
                "attributes": {
                    "period_end": "2026-06-30",
                    "json_url": "/b.json",
                    "report_url": "/b.xhtml",
                },
            },
        ]
    }
    parsed = parse_filings(data)
    assert [f.id for f in parsed] == ["2", "1"]
    assert parsed[0].json_url == "https://filings.xbrl.org/b.json"


def test_novo_esef_rolls_h1_into_a_ttm_with_sec_shares() -> None:
    esef = [
        Fact(x.concept, x.start, x.end, x.value, x.unit, "esef", "25919", dt.date(2026, 9, 2))
        for x in parse_xbrl_json(fixture("novo-2026-06-30.json"), ALL_CONCEPTS)
    ]
    annual = fact(
        "2025-01-01", "2025-12-31", "23.03", "ifrs-full:DilutedEarningsLossPerShare", "DKK/shares"
    )
    shares = fact(None, "2025-12-31", "4444000000", "ifrs-full:NumberOfSharesOutstanding", "shares")
    figures = compute("esef", [*esef, annual, shares], ["DKK"], TODAY)
    assert figures is not None and figures.eps_ttm is not None
    assert figures.basis == "IFRS"
    assert figures.eps_ttm.value == pytest.approx(26.20)
    assert figures.book_value_per_share is not None
    assert figures.book_value_per_share.value == pytest.approx(221277000000 / 4444000000)


def test_us_gaap_and_ifrs_are_kept_apart() -> None:
    gaap = fact("2025-01-01", "2025-12-31", "24.71", unit="EUR/shares")
    ifrs = fact(
        "2025-01-01", "2025-12-31", "26.26", "ifrs-full:DilutedEarningsLossPerShare", "EUR/shares"
    )
    figures = compute("sec", [gaap, ifrs], ["EUR"], TODAY)
    assert figures is not None and figures.eps_ttm is not None
    assert (figures.basis, figures.eps_ttm.value) == ("US GAAP", 24.71)
    assert [(a.basis, a.eps_ttm.value if a.eps_ttm else None) for a in figures.alternatives] == [
        ("IFRS", 26.26)
    ]


# ---------------------------------------------------------------------------
# Sources: fund figures, filing index
# ---------------------------------------------------------------------------


def test_ishares_characteristics() -> None:
    figures = parse_ishares((FIXTURES / "ishares_cspx.html").read_text(), "https://ishares/x")
    assert figures.as_of == dt.date(2026, 9, 25)
    assert figures.pe == Decimal("30.36548")
    assert figures.pb == Decimal("5.71533")
    assert "P/E ratios over 60 are set to 60" in figures.methodology
    assert "over 25 are set to 25" in figures.methodology
    assert set(figures.raw["dataPoints"]) >= {"priceEarnings", "priceBook"}


def test_ishares_page_without_the_component_fails_loudly() -> None:
    with pytest.raises(FundDataError):
        parse_ishares("<html>redesigned</html>", "https://ishares/x")


def test_vanguard_characteristics() -> None:
    figures = parse_vanguard(fixture("vanguard_9679.json"), "https://vanguard/gpx/graphql")
    assert figures.as_of == dt.date(2026, 8, 31)
    assert figures.pe == Decimal("21.0149600000")
    assert figures.index_pe == Decimal("20.9986900000")
    assert figures.dividend_yield_kind == "forecast"
    assert figures.dividend_yield == Decimal("0.0152721000")


def test_vanguard_errors_fail_loudly() -> None:
    with pytest.raises(FundDataError):
        parse_vanguard({"errors": [{"message": "Cannot query field"}]}, "u")


# ---------------------------------------------------------------------------
# Ratios and the portfolio aggregate
# ---------------------------------------------------------------------------


def _price(close: float, currency: str = "USD") -> ValuationPrice:
    return ValuationPrice(symbol="x.us", close=close, currency=currency, date=TODAY, stale=False)


def _figure(value: float, currency: str = "USD") -> PerShareFigure:
    return PerShareFigure(
        value=value,
        currency=currency,
        period_start=None,
        period_end=TODAY,
        method="m",
        annual_only=False,
        stale=False,
    )


def test_pe_uses_the_depositary_ratio() -> None:
    result = ratio(_price(242.1), _figure(2.09), 5, None, "pe", "")
    assert result.value == pytest.approx(242.1 / 10.45)


def test_negative_earnings_are_not_meaningful() -> None:
    result = ratio(_price(10), _figure(-1), 1, None, "pe", "")
    assert result.value is None and result.reason == "n/m: negative earnings"
    book = ratio(_price(10), _figure(-1), 1, None, "pb", "")
    assert book.reason == "n/m: negative book value"


def test_price_is_converted_into_the_figures_currency() -> None:
    rates = [
        FxRate("cnb", TODAY, "EUR", Decimal("24.5"), Decimal(1), Decimal("24.5")),
        FxRate("cnb", TODAY, "USD", Decimal("21"), Decimal(1), Decimal("21")),
    ]
    fx = FxTable("cnb", rates)
    result = ratio(_price(100, "USD"), _figure(2, "EUR"), 1, fx, "pe", "")
    assert result.value == pytest.approx(100 * 21 / 24.5 / 2)
    missing = ratio(_price(100, "USD"), _figure(2, "TWD"), 1, fx, "yield", "")
    assert missing.value is None and missing.reason == "no CNB reference rate for TWD"


def _row(
    weight: float,
    pe: float | None,
    pb: float | None = None,
    dy: float | None = None,
    note: str | None = None,
) -> ValuationContribution:
    return ValuationContribution(
        ticker="T",
        name=None,
        kind="stock",
        weight=weight,
        pe=pe,
        pe_raw=pe,
        pe_note=note,
        pb=pb,
        pb_raw=pb,
        pb_note=None,
        dividend_yield=dy,
        source_label=None,
        stale=False,
    )


def test_portfolio_pe_is_a_weighted_harmonic_mean_with_coverage() -> None:
    result = aggregate(
        [
            _row(0.5, 20, 4, 0.01),
            _row(0.25, 40, None, None),
            _row(0.15, None, None, None, "n/m: negative earnings"),
            _row(0.10, None, None, None, "no EPS"),
        ]
    )
    assert result.pe == pytest.approx(0.75 / (0.5 / 20 + 0.25 / 40))
    assert result.coverage.pe == pytest.approx(0.9)  # the n/m holding counts as covered
    assert result.pb == pytest.approx(4)
    assert result.dividend_yield == pytest.approx(0.01)
    assert result.coverage.dividend_yield == pytest.approx(0.5)


def test_manual_fundamentals_need_a_figure() -> None:
    with pytest.raises(ValidationError):
        ManualFundamentals(currency="CZK", as_of=dt.date(2025, 12, 31))


def test_default_funds_stand_in_for_the_benchmarks() -> None:
    funds = JobsConfig().valuation.funds
    assert [(f.key, f.provider, f.benchmark_symbols) for f in funds] == [
        ("CSPX", "ishares", ["^spx"]),
        ("VWCE", "vanguard", []),
    ]
    with pytest.raises(ValidationError):
        FundValuationSource(key="X", label="x", provider="vanguard", url="https://v/gpx/graphql")


# ---------------------------------------------------------------------------
# The job and the endpoints (database)
# ---------------------------------------------------------------------------

pytestmark_db = pytest.mark.db


def _transport(calls: list[str]) -> httpx.MockTransport:
    instance = (FIXTURES / "tsm-20251231_htm.xml").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if url == "https://www.sec.gov/files/company_tickers.json":
            return httpx.Response(200, json={"0": {"cik_str": 1046179, "ticker": "TSM"}})
        if "submissions/CIK0001046179" in url:
            return httpx.Response(200, json=fixture("submissions_0001046179.json"))
        if "companyfacts/CIK0001046179" in url:
            return httpx.Response(200, json=fixture("companyfacts_0001046179.json"))
        if url.endswith("000162828026025362/index.json"):
            names = [{"name": "tsm-20251231.htm"}, {"name": "tsm-20251231_htm.xml"}]
            return httpx.Response(200, json={"directory": {"item": names}})
        if url.endswith("tsm-20251231_htm.xml"):
            return httpx.Response(200, content=instance)
        if "filings.xbrl.org" in url:
            return httpx.Response(404)
        if "ishares.com" in url:
            return httpx.Response(200, text=(FIXTURES / "ishares_cspx.html").read_text())
        if "vanguard" in url:
            return httpx.Response(200, json=fixture("vanguard_9679.json"))
        return httpx.Response(500)

    return httpx.MockTransport(handler)


async def _seed(pool: asyncpg.Pool) -> None:
    job_id = await pool.fetchval(
        "insert into public.job (job_type, trigger, status) values"
        " ('portfolio_sync', 'schedule', 'succeeded') returning id"
    )
    snap = dt.date(2026, 9, 25)
    await pool.execute(
        "insert into public.account_snapshot (snapshot_date, job_id, captured_at,"
        " t212_account_id, account_currency, total_value, cash_available, cash_in_pies,"
        " cash_reserved, invested_value, invested_cost, unrealized_ppl, realized_ppl,"
        " base_currency, fx_source, fx_rate, cash_base, invested_base, total_base,"
        " weight_basis) values ($1, $2, now(), 1, 'CZK', 0, 0, 0, 0, 0, 0, 0, 0, 'CZK', 'cnb',"
        " 1, 0, 1000, 1000, 'total')",
        snap,
        job_id,
    )
    for ticker, currency, symbol, security, value in (
        ("TSM_US_EQ", "USD", "tsm.us", "Common Stock", 400),
        ("CSPXl_EQ", "USD", "cspx.uk", "ETP", 400),
        ("CEZ_EQ", "CZK", "cez.cz", "Common Stock", 200),
    ):
        await pool.execute(
            "insert into public.instrument (t212_ticker, currency, price_symbol, security_type)"
            " values ($1, $2, $3, $4)",
            ticker,
            currency,
            symbol,
            security,
        )
        await pool.execute(
            "insert into public.position_snapshot (snapshot_date, ticker, job_id, captured_at,"
            " quantity, avg_price, current_price, instrument_currency, value, account_currency,"
            " value_account, cost_account, ppl, base_currency, fx_source, fx_rate, value_base)"
            " values ($1, $2, $3, now(), 1, 1, 1, $4, 1, 'CZK', $5, $5, 0, 'CZK', 'cnb', 1, $5)",
            snap,
            ticker,
            job_id,
            currency,
            Decimal(value),
        )
    await pool.executemany(
        "insert into public.price_bar (symbol, date, close, source) values ($1, $2, $3, 'stooq')",
        [
            ("tsm.us", dt.date(2026, 9, 25), Decimal("242.10")),
            ("tsm.us", dt.date(2026, 5, 1), Decimal("200")),
            ("cez.cz", dt.date(2026, 9, 25), Decimal("1162")),
        ],
    )
    await pool.execute(
        "insert into public.fx_rate (source, rate_date, currency, home_currency, rate,"
        " published_amount, published_rate) values ('cnb', '2026-09-25', 'USD', 'CZK', 21, 1, 21)"
    )
    async with pool.acquire() as conn, conn.transaction():
        await save_config(
            conn,
            "holding_profiles",
            HoldingProfilesConfig(
                profiles=[
                    HoldingProfile(
                        ticker="TSM_US_EQ",
                        sec_cik="1046179",
                        reporting_currency="TWD",
                    ),
                    HoldingProfile(
                        ticker="CEZ_EQ",
                        manual_fundamentals=ManualFundamentals(
                            eps_ttm=52.4,
                            book_value_per_share=449.9,
                            dividend_per_share=47,
                            currency="CZK",
                            as_of=dt.date(2025, 12, 31),
                            source_note="Annual Financial Report 2025",
                        ),
                    ),
                ]
            ),
            created_by=None,
        )


@pytest.fixture
async def api(backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def _refresh(
    pool: asyncpg.Pool, calls: list[str], params: dict[str, Any] | None = None
) -> str:
    async with httpx.AsyncClient(transport=_transport(calls)) as http:
        deps = ValuationDeps(
            http=http, edgar=EdgarClient(http, "test test@example.com"), today=TODAY
        )

        async def job(ctx: JobContext) -> None:
            await run_valuation(ctx, deps)

        return await run_job(pool, "valuation_refresh", job, trigger="manual", params=params)


@pytestmark_db
async def test_refresh_falls_back_to_the_instance_and_serves_ratios(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    await _seed(backend_pool)
    calls: list[str] = []
    assert await _refresh(backend_pool, calls) == "succeeded"
    assert any(c.endswith("tsm-20251231_htm.xml") for c in calls)

    row = await backend_pool.fetchrow(
        "select document_ref, period_end, currency, raw from public.fundamentals"
        " where ticker = 'TSM_US_EQ' and source = 'sec'"
    )
    assert row["document_ref"] == "0001628280-26-025362"
    assert row["period_end"] == dt.date(2025, 12, 31)
    assert row["currency"] == "USD"
    raw = json.loads(row["raw"])
    assert raw["instance"]["url"].endswith("tsm-20251231_htm.xml")
    assert "EarningsPerShareDiluted" not in json.dumps(raw["companyfacts"])  # IFRS filer

    tsm = (await api.get("/holdings/TSM_US_EQ/valuation")).json()
    # No share basis in the profile: the documented default (1 ADS = 5 common shares).
    assert tsm["ads_ratio"] == 5
    assert tsm["ads_ratio_source"].startswith("default: TSMC 20-F")
    assert tsm["pe"]["value"] == pytest.approx(242.10 / (2.09 * 5))
    assert tsm["eps_ttm"]["annual_only"] is True
    assert tsm["eps_ttm"]["stale"] is True  # period ended over 200 days ago
    assert tsm["fundamentals"]["source_label"] == "SEC filing's XBRL instance"
    # Dividends are tagged in TWD only, and there is no TWD reference rate.
    assert tsm["dividend_yield"] == {"value": None, "reason": "no CNB reference rate for TWD"}
    assert tsm["pe_history"] and tsm["pe_history"][0]["date"] == "2026-05-01"
    assert tsm["pe_history"][0]["eps"] == pytest.approx(2.09)  # effective from 2026-04-16

    cez = (await api.get("/holdings/CEZ_EQ/valuation")).json()
    assert cez["fundamentals"]["source_label"] == "entered manually"
    assert cez["pe"]["value"] == pytest.approx(1162 / 52.4)
    assert cez["dividend_yield"]["value"] == pytest.approx(47 / 1162)

    cspx = (await api.get("/holdings/CSPXl_EQ/valuation")).json()
    assert cspx["kind"] == "fund"
    assert cspx["pe"]["value"] == pytest.approx(30.36548)
    assert cspx["fund"]["provider_label"] == "iShares"

    portfolio = (await api.get("/portfolio/valuation")).json()
    assert portfolio["benchmark"]["label"] == "S&P 500"
    assert portfolio["benchmark"]["pe"] == pytest.approx(30.36548)
    weights = {"TSM_US_EQ": 0.4, "CSPXl_EQ": 0.4, "CEZ_EQ": 0.2}
    pes = {"TSM_US_EQ": 242.10 / 10.45, "CSPXl_EQ": 30.36548, "CEZ_EQ": 1162 / 52.4}
    expected = 1 / sum(weights[t] / pes[t] for t in weights)
    assert portfolio["portfolio"]["pe"] == pytest.approx(expected)
    assert portfolio["portfolio"]["coverage"]["pe"] == pytest.approx(1)
    labels = [c["label"] for c in portfolio["comparisons"]]
    assert labels == ["S&P 500", "FTSE All-World", "FTSE All-World (index)"]
    assert "harmonic mean" in portfolio["method"]
    assert PE_CAP == 60


@pytestmark_db
async def test_second_run_skips_unchanged_filings(backend_pool: asyncpg.Pool) -> None:
    await _seed(backend_pool)
    first: list[str] = []
    await _refresh(backend_pool, first)
    second: list[str] = []
    assert await _refresh(backend_pool, second) == "succeeded"
    assert not any("companyfacts" in c for c in second)  # same latest 20-F: not re-read
    forced: list[str] = []
    await _refresh(backend_pool, forced, {"force": True})
    assert any("companyfacts" in c for c in forced)


@pytestmark_db
async def test_a_failing_source_is_recorded_not_fatal(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    await _seed(backend_pool)

    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(broken)) as http:
        deps = ValuationDeps(http=http, edgar=EdgarClient(http, "t t@example.com"), today=TODAY)

        async def job(ctx: JobContext) -> None:
            await run_valuation(ctx, deps)

        assert (
            await run_job(backend_pool, "valuation_refresh", job, trigger="manual") == "succeeded"
        )
    tsm = (await api.get("/holdings/TSM_US_EQ/valuation")).json()
    assert tsm["pe"]["value"] is None
    assert "sec" in tsm["errors"]
    assert tsm["pe"]["reason"].startswith("the last refresh failed")
    portfolio = (await api.get("/portfolio/valuation")).json()
    assert portfolio["benchmark"] is None  # no fund figures stored


@pytestmark_db
async def test_valuation_before_anything_is_stored(api: httpx.AsyncClient) -> None:
    body = (await api.get("/portfolio/valuation")).json()
    assert body["portfolio"]["pe"] is None
    assert body["holdings"] == []
    one = (await api.get("/holdings/MSFT_US_EQ/valuation")).json()
    assert one["pe"] == {"value": None, "reason": "no stored price"}


def _chosen(source: str, steps: list[tuple[str, float]]) -> _Chosen:
    return _Chosen(
        fundamentals=FundamentalsSource(
            source=source,
            source_label=source,
            basis="IFRS",
            eps_kind="diluted",
            document_ref=None,
            document_url=None,
            fetched_at=None,
            as_of=None,
            notes=[],
            last_error=None,
        ),
        eps=_figure(1, "DKK"),
        bvps=None,
        dps=None,
        steps=[
            EpsStep(
                effective=dt.date.fromisoformat(d),
                period_end=dt.date.fromisoformat(d),
                eps=e,
                method="m",
            )
            for d, e in steps
        ],
    )


def test_pe_history_starts_with_the_other_sources_older_steps() -> None:
    esef = _chosen("esef", [("2026-03-04", 23.03), ("2026-09-02", 26.2)])
    sec = _chosen("sec", [("2025-02-05", 22.63), ("2026-02-04", 23.03)])
    merged = merged_steps(esef, [esef, sec])
    assert [(s.effective.isoformat(), s.eps) for s in merged] == [
        ("2025-02-05", 22.63),
        ("2026-02-04", 23.03),
        ("2026-03-04", 23.03),
        ("2026-09-02", 26.2),
    ]


@pytestmark_db
async def test_ads_ratio_from_the_share_basis_and_the_20f(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    await _seed(backend_pool)
    await backend_pool.execute(
        "insert into public.shares_outstanding (ticker, cik, shares, as_of, method, accession,"
        " form, ads_ratio_reported, ads_ratio_text, raw) values ('TSM_US_EQ', '0001046179',"
        " 25932524521, '2025-12-31', 'dei_instance', '0001628280-26-025362', '20-F', 5,"
        " 'each ADS represents five (5) common shares', '{}'::jsonb)"
    )
    stated = (await api.get("/holdings/TSM_US_EQ/valuation")).json()
    assert stated["ads_ratio"] == 5
    assert stated["ads_ratio_source"] == "stated in the 20-F 0001628280-26-025362"
    async with backend_pool.acquire() as conn, conn.transaction():
        await save_config(
            conn,
            "holding_profiles",
            HoldingProfilesConfig(
                profiles=[
                    HoldingProfile(
                        ticker="TSM_US_EQ", sec_cik="1046179", share_basis={"ads_ratio": 2}
                    )
                ]
            ),
            created_by=None,
        )
    profile = (await api.get("/holdings/TSM_US_EQ/valuation")).json()
    assert (profile["ads_ratio"], profile["ads_ratio_source"]) == (
        2,
        "holding profile (share basis)",
    )


@pytestmark_db
async def test_price_comes_from_the_yahoo_fallback_when_stooq_has_nothing(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    """Once Stooq stops delivering, the price layer's Yahoo series is newer and is the one
    every reader uses; the P/E must follow it rather than read Stooq's stale close."""
    await _seed(backend_pool)  # Stooq cez.cz close of 25 Sep
    await backend_pool.execute(
        "insert into public.price_bar (symbol, date, close, source, currency)"
        " values ('CEZ.PR', '2026-09-26', 1100, 'yahoo', 'CZK')"
    )
    cez = (await api.get("/holdings/CEZ_EQ/valuation")).json()
    assert cez["price"]["symbol"] == "CEZ.PR"
    assert cez["price"]["source"] == "Yahoo daily close (unofficial)"
    assert cez["price"]["currency"] == "CZK"
    assert cez["pe"]["value"] == pytest.approx(1100 / 52.4)
