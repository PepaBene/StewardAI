"""Issuer investor-relations releases: fetch, version, parse, and compare with the actual.

Plan D4.10. A holding's IR pages are registered in `issuer_ir_source`. The `ir_releases` job
fetches each page, keeps one `issuer_ir_release` row per distinct content hash (a changed page
is a new version pointing at the previous one; an unchanged page only updates the source's
`last_checked_at`), stores the document as a cited `source_document_version` and parses what it
can read into `forward_observation` rows (company guidance, published consensus with its
analyst count) each citing the sentence or table row it came from.

Formats: PDF (`pypdf` text), XLSX (the workbook's XML read with the standard library), HTML
and plain text (`steward.documents.text`). Layouts handled, because they are what IR pages
publish:

- a table with a header row naming a metric, a period and a figure (`Metric | Period | Low |
  High | Mean | Analysts | Currency`; any subset, any order, synonyms below);
- guidance and capital-return sentences: "expects revenue of between $10.5 billion and $11.0
  billion for the fourth quarter of fiscal 2026", "diluted EPS of $2.10 to $2.20 for FY2026",
  "authorized a $5 billion share repurchase".

What the parsers do NOT do: infer a metric the text does not name, read figures from images or
scanned PDFs, or interpret footnotes. A release that yields nothing is stored as a version with
`forward_rows: 0` so the gap is visible, never guessed. Page layouts change; every parser is
tested on fixtures in the documented layout only.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import ipaddress
import json
import re
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import asyncpg
import httpx

from steward import __version__
from steward.documents.text import html_to_text
from steward.free_sources import ForwardValue, store_forward_value
from steward.jobs.runner import JobContext
from steward.sources.documents import link_public_document
from steward.sources.xml_guard import UnsafeXml, reject_dtd

PARSER_VERSION = "ir-1"
MAX_BYTES = 25 * 1024 * 1024
KIND_BY_RELEASE = {
    "guidance": "company_guidance",
    "consensus": "published_analyst_consensus",
    "segment_consensus": "published_analyst_consensus",
}


class IrError(Exception):
    pass


# --------------------------------------------------------------------------------- fetching


def assert_fetchable(url: str) -> None:
    """HTTPS only, and never a literal private / loopback address or an internal hostname: the
    URL is owner-entered but the job runs inside the deployment."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        raise IrError("IR sources must be https URLs")
    if host in {"localhost"} or host.endswith((".local", ".internal", ".localhost")):
        raise IrError("IR source host is not a public address")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise IrError("IR source host is not a public address")


@dataclass(frozen=True)
class FetchedDocument:
    url: str
    body: bytes
    content_type: str
    last_modified: dt.datetime | None
    sha256: str


async def fetch_document(
    http: httpx.AsyncClient, url: str, max_bytes: int = MAX_BYTES
) -> FetchedDocument:
    assert_fetchable(url)
    async with http.stream("GET", url, follow_redirects=True) as response:
        if response.status_code != 200:
            raise IrError(f"{url} returned HTTP {response.status_code}")
        assert_fetchable(str(response.url))
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > max_bytes:
                raise IrError(f"{url} is larger than {max_bytes} bytes")
            chunks.append(chunk)
        modified = None
        header = response.headers.get("last-modified")
        if header:
            try:
                from email.utils import parsedate_to_datetime

                modified = parsedate_to_datetime(header)
            except (TypeError, ValueError):
                modified = None
        body = b"".join(chunks)
        return FetchedDocument(
            url=url,
            body=body,
            content_type=response.headers.get("content-type", "").split(";")[0].strip().lower(),
            last_modified=modified,
            sha256=hashlib.sha256(body).hexdigest(),
        )


def detect_format(content_type: str, body: bytes) -> str:
    """ "pdf", "xlsx", "html" or "text", by magic bytes first (servers mislabel downloads)."""
    if body.startswith(b"%PDF"):
        return "pdf"
    if body.startswith(b"PK\x03\x04"):
        return "xlsx"
    head = body[:2048].lstrip().lower()
    if head.startswith((b"<!doctype html", b"<html")) or b"<table" in body[:200_000].lower():
        return "html"
    if "html" in content_type:
        return "html"
    return "text"


