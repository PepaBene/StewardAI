"""N4: official disclosure sources (TDnet, HKEXnews, CNINFO, DART, The Gazette).

The TDnet, HKEXnews and CNINFO fixtures are trimmed copies of live responses fetched
2026-10-10; the DART and Gazette ones follow the documentation (no key / site in maintenance).
"""

import datetime as dt
import json

import httpx
import pytest

from steward.config.event_rules import EventRulesConfig
from steward.config.news_sources import PRIMARY_COUNTRY, NewsSourcesConfig
from steward.news.feeds import HoldingFeedInfo
from steward.news.primary_sources import (
    code_key,
    collect_cninfo,
    collect_dart,
    collect_gazette,
    collect_hkex,
    collect_tdnet,
    holdings_by_code,
    name_key,
    parse_cninfo,
    parse_dart,
    parse_hkex,
    parse_tdnet,
)

TODAY = dt.date(2026, 10, 9)
RULES = EventRulesConfig()

TDNET_PAGE = """<table id="main-list-table">
<tr>
<td class="oddnew-L kjTime" noWrap>19:30</td>
<td class="oddnew-M kjCode" noWrap>76030</td>
<td class="oddnew-M kjName" noWrap>ジーイエット            </td>
<td class="oddnew-M kjTitle" align="left"><a href="140120261009549010.pdf" target="_blank">連結子会社における新たな事業の開始に関するお知らせ</a></td>
<td class="oddnew-M kjXbrl" noWrap align="center"> </td>
</tr>
<tr>
<td class="evennew-L kjTime" noWrap>15:00</td>
<td class="evennew-M kjCode" noWrap>72030</td>
<td class="evennew-M kjName" noWrap>トヨタ自動車</td>
<td class="evennew-M kjTitle" align="left"><a href="140120261009000001.pdf">2027年3月期 第2四半期 決算短信〔IFRS〕(連結)</a></td>
<td class="evennew-M kjXbrl" noWrap align="center"><a href="x.zip">XBRL</a></td>
</tr>
</table>"""

HKEX_BODY = {
    "result": json.dumps(
        [
            {
                "NEWS_ID": "1",
                "STOCK_NAME": "TENCENT",
                "STOCK_CODE": "00700",
                "TITLE": "PROFIT WARNING\nFOR THE SIX MONTHS",
                "LONG_TEXT": "Announcements and Notices - [Inside Information]",
                "DATE_TIME": "09/10/2026 22:57",
                "FILE_LINK": "/listedco/listconews/sehk/2026/1009/2026100901435.pdf",
            },
            {
                "NEWS_ID": "2",
                "STOCK_NAME": "RUSAL",
                "STOCK_CODE": "00486",
                "TITLE": "OVERSEAS REGULATORY ANNOUNCEMENT",
                "LONG_TEXT": "x",
                "DATE_TIME": "09/10/2026 22:57",
                "FILE_LINK": "/a.pdf",
            },
        ]
    )
}

CNINFO_BODY = {
    "announcements": [
        {
            "secCode": "002475",
            "secName": "立讯精密",
            "announcementTitle": "关于公司涉及美国国际贸易委员会337调查的公告",
            "announcementTime": 1791637542000,
            "adjunctUrl": "finalpage/2026-10-10/1225600359.pdf",
        }
    ]
}

DART_BODY = {
    "status": "000",
    "total_page": 1,
    "list": [
        {
            "corp_name": "삼성전자",
            "stock_code": "005930",
            "report_nm": "연결재무제표기준영업(잠정)실적(공정공시)",
            "rcept_no": "20261009000123",
            "flr_nm": "삼성전자",
            "rcept_dt": "20261009",
        },
        {
            "corp_name": "다른회사",
            "stock_code": "000001",
            "report_nm": "합병결정",
            "rcept_no": "20261009000124",
            "flr_nm": "다른회사",
            "rcept_dt": "20261009",
        },
    ],
}

