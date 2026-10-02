"""SEC EDGAR adapter: CIK lookup and the earnings releases US and foreign issuers file.

- US issuers furnish results on Form 8-K, item 2.02 (Results of Operations), with the press
  release as exhibit 99.1.
- Foreign private issuers (e.g. ASML, SAP) furnish them on Form 6-K, usually as exhibit 99.1.

No key is needed, but the SEC requires a descriptive User-Agent with a contact address
(SEC_USER_AGENT) and at most 10 requests per second.
"""

import asyncio
import datetime as dt
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from steward.documents.text import html_to_text

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
# Older filings: `filings.recent` holds at least a year or 1,000 filings (whichever is more);
# the rest are in the JSON pages listed in `filings.files` (name, filingFrom, filingTo).
SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{name}"

MIN_INTERVAL = 0.15  # seconds between requests (< 10/s)


class EdgarError(Exception):
    pass


@dataclass(frozen=True)
class Filing:
    cik: str
    form: str
    accession: str
    filing_date: dt.date
    report_date: dt.date | None
    primary_document: str
    items: str
    accepted_at: dt.datetime | None = None

    @property
    def folder(self) -> str:
        return self.accession.replace("-", "")

    def url(self, name: str) -> str:
        return ARCHIVE_URL.format(cik=int(self.cik), folder=self.folder, name=name)

    @property
    def index_url(self) -> str:
        return self.url(f"{self.accession}-index.html")


def pad_cik(cik: str | int) -> str:
    return str(int(cik)).zfill(10)


class EdgarClient:
    def __init__(self, http: httpx.AsyncClient, user_agent: str) -> None:
        if "@" not in user_agent:
            raise EdgarError("SEC_USER_AGENT must include a contact email address")
        self._http = http
        self._headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
        self._last = 0.0
        self._lock = asyncio.Lock()
        self._tickers: dict[str, str] | None = None

    @property
    def headers(self) -> dict[str, str]:
        return self._headers

    async def _get(self, url: str) -> httpx.Response:
        async with self._lock:
            wait = MIN_INTERVAL - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
        response = await self._http.get(url, headers=self._headers, follow_redirects=True)
        if response.status_code != 200:
            raise EdgarError(f"EDGAR {url} returned HTTP {response.status_code}")
        return response

    @staticmethod
    def _json(response: httpx.Response, url: str) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise EdgarError(f"EDGAR {url} sent no JSON") from exc

    async def _get_json(self, url: str) -> Any:
        body = self._json(await self._get(url), url)
        if not isinstance(body, dict):
            raise EdgarError(f"EDGAR {url} sent an unexpected JSON shape")
        return body

    async def cik_for_ticker(self, ticker: str) -> str | None:
        if self._tickers is None:
            data: dict[str, Any] = await self._get_json(TICKERS_URL)
            self._tickers = {
                str(row["ticker"]).upper(): pad_cik(row["cik_str"]) for row in data.values()
            }
        return self._tickers.get(ticker.upper())

    async def filings(self, cik: str, since: dt.date | None = None) -> list[Filing]:
        """The issuer's filings. `recent` covers at least the last year (or 1,000 filings);
        with `since` earlier than that, the older pages covering it are read too."""
        data = await self._get_json(SUBMISSIONS_URL.format(cik=pad_cik(cik)))
        filings = parse_submissions(pad_cik(cik), data)
        if since is None:
            return filings
        oldest = min((f.filing_date for f in filings), default=None)
        if oldest is not None and oldest <= since:
            return filings
        for page in older_pages(data, since):
            body = await self._get_json(SUBMISSIONS_PAGE_URL.format(name=page))
            filings += parse_submissions(pad_cik(cik), {"filings": {"recent": body}})
        return filings

    async def exchanges(self, cik: str) -> list[str]:
        """Exchanges the issuer's securities trade on, as EDGAR lists them ("Nasdaq")."""
        data = (await self._get(SUBMISSIONS_URL.format(cik=pad_cik(cik)))).json()
        return [str(e) for e in data.get("exchanges") or [] if e]

    async def company_facts(self, cik: str) -> dict[str, Any]:
        """Every XBRL fact the issuer has filed (10-Q, 10-K, 20-F, ...), by taxonomy and
        concept. A 404 (no XBRL filings) is an empty result, not an error."""
        try:
            response = await self._get(COMPANY_FACTS_URL.format(cik=pad_cik(cik)))
        except EdgarError as exc:
            if "HTTP 404" in str(exc):
                return {}
            raise
        data: dict[str, Any] = self._json(response, COMPANY_FACTS_URL)
        return data

    async def filing_files(self, filing: Filing) -> list[str]:
        """Names of every file in the filing's archive folder (its `index.json`)."""
        data: dict[str, Any] = await self._get_json(filing.url("index.json"))
        items = (data.get("directory") or {}).get("item") or []
        return [str(i["name"]) for i in items if isinstance(i, dict) and i.get("name")]

    async def document(self, url: str, max_bytes: int = 64 * 1024 * 1024) -> bytes:
        """A filing document's bytes (an XBRL instance is 3-11 MB)."""
        content = (await self._get(url)).content
        if len(content) > max_bytes:
            raise EdgarError(f"EDGAR {url} is {len(content)} bytes, over the {max_bytes} limit")
        return content

    async def exhibit_documents(self, filing: Filing) -> list[tuple[str, str]]:
        """(type, file name) of every document in the filing, from its index page."""
        text = html_to_text((await self._get(filing.index_url)).text)
        return parse_index(text)

    async def find_earnings_release(
        self, cik: str, report_date: dt.date, window_days: int = 7
    ) -> tuple[Filing, str] | None:
        """The results filing on or shortly after `report_date` and its press-release URL."""
        candidates = [
            f
            for f in await self.filings(cik, since=report_date - dt.timedelta(days=1))
            if report_date - dt.timedelta(days=1)
            <= f.filing_date
            <= report_date + dt.timedelta(days=window_days)
            and ((f.form in ("8-K", "8-K/A") and "2.02" in f.items) or f.form in ("6-K", "6-K/A"))
        ]
        candidates.sort(key=lambda f: (f.filing_date, f.form.endswith("/A")))
        for filing in candidates:
            documents = await self.exhibit_documents(filing)
            name = pick_press_release(documents)
            if name is not None:
                return filing, filing.url(name)
        return None


