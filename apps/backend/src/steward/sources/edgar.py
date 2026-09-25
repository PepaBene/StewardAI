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

    async def cik_for_ticker(self, ticker: str) -> str | None:
        if self._tickers is None:
            data: dict[str, Any] = (await self._get(TICKERS_URL)).json()
            self._tickers = {
                str(row["ticker"]).upper(): pad_cik(row["cik_str"]) for row in data.values()
            }
        return self._tickers.get(ticker.upper())

    async def filings(self, cik: str) -> list[Filing]:
        data = (await self._get(SUBMISSIONS_URL.format(cik=pad_cik(cik)))).json()
        return parse_submissions(pad_cik(cik), data)

    async def company_facts(self, cik: str) -> dict[str, Any]:
        """Every XBRL fact the issuer has filed (10-Q, 10-K, 20-F, ...), by taxonomy and
        concept. A 404 (no XBRL filings) is an empty result, not an error."""
        try:
            response = await self._get(COMPANY_FACTS_URL.format(cik=pad_cik(cik)))
        except EdgarError as exc:
            if "HTTP 404" in str(exc):
                return {}
            raise
        data: dict[str, Any] = response.json()
        return data

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
            for f in await self.filings(cik)
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
    """Rows of a filing index page as converted text: '2 | EX-99.1 | name.htm | EX-99.1 | 1234'."""
    documents = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.split(" | ")]
        if len(cells) < 4 or not cells[0].isdigit():
            continue
        name = next(
            (c.split()[0] for c in cells if re.search(r"\.(htm|html|txt|pdf)\b", c, re.I)), None
        )
        doc_type = cells[3] if len(cells) >= 4 else ""
        if name:
            documents.append((doc_type.upper(), name))
    return documents


def pick_press_release(documents: list[tuple[str, str]]) -> str | None:
    """Exhibit 99.1 if present, else the first EX-99.x, as HTML before PDF."""
    exhibits = [(t, n) for t, n in documents if t.startswith("EX-99")]
    exhibits.sort(key=lambda d: (d[0] != "EX-99.1", d[1].lower().endswith(".pdf"), d[0]))
    return exhibits[0][1] if exhibits else None
