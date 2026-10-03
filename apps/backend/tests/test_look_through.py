"""ETF look-through: the iShares and Vanguard adapters against recorded (trimmed) payloads,
the job's skip/gap/fallback logic, storage, and the look-through aggregation and endpoint.
No live network: every request goes through httpx.MockTransport."""

import datetime as dt
import json
from collections.abc import AsyncIterator, Callable
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
from steward.config.jobs import (
    CSPX_ISIN,
    VWCE_ISIN,
    BenchmarkProxy,
    JobsConfig,
    LookThroughFund,
    LookThroughSettings,
)
from steward.jobs.registry import JOB_HANDLERS
from steward.jobs.runner import JobContext, run_job
from steward.lookthrough.classify import country_code, normalize_sector, region_of
from steward.lookthrough.collect import (
    STATUS_KEY,
    LookThroughDeps,
    expected_as_of,
    run_look_through,
)
from steward.lookthrough.read import (
    CASH,
    FUND_CASH,
    NOT_LOOKED,
    BreakdownIn,
    PortfolioIn,
    PositionIn,
    RowIn,
    SnapshotIn,
    compute_look_through,
)
from steward.sources.etf_issuers import (
    IssuerHttpError,
    IssuerSchemaError,
    fetch_ishares,
    fetch_vanguard_fund,
    fetch_vanguard_holdings,
    parse_ishares_exposure,
    parse_ishares_holdings,
    parse_ishares_spreadsheet,
    parse_vanguard,
)

FIXTURES = Path(__file__).parent / "fixtures" / "look_through"
OWNER = "00000000-0000-0000-0000-00000000000b"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def xml() -> str:
    return (FIXTURES / "ishares_fund.xml").read_text()