def parse_submissions(cik: str, data: dict[str, Any]) -> list[Filing]:
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    filings = []
    for i, form in enumerate(forms):
        try:
            report = recent.get("reportDate", [""] * len(forms))[i]
            accepted = (recent.get("acceptanceDateTime") or [""] * len(forms))[i]
            filings.append(
                Filing(
                    cik=cik,
                    form=form,
                    accession=recent["accessionNumber"][i],
                    filing_date=dt.date.fromisoformat(recent["filingDate"][i]),
                    report_date=dt.date.fromisoformat(report) if report else None,
                    primary_document=recent["primaryDocument"][i],
                    items=(recent.get("items") or [""] * len(forms))[i] or "",
                    accepted_at=_accepted(accepted),
                )
            )
        except (KeyError, IndexError, ValueError):
            continue
    return filings


def older_pages(data: dict[str, Any], since: dt.date) -> list[str]:
    """Names of the `filings.files` pages holding filings on or after `since`."""
    names = []
    for page in data.get("filings", {}).get("files") or []:
        try:
            last = dt.date.fromisoformat(str(page.get("filingTo")))
        except ValueError:
            last = None
        if page.get("name") and (last is None or last >= since):
            names.append(str(page["name"]))
    return names


def _accepted(value: str) -> dt.datetime | None:
    """EDGAR's acceptanceDateTime ("2026-07-31T16:30:21.000Z"), as an aware datetime."""
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def parse_index(text: str) -> list[tuple[str, str]]:
    """Rows of a filing index page as converted text: '2 | EX-99.1 | name.htm | EX-99.1 | 1234'.

    Columns are Seq | Description | Document | Type | Size, but the text conversion drops
    empty cells, so a blank Description shifts the others left. The Type is therefore read
    as the cell right after the Document cell, not by position."""
    documents = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.split(" | ")]
        if len(cells) < 3 or not cells[0].isdigit():
            continue
        at = next(
            (i for i, c in enumerate(cells) if re.search(r"\.(htm|html|txt|pdf)\b", c, re.I)),
            None,
        )
        if at is None or at == 0:
            continue
        name = cells[at].split()[0]
        after = cells[at + 1] if at + 1 < len(cells) else ""
        # A blank Type leaves the Size (digits) next to the Document.
        doc_type = "" if re.fullmatch(r"[\d,]*", after) else after
        documents.append((doc_type.upper(), name))
    return documents


PERIODIC_FORMS = ("10-K", "10-Q", "20-F", "40-F", "10-K/A", "20-F/A")
ANNUAL_FORMS = ("10-K", "20-F", "40-F", "10-K/A", "20-F/A", "40-F/A")
QUARTER_FORMS = ("10-Q", "10-Q/A")


