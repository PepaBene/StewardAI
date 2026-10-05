"""Registry of every source adapter's parsers and fetchers for the D8.1 contract harness.

Each `ParserCase` names a parser, a recorded baseline input and how that input degrades
(empty, schema drift, partial).  Each `FetchCase` names an HTTP entry point and the recorded
healthy response.  `tests/test_adapter_contracts.py` runs the same assertions over all of them.
Everything is offline: baselines come from tests/fixtures (documented provider formats), never
from a live call.
"""

from __future__ import annotations

import datetime as dt
import json
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx

from steward.documents.text import html_to_text
from steward.macro.providers import (
    parse_comtrade,
    parse_fred_vintages,
    parse_sdmx_json,
    parse_world_bank,
)
from steward.ownership.insider import parse_ownership_xml
from steward.sources import cnb, esef, esma, openfigi, pse, twelvedata, wikidata, xbrl
from steward.sources import market as market_src
from steward.sources.alphavantage import AlphaVantageError, company_overview
from steward.sources.company_research import parse_ops_patent_xml, parse_pdl_company
from steward.sources.edgar import EdgarClient, EdgarError, Filing, parse_index, parse_submissions
from steward.sources.etf_issuers import (
    IssuerError,
    fetch_ishares,
    fetch_vanguard_fund,
    parse_ishares_exposure,
    parse_ishares_holdings,
    parse_ishares_spreadsheet,
)
from steward.sources.finnhub import FinnhubError, basic_financials, next_earnings_date
from steward.sources.form13f import dataset_links, parse_dataset
from steward.sources.free_market import (
    FreeSourceError,
    alpha_daily,
    alpha_news_sentiment,
    alpha_statement,
    eodhd_daily,
    eodhd_exchange_symbols,
    finnhub_earnings_surprises,
    finnhub_peers,
    parse_alpha_daily,
    parse_alpha_news,
    parse_alpha_series,
    parse_alpha_statement_facts,
)
from steward.sources.fund_characteristics import (
    FundDataError,
    ishares_characteristics,
    parse_ishares,
    parse_vanguard,
    vanguard_characteristics,
)
from steward.sources.gleif import GleifClient, GleifError, parse_record
from steward.sources.nport import NportError, parse_nport
from steward.sources.pdmr_uk import parse_pdmr_notification
from steward.sources.schedule13 import Schedule13Error, parse_schedule13_xml
from steward.sources.yahoo import YahooError, parse_chart, yahoo_intraday

FIX = Path(__file__).parent / "fixtures"
CON = FIX / "contracts"


def jfix(*parts: str) -> Any:
    return json.loads(FIX.joinpath(*parts).read_text())


def tfix(*parts: str) -> str:
    return FIX.joinpath(*parts).read_text()


def bfix(*parts: str) -> bytes:
    return FIX.joinpath(*parts).read_bytes()


# ---------------------------------------------------------------------------------------
# Degradation helpers
# ---------------------------------------------------------------------------------------
def rename_keys(value: Any) -> Any:
    """Schema drift: every field is renamed, structure is kept."""
    if isinstance(value, dict):
        return {f"{k}_v2": rename_keys(v) for k, v in value.items()}
    if isinstance(value, list):
        return [rename_keys(v) for v in value]
    return value


def drop_last_keys(value: Any) -> Any:
    """Partial record: the last field of every object is missing."""
    if isinstance(value, dict):
        keys = list(value)
        return {k: drop_last_keys(value[k]) for k in keys[:-1]} if keys else {}
    if isinstance(value, list):
        return [drop_last_keys(v) for v in value]
    return value