GAZETTE_ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
 <title>The Gazette</title>
 <entry><title>Notice of winding-up of Acme Widgets PLC</title>
  <link href="https://www.thegazette.co.uk/notice/1"/><updated>2026-10-08T10:00:00Z</updated>
  <summary>Resolutions for winding-up of Acme Widgets PLC</summary></entry>
 <entry><title>Acme Widget Shop Limited: strike-off</title>
  <link href="https://www.thegazette.co.uk/notice/2"/><updated>2026-10-08T11:00:00Z</updated>
  <summary>Another company with a similar name</summary></entry>
</feed>"""


def holding(ticker: str, country: str, *codes: str, name: str = "X") -> HoldingFeedInfo:
    return HoldingFeedInfo(
        ticker, (name,), None, None, home_country=country, listing_codes=tuple(codes)
    )


def client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_codes_compare_without_zero_padding_and_only_inside_the_home_country() -> None:
    assert code_key("00700") == code_key("700") == "700"
    holdings = [holding("0700_HK_EQ", "HK", "0700"), holding("700_US_EQ", "US", "700")]
    assert holdings_by_code(holdings, "HK")["700"].ticker == "0700_HK_EQ"
    assert holdings_by_code(holdings, "JP") == {}


def test_tdnet_rows_carry_tokyo_time_the_four_digit_code_and_an_absolute_link() -> None:
    rows = parse_tdnet(TDNET_PAGE, TODAY)
    assert [r.code for r in rows] == ["7603", "7203"]
    toyota = rows[1]
    assert toyota.company == "トヨタ自動車"
    assert toyota.url == "https://www.release.tdnet.info/inbs/140120261009000001.pdf"
    assert toyota.published_at == dt.datetime(2026, 10, 9, 6, 0, tzinfo=dt.UTC)


def test_hkex_result_is_a_json_string_and_times_are_hong_kong() -> None:
    rows = parse_hkex(HKEX_BODY)
    assert rows[0].title == "PROFIT WARNING FOR THE SIX MONTHS"
    assert (
        rows[0].url
        == "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/1009/2026100901435.pdf"
    )
    assert rows[0].published_at == dt.datetime(2026, 10, 9, 14, 57, tzinfo=dt.UTC)


def test_cninfo_and_dart_parse() -> None:
    (cn,) = parse_cninfo(CNINFO_BODY)
    assert cn.url == "https://static.cninfo.com.cn/finalpage/2026-10-10/1225600359.pdf"
    assert cn.published_at is not None and cn.published_at.year == 2026
    rows, pages = parse_dart(DART_BODY)
    assert pages == 1 and rows[0].url.endswith("rcpNo=20261009000123")
    assert parse_dart({"status": "013"}) == ([], 0)
    with pytest.raises(ValueError, match="status 010"):
        parse_dart({"status": "010", "message": "unregistered key"})


def test_default_rules_map_each_market_in_its_own_language() -> None:
    event = RULES.announcement_event
    assert event("tdnet", "2027年3月期 第2四半期 決算短信") == "earnings_release"
    assert event("tdnet", "業績予想の修正に関するお知らせ") == "guidance_change"
    assert event("hkex", "PROFIT WARNING") == "guidance_change"
    assert event("hkex", "ANNOUNCEMENT ON DISCLOSEABLE TRANSACTION") == "m_and_a"
    assert event("cninfo", "2026年半年度报告") == "earnings_release"
    assert event("cninfo", "关于股东减持股份的公告") == "insider_transaction"
    assert event("dart", "연결재무제표기준영업(잠정)실적(공정공시)") == "earnings_release"
    assert event("dart", "임원ㆍ주요주주특정증권등소유상황보고서") == "insider_transaction"
    assert event("gazette", "Notice of winding-up") == "entity_status"
    assert event("filings", "有価証券報告書") == "earnings_release"
    assert event("filings", "臨時報告書") is None  # left to the classifier
    assert event("tdnet", "お知らせ") is None


def test_every_primary_source_type_has_a_seeded_off_by_default_source() -> None:
    seeded = {s.type: s for s in NewsSourcesConfig().sources}
    for kind in (*PRIMARY_COUNTRY, "filings"):
        assert kind in seeded and seeded[kind].enabled is False and seeded[kind].tier == 1
    macro = {s.id for s in NewsSourcesConfig().sources if s.macro}
    assert {"boj-whatsnew", "boe-news", "bundesbank-press", "boc-press"} <= macro


@pytest.mark.asyncio
async def test_tdnet_keeps_only_held_codes_and_stops_at_the_first_missing_page() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path.rsplit("/", 1)[-1])
        if "_001_20261009" in request.url.path:
            return httpx.Response(200, text=TDNET_PAGE)
        return httpx.Response(404, text="not found")

    async with client(handler) as http:
        result = await collect_tdnet(
            http, "tdnet", [holding("7203_JP_EQ", "JP", "7203")], RULES, TODAY, days=1
        )
    assert seen == ["I_list_001_20261009.html", "I_list_002_20261009.html"]
    assert [i.tickers for i in result.items] == [("7203_JP_EQ",)]
    item = result.items[0]
    assert (item.language, item.region, item.event_type) == ("ja", "JP", "earnings_release")
    assert item.headline.startswith("トヨタ自動車: ")
    assert result.errors == []


@pytest.mark.asyncio
async def test_no_request_is_made_when_no_holding_is_listed_in_the_market() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    async with client(handler) as http:
        us = [holding("AAPL_US_EQ", "US", "AAPL")]
        assert (await collect_tdnet(http, "tdnet", us, RULES, TODAY)).items == []
        assert (await collect_hkex(http, "hkex", us, RULES, TODAY)).items == []
        assert (await collect_dart(http, "k", "dart", us, RULES, TODAY)).items == []


@pytest.mark.asyncio
async def test_hkex_one_request_for_the_window_and_the_held_code_only() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params)
        return httpx.Response(200, json=HKEX_BODY)

    async with client(handler) as http:
        result = await collect_hkex(
            http, "hkexnews", [holding("0700_HK_EQ", "HK", "0700")], RULES, TODAY
        )
    assert len(calls) == 1 and calls[0]["fromDate"] == "20261008"
    assert [i.event_type for i in result.items] == ["guidance_change"]
    assert result.items[0].tickers == ("0700_HK_EQ",)


@pytest.mark.asyncio
async def test_cninfo_looks_up_the_org_id_then_queries_that_stock() -> None:
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("topSearch/query"):
            return httpx.Response(200, json=[{"code": "002475", "orgId": "9900014448"}])
        bodies.append(request.content.decode())
        return httpx.Response(200, json=CNINFO_BODY)

    async with client(handler) as http:
        result = await collect_cninfo(
            http, "cninfo", [holding("002475_CN_EQ", "CN", "002475")], RULES, TODAY
        )
    assert "stock=002475%2C9900014448" in bodies[0]
    assert result.items[0].language == "zh" and result.items[0].tickers == ("002475_CN_EQ",)


@pytest.mark.asyncio
async def test_dart_failure_never_puts_the_key_in_the_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async with client(handler) as http:
        result = await collect_dart(
            http, "SECRET-KEY", "dart", [holding("005930_KR_EQ", "KR", "005930")], RULES, TODAY
        )
    assert result.items == [] and result.errors
    assert all("SECRET-KEY" not in e for e in result.errors)


@pytest.mark.asyncio
async def test_dart_filters_to_held_stock_codes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=DART_BODY)

    async with client(handler) as http:
        result = await collect_dart(
            http, "k", "dart", [holding("005930_KR_EQ", "KR", "005930")], RULES, TODAY
        )
    assert [i.event_type for i in result.items] == ["earnings_release"]


def test_gazette_name_match_needs_the_whole_company_name() -> None:
    assert name_key("Acme Widgets PLC") == "acme widgets"
    assert name_key("Acme Widgets PLC") in name_key("winding-up of ACME WIDGETS plc")
    assert name_key("Acme Widgets PLC") not in name_key("Acme Widget Shop Limited")


@pytest.mark.asyncio
async def test_gazette_keeps_notices_naming_the_company_and_labels_the_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text=GAZETTE_ATOM, headers={"content-type": "application/atom+xml"}
        )

    async with client(handler) as http:
        result = await collect_gazette(
            http, "gazette", [holding("ACME_GB_EQ", "GB", name="Acme Widgets PLC")], RULES, TODAY
        )
    assert [i.url for i in result.items] == ["https://www.thegazette.co.uk/notice/1"]
    assert result.items[0].event_type == "entity_status"
