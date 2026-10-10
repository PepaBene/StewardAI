"""Official disclosure sources by market (plan N4): the exchange or regulator's own announcement
list, read per holding by exchange stock code.

- tdnet (JP): TDnet's daily list pages (`release.tdnet.info/inbs/I_list_NNN_YYYYMMDD.html`),
  Japanese titles, 5-digit codes whose first four digits are the Tokyo code. TDnet keeps about a
  month of days. No key.
- hkex (HK): HKEXnews title search, one request for the whole window. English titles. No key.
- cninfo (CN): CNINFO announcement query, per holding after a code -> orgId lookup. Chinese
  titles. No key.
- dart (KR): DART `list.json` (key `DART_API_KEY`), the market-wide list for the window filtered
  to the holdings' stock codes. Korean report names. The envelope and field names follow the
  OpenDART documentation and were NOT checked against a live response (no key available).
- gazette (GB): The Gazette's Atom notice feed, one query per holding by company name, kept only
  when the notice text contains the whole normalised name. NOT checked live (the site was in
  scheduled maintenance when this was written); the Atom parsing is generic.
- filings: the EDINET and Companies House filings the disclosure jobs already stored in
  `holding_filing_event`, turned into items (no second fetch, so their request budgets hold).

A holding is matched by its home country (FIRDS/FITRS) and a stock code from its listing
tickers, never by name, except the Gazette, which has no code. Items carry the holding's ticker,
so they are linked. The event type comes from the announcement rules (`EventRulesConfig.
announcement_event`), in the filing's own language; titles are translated by the language step.
"""

import datetime as dt
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from html import unescape
from typing import Any
from urllib.parse import quote, urljoin
from zoneinfo import ZoneInfo

import asyncpg
import httpx

from steward.config.event_rules import EventRulesConfig
from steward.documents.fetch import FetchError, default_headers, fetch_document
from steward.news.feeds import CollectResult, HoldingFeedInfo, RawItem, parse_feed
from steward.news.normalize import clean_text

TOKYO = ZoneInfo("Asia/Tokyo")
HONG_KONG = ZoneInfo("Asia/Hong_Kong")
SEOUL = ZoneInfo("Asia/Seoul")
MAX_PAGE_BYTES = 3_000_000

TDNET_BASE = "https://www.release.tdnet.info/inbs/"
TDNET_PAGE = TDNET_BASE + "I_list_{page:03d}_{day}.html"
TDNET_MAX_PAGES = 20
HKEX_SEARCH = "https://www1.hkexnews.hk/search/titleSearchServlet.do"
HKEX_BASE = "https://www1.hkexnews.hk"
CNINFO_QUERY = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_LOOKUP = "https://www.cninfo.com.cn/new/information/topSearch/query"
CNINFO_FILES = "https://static.cninfo.com.cn/"
DART_LIST = "https://opendart.fss.or.kr/api/list.json"
DART_VIEW = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
DART_MAX_PAGES = 40
GAZETTE_FEED = "https://www.thegazette.co.uk/all-notices/notice/data.feed"


@dataclass(frozen=True)
class Disclosure:
    url: str
    company: str
    title: str
    # What the announcement rules match against: the title, plus the exchange's category.
    kind: str
    published_at: dt.datetime | None
    code: str | None = None


def code_key(code: str | None) -> str:
    """A stock code as the digits it is compared on: Hong Kong 00700 and 700 are one code."""
    digits = re.sub(r"\D", "", code or "")
    return digits.lstrip("0")


def holdings_by_code(
    holdings: Sequence[HoldingFeedInfo], country: str
) -> dict[str, HoldingFeedInfo]:
    """The holdings listed in `country`, by exchange stock code."""
    out: dict[str, HoldingFeedInfo] = {}
    for holding in holdings:
        if holding.home_country != country:
            continue
        for code in holding.listing_codes:
            key = code_key(code)
            if key and key not in out:
                out[key] = holding
    return out