def half_text(value: str) -> str:
    return value[: max(1, len(value) // 2)]


def half_bytes(value: bytes) -> bytes:
    return value[: max(1, len(value) // 2)]


def text_drift(value: str) -> str:
    return value.replace("|", ";").replace("<", "<x-").replace(",", ";")


def bytes_drift(value: bytes) -> bytes:
    return value.replace(b"<", b"<x-").replace(b"x-/", b"/x-").replace(b"<x-/", b"</x-")


def rows_of(result: Any) -> int:
    if result is None:
        return 0
    if isinstance(result, tuple):
        return sum(rows_of(r) for r in result)
    if isinstance(result, list | dict | set | str | bytes):
        return len(result)
    for name in ("holdings", "rows", "facts", "persons", "transactions", "points"):
        inner = getattr(result, name, None)
        if isinstance(inner, list | tuple):
            return len(inner)
    return 1


@dataclass(frozen=True)
class ParserCase:
    id: str
    module: str
    parse: Callable[[Any], Any]
    baseline: Callable[[], Any]
    typed: tuple[type[BaseException], ...]
    empty: Any = field(default_factory=dict)
    drift: Callable[[Any], Any] = rename_keys
    partial: Callable[[Any], Any] = drop_last_keys
    rows: Callable[[Any], int] = rows_of
    # A baseline that yields no rows proves nothing (drift/partial would be vacuous), so every
    # case must produce at least this many rows from its recorded baseline; never below 1.
    min_rows: int = 1


TS = dt.datetime(2026, 10, 1, tzinfo=dt.UTC)
PDMR_TEXT = html_to_text(tfix("pdmr_uk", "rns_pdmr.html"))


def _form4_filing() -> Filing:
    return Filing(
        cik="0000111111",
        form="4",
        accession="0001222222-26-000101",
        filing_date=dt.date(2026, 8, 12),
        report_date=None,
        primary_document="form4.xml",
        items="",
    )


def _zip_13f() -> bytes:
    sample = FIX / "ownership" / "form13f_sample.zip"
    return sample.read_bytes()


def _sdmx(body: Any) -> Any:
    return parse_sdmx_json(
        body,
        source="bis",
        series_id="credit",
        geography="CA",
        unit="%",
        frequency="quarterly",
        source_url="x",
        vintage="2026-01",
    )


def _dataset(raw: bytes) -> Any:
    return parse_dataset(BytesIO(raw), {"037833100"})


def _esef_doc() -> Any:
    return jfix("valuation", "novo-2026-06-30.json")


def _instance(raw: bytes) -> Any:
    return xbrl.parse_instance(raw)


STOOQ_CSV = (
    "Date,Open,High,Low,Close,Volume\n"
    "2026-09-29,100.0,102.0,99.0,101.0,1500\n"
    "2026-09-30,101.0,103.0,100.0,102.5,1800\n"
)
FRANKFURTER = {
    "base": "EUR",
    "start_date": "2026-09-29",
    "end_date": "2026-09-30",
    "rates": {"2026-09-29": {"USD": 1.1, "CZK": 24.5}, "2026-09-30": {"USD": 1.11, "CZK": 24.4}},
}

PARSER_CASES: list[ParserCase] = [
    # --- macro providers ------------------------------------------------------------
    ParserCase(
        "world_bank",
        "steward.macro.providers",
        lambda b: parse_world_bank(b, "u"),
        lambda: json.loads((CON / "world_bank.json").read_text()),
        (),
        empty=[],
        drift=lambda b: [b[0], rename_keys(b[1])],
        partial=lambda b: [b[0], drop_last_keys(b[1])],
    ),
    ParserCase(
        "sdmx",
        "steward.macro.providers",
        _sdmx,
        lambda: json.loads((CON / "sdmx.json").read_text()),
        (),
    ),
    ParserCase(
        "comtrade",
        "steward.macro.providers",
        parse_comtrade,
        lambda: json.loads((CON / "comtrade.json").read_text()),
        (),
    ),
    ParserCase(
        "fred_vintages",
        "steward.macro.providers",
        lambda b: parse_fred_vintages(b, series_id="GDP", unit="USD", source_url="u"),
        lambda: json.loads((CON / "fred_vintages.json").read_text()),
        (),
    ),
    # --- Czech National Bank ----------------------------------------------------------
    ParserCase(
        "cnb_czeonia_yearly",
        "steward.sources.cnb",
        cnb.parse_czeonia_yearly,
        lambda: tfix("cnb", "czeonia_2026.txt"),
        (cnb.CnbError,),
        empty="",
        drift=text_drift,
        partial=half_text,
    ),
    ParserCase(
        "cnb_czeonia_daily",
        "steward.sources.cnb",
        cnb.parse_czeonia_daily,
        lambda: tfix("cnb", "czeonia_daily.txt"),
        (cnb.CnbError,),
        empty="",
        drift=text_drift,
        partial=half_text,
    ),
    ParserCase(
        "cnb_repo_rate",
        "steward.sources.cnb",
        cnb.parse_repo_rate,
        lambda: tfix("cnb", "repo_2w.txt"),
        (cnb.CnbError,),
        empty="",
        drift=text_drift,
        partial=half_text,
    ),
    # --- Alpha Vantage / Finnhub / EODHD ---------------------------------------------
    ParserCase(
        "alpha_news",
        "steward.sources.free_market",
        lambda b: parse_alpha_news(b, "AAA"),
        lambda: json.loads((CON / "alpha_news.json").read_text()),
        (FreeSourceError,),
    ),
    ParserCase(
        "alpha_series",
        "steward.sources.free_market",
        parse_alpha_series,
        lambda: json.loads((CON / "alpha_series.json").read_text()),
        (FreeSourceError,),
    ),
    ParserCase(
        "alpha_daily",
        "steward.sources.free_market",
        parse_alpha_daily,
        lambda: json.loads((CON / "alpha_daily.json").read_text()),
        (FreeSourceError,),
    ),
    ParserCase(
        "alpha_statement_facts",
        "steward.sources.free_market",
        lambda b: parse_alpha_statement_facts(b, statement="income"),
        lambda: json.loads((CON / "alpha_statement.json").read_text()),
        (FreeSourceError,),
    ),
    # --- market data / reference -------------------------------------------------------
    ParserCase(
        "stooq",
        "steward.sources.market",
        lambda b: market_src.parse_stooq("sap.de", b),
        lambda: STOOQ_CSV,
        (market_src.MarketDataError,),
        empty="",
        drift=lambda t: t.replace("Date", "Datum").replace("Close", "Cierre"),
        partial=half_text,
    ),
    ParserCase(
        "frankfurter",
        "steward.sources.market",
        market_src.parse_frankfurter,
        lambda: FRANKFURTER,
        (market_src.MarketDataError,),
    ),
    ParserCase(
        "nager_holidays",
        "steward.sources.market",
        lambda b: market_src.parse_nager("CZ", b),
        lambda: jfix("reference", "nager_cz_2026.json"),
        (market_src.MarketDataError,),
        empty=[],
    ),
    ParserCase(
        "yahoo_chart",
        "steward.sources.yahoo",
        lambda b: parse_chart("MSFT", b),
        lambda: jfix("prices", "yahoo_msft_5m.json"),
        (YahooError,),
    ),
    ParserCase(
        "pse_index_page",
        "steward.sources.pse",
        lambda t: pse.parse_detail(pse.PX_ISIN, t),
        lambda: tfix("prices", "pse_px_detail.html"),
        (pse.PseError,),
        empty="",
        drift=text_drift,
        partial=half_text,
    ),
    ParserCase(
        "firds",
        "steward.sources.esma",
        esma.parse_firds,
        lambda: jfix("reference", "firds_sap.json"),
        (),
    ),
    ParserCase(
        "firds_history",
        "steward.sources.esma",
        esma.parse_firds_history,
        lambda: jfix("reference", "firds_sap.json"),
        (),
    ),
    ParserCase(
        "fitrs",
        "steward.sources.esma",
        esma.parse_fitrs,
        lambda: jfix("reference", "fitrs_sap.json"),
        (),
    ),
    ParserCase(
        "openfigi",
        "steward.sources.openfigi",
        openfigi.parse_mapping,
        lambda: jfix("reference", "openfigi_sap.json")[0],
        (openfigi.OpenFigiError,),
    ),
    ParserCase(
        "gleif_record",
        "steward.sources.gleif",
        parse_record,
        lambda: jfix("reference", "gleif_sap.json")["data"],
        (GleifError, KeyError),
        drift=lambda b: rename_keys(b),
    ),
    ParserCase(
        "wikidata_properties",
        "steward.sources.wikidata",
        wikidata.parse_properties,
        lambda: jfix("reference", "wikidata_sap_props.json"),
        (wikidata.WikidataError,),
    ),
    ParserCase(
        "wikidata_aliases",
        "steward.sources.wikidata",
        wikidata.parse_aliases,
        lambda: jfix("reference", "wikidata_sap_aliases.json"),
        (wikidata.WikidataError,),
    ),
    ParserCase(
        "wikidata_competitors",
        "steward.sources.wikidata",
        wikidata.parse_competitors,
        # Recorded SPARQL result in the documented ?c / ?cLabel shape (the third binding is an
        # unlabelled "Q..." entity the parser must drop), so the baseline really yields rows.
        lambda: json.loads((CON / "wikidata_competitors.json").read_text()),
        (wikidata.WikidataError,),
    ),
    # --- SEC / EDGAR / XBRL / ESEF ------------------------------------------------------
    ParserCase(
        "edgar_submissions",
        "steward.sources.edgar",
        lambda b: parse_submissions("320193", b),
        lambda: json.loads((CON / "edgar_submissions.json").read_text()),
        (EdgarError,),
    ),
    ParserCase(
        "edgar_index",
        "steward.sources.edgar",
        parse_index,
        lambda: html_to_text(
            "<table><tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th></tr>"
            '<tr><td>1</td><td>8-K</td><td><a href="x">aapl-20250731.htm</a> iXBRL</td>'
            "<td>8-K</td><td>39036</td></tr>"
            '<tr><td>2</td><td>EX-99.1</td><td><a href="y">a8-kex991.htm</a></td>'
            "<td>EX-99.1</td><td>168258</td></tr></table>"
        ),
        (EdgarError,),
        empty="",
        drift=lambda t: t.replace(" | ", " ; "),
        partial=half_text,
    ),
    ParserCase(
        "esef_filings",
        "steward.sources.esef",
        esef.parse_filings,
        lambda: {
            "data": [
                {
                    "id": "1",
                    "attributes": {
                        "json_url": "/x/report.json",
                        "period_end": "2025-12-31",
                        "date_added": "2026-04-01 10:00:00",
                        "country": "DK",
                    },
                }
            ]
        },
        (xbrl.XbrlError,),
    ),
    ParserCase(
        "esef_xbrl_json",
        "steward.sources.esef",
        esef.parse_xbrl_json,
        _esef_doc,
        (xbrl.XbrlError, ValueError),
    ),
    ParserCase(
        "xbrl_instance",
        "steward.sources.xbrl",
        _instance,
        lambda: bfix("xbrl", "asml-20251231_htm.xml"),
        (xbrl.XbrlError,),
        empty=b"",
        drift=bytes_drift,
        partial=half_bytes,
        rows=lambda i: len(i.facts) if hasattr(i, "facts") else 1,
    ),
    ParserCase(
        "xbrl_labels",
        "steward.sources.xbrl",
        xbrl.parse_labels,
        lambda: bfix("xbrl", "asml-20251231_lab.xml"),
        (xbrl.XbrlError,),
        empty=b"",
        drift=bytes_drift,
        partial=half_bytes,
    ),
    ParserCase(
        "form13f_dataset",
        "steward.sources.form13f",
        _dataset,
        _zip_13f,
        (zipfile.BadZipFile, KeyError),
        empty=b"",
        drift=lambda raw: raw[:100],
        partial=half_bytes,
        rows=lambda d: len(d.positions) if hasattr(d, "positions") else 1,
    ),
    ParserCase(
        "form13f_links",
        "steward.sources.form13f",
        lambda t: dataset_links(t, "https://www.sec.gov/data/"),
        lambda: tfix("ownership", "form13f_index.html"),
        (),
        empty="",
        drift=lambda t: t.replace("href", "data-x"),
        partial=half_text,
    ),
    ParserCase(
        "nport",
        "steward.sources.nport",
        parse_nport,
        lambda: bfix("funds", "nport_p_sample.xml"),
        (NportError,),
        empty=b"",
        drift=bytes_drift,
        partial=half_bytes,
    ),
    ParserCase(
        "schedule13",
        "steward.sources.schedule13",
        parse_schedule13_xml,
        lambda: bfix("control_stakes", "sc13da_group.xml"),
        (Schedule13Error,),
        empty=b"",
        drift=bytes_drift,
        partial=half_bytes,
    ),
    ParserCase(
        "form4_ownership",
        "steward.ownership.insider",
        lambda xml: parse_ownership_xml(xml, _form4_filing()),
        lambda: bfix("insider", "form4_original.xml"),
        (ValueError,),
        empty=b"",
        drift=bytes_drift,
        partial=half_bytes,
        rows=lambda f: len(f.rows),
    ),
    ParserCase(
        "pdmr_uk",
        "steward.sources.pdmr_uk",
        parse_pdmr_notification,
        lambda: PDMR_TEXT,
        (),
        empty="",
        drift=lambda t: t.replace("Details of the person", "Particulars of someone"),
        partial=half_text,
    ),
    # --- ETF / fund issuers -----------------------------------------------------------
    ParserCase(
        "ishares_holdings",
        "steward.sources.etf_issuers",
        lambda b: parse_ishares_holdings(b, "253743"),
        lambda: jfix("look_through", "ishares_holdings.json"),
        (IssuerError,),
        rows=lambda s: len(s.rows),
    ),
    ParserCase(
        "ishares_exposure",
        "steward.sources.etf_issuers",
        parse_ishares_exposure,
        lambda: jfix("look_through", "ishares_exposure.json"),
        (IssuerError,),
    ),
    ParserCase(
        "ishares_spreadsheet",
        "steward.sources.etf_issuers",
        lambda t: parse_ishares_spreadsheet(t, "253743"),
        lambda: tfix("look_through", "ishares_fund.xml"),
        (IssuerError,),
        empty="",
        drift=text_drift,
        partial=half_text,
        rows=lambda s: len(s.rows),
    ),
    ParserCase(
        "fund_ishares_characteristics",
        "steward.sources.fund_characteristics",
        lambda t: parse_ishares(t, "u"),
        lambda: tfix("valuation", "ishares_cspx.html"),
        (FundDataError,),
        empty="",
        drift=lambda t: t.replace("priceEarnings", "peRatioX").replace("priceBook", "pbRatioX"),
        partial=half_text,
    ),
    ParserCase(
        "fund_vanguard_characteristics",
        "steward.sources.fund_characteristics",
        lambda b: parse_vanguard(b, "u"),
        lambda: jfix("valuation", "vanguard_9679.json"),
        (FundDataError,),
    ),
    # --- company research -------------------------------------------------------------
    ParserCase(
        "pdl_company",
        "steward.sources.company_research",
        lambda b: parse_pdl_company(b, "example.com"),
        lambda: {"likelihood": 9, "website": "example.com", "employee_count": 120},
        (FreeSourceError,),
        min_rows=1,
    ),
    ParserCase(
        "epo_ops_patents",
        "steward.sources.company_research",
        lambda x: parse_ops_patent_xml(x, ("Example Corporation",)),
        lambda: (
            '<ops:world-patent-data xmlns:ops="urn:ops" xmlns:ex="urn:ex">'
            '<ex:exchange-document family-id="9001" country="EP" doc-number="123" kind="A1">'
            "<ex:bibliographic-data>"
            "<ex:publication-reference>"
            '<ex:document-id document-id-type="docdb"><ex:country>EP</ex:country>'
            "<ex:doc-number>123</ex:doc-number><ex:kind>A1</ex:kind>"
            "<ex:date>20240601</ex:date></ex:document-id></ex:publication-reference>"
            '<ex:priority-claim><ex:document-id document-id-type="docdb">'
            "<ex:date>20240102</ex:date></ex:document-id></ex:priority-claim>"
            '<ex:parties><ex:applicants><ex:applicant data-format="original">'
            "<ex:applicant-name><ex:name>Example Corporation</ex:name></ex:applicant-name>"
            "</ex:applicant></ex:applicants></ex:parties>"
            '<ex:invention-title lang="en">Example invention</ex:invention-title>'
            "</ex:bibliographic-data></ex:exchange-document></ops:world-patent-data>"
        ),
        (FreeSourceError, ValueError),
        empty="",
        drift=lambda t: t.replace("ex:name", "ex:person-name"),
        partial=half_text,
    ),
]


# ---------------------------------------------------------------------------------------
# HTTP entry points
# ---------------------------------------------------------------------------------------
def json_response(body: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body)


def text_response(text: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=text)


@dataclass(frozen=True)
class FetchCase:
    id: str
    module: str
    call: Callable[[httpx.AsyncClient], Awaitable[Any]]
    ok: Callable[[httpx.Request], httpx.Response]
    typed: tuple[type[BaseException], ...]
    # Adapters that return an AccessResult/FetchResult instead of raising on denial.
    access_of: Callable[[Any], Any] | None = None
    # A 404 is a legitimate "not covered" answer for this endpoint.
    not_found_is_empty: bool = False
    # Whether a 200 response with an HTML/garbage body must stay typed.
    garbage_typed: bool = True


UA = "StewardTest ops@example.com"
D1, D2 = dt.date(2026, 9, 1), dt.date(2026, 9, 30)
ISHARES_PAGE_TEXT = None


def _ok_json(name: str) -> Callable[[httpx.Request], httpx.Response]:
    return lambda req: json_response(json.loads((CON / name).read_text()))


def _edgar_call(http: httpx.AsyncClient) -> Awaitable[Any]:
    return EdgarClient(http, UA).filings("320193")


def _gleif_call(http: httpx.AsyncClient) -> Awaitable[Any]:
    return GleifClient(http).record("529900D6BF99LW9R2E68")


def _wikidata_call(http: httpx.AsyncClient) -> Awaitable[Any]:
    return wikidata.WikidataClient(http, UA).properties("Q1")


async def _public_macro(http: httpx.AsyncClient, which: str) -> Any:
    from steward.sources.public_macro import ComtradeAdapter, SdmxAdapter, WorldBankAdapter

    if which == "world_bank":
        return await WorldBankAdapter(http).fetch(
            {"country": "CZ", "indicators": ["NY.GDP.MKTP.CD"]}
        )
    if which == "sdmx":
        return await SdmxAdapter(http, "bis", "https://stats.bis.org/api/v2").fetch(
            {"flow": "WS_CREDIT_GAP", "key": "Q.CZ", "series_id": "x"}
        )
    return await ComtradeAdapter(http, api_key="k").fetch(
        {"reporter": 203, "period": "2024", "hs_code": "85"}
    )


def _esef_filing_call(http: httpx.AsyncClient) -> Awaitable[Any]:
    filing = esef.EsefFiling(
        "1", dt.date(2025, 12, 31), None, "DK", "https://x.test/r.json", None, {}
    )
    return esef.esef_document(http, filing)


def _public_macro_case(which: str, body: Any) -> FetchCase:
    return FetchCase(
        f"public_macro_{which}",
        "steward.sources.public_macro",
        lambda http: _public_macro(http, which),
        lambda req: json_response(body),
        (),
        access_of=lambda result: result.access,
        garbage_typed=False,
    )


FETCH_CASES: list[FetchCase] = [
    FetchCase(
        "alpha_overview",
        "steward.sources.alphavantage",
        lambda http: company_overview(http, "k", "AAA"),
        _ok_json("alpha_overview.json"),
        (AlphaVantageError,),
    ),
    FetchCase(
        "finnhub_calendar",
        "steward.sources.finnhub",
        lambda http: next_earnings_date(
            http, "k", "AAA", dt.date(2026, 10, 1), dt.date(2026, 12, 1)
        ),
        _ok_json("finnhub_calendar.json"),
        (FinnhubError,),
    ),
    FetchCase(
        "finnhub_metric",
        "steward.sources.finnhub",
        lambda http: basic_financials(http, "k", "AAA"),
        _ok_json("finnhub_metric.json"),
        (FinnhubError,),
    ),
    FetchCase(
        "alpha_news_sentiment",
        "steward.sources.free_market",
        lambda http: alpha_news_sentiment(http, "k", "AAA"),
        _ok_json("alpha_news.json"),
        (FreeSourceError,),
    ),
    FetchCase(
        "alpha_daily_fetch",
        "steward.sources.free_market",
        lambda http: alpha_daily(http, "k", "AAA"),
        _ok_json("alpha_daily.json"),
        (FreeSourceError,),
    ),
    FetchCase(
        "alpha_statement_fetch",
        "steward.sources.free_market",
        lambda http: alpha_statement(http, "k", "AAA", "INCOME_STATEMENT"),
        _ok_json("alpha_statement.json"),
        (FreeSourceError,),
    ),
    FetchCase(
        "eodhd_symbols",
        "steward.sources.free_market",
        lambda http: eodhd_exchange_symbols(http, "k", "US"),
        _ok_json("eodhd_symbols.json"),
        (FreeSourceError,),
    ),
    FetchCase(
        "eodhd_daily_fetch",
        "steward.sources.free_market",
        lambda http: eodhd_daily(http, "k", "AAA.US"),
        _ok_json("eodhd_daily.json"),
        (FreeSourceError,),
    ),
    FetchCase(
        "finnhub_peers_fetch",
        "steward.sources.free_market",
        lambda http: finnhub_peers(http, "k", "AAA"),
        _ok_json("finnhub_peers.json"),
        (FreeSourceError,),
    ),
    FetchCase(
        "finnhub_earnings_fetch",
        "steward.sources.free_market",
        lambda http: finnhub_earnings_surprises(http, "k", "AAA"),
        _ok_json("finnhub_earnings.json"),
        (FreeSourceError,),
    ),
    FetchCase(
        "stooq_fetch",
        "steward.sources.market",
        lambda http: market_src.stooq_daily(http, "sap.de", D1, D2),
        lambda req: text_response(STOOQ_CSV),
        (market_src.MarketDataError,),
    ),
    FetchCase(
        "frankfurter_fetch",
        "steward.sources.market",
        lambda http: market_src.frankfurter_series(http, D1, D2),
        lambda req: json_response(FRANKFURTER),
        (market_src.MarketDataError,),
    ),
    FetchCase(
        "nager_fetch",
        "steward.sources.market",
        lambda http: market_src.public_holidays(http, "CZ", 2026),
        lambda req: json_response(jfix("reference", "nager_cz_2026.json")),
        (market_src.MarketDataError,),
        not_found_is_empty=True,
    ),
    FetchCase(
        "yahoo_fetch",
        "steward.sources.yahoo",
        lambda http: yahoo_intraday(http, "MSFT", retry=False),
        lambda req: json_response(jfix("prices", "yahoo_msft_5m.json")),
        (YahooError,),
    ),
    FetchCase(
        "firds_fetch",
        "steward.sources.esma",
        lambda http: esma.firds(http, "DE0007164600"),
        lambda req: json_response(jfix("reference", "firds_sap.json")),
        (esma.EsmaError,),
    ),
    FetchCase(
        "fitrs_fetch",
        "steward.sources.esma",
        lambda http: esma.fitrs(http, "DE0007164600"),
        lambda req: json_response(jfix("reference", "fitrs_sap.json")),
        (esma.EsmaError,),
    ),
    FetchCase(
        "esef_filings_fetch",
        "steward.sources.esef",
        lambda http: esef.esef_filings(http, "529900D6BF99LW9R2E68"),
        lambda req: json_response(
            {
                "data": [
                    {
                        "id": "1",
                        "attributes": {
                            "json_url": "/x/r.json",
                            "period_end": "2025-12-31",
                            "country": "DK",
                        },
                    }
                ]
            }
        ),
        (xbrl.XbrlError,),
        not_found_is_empty=True,
    ),
    FetchCase(
        "esef_document_fetch",
        "steward.sources.esef",
        _esef_filing_call,
        lambda req: json_response(_esef_doc()),
        (xbrl.XbrlError,),
    ),
    FetchCase(
        "openfigi_fetch",
        "steward.sources.openfigi",
        lambda http: openfigi.map_isin(http, "DE0007164600"),
        lambda req: json_response(jfix("reference", "openfigi_sap.json")),
        (openfigi.OpenFigiError,),
    ),
    FetchCase(
        "pse_fetch",
        "steward.sources.pse",
        lambda http: pse.pse_index(http),
        lambda req: text_response(tfix("prices", "pse_px_detail.html")),
        (pse.PseError,),
    ),
    FetchCase(
        "wikidata_fetch",
        "steward.sources.wikidata",
        _wikidata_call,
        lambda req: json_response(jfix("reference", "wikidata_sap_props.json")),
        (wikidata.WikidataError,),
    ),
    FetchCase(
        "gleif_fetch",
        "steward.sources.gleif",
        _gleif_call,
        lambda req: json_response(jfix("reference", "gleif_sap.json")),
        (GleifError,),
        not_found_is_empty=True,
    ),
    FetchCase(
        "edgar_fetch",
        "steward.sources.edgar",
        _edgar_call,
        _ok_json("edgar_submissions.json"),
        (EdgarError,),
    ),
    FetchCase(
        "cnb_repo_fetch",
        "steward.sources.cnb",
        cnb.fetch_repo_rate,
        lambda req: text_response(tfix("cnb", "repo_2w.txt")),
        (cnb.CnbError,),
    ),
    FetchCase(
        "cnb_czeonia_fetch",
        "steward.sources.cnb",
        lambda http: cnb.fetch_czeonia_year(http, 2026),
        lambda req: text_response(tfix("cnb", "czeonia_2026.txt")),
        (cnb.CnbError,),
    ),
    FetchCase(
        "ishares_fetch",
        "steward.sources.etf_issuers",
        lambda http: fetch_ishares(http, "253743", retries=0, retry_delay=0),
        lambda req: json_response(jfix("look_through", "ishares_holdings.json")),
        (IssuerError,),
    ),
    FetchCase(
        "vanguard_fetch",
        "steward.sources.etf_issuers",
        lambda http: fetch_vanguard_fund(http, "9679", retries=0, retry_delay=0),
        lambda req: json_response({"data": {"funds": [{"profile": {}}]}}),
        (IssuerError,),
    ),
    FetchCase(
        "fund_ishares_page_fetch",
        "steward.sources.fund_characteristics",
        lambda http: ishares_characteristics(http, "https://ishares.test/p"),
        lambda req: text_response(tfix("valuation", "ishares_cspx.html")),
        (FundDataError,),
    ),
    FetchCase(
        "fund_vanguard_fetch",
        "steward.sources.fund_characteristics",
        lambda http: vanguard_characteristics(http, "https://vg.test/gql", "9679", "ie0"),
        lambda req: json_response(jfix("valuation", "vanguard_9679.json")),
        (FundDataError,),
    ),
    _public_macro_case("world_bank", json.loads((CON / "world_bank.json").read_text())),
    _public_macro_case("sdmx", json.loads((CON / "sdmx.json").read_text())),
    _public_macro_case("comtrade", json.loads((CON / "comtrade.json").read_text())),
    FetchCase(
        "twelvedata_time_series",
        "steward.sources.twelvedata",
        lambda http: twelvedata.probe_time_series(http, "k", "ASML", "Euronext"),
        _ok_json("twelvedata_time_series.json"),
        (twelvedata.TwelveDataError,),
        access_of=lambda probe: probe,  # the probe carries .state and .detail
        garbage_typed=False,
    ),
]

# Modules with no parser or network contract of their own, and why.
NON_ADAPTER_MODULES: dict[str, str] = {
    "steward.sources": "package marker",
    "steward.sources.base": "the contract itself (AccessState, access_from_http)",
    "steward.sources.governor": "quota governor, covered by test_quota_reset_* in the harness",
    "steward.sources.store": "persistence boundary, covered by DB tests",
    "steward.sources.reconcile": "pure comparison, covered by test_free_sources",
    "steward.sources.documents": "persistence of fetched documents, covered by DB tests",
    "steward.sources.audit": "endpoint probe classifier, covered by CLASSIFIER_CASES",
    "steward.sources.fund_probes": "probe classifier and normalizers, covered by CLASSIFIER_CASES",
    "steward.sources.xml_guard": "DTD/entity guard, covered by test_funds_stream_hardening",
    "steward.sources.ciks": "CIK normalisation helper, covered by test_funds_stream_hardening",
    "steward.sources.credit_group": "governor credit-group reservation helper: no parser and no "
    "network contract of its own",
    "steward.sources.fmp": "FMP profile/estimates parsers and transport, covered by "
    "tests/test_provider_jobs.py (drift, empty, 402/429 and error-envelope cases; payload field "
    "names unverified against a live response)",
    "steward.sources.provider_jobs": "scheduled job orchestration over the governed collectors: "
    "no parser and no network contract of its own (planning covered by test_provider_jobs)",
    "steward.sources.xbrl_concepts": "static XBRL concept classification table: no parser and no "
    "network contract of its own (the parsers that use it are covered under steward.sources.xbrl)",
}

# (status, body, allowed states) for the two probe classifiers.
CLASSIFIER_STATUS_MATRIX: list[tuple[int, Any, set[str]]] = [
    (401, {}, {"access_unverified"}),
    (402, {}, {"paid_only"}),
    (403, {}, {"access_unverified", "paid_only"}),
    (403, {"message": "upgrade to the premium plan"}, {"paid_only"}),
    (429, {}, {"quota_exhausted"}),
    (500, {}, {"temporarily_failed"}),
    (503, {}, {"temporarily_failed"}),
    (200, {"Note": "Our standard API call frequency is 25 requests per day"}, {"quota_exhausted"}),
    (200, {"Information": "premium endpoint, please subscribe"}, {"paid_only"}),
    (200, {"Error Message": "Invalid API call. symbol not found"}, {"not_covered"}),
    (200, [], {"not_covered"}),
]