# --------------------------------------------------------------------------------- readers

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _column_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref)
    index = 0
    for char in letters.group(0) if letters else "A":
        index = index * 26 + (ord(char) - 64)
    return index - 1


def xlsx_rows(body: bytes) -> list[list[str]]:
    """Every row of every sheet of a workbook as strings. Shared strings, inline strings and
    numbers are read; formulas yield their cached value; styles and dates are not interpreted
    (a date cell comes back as its serial number)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile as exc:
        raise IrError("not a valid xlsx file") from exc
    if sum(i.file_size for i in archive.infolist()) > 200 * 1024 * 1024:
        raise IrError("xlsx expands to an unreasonable size")

    def parse(name: str) -> ET.Element | None:
        try:
            data = archive.read(name)
        except KeyError:
            return None
        try:
            reject_dtd(data)
            return ET.fromstring(data)  # noqa: S314 - DTDs refused above
        except (UnsafeXml, ET.ParseError) as exc:
            raise IrError(f"unsafe or malformed xlsx part {name}") from exc

    shared: list[str] = []
    strings = parse("xl/sharedStrings.xml")
    if strings is not None:
        for item in strings.iter(f"{_NS}si"):
            shared.append("".join(t.text or "" for t in item.iter(f"{_NS}t")))
    rows: list[list[str]] = []
    sheets = sorted(
        n for n in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)
    )
    for name in sheets:
        sheet = parse(name)
        if sheet is None:
            continue
        for row in sheet.iter(f"{_NS}row"):
            cells: dict[int, str] = {}
            for cell in row.iter(f"{_NS}c"):
                kind = cell.get("t")
                value = cell.findtext(f"{_NS}v")
                if kind == "s" and value is not None:
                    text = shared[int(value)] if int(value) < len(shared) else ""
                elif kind == "inlineStr":
                    text = "".join(t.text or "" for t in cell.iter(f"{_NS}t"))
                else:
                    text = value or ""
                if text.strip():
                    cells[_column_index(cell.get("r", "A1"))] = text.strip()
            if cells:
                width = max(cells) + 1
                rows.append([cells.get(i, "") for i in range(width)])
    return rows


def pdf_lines(body: bytes) -> list[str]:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io.BytesIO(body))
        pages = [page.extract_text() or "" for page in reader.pages[:200]]
    except (PyPdfError, ValueError, KeyError, OSError) as exc:
        raise IrError(f"unreadable PDF: {exc}") from exc
    return [line.strip() for page in pages for line in page.splitlines() if line.strip()]


def document_lines(fetched_body: bytes, content_type: str = "") -> list[str]:
    """Text lines of a document in any supported format; table rows are `a | b | c`."""
    fmt = detect_format(content_type, fetched_body)
    if fmt == "pdf":
        return pdf_lines(fetched_body)
    if fmt == "xlsx":
        return [" | ".join(cell for cell in row) for row in xlsx_rows(fetched_body)]
    text = fetched_body.decode("utf-8", errors="replace")
    if fmt == "html":
        text = html_to_text(text)
    return [line.strip() for line in text.splitlines() if line.strip()]


# --------------------------------------------------------------------------------- parsing

METRICS: tuple[tuple[str, str], ...] = (
    ("free cash flow", "free_cash_flow"),
    ("capital expenditures", "capex"),
    ("capital expenditure", "capex"),
    ("capex", "capex"),
    ("gross margin", "gross_margin"),
    ("operating margin", "operating_margin"),
    ("operating income", "operating_income"),
    ("net income", "net_income"),
    ("diluted earnings per share", "eps_diluted"),
    ("earnings per share", "eps_diluted"),
    ("diluted eps", "eps_diluted"),
    ("eps", "eps_diluted"),
    ("total revenues", "revenue"),
    ("total revenue", "revenue"),
    ("net sales", "revenue"),
    ("revenues", "revenue"),
    ("revenue", "revenue"),
    ("sales", "revenue"),
)
SCALES = {
    "billion": Decimal(10) ** 9,
    "bn": Decimal(10) ** 9,
    "b": Decimal(10) ** 9,
    "million": Decimal(10) ** 6,
    "mm": Decimal(10) ** 6,
    "m": Decimal(10) ** 6,
    "thousand": Decimal(10) ** 3,
    "k": Decimal(10) ** 3,
}
CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}
CURRENCY_CODES = {"USD", "EUR", "GBP", "CHF", "DKK", "SEK", "NOK", "JPY", "CZK", "CAD", "AUD"}
_AMOUNT = re.compile(
    r"(?P<cur>US\$|[$€£¥]|\b[A-Z]{3}\b)?\s*(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(?P<scale>billion|million|thousand|bn|mm|k|b|m)?\b(?P<pct>\s*%)?",
    re.IGNORECASE,
)
_RANGE = re.compile(
    r"(?:between|from|in the range of|ranging from)?\s*(?P<a>(?:US\$|[$€£¥]|[A-Z]{3}\s*)?"
    r"\d[\d,]*(?:\.\d+)?\s*(?:billion|million|thousand|bn|mm|k|b|m)?\s*%?)\s*"
    r"(?:and|to|-|\u2013|\u2014)\s*(?P<b>(?:US\$|[$€£¥]|[A-Z]{3}\s*)?\d[\d,]*(?:\.\d+)?"
    r"\s*(?:billion|million|thousand|bn|mm|k|b|m)?\s*%?)",
    re.IGNORECASE,
)
_QUARTER_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4}


@dataclass(frozen=True)
class ParsedForward:
    kind: str
    metric: str
    fiscal_period: str
    value: Decimal | None
    low: Decimal | None
    high: Decimal | None
    currency: str | None
    analyst_count: int | None
    basis: str | None
    passage: str


@dataclass
class ParsedRelease:
    forward: list[ParsedForward] = field(default_factory=list)
    capital_return: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def fiscal_period_of(text: str) -> str | None:
    """ "FY2026Q4" / "FY2026" from the wording around a figure."""
    lowered = text.lower()
    match = re.search(r"\b(?:fy)?\s*(20\d{2}|\d{2})\s*-?\s*q([1-4])\b", lowered)
    if match and (lowered[: match.start(1)].rstrip().endswith("fy") or len(match.group(1)) == 4):
        year = match.group(1)
        return f"FY{year if len(year) == 4 else '20' + year}Q{match.group(2)}"
    match = re.search(
        r"\b(first|second|third|fourth)\s+quarter(?:\s+of)?(?:\s+fiscal)?(?:\s+year)?\s+"
        r"(?:fy\s*)?(20\d{2})\b",
        lowered,
    )
    if match:
        return f"FY{match.group(2)}Q{_QUARTER_WORDS[match.group(1)]}"
    match = re.search(r"\bq([1-4])\s*(?:fy)?\s*'?(20\d{2}|\d{2})\b", lowered)
    if match:
        year = match.group(2)
        return f"FY{year if len(year) == 4 else '20' + year}Q{match.group(1)}"
    match = re.search(
        r"\b(?:fy|fiscal(?:\s+year)?|full[- ]year(?:\s+fiscal)?)\s*'?(20\d{2}|\d{2})\b", lowered
    )
    if match:
        year = match.group(1)
        return f"FY{year if len(year) == 4 else '20' + year}"
    match = re.search(r"\b(20\d{2})\s+(?:full[- ]year|fiscal year)\b", lowered)
    if match:
        return f"FY{match.group(1)}"
    return None


def _metric_match(text: str) -> tuple[str, int] | None:
    """(metric, offset just after the phrase) of the first listed metric named in `text`."""
    lowered = text.lower()
    for phrase, name in METRICS:
        found = re.search(rf"\b{re.escape(phrase)}\b", lowered)
        if found:
            return name, found.end()
    return None


def _metric_of(text: str) -> str | None:
    match = _metric_match(text)
    return match[0] if match else None


def _basis_of(text: str) -> str | None:
    lowered = text.lower()
    if "non-gaap" in lowered or "adjusted" in lowered:
        return "non-GAAP"
    if "gaap" in lowered:
        return "GAAP"
    return None


def parse_amount(text: str) -> tuple[Decimal | None, str | None, bool]:
    """(value, currency, is_percent) of the first amount in `text`, scaled to units."""
    match = _AMOUNT.search(text)
    if not match:
        return None, None, False
    try:
        number = Decimal(match.group("num").replace(",", ""))
    except InvalidOperation:
        return None, None, False
    cur = (match.group("cur") or "").strip()
    currency = CURRENCY_SYMBOLS.get(cur) or ("USD" if cur.upper() == "US$" else None)
    if currency is None and cur.upper() in CURRENCY_CODES:
        currency = cur.upper()
    percent = bool(match.group("pct"))
    scale = SCALES.get((match.group("scale") or "").lower())
    if scale is not None and not percent:
        number *= scale
    if percent:
        number /= 100
    return number, currency, percent


def _range_of(text: str) -> tuple[Decimal | None, Decimal | None, str | None] | None:
    match = _RANGE.search(text)
    if not match:
        return None
    low, currency, pct_a = parse_amount(match.group("a"))
    high, currency_b, pct_b = parse_amount(match.group("b"))
    if low is None or high is None:
        return None
    # "$10.5 and $11.0 billion": the unit written once at the end applies to both ends.
    scale_b = re.search(
        r"(billion|million|thousand|bn|mm|k|b|m)\s*$", match.group("b").strip(), re.I
    )
    scale_a = re.search(
        r"(billion|million|thousand|bn|mm|k|b|m)\s*$", match.group("a").strip(), re.I
    )
    if scale_b and not scale_a and not (pct_a or pct_b):
        low *= SCALES[scale_b.group(1).lower()]
    if low > high:
        low, high = high, low
    return low, high, currency or currency_b


_HEADER_WORDS = {
    "metric": ("metric", "item", "measure", "line item"),
    "period": ("period", "fiscal period", "quarter", "year"),
    "low": ("low", "minimum", "lower"),
    "high": ("high", "maximum", "upper"),
    "value": ("mean", "consensus", "average", "median", "estimate", "guidance", "value"),
    "analysts": ("analysts", "# of analysts", "number of analysts", "estimates", "no. of analysts"),
    "currency": ("currency", "ccy"),
    "basis": ("basis", "gaap"),
}


def _header_map(cells: list[str]) -> dict[str, int] | None:
    mapping: dict[str, int] = {}
    for index, cell in enumerate(cells):
        lowered = cell.strip().lower()
        for key, words in _HEADER_WORDS.items():
            if key not in mapping and lowered in words:
                mapping[key] = index
    if (
        "metric" in mapping
        and "period" in mapping
        and ("value" in mapping or "low" in mapping or "high" in mapping)
    ):
        return mapping
    return None


def _cell(cells: list[str], mapping: dict[str, int], key: str) -> str:
    index = mapping.get(key)
    return cells[index].strip() if index is not None and index < len(cells) else ""


def _number(text: str) -> Decimal | None:
    value, _, _ = parse_amount(text) if text else (None, None, False)
    return value


def parse_release(
    lines: list[str], release_type: str, *, default_basis: str | None = None
) -> ParsedRelease:
    """Forward values and capital-return facts from the lines of a release."""
    kind = KIND_BY_RELEASE.get(release_type)
    result = ParsedRelease()
    mapping: dict[str, int] | None = None
    for line in lines:
        if " | " in line:
            cells = [c.strip() for c in line.split(" | ")]
            header = _header_map(cells)
            if header is not None:
                mapping = header
                continue
            if mapping is None or kind is None:
                continue
            metric = _metric_of(_cell(cells, mapping, "metric"))
            period = fiscal_period_of(_cell(cells, mapping, "period")) or fiscal_period_of(
                "fy " + _cell(cells, mapping, "period")
            )
            if metric is None or period is None:
                continue
            value = _number(_cell(cells, mapping, "value"))
            low = _number(_cell(cells, mapping, "low"))
            high = _number(_cell(cells, mapping, "high"))
            if value is None and low is None and high is None:
                continue
            count_text = _cell(cells, mapping, "analysts")
            count = int(count_text) if count_text.isdigit() else None
            if kind == "published_analyst_consensus" and count is None:
                result.notes.append(f"consensus row without an analyst count skipped: {line[:120]}")
                continue
            ccy = (
                _cell(cells, mapping, "currency").upper()
                or parse_amount(_cell(cells, mapping, "value") or _cell(cells, mapping, "low"))[1]
            )
            result.forward.append(
                ParsedForward(
                    kind,
                    metric,
                    period,
                    value,
                    low,
                    high,
                    ccy or None,
                    count,
                    _cell(cells, mapping, "basis") or _basis_of(line) or default_basis,
                    line,
                )
            )
            continue
        mapping = None
        for sentence in re.split(r"(?<=[.;])\s+(?=[A-Z])", line):
            lowered = sentence.lower()
            if re.search(r"\b(repurchase|buyback|dividend)\b", lowered):
                amount, currency, percent = parse_amount(sentence)
                if amount is not None and not percent:
                    result.capital_return.append(
                        {
                            "type": "dividend" if "dividend" in lowered else "repurchase",
                            "amount": str(amount),
                            "currency": currency,
                            "passage": sentence.strip()[:500],
                        }
                    )
                continue
            if kind != "company_guidance":
                continue
            if not re.search(
                r"\b(expects?|expected|guidance|outlook|forecasts?|anticipates?|targets?)\b",
                lowered,
            ):
                continue
            matched = _metric_match(sentence)
            period = fiscal_period_of(sentence)
            if matched is None or period is None:
                continue
            metric, after = matched
            found = _range_of(sentence[after:])
            if found is not None:
                low, high, currency = found
                value = None
            else:
                value, currency, _ = parse_amount(sentence[after:])
                low = high = None
            if value is None and low is None:
                continue
            result.forward.append(
                ParsedForward(
                    "company_guidance",
                    metric,
                    period,
                    value,
                    low,
                    high,
                    currency,
                    None,
                    _basis_of(sentence) or default_basis,
                    sentence.strip()[:500],
                )
            )
    return result


# ---------------------------------------------------------------------------------- the job


@dataclass
class SourceRow:
    id: int
    ticker: str
    url: str
    release_type: str
    basis: str | None
    last_hash: str | None


async def store_version(
    pool: asyncpg.Pool,
    source: SourceRow,
    fetched: FetchedDocument,
    parsed: ParsedRelease | None,
    error: str | None,
) -> tuple[int, int]:
    """(release id, forward rows stored). The release row is created once per content hash."""
    now = dt.datetime.now(dt.UTC)
    version_id = await link_public_document(
        pool,
        regulator="issuer_ir",
        external_id=f"{source.ticker}:{hashlib.sha256(source.url.encode()).hexdigest()[:24]}",
        stable_url=source.url,
        document_type=source.release_type,
        content=fetched.body,
        content_hash=fetched.sha256,
        known_at=now,
        published_at=fetched.last_modified,
    )
    previous = await pool.fetchrow(
        """select id,document_version_id from public.issuer_ir_release
           where ticker=$1 and source_url=$2 and content_hash<>$3
           order by known_at desc,id desc limit 1""",
        source.ticker,
        source.url,
        fetched.sha256,
    )
    metadata: dict[str, Any] = {
        "parser": PARSER_VERSION,
        "format": detect_format(fetched.content_type, fetched.body),
        "forward_rows": len(parsed.forward) if parsed else 0,
        "notes": parsed.notes if parsed else [],
        "capital_return": parsed.capital_return if parsed else [],
    }
    if error:
        metadata["error"] = error
    release_id: int = await pool.fetchval(
        """insert into public.issuer_ir_release
           (ticker,release_type,source_url,content_hash,document_version_id,published_at,known_at,
            parser_version,analyst_count,basis,status,metadata,source_id,previous_release_id,
            content_type,byte_size,fetched_at)
           values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13,$14,$15,$16,$7)
           on conflict(ticker,source_url,content_hash) do update set
             source_id=coalesce(issuer_ir_release.source_id,excluded.source_id),
             document_version_id=coalesce(issuer_ir_release.document_version_id,
                                          excluded.document_version_id)
           returning id""",
        source.ticker,
        source.release_type,
        source.url,
        fetched.sha256,
        version_id,
        fetched.last_modified,
        now,
        PARSER_VERSION,
        max((p.analyst_count or 0 for p in parsed.forward), default=0) or None if parsed else None,
        source.basis,
        "parse_failed" if error else "available",
        json.dumps(metadata, default=str),
        source.id,
        previous["id"] if previous else None,
        fetched.content_type or None,
        len(fetched.body),
    )
    if (
        previous is not None
        and previous["document_version_id"]
        and previous["document_version_id"] != version_id
    ):
        await pool.execute(
            "update public.source_document_version set superseded_by_id=$2"
            " where id=$1 and superseded_by_id is null",
            previous["document_version_id"],
            version_id,
        )
    stored = 0
    for item in parsed.forward if parsed else []:
        evidence_id = await pool.fetchval(
            "select id from public.source_evidence"
            " where document_version_id=$1 and quote=$2 limit 1",
            version_id,
            item.passage,
        )
        if evidence_id is None:
            evidence_id = await pool.fetchval(
                "insert into public.source_evidence(document_version_id,quote) values($1,$2)"
                " returning id",
                version_id,
                item.passage,
            )
        await store_forward_value(
            pool,
            ForwardValue(
                ticker=source.ticker,
                kind=item.kind,  # type: ignore[arg-type]
                provider="issuer_ir",
                metric=item.metric,
                basis=item.basis,
                fiscal_period=item.fiscal_period,
                currency=item.currency,
                value=item.value,
                low=item.low,
                high=item.high,
                analyst_count=item.analyst_count,
                vintage=fetched.sha256[:12],
                published_at=fetched.last_modified,
                known_at=now,
                source_url=source.url,
                evidence_id=evidence_id,
                raw={"release_id": release_id, "parser": PARSER_VERSION},
            ),
        )
        stored += 1
    return release_id, stored


async def check_source(
    pool: asyncpg.Pool, http: httpx.AsyncClient, source: SourceRow, detail: dict[str, Any]
) -> None:
    now = dt.datetime.now(dt.UTC)
    try:
        fetched = await fetch_document(http, source.url)
    except (IrError, httpx.HTTPError) as exc:
        detail["error"] = f"{type(exc).__name__}: {exc}"[:300]
        await pool.execute(
            "update public.issuer_ir_source set last_checked_at=$2,last_status='fetch_failed',"
            " last_error=$3 where id=$1",
            source.id,
            now,
            detail["error"],
        )
        return
    if fetched.sha256 == source.last_hash:
        detail["unchanged"] = True
        await pool.execute(
            "update public.issuer_ir_source set last_checked_at=$2,last_status='unchanged',"
            " last_error=null where id=$1",
            source.id,
            now,
        )
        return
    parsed: ParsedRelease | None = None
    error: str | None = None
    try:
        parsed = parse_release(
            document_lines(fetched.body, fetched.content_type),
            source.release_type,
            default_basis=source.basis,
        )
    except (IrError, ValueError, KeyError) as exc:
        error = f"{type(exc).__name__}: {exc}"[:300]
    release_id, stored = await store_version(pool, source, fetched, parsed, error)
    detail.update(release_id=release_id, forward_rows=stored, parse_error=error)
    await pool.execute(
        "update public.issuer_ir_source set last_checked_at=$2,last_changed_at=$2,last_hash=$3,"
        " last_status=$4,last_error=$5 where id=$1",
        source.id,
        now,
        fetched.sha256,
        "parse_failed" if error else "changed",
        error,
    )


async def run_ir_releases(ctx: JobContext, http: httpx.AsyncClient) -> None:
    """The job body. One failing page (fetch, parse or database) never stops the others."""
    only = ctx.params.get("ticker")
    rows = await ctx.pool.fetch(
        """select id,ticker,url,release_type,basis,last_hash from public.issuer_ir_source
           where active and ($1::text is null or ticker=$1) order by id""",
        only if isinstance(only, str) and only else None,
    )
    failures: list[str] = []
    for row in rows:
        source = SourceRow(**dict(row))
        async with ctx.step(f"ir:{source.ticker}:{source.id}") as step:
            step.detail["url"] = source.url
            try:
                await check_source(ctx.pool, http, source, step.detail)
            except asyncpg.PostgresError as exc:
                step.detail["error"] = f"{type(exc).__name__}: {exc}"[:300]
            if step.detail.get("error") or step.detail.get("parse_error"):
                failures.append(source.url)
    async with ctx.step("summary") as step:
        step.detail.update(sources=len(rows), failed=failures)


async def ir_releases_handler(ctx: JobContext) -> None:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, read=60.0),
        headers={"User-Agent": f"StewardAI/{__version__} (personal portfolio tool)"},
        follow_redirects=True,
    ) as http:
        await run_ir_releases(ctx, http)


# ------------------------------------------------------------------- actual vs consensus

# consensus / guidance metric -> statement line item
LINE_ITEM_OF_METRIC = {
    "revenue": "revenue",
    "eps_diluted": "eps_diluted",
    "net_income": "net_income",
    "operating_income": "operating_income",
    "capex": "capex",
}


async def actual_vs_consensus(
    pool: asyncpg.Pool, ticker: str, metric: str, fiscal_period: str
) -> dict[str, Any]:
    """The reported figure of a period against the last published consensus known before the
    report (D4.10). Everything is point in time: the consensus is the vintage with the latest
    `known_at` not after the actual's `known_at`; a consensus vintage that arrived after the
    report is not "the expectation". Absent either side the reason is stated, nothing is
    estimated."""
    line_item = LINE_ITEM_OF_METRIC.get(metric)
    if line_item is None:
        return {"state": "unsupported_metric", "metric": metric}
    actual = await pool.fetchrow(
        """select id,value,unit,currency,period_end,known_at,accession,source,
                  coalesce((validation->>'reconciled')::boolean,false) reconciled
           from public.financial_statement_fact
           where ticker=$1 and line_item=$2 and fiscal_period=$3 and value is not null
             and not (jsonb_array_length(coalesce(dimensions->'members','[]'::jsonb))>0)
           order by published desc,known_at desc,revision desc limit 1""",
        ticker,
        line_item,
        fiscal_period,
    )
    if actual is None:
        return {"state": "no_actual", "reason": "no reported figure stored for this period"}
    consensus = await pool.fetchrow(
        """select id,provider,value,low,high,analyst_count,currency,basis,vintage,known_at
           from public.forward_observation
           where ticker=$1 and kind='published_analyst_consensus' and metric=$2
             and fiscal_period=$3 and known_at<=$4 and value is not null
           order by known_at desc,id desc limit 1""",
        ticker,
        metric,
        fiscal_period,
        actual["known_at"],
    )
    result: dict[str, Any] = {
        "metric": metric,
        "fiscal_period": fiscal_period,
        "actual": {
            "value": actual["value"],
            "unit": actual["unit"],
            "currency": actual["currency"],
            "known_at": actual["known_at"],
            "accession": actual["accession"],
            "reconciled": actual["reconciled"],
        },
    }
    if consensus is None:
        return {
            **result,
            "state": "no_consensus_before_report",
            "reason": "no consensus vintage was known before the figure was reported",
        }
    if consensus["currency"] and actual["currency"] and consensus["currency"] != actual["currency"]:
        return {
            **result,
            "state": "currency_mismatch",
            "consensus": dict(consensus),
            "reason": f"consensus in {consensus['currency']}, actual in {actual['currency']}",
        }
    expected = Decimal(consensus["value"])
    surprise = Decimal(actual["value"]) - expected
    return {
        **result,
        "state": "available",
        "consensus": dict(consensus),
        "surprise": surprise,
        "surprise_percent": (surprise / abs(expected)) if expected != 0 else None,
    }