def is_amendment(form: str) -> bool:
    return form.upper().endswith("/A")


def _sort_key(f: Filing) -> tuple[dt.date, str]:
    return (f.filing_date, f.accession)


def latest_periodic(
    filings: list[Filing], full_amendments: frozenset[str] | set[str] = frozenset()
) -> tuple[Filing | None, Filing | None]:
    """The latest annual report (10-K / 20-F / 40-F) and the latest 10-Q filed after it.

    An amendment (10-K/A) is usually Part III only: it carries the cover page, not the
    financial statements, so taking it as "the annual report" would hide the original from
    every parser (B28). An amendment counts only when its accession is in `full_amendments`
    (the caller read its instance and found the full financial statements, i.e. a
    restatement); otherwise the latest original wins."""
    ordered = sorted(filings, key=_sort_key, reverse=True)

    def usable(f: Filing) -> bool:
        return not is_amendment(f.form) or f.accession in full_amendments

    annual = next((f for f in ordered if f.form in ANNUAL_FORMS and usable(f)), None)
    quarter = next(
        (
            f
            for f in ordered
            if f.form in QUARTER_FORMS
            and usable(f)
            and (annual is None or _sort_key(f) > _sort_key(annual))
        ),
        None,
    )
    return annual, quarter


def amendments_to_check(filings: list[Filing]) -> list[Filing]:
    """Periodic-report amendments filed after the latest original of their kind: the ones
    whose instance must be read to know whether they restate the statements."""
    ordered = sorted(filings, key=_sort_key, reverse=True)
    out: list[Filing] = []
    for forms in (ANNUAL_FORMS, QUARTER_FORMS):
        original = next((f for f in ordered if f.form in forms and not is_amendment(f.form)), None)
        out += [
            f
            for f in ordered
            if f.form in forms
            and is_amendment(f.form)
            and (original is None or _sort_key(f) > _sort_key(original))
        ]
    return out


def amended_filing(filings: list[Filing], amendment: Filing) -> Filing | None:
    """The filing an amendment amends: the latest earlier original of the base form for the
    same report date (else the latest earlier original of that form)."""
    base = amendment.form.upper().removesuffix("/A")
    earlier = [f for f in filings if f.form.upper() == base and _sort_key(f) < _sort_key(amendment)]
    same_period = [f for f in earlier if f.report_date and f.report_date == amendment.report_date]
    pool = same_period or earlier
    return max(pool, key=_sort_key) if pool else None


def _after(a: Filing, b: Filing) -> bool:
    return (a.filing_date, a.accession) > (b.filing_date, b.accession)


def xbrl_documents(filing: Filing, files: list[str]) -> tuple[str | None, str | None]:
    """The filing's XBRL instance and label linkbase names. Inline XBRL filings carry the
    SEC's extracted instance `<primary document>_htm.xml`; older ones a plain `.xml`. Labels
    come from `_lab.xml`, or from the company schema (`.xsd`) that embeds the linkbases."""
    names = set(files)
    base = filing.primary_document.rsplit(".", 1)[0]
    instance: str | None = f"{base}_htm.xml" if f"{base}_htm.xml" in names else None
    if instance is None:
        instance = next((n for n in files if n.endswith("_htm.xml")), None)
    if instance is None:
        linkbases = ("_cal.xml", "_def.xml", "_lab.xml", "_pre.xml")
        instance = next(
            (
                n
                for n in files
                if n.endswith(".xml") and not n.endswith(linkbases) and n != "FilingSummary.xml"
            ),
            None,
        )
    # Some filing agents embed the linkbases in the company schema instead of `_lab.xml`.
    label = next((n for n in files if n.endswith("_lab.xml")), None) or next(
        (n for n in files if n.endswith(".xsd")), None
    )
    return instance, label


def pick_press_release(documents: list[tuple[str, str]]) -> str | None:
    """Exhibit 99.1 if present, else the first EX-99.x, as HTML before PDF."""
    exhibits = [(t, n) for t, n in documents if t.startswith("EX-99")]
    exhibits.sort(key=lambda d: (d[0] != "EX-99.1", d[1].lower().endswith(".pdf"), d[0]))
    return exhibits[0][1] if exhibits else None


# EDGAR's exchange names -> operating MIC.
EXCHANGE_MIC: dict[str, str] = {
    "Nasdaq": "XNAS",
    "NYSE": "XNYS",
    "NYSE American": "XASE",
    "NYSE Arca": "ARCX",
    "CBOE": "BATS",
    "Cboe BZX": "BATS",
}