def mock(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# Classification tables
# ---------------------------------------------------------------------------


def test_country_and_sector_normalisation() -> None:
    assert country_code("United States") == "US"
    assert country_code("Korea (South)") == "KR"
    assert country_code("tw") == "TW"
    assert country_code("European Union") is None  # a cash line's pseudo-country
    assert region_of("CZ") == "Europe" and region_of("TW") == "Asia Pacific"
    assert normalize_sector("Communication") == "Communication Services"
    assert normalize_sector("Semiconductors") == "Information Technology"
    assert normalize_sector("Cash and/or Derivatives") is None
    assert normalize_sector("Internet") is None  # ambiguous: never guessed


# ---------------------------------------------------------------------------
# iShares
# ---------------------------------------------------------------------------


def test_ishares_holdings_json_is_read_column_by_column() -> None:
    snap = parse_ishares_holdings(load("ishares_holdings.json"), "253743")
    assert snap.as_of == dt.date(2026, 9, 25)
    assert snap.source == "ishares_json" and snap.country_basis == "country_of_risk"
    assert snap.fund_name == "iShares Core S&P 500 UCITS ETF"
    nvda = snap.rows[0]
    assert (nvda.name, nvda.isin, nvda.ticker) == ("NVIDIA", "US67066G1040", "NVDA")
    assert nvda.weight == Decimal("0.0814417")
    assert (nvda.sector, nvda.country, nvda.currency) == ("Information Technology", "US", "USD")
    assert not nvda.currency_derived
    classes = [r.asset_class for r in snap.rows]
    assert classes.count("equity") == 6 and classes.count("cash") == 5
    assert classes.count("derivative") == 1
    eur_cash = next(r for r in snap.rows if r.name == "EUR CASH")
    assert eur_cash.country is None and eur_cash.currency == "EUR" and eur_cash.sector is None
    assert snap.raw == {"holdings": load("ishares_holdings.json")}


def test_ishares_schema_changes_are_errors_not_guesses() -> None:
    body = load("ishares_holdings.json")
    del body["componentsByNameMap"]["holdings"]["containersByNameMap"]["all"]
    with pytest.raises(IssuerSchemaError, match=r"containersByNameMap\.all"):
        parse_ishares_holdings(body, "253743")

    body = load("ishares_holdings.json")
    points = body["componentsByNameMap"]["holdings"]["containersByNameMap"]["all"]
    points["dataPointsByNameMap"]["isin"]["value"].pop()
    with pytest.raises(IssuerSchemaError, match="'isin' has 11 rows"):
        parse_ishares_holdings(body, "253743")

    body = load("ishares_holdings.json")
    points = body["componentsByNameMap"]["holdings"]["containersByNameMap"]["all"]
    del points["dataPointsByNameMap"]["holdingPercent"]
    with pytest.raises(IssuerSchemaError, match="holdingPercent"):
        parse_ishares_holdings(body, "253743")


def test_ishares_sector_table() -> None:
    rows = parse_ishares_exposure(load("ishares_exposure.json"))
    by_label = {r.label: r.fund_weight for r in rows}
    assert by_label["Information Technology"] == Decimal("0.393")
    assert len(rows) == 12 and rows[0].as_of == dt.date(2026, 9, 25)
    with pytest.raises(IssuerSchemaError, match="sector container"):
        parse_ishares_exposure({"componentsByNameMap": {"exposureBreakdowns": {
            "containersByNameMap": {"country": {}}}}})  # fmt: skip


async def test_ishares_fetch_keeps_the_holdings_when_the_sector_table_fails() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        component = request.url.params["component"]
        seen.append(component)
        assert request.url.params["portfolioId"] == "253743"
        if component == "holdings":
            return httpx.Response(200, json=load("ishares_holdings.json"))
        return httpx.Response(400, json={"message": "Your input parameter Component has no"})

    async with mock(handler) as http:
        snap = await fetch_ishares(http, "253743", retry_delay=0)
    assert seen == ["holdings", "exposureBreakdowns", "keyFundFacts"]
    assert snap.breakdowns == [] and "sector table unavailable" in snap.warnings[0]
    assert "key fund facts unavailable" in snap.warnings[1]
    assert snap.raw is not None and set(snap.raw) == {"holdings"}


async def test_ishares_http_400_and_retries() -> None:
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        if request.url.params["component"] == "holdings":
            return httpx.Response(200, json=load("ishares_holdings.json"))
        return httpx.Response(200, json=load("ishares_exposure.json"))

    async with mock(flaky) as http:
        snap = await fetch_ishares(http, "253743", retry_delay=0)
    assert calls["n"] == 4 and len(snap.breakdowns) == 12  # 503 retry + the three components
    assert snap.raw is not None
    assert set(snap.raw) == {"holdings", "exposureBreakdowns", "keyFundFacts"}

    async with mock(lambda r: httpx.Response(400, text="BAD_REQUEST")) as http:
        with pytest.raises(IssuerHttpError, match="HTTP 400"):
            await fetch_ishares(http, "253743", retry_delay=0)


def test_ishares_spreadsheet_fallback_has_no_country_or_isin() -> None:
    snap = parse_ishares_spreadsheet(xml(), "253743")
    assert snap.as_of == dt.date(2026, 9, 25) and snap.source == "ishares_spreadsheet"
    assert snap.raw_text == xml() and snap.raw is None
    nvda = snap.rows[0]
    assert (nvda.name, nvda.ticker, nvda.weight) == ("NVIDIA", "NVDA", Decimal("0.0814"))
    assert nvda.market_value == Decimal("12981700220.07")
    assert nvda.country is None and nvda.isin is None and nvda.currency == "USD"
    assert snap.rows[-1].asset_class == "cash"
    with pytest.raises(IssuerSchemaError, match="DTD"):
        parse_ishares_spreadsheet('<!DOCTYPE x [<!ENTITY a "b">]><x/>', "253743")
    with pytest.raises(IssuerSchemaError, match="Holdings"):
        parse_ishares_spreadsheet(xml().replace('Name="Holdings"', 'Name="Other"'), "253743")


# ---------------------------------------------------------------------------
# Vanguard
# ---------------------------------------------------------------------------


def vanguard_handler(requests: list[dict[str, Any]]) -> Callable[[httpx.Request], httpx.Response]:
    pages = load("vanguard_holdings_pages.json")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("X-Consumer-ID") != "ie0":
            return httpx.Response(400, json={"errors": [{"message": "x-consumer-id must be"}]})
        body = json.loads(request.content)
        requests.append(body)
        if "funds(" in body["query"]:
            return httpx.Response(200, json=load("vanguard_fund.json"))
        return httpx.Response(200, json=pages[0 if body["variables"]["lastItemKey"] is None else 1])

    return handler


async def test_vanguard_pages_the_holdings_and_reads_the_fund_tables() -> None:
    requests: list[dict[str, Any]] = []
    async with mock(vanguard_handler(requests)) as http:
        fund = await fetch_vanguard_fund(http, "9679", retry_delay=0)
        pages = await fetch_vanguard_holdings(http, "9679", retry_delay=0)
    assert len(pages) == 2
    assert requests[1]["variables"] == {"portIds": ["9679"], "lastItemKey": None}
    assert requests[2]["variables"]["lastItemKey"].startswith('{"skey"')
    assert "limit: 1500" in requests[1]["query"]

    snap = parse_vanguard(fund, pages, "9679")
    assert snap.as_of == dt.date(2026, 8, 31) and snap.source == "vanguard_graphql"
    assert snap.country_basis == "listing_country" and snap.currency_basis == "derived"
    assert snap.fund_name == "Vanguard FTSE All-World UCITS ETF (USD) Accumulating"
    tsmc = next(r for r in snap.rows if r.isin == "TW0002330008")
    assert (tsmc.country, tsmc.currency, tsmc.currency_derived) == ("TW", "TWD", True)
    assert tsmc.sector == "Information Technology" and tsmc.sedol
    cash = next(r for r in snap.rows if r.asset_class == "cash")
    assert (cash.currency, cash.currency_derived) == ("USD", False)
    forward = next(r for r in snap.rows if r.asset_class == "derivative")
    assert forward.currency is None
    us = next(b for b in snap.breakdowns if b.scheme == "ftse_country" and b.code == "US")
    assert us.fund_weight is not None and Decimal("0.6") < us.fund_weight < Decimal("0.63")
    assert us.benchmark_weight is not None and us.benchmark_weight > Decimal("0.5")
    assert {b.scheme for b in snap.breakdowns} == {
        "ftse_country",
        "msci_country",
        "ftse_region",
        "icb_industry",
    }
    assert snap.raw is not None and snap.raw["holdings_pages"] == pages


async def test_vanguard_errors_are_recorded_not_guessed() -> None:
    # No X-Consumer-ID header: the endpoint answers 400.
    async with mock(vanguard_handler([])) as http:
        with pytest.raises(IssuerHttpError, match="HTTP 400"):
            await fetch_vanguard_fund(http, "9679", consumer_id="", retry_delay=0)

    async with mock(
        lambda r: httpx.Response(200, json={"errors": [{"message": "Cannot query field"}]})
    ) as http:
        with pytest.raises(IssuerSchemaError, match="Cannot query field"):
            await fetch_vanguard_fund(http, "9679", retry_delay=0)

    looping = {"data": {"borHoldings": [{"holdings": {"items": [], "lastItemKey": "same"}}]}}
    async with mock(lambda r: httpx.Response(200, json=looping)) as http:
        with pytest.raises(IssuerSchemaError, match="cursor repeated"):
            await fetch_vanguard_holdings(http, "9679", retry_delay=0)

    with pytest.raises(IssuerSchemaError, match="no fund"):
        parse_vanguard({"data": {"funds": []}}, load("vanguard_holdings_pages.json"), "9679")


# ---------------------------------------------------------------------------
# Config and scheduling
# ---------------------------------------------------------------------------


def test_look_through_config_defaults_and_validation() -> None:
    config = JobsConfig()
    assert config.schedules()["etf_look_through"].cron == "40 6 * * 6"
    assert "etf_look_through" in JOB_HANDLERS
    funds = {f.label: (f.isin, f.provider, f.portfolio_id) for f in config.look_through.funds}
    assert funds == {
        "CSPX": (CSPX_ISIN, "ishares", "253743"),
        "VWCE": (VWCE_ISIN, "vanguard", "9679"),
    }
    assert config.look_through.benchmark_proxies[0].symbol == "^spx"
    with pytest.raises(ValidationError, match="not in funds"):
        LookThroughSettings(
            benchmark_proxies=[BenchmarkProxy(symbol="^spx", fund_isin="IE00B4L5Y983", label="x")]
        )
    with pytest.raises(ValidationError, match="duplicate"):
        LookThroughSettings(
            funds=[
                LookThroughFund(isin=CSPX_ISIN, label="A", provider="ishares", portfolio_id="1"),
                LookThroughFund(isin=CSPX_ISIN, label="B", provider="ishares", portfolio_id="2"),
            ],
            benchmark_proxies=[],
        )


def test_expected_as_of() -> None:
    monday = dt.date(2026, 9, 28)
    assert expected_as_of("ishares", monday) == dt.date(2026, 9, 25)  # the Friday before
    assert expected_as_of("ishares", dt.date(2026, 9, 30)) == dt.date(2026, 9, 29)
    assert expected_as_of("vanguard", monday) == dt.date(2026, 8, 31)


# ---------------------------------------------------------------------------
# Aggregation (pure)
# ---------------------------------------------------------------------------

TODAY = dt.date(2026, 9, 28)


def _snapshot(isin: str, source: str, rows: list[RowIn], **kw: Any) -> SnapshotIn:
    return SnapshotIn(
        isin=isin,
        source=source,
        as_of=kw.pop("as_of", dt.date(2026, 9, 25)),
        fetched_at=kw.pop("fetched_at", dt.datetime(2026, 9, 27, tzinfo=dt.UTC)),
        fund_name=None,
        country_basis=kw.pop("country_basis", "country_of_risk"),
        currency_basis=kw.pop("currency_basis", "issuer"),
        warnings=[],
        rows=rows,
        **kw,
    )


def _row(name: str, isin: str | None, weight: float, **kw: Any) -> RowIn:
    return RowIn(
        name=name,
        isin=isin,
        ticker=kw.get("ticker"),
        asset_class=kw.get("asset_class", "equity"),
        weight=weight,
        sector=kw.get("sector", "Information Technology"),
        country=kw.get("country", "US"),
        currency=kw.get("currency", "USD"),
        currency_derived=kw.get("currency_derived", False),
    )


NVDA = "US67066G1040"
AAPL = "US0378331005"
CSPX = _snapshot(
    CSPX_ISIN,
    "ishares_json",
    [
        _row("NVIDIA", NVDA, 0.5, ticker="NVDA"),
        _row("APPLE", AAPL, 0.49, ticker="AAPL"),
        _row("USD CASH", None, 0.01, asset_class="cash", sector=None),
    ],
)


def _portfolio(*positions: PositionIn, cash: float = 0.0) -> PortfolioIn:
    return PortfolioIn(
        snapshot_date=TODAY,
        base_currency="CZK",
        account_currency="CZK",
        cash_base=cash,
        positions=list(positions),
    )


def _pos(ticker: str, isin: str | None, value: float, **kw: Any) -> PositionIn:
    return PositionIn(
        ticker=ticker,
        name=kw.get("name", ticker),
        isin=isin,
        security_type=kw.get("security_type", "Common Stock"),
        home_country=kw.get("home_country", "US"),
        currency=kw.get("currency", "USD"),
        value_base=value,
        strategy_sector=kw.get("strategy_sector"),
    )


def test_look_through_expands_funds_and_merges_direct_holdings() -> None:
    portfolio = _portfolio(
        _pos("NVDA_US_EQ", NVDA, 100.0, name="NVIDIA Corp"),
        _pos("CSPXl_EQ", CSPX_ISIN, 100.0, security_type="ETP", home_country="IE"),
        _pos("CEZ_EQ", "CZ0005112300", 40.0, home_country="CZ", currency="CZK",
             strategy_sector="Utilities"),
        cash=60.0,
    )  # fmt: skip
    report = compute_look_through(
        portfolio,
        LookThroughSettings(),
        {CSPX_ISIN: CSPX},
        {},
        benchmark_symbols=["^spx"],
        today=TODAY,
    )
    total = 300.0
    assert report.available and report.coverage is not None
    assert report.coverage.fund_share_looked_through == 1.0
    assert report.coverage.portfolio_share_looked_through == pytest.approx(240 / total)

    top = report.top_holdings[0]
    assert top.name == "NVIDIA Corp" and top.isin == NVDA
    assert top.weight == pytest.approx((100 + 50) / total)
    assert [(v.kind, v.label) for v in top.via] == [("direct", "Direct"), ("fund", "CSPX")]
    assert top.benchmark_weight == pytest.approx(0.5 / 0.99)

    country = {
        b.key: b for b in next(e for e in report.exposures if e.dimension == "country").buckets
    }
    assert country["US"].weight == pytest.approx((100 + 99) / total)
    assert country["US"].direct_weight == pytest.approx(100 / total)
    assert country["CZ"].weight == pytest.approx(40 / total)
    assert country["CZ"].benchmark_weight == 0.0  # the S&P 500 proxy has none
    assert country[FUND_CASH].weight == pytest.approx(1 / total)
    assert country[CASH].weight == pytest.approx(60 / total) and country[CASH].kind == "cash"
    sector = {
        b.key: b.weight
        for b in next(e for e in report.exposures if e.dimension == "sector").buckets
    }
    # CEZ: no fund holds it, so its strategy bucket ("Utilities") is used.
    assert sector["Utilities"] == pytest.approx(40 / total)

    assert report.benchmark is not None and report.benchmark.label == "S&P 500"
    assert report.benchmark.foreign_share == 1.0
    bench = report.benchmark.concentration
    assert bench is not None and bench.holdings == 2
    assert bench.hhi == pytest.approx((0.5 / 0.99) ** 2 + (0.49 / 0.99) ** 2)

    c = report.concentration
    assert c is not None and c.holdings == 3
    weights = [150 / 239, 49 / 239, 40 / 239]
    assert c.hhi == pytest.approx(sum(w * w for w in weights))
    assert c.effective_n == pytest.approx(1 / c.hhi) and c.top10_share == pytest.approx(1.0)

    fx = report.fx
    assert fx is not None and fx.base_currency == "CZK"
    # Known currencies: USD 199 (100 direct + 99 via CSPX), CZK 40 + 60 cash; CSPX's cash
    # line is fund cash, not a known currency.
    assert fx.foreign_share == pytest.approx(199 / 299)
    assert fx.benchmark_foreign_share == 1.0

    cspx = next(f for f in report.funds if f.label == "CSPX")
    assert cspx.status == "ok" and cspx.as_of == dt.date(2026, 9, 25) and cspx.tier == 1
    assert cspx.top_holdings[0].portfolio_weight == pytest.approx(0.5 * 100 / total)
    assert cspx.benchmark_proxy_for == ["^spx"]


def test_funds_without_a_snapshot_are_their_own_bucket() -> None:
    portfolio = _portfolio(
        _pos("VWCEd_EQ", VWCE_ISIN, 100.0, security_type="ETP"),
        _pos("EQQQ_EQ", "IE0032077012", 50.0, security_type="ETP"),
        _pos("MSFT_US_EQ", "US5949181045", 50.0),
    )
    failed = {
        "checked_at": "2026-09-27T06:40:00+00:00",
        "ok": False,
        "outcome": "gap",
        "error": "Vanguard GraphQL returned HTTP 400",
    }
    report = compute_look_through(
        portfolio,
        LookThroughSettings(),
        {},
        {VWCE_ISIN: failed},
        benchmark_symbols=["^spx"],
        today=TODAY,
    )
    assert not report.available
    region = {b.key: b.weight for b in report.exposures[1].buckets}
    assert region[NOT_LOOKED] == pytest.approx(150 / 200)
    status = {f.label: f for f in report.funds}
    assert status["VWCE"].status == "missing"
    assert status["VWCE"].last_attempt is not None
    assert status["VWCE"].last_attempt.error == "Vanguard GraphQL returned HTTP 400"
    assert status["EQQQ_EQ"].status == "not_configured"
    assert status["CSPX"].status == "missing" and not status["CSPX"].held
    assert report.benchmark is None and "no snapshot of its proxy fund" in report.notes[0]
    assert "not looked through" in report.notes[1]


def test_vanguard_country_comes_from_its_fund_table_and_ftse_benchmark() -> None:
    rows = [
        _row("NVIDIA Corp", NVDA, 0.6, currency_derived=True),
        _row("Tencent", "KYG875721634", 0.39, country="HK", currency="HKD", currency_derived=True),
        _row("US Dollar", None, 0.01, asset_class="cash", sector=None),
    ]
    breakdowns = [
        BreakdownIn("country", "ftse_country", "US", "United States", 0.62, 0.61, None),
        BreakdownIn("country", "ftse_country", "CN", "China", 0.38, 0.39, None),
        BreakdownIn("sector", "icb_industry", None, "Technology", 1.0, 1.0, None),
    ]
    vwce = _snapshot(
        VWCE_ISIN, "vanguard_graphql", rows, as_of=dt.date(2026, 5, 31),
        country_basis="listing_country", currency_basis="derived", breakdowns=breakdowns,
    )  # fmt: skip
    report = compute_look_through(
        _portfolio(_pos("VWCEd_EQ", VWCE_ISIN, 100.0, security_type="ETP")),
        LookThroughSettings(),
        {VWCE_ISIN: vwce},
        {},
        benchmark_symbols=["^spx"],
        today=TODAY,
    )
    country = {b.key: b.weight for b in report.exposures[0].buckets}
    # The official FTSE table (China 38%), not the listing country (Hong Kong).
    assert country["CN"] == pytest.approx(0.99 * 0.38) and "HK" not in country
    assert country[FUND_CASH] == pytest.approx(0.01)
    fund = next(f for f in report.funds if f.label == "VWCE")
    assert fund.status == "stale"  # as of 31 May, older than 60 days
    assert "FTSE" in (fund.country_basis or "")
    ftse = next(b for b in report.benchmarks if b.key == "ftse_all_world")
    assert ftse.sector_scheme == "icb_industry" and ftse.currency_derived
    assert {b.key: b.weight for b in ftse.exposures["currency"]} == pytest.approx(
        {"USD": 0.61, "CNY": 0.39}
    )
    assert ftse.concentration is None


# ---------------------------------------------------------------------------
# The job and the endpoint (database)
# ---------------------------------------------------------------------------


async def _seed_portfolio(pool: asyncpg.Pool) -> None:
    job_id = await pool.fetchval(
        "insert into public.job (job_type, trigger, status) values"
        " ('portfolio_sync', 'schedule', 'succeeded') returning id"
    )
    await pool.execute(
        "insert into public.account_snapshot (snapshot_date, job_id, captured_at,"
        " t212_account_id, account_currency, total_value, cash_available, cash_in_pies,"
        " cash_reserved, invested_value, invested_cost, unrealized_ppl, realized_ppl,"
        " base_currency, fx_source, fx_rate, cash_base, invested_base, total_base,"
        " weight_basis) values ($1, $2, now(), 1, 'CZK', 0, 0, 0, 0, 0, 0, 0, 0, 'CZK', 'cnb',"
        " 1, 100, 300, 400, 'total')",
        TODAY,
        job_id,
    )
    for ticker, isin, value, security_type in (
        ("CSPXl_EQ", CSPX_ISIN, 200, "ETP"),
        ("NVDA_US_EQ", NVDA, 100, "Common Stock"),
    ):
        await pool.execute(
            "insert into public.instrument (t212_ticker, isin, name, currency, security_type,"
            " home_country) values ($1, $2, $1, 'USD', $3, 'US')",
            ticker,
            isin,
            security_type,
        )
        await pool.execute(
            "insert into public.position_snapshot (snapshot_date, ticker, job_id, captured_at,"
            " quantity, avg_price, current_price, instrument_currency, value, account_currency,"
            " value_account, cost_account, ppl, base_currency, fx_source, fx_rate, value_base)"
            " values ($1, $2, $3, now(), 1, $4, $4, 'USD', $4, 'CZK', $4, $4, 0, 'CZK', 'cnb',"
            " 1, $4)",
            TODAY,
            ticker,
            job_id,
            Decimal(value),
        )


def issuer_handler(
    calls: list[str], *, ishares_json: bool = True, spreadsheet: bool = True
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.blackrock.com":
            component = request.url.params["component"]
            calls.append(component)
            if component == "fundDownloadV2":
                return httpx.Response(200, text=xml()) if spreadsheet else httpx.Response(500)
            if not ishares_json:
                return httpx.Response(400, json={"error": "BAD_REQUEST_INVALID_PARAM_VALUES"})
            name = "ishares_holdings.json" if component == "holdings" else "ishares_exposure.json"
            return httpx.Response(200, json=load(name))
        calls.append(request.url.host)
        return httpx.Response(404)

    return handler


async def _run(
    pool: asyncpg.Pool, handler: Callable[[httpx.Request], httpx.Response], today: dt.date
) -> None:
    async with mock(handler) as http:

        async def job(ctx: JobContext) -> None:
            await run_look_through(
                ctx, LookThroughDeps(http=http, today=today, retry_delay=0), JobsConfig()
            )

        assert await run_job(pool, "etf_look_through", job, trigger="manual") == "succeeded"


async def _steps(pool: asyncpg.Pool) -> dict[str, dict[str, Any]]:
    rows = await pool.fetch(
        "select name, detail from public.job_step where job_id = (select max(id) from"
        " public.job where job_type = 'etf_look_through')"
    )
    return {r["name"]: json.loads(r["detail"]) for r in rows}


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


async def test_job_stores_snapshots_skips_when_current_and_serves_the_endpoint(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await _seed_portfolio(backend_pool)
    calls: list[str] = []
    await _run(backend_pool, issuer_handler(calls), dt.date(2026, 9, 26))
    # VWCE is configured but not held (and proxies no benchmark): never requested.
    assert calls == ["holdings", "exposureBreakdowns", "keyFundFacts"]
    steps = await _steps(backend_pool)
    assert steps["fund:CSPX"]["outcome"] == "stored" and "fund:VWCE" not in steps
    snap = await backend_pool.fetchrow("select * from public.etf_snapshot")
    assert snap["as_of"] == dt.date(2026, 9, 25) and snap["holdings_count"] == 6
    assert json.loads(snap["raw"])["holdings"] == load("ishares_holdings.json")
    assert await backend_pool.fetchval("select count(*) from public.etf_holding") == 12
    assert await backend_pool.fetchval("select count(*) from public.etf_breakdown") == 12

    # The next run (a Saturday): the stored as-of date is the latest the issuer can have.
    calls.clear()
    await _run(backend_pool, issuer_handler(calls), dt.date(2026, 9, 26))
    assert calls == [] and (await _steps(backend_pool))["fund:CSPX"]["outcome"] == "skipped"

    body = (await api.get("/portfolio/look-through")).json()
    assert body["available"] is True
    fund = next(f for f in body["funds"] if f["label"] == "CSPX")
    assert fund["as_of"] == "2026-09-25" and fund["source"] == "ishares_json"
    assert fund["held"] is True and fund["ticker"] == "CSPXl_EQ"
    nvda = body["top_holdings"][0]
    assert nvda["isin"] == NVDA and [v["label"] for v in nvda["via"]] == ["Direct", "CSPX"]
    assert body["benchmark"]["label"] == "S&P 500"
    assert body["concentration"]["holdings"] == 6


async def test_a_failed_fetch_is_a_gap_with_fallbacks(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await _seed_portfolio(backend_pool)
    calls: list[str] = []
    # No snapshot yet and the JSON answers 400: the SpreadsheetML download is used.
    await _run(backend_pool, issuer_handler(calls, ishares_json=False), dt.date(2026, 9, 29))
    assert calls == ["holdings", "fundDownloadV2"]
    step = (await _steps(backend_pool))["fund:CSPX"]
    assert step["outcome"] == "gap" and step["fallback"] == "spreadsheet"
    assert "HTTP 400" in step["error"]
    source = await backend_pool.fetchval("select source from public.etf_snapshot")
    assert source == "ishares_spreadsheet"

    # The JSON works again: the spreadsheet snapshot is replaced by a full one.
    calls.clear()
    await _run(backend_pool, issuer_handler(calls), dt.date(2026, 9, 29))
    assert calls == ["holdings", "exposureBreakdowns", "keyFundFacts"]

    # It fails again later: the recent stored snapshot stays in use (cached), no spreadsheet.
    calls.clear()
    await _run(backend_pool, issuer_handler(calls, ishares_json=False), dt.date(2026, 10, 3))
    assert calls == ["holdings"]
    step = (await _steps(backend_pool))["fund:CSPX"]
    assert step["fallback"] == "cached"
    status = json.loads(
        await backend_pool.fetchval(
            "select value from public.sync_state where key = $1", STATUS_KEY
        )
    )
    assert status[CSPX_ISIN]["ok"] is False and status[CSPX_ISIN]["last_success_at"]

    body = (await api.get("/portfolio/look-through")).json()
    fund = next(f for f in body["funds"] if f["label"] == "CSPX")
    assert fund["fallback"] == "cached" and fund["source"] == "ishares_json"
    assert "HTTP 400" in fund["last_attempt"]["error"]

    # Neither the JSON nor the spreadsheet and no recent snapshot: nothing is stored.
    await backend_pool.execute("delete from public.etf_snapshot")
    calls.clear()
    await _run(
        backend_pool,
        issuer_handler(calls, ishares_json=False, spreadsheet=False),
        dt.date(2026, 10, 3),
    )
    step = (await _steps(backend_pool))["fund:CSPX"]
    assert step["fallback"] == "none" and "fallback_error" in step
    body = (await api.get("/portfolio/look-through")).json()
    assert body["available"] is False
    assert next(f for f in body["funds"] if f["label"] == "CSPX")["status"] == "missing"