def _text(html: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


def disclosure_items(
    disclosures: Sequence[Disclosure],
    holding: HoldingFeedInfo,
    source_id: str,
    service: str,
    rules: EventRulesConfig,
    outlet_host: str,
    outlet_name: str,
    language: str,
    region: str,
) -> list[RawItem]:
    return [
        RawItem(
            source_id=source_id,
            url=d.url,
            headline=clean_text(f"{d.company}: {d.title}" if d.company else d.title, 500)
            or d.title,
            published_at=d.published_at,
            summary=clean_text(d.kind, 500),
            language=language,
            outlet_host=outlet_host,
            outlet_name=outlet_name,
            tickers=(holding.ticker,),
            event_type=rules.announcement_event(service, f"{d.title} {d.kind}"),
            region=region,
        )
        for d in disclosures
    ]


# ---------------------------------------------------------------------------
# TDnet (Tokyo)
# ---------------------------------------------------------------------------

_TD_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_TD_CELL = re.compile(
    r'<td class="[^"]*\b(kj(?:Time|Code|Name|Title))\b[^"]*"[^>]*>(.*?)</td>', re.S
)
_TD_LINK = re.compile(r'<a[^>]*href="([^"]+)"', re.S)


def parse_tdnet(html: str, day: dt.date) -> list[Disclosure]:
    out = []
    for row in _TD_ROW.findall(html):
        cells = {name: body for name, body in _TD_CELL.findall(row)}
        link = _TD_LINK.search(cells.get("kjTitle", ""))
        if "kjCode" not in cells or link is None:
            continue
        clock = _text(cells.get("kjTime", ""))
        try:
            stamp = dt.datetime.strptime(f"{day.isoformat()} {clock}", "%Y-%m-%d %H:%M")
            published: dt.datetime | None = stamp.replace(tzinfo=TOKYO).astimezone(dt.UTC)
        except ValueError:
            published = None
        code = _text(cells["kjCode"])
        out.append(
            Disclosure(
                url=urljoin(TDNET_BASE, link.group(1)),
                company=_text(cells.get("kjName", "")),
                title=_text(cells["kjTitle"]),
                kind="TDnet timely disclosure",
                published_at=published,
                # TDnet codes are five digits: the Tokyo code and a check digit.
                code=code[:4] if len(code) == 5 else code,
            )
        )
    return out


async def collect_tdnet(
    http: httpx.AsyncClient,
    source_id: str,
    holdings: Sequence[HoldingFeedInfo],
    rules: EventRulesConfig,
    today: dt.date,
    days: int = 2,
) -> CollectResult:
    result = CollectResult()
    by_code = holdings_by_code(holdings, "JP")
    if not by_code:
        return result
    for back in range(days):
        day = today - dt.timedelta(days=back)
        for page in range(1, TDNET_MAX_PAGES + 1):
            url = TDNET_PAGE.format(page=page, day=day.strftime("%Y%m%d"))
            try:
                document = await fetch_document(
                    http, url, max_bytes=MAX_PAGE_BYTES, headers=default_headers()
                )
            except FetchError as exc:
                # A day with no further page answers 404: the end of that day's list.
                if page == 1 and "HTTP 404" not in str(exc):
                    result.errors.append(f"{day}: {exc}")
                break
            rows = parse_tdnet(document.data.decode("utf-8", errors="replace"), day)
            if not rows:
                break
            for holding in {h.ticker: h for h in by_code.values()}.values():
                mine = [d for d in rows if by_code.get(code_key(d.code)) is holding]
                result.items.extend(
                    disclosure_items(
                        mine, holding, source_id, "tdnet", rules,
                        "release.tdnet.info", "TDnet", "ja", "JP",
                    )
                )  # fmt: skip
    return result


# ---------------------------------------------------------------------------
# HKEXnews (Hong Kong)
# ---------------------------------------------------------------------------


def parse_hkex(body: Any) -> list[Disclosure]:
    """`result` is itself a JSON string holding the rows."""
    raw = body.get("result") if isinstance(body, dict) else None
    rows = json.loads(raw) if isinstance(raw, str) and raw else (raw or [])
    out = []
    for row in rows:
        link = row.get("FILE_LINK")
        title = _text(str(row.get("TITLE") or ""))
        if not link or not title:
            continue
        try:
            stamp = dt.datetime.strptime(str(row.get("DATE_TIME")), "%d/%m/%Y %H:%M")
            published: dt.datetime | None = stamp.replace(tzinfo=HONG_KONG).astimezone(dt.UTC)
        except ValueError:
            published = None
        out.append(
            Disclosure(
                url=urljoin(HKEX_BASE, str(link)),
                company=_text(str(row.get("STOCK_NAME") or "")),
                title=title,
                kind=_text(str(row.get("LONG_TEXT") or row.get("SHORT_TEXT") or "")),
                published_at=published,
                code=str(row.get("STOCK_CODE") or ""),
            )
        )
    return out


async def collect_hkex(
    http: httpx.AsyncClient,
    source_id: str,
    holdings: Sequence[HoldingFeedInfo],
    rules: EventRulesConfig,
    today: dt.date,
    days: int = 2,
) -> CollectResult:
    result = CollectResult()
    by_code = holdings_by_code(holdings, "HK")
    if not by_code:
        return result
    start = today - dt.timedelta(days=days - 1)
    params = {
        "sortDir": "0", "sortByOptions": "DateTime", "category": "0", "market": "SEHK",
        "stockId": "-1", "documentType": "-1", "fromDate": start.strftime("%Y%m%d"),
        "toDate": today.strftime("%Y%m%d"), "title": "", "searchType": "0", "t1code": "-2",
        "t2Gcode": "-2", "t2code": "-2", "rowRange": "3000", "lang": "EN",
    }  # fmt: skip
    try:
        response = await http.get(HKEX_SEARCH, params=params, headers=default_headers())
        response.raise_for_status()
        rows = parse_hkex(response.json())
    except (httpx.HTTPError, ValueError) as exc:
        result.errors.append(f"HKEXnews: {exc}")
        return result
    for holding in {h.ticker: h for h in by_code.values()}.values():
        mine = [d for d in rows if by_code.get(code_key(d.code)) is holding]
        result.items.extend(
            disclosure_items(
                mine, holding, source_id, "hkex", rules,
                "hkexnews.hk", "HKEXnews", "en", "HK",
            )
        )  # fmt: skip
    return result


# ---------------------------------------------------------------------------
# CNINFO (Shanghai, Shenzhen)
# ---------------------------------------------------------------------------


def parse_cninfo(body: Any) -> list[Disclosure]:
    out = []
    for row in (body.get("announcements") if isinstance(body, dict) else None) or []:
        path, title, millis = (
            row.get("adjunctUrl"),
            row.get("announcementTitle"),
            row.get("announcementTime"),
        )
        if not path or not title:
            continue
        published = (
            dt.datetime.fromtimestamp(millis / 1000, tz=dt.UTC)
            if isinstance(millis, int | float)
            else None
        )
        out.append(
            Disclosure(
                url=urljoin(CNINFO_FILES, str(path)),
                company=str(row.get("secName") or ""),
                title=_text(str(title)),
                kind="CNINFO announcement",
                published_at=published,
                code=str(row.get("secCode") or ""),
            )
        )
    return out


async def collect_cninfo(
    http: httpx.AsyncClient,
    source_id: str,
    holdings: Sequence[HoldingFeedInfo],
    rules: EventRulesConfig,
    today: dt.date,
    days: int = 3,
) -> CollectResult:
    result = CollectResult()
    by_code = holdings_by_code(holdings, "CN")
    start = today - dt.timedelta(days=days - 1)
    for holding in {h.ticker: h for h in by_code.values()}.values():
        code = next((c for c in holding.listing_codes if re.fullmatch(r"\d{6}", c)), None)
        if code is None:
            result.errors.append(f"{holding.ticker}: no six-digit CNINFO stock code in its tickers")
            continue
        try:
            lookup = await http.post(
                CNINFO_LOOKUP, data={"keyWord": code, "maxNum": "5"}, headers=default_headers()
            )
            lookup.raise_for_status()
            org = next((r["orgId"] for r in lookup.json() if r.get("code") == code), None)
            if org is None:
                result.errors.append(f"{holding.ticker}: CNINFO does not know code {code}")
                continue
            response = await http.post(
                CNINFO_QUERY,
                data={
                    "pageNum": "1", "pageSize": "50", "column": "szse", "tabName": "fulltext",
                    "stock": f"{code},{org}", "seDate": f"{start.isoformat()}~{today.isoformat()}",
                    "isHLtitle": "true",
                },
                headers=default_headers(),
            )  # fmt: skip
            response.raise_for_status()
            rows = parse_cninfo(response.json())
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            result.errors.append(f"{holding.ticker}: {exc}")
            continue
        result.items.extend(
            disclosure_items(
                rows, holding, source_id, "cninfo", rules, "cninfo.com.cn", "CNINFO", "zh", "CN"
            )
        )
    return result


# ---------------------------------------------------------------------------
# DART (Korea)
# ---------------------------------------------------------------------------


def parse_dart(body: Any) -> tuple[list[Disclosure], int]:
    """Rows and the page count. Status "000" is success; "013" is an empty result."""
    if not isinstance(body, dict):
        raise ValueError("DART sent no JSON object")
    status = str(body.get("status"))
    if status == "013":
        return [], 0
    if status != "000":
        raise ValueError(f"DART status {status}: {body.get('message')}")
    out = []
    for row in body.get("list") or []:
        number, name = row.get("rcept_no"), row.get("report_nm")
        if not number or not name:
            continue
        try:
            day = dt.datetime.strptime(str(row.get("rcept_dt")), "%Y%m%d")
            # DART gives the receipt date only: noon Seoul time stands for it.
            published: dt.datetime | None = day.replace(hour=12, tzinfo=SEOUL).astimezone(dt.UTC)
        except ValueError:
            published = None
        out.append(
            Disclosure(
                url=DART_VIEW.format(rcept_no=number),
                company=str(row.get("corp_name") or ""),
                title=_text(str(name)),
                kind=f"DART filing by {row.get('flr_nm') or 'the company'}",
                published_at=published,
                code=str(row.get("stock_code") or ""),
            )
        )
    return out, int(body.get("total_page") or 1)


async def collect_dart(
    http: httpx.AsyncClient,
    api_key: str,
    source_id: str,
    holdings: Sequence[HoldingFeedInfo],
    rules: EventRulesConfig,
    today: dt.date,
    days: int = 2,
) -> CollectResult:
    result = CollectResult()
    by_code = holdings_by_code(holdings, "KR")
    if not by_code:
        return result
    start = today - dt.timedelta(days=days - 1)
    rows: list[Disclosure] = []
    page, pages = 1, 1
    try:
        while page <= min(pages, DART_MAX_PAGES):
            response = await http.get(
                DART_LIST,
                params={
                    "crtfc_key": api_key, "bgn_de": start.strftime("%Y%m%d"),
                    "end_de": today.strftime("%Y%m%d"), "page_no": page, "page_count": 100,
                },
                headers=default_headers(),
            )  # fmt: skip
            response.raise_for_status()
            batch, pages = parse_dart(response.json())
            rows.extend(batch)
            page += 1
    except (httpx.HTTPError, ValueError) as exc:
        # The key is a query parameter: never echo the URL, only the failure class.
        result.errors.append(f"DART: {type(exc).__name__}")
    if pages > DART_MAX_PAGES:
        result.errors.append(f"DART: read {DART_MAX_PAGES} of {pages} pages")
    for holding in {h.ticker: h for h in by_code.values()}.values():
        mine = [d for d in rows if by_code.get(code_key(d.code)) is holding]
        result.items.extend(
            disclosure_items(
                mine, holding, source_id, "dart", rules, "dart.fss.or.kr", "DART", "ko", "KR"
            )
        )
    return result


# ---------------------------------------------------------------------------
# The Gazette (UK)
# ---------------------------------------------------------------------------

_COMPANY_SUFFIX = re.compile(
    r"\b(plc|ltd|limited|llp|lp|group|holdings|holding|company|co|the)\b", re.I
)


def name_key(name: str) -> str:
    """A company name with legal-form words and punctuation removed, for a whole-name match."""
    return " ".join(_COMPANY_SUFFIX.sub(" ", re.sub(r"[^\w\s]", " ", name.lower())).split())


async def collect_gazette(
    http: httpx.AsyncClient,
    source_id: str,
    holdings: Sequence[HoldingFeedInfo],
    rules: EventRulesConfig,
    today: dt.date,
    days: int = 7,
) -> CollectResult:
    result = CollectResult()
    start = today - dt.timedelta(days=days)
    for holding in holdings:
        if holding.home_country != "GB" or not holding.names:
            continue
        wanted = name_key(holding.names[0])
        if len(wanted) < 4:
            continue
        url = (
            f"{GAZETTE_FEED}?text={quote(chr(34) + holding.names[0] + chr(34))}"
            f"&start-publish-date={start.isoformat()}&end-publish-date={today.isoformat()}"
        )
        try:
            document = await fetch_document(
                http, url, max_bytes=MAX_PAGE_BYTES, headers=default_headers()
            )
        except FetchError as exc:
            result.errors.append(f"{holding.ticker}: {exc}")
            continue
        for item in parse_feed(document.data, source_id, tickers=(holding.ticker,), feed_url=url):
            if wanted not in name_key(f"{item.headline} {item.summary or ''}"):
                continue
            result.items.append(
                RawItem(
                    source_id=item.source_id,
                    url=item.url,
                    headline=item.headline,
                    published_at=item.published_at,
                    summary=item.summary,
                    language="en",
                    outlet_host="thegazette.co.uk",
                    outlet_name="The Gazette",
                    tickers=(holding.ticker,),
                    event_type=rules.announcement_event(
                        "gazette", f"{item.headline} {item.summary or ''}"
                    ),
                    region="GB",
                )
            )
    return result


# ---------------------------------------------------------------------------
# EDINET and Companies House (already stored by the disclosure jobs)
# ---------------------------------------------------------------------------

_CH_HOST = "find-and-update.company-information.service.gov.uk"
# regulator -> (language, region, outlet host, outlet name)
_FILING_SERVICES = {
    "edinet": ("ja", "JP", "disclosure2.edinet-fsa.go.jp", "EDINET"),
    "companies_house": ("en", "GB", _CH_HOST, "Companies House"),
}


async def collect_stored_filings(
    pool: asyncpg.Pool,
    source_id: str,
    holdings: Sequence[HoldingFeedInfo],
    rules: EventRulesConfig,
    since: dt.datetime,
) -> CollectResult:
    """Filings the EDINET and Companies House jobs recorded since `since`, as items.

    The ticker is the prefix of the record key (`<ticker>:<document>`); the filing's own
    description is the headline, so what the app shows is what the regulator published."""
    result = CollectResult()
    known = {h.ticker: h for h in holdings}
    rows = await pool.fetch(
        "select r.record_key, d.regulator, d.stable_url, d.document_type,"
        " coalesce(v.filed_at, v.known_at) as filed_at, e.quote"
        " from public.source_record_evidence r"
        " join public.source_document_version v on v.id = r.document_version_id"
        " join public.source_document d on d.id = v.document_id"
        " left join public.source_evidence e on e.id = r.evidence_id"
        " where r.domain_table = 'holding_filing_event' and r.known_at >= $1"
        " and d.regulator = any($2::text[]) order by r.known_at desc limit 500",
        since,
        list(_FILING_SERVICES),
    )
    for row in rows:
        ticker = str(row["record_key"]).split(":", 1)[0]
        holding = known.get(ticker)
        if holding is None:
            continue
        language, region, host, outlet = _FILING_SERVICES[row["regulator"]]
        title = clean_text(row["quote"], 400) or str(row["document_type"])
        result.items.extend(
            disclosure_items(
                [
                    Disclosure(
                        url=row["stable_url"],
                        company=holding.names[0] if holding.names else ticker,
                        title=title,
                        kind=f"{outlet} filing ({row['document_type']})",
                        published_at=row["filed_at"],
                    )
                ],
                holding, source_id, "filings", rules, host, outlet, language, region,
            )
        )  # fmt: skip
    return result
