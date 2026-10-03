"""SEC "Financial Statement and Notes" data sets: a backfill of the statement fact store.

The SEC publishes every quarter's XBRL financial statements and notes as a zip of
tab-separated files (https://www.sec.gov/dera/data/financial-statement-and-notes-data-sets).
This module reads the documented layout and writes `financial_statement_fact` rows with
`source='sec_fsds'`, so filings older than the live XBRL job's reach (or its gaps) have the
same statement history. It is an optional job (`sec_fsds_backfill`, params
`{"quarter": "2025q3"}` or `{"url": ...}`, optionally `{"ticker": ...}`). Without a quarter or url
(the scheduled run) it takes the latest completed quarter whose data set is published.

Layout read (names are the SEC's; a column that is absent is treated as empty):

- `sub`: one row per filing. `adsh` accession, `cik`, `form`, `period` (YYYYMMDD period of the
  report), `fy`, `fp` (fiscal year / period focus: FY, Q1..Q3, H1...), `filed` (YYYYMMDD),
  `accepted` ("2025-02-01 16:05:11.0", US Eastern), `prevrpt` (1: superseded by an amendment),
  `fye` (MMDD).
- `num`: one row per numeric fact. `adsh`, `tag`, `version` (taxonomy and year, "us-gaap/2025";
  the accession for a company extension), `ddate` (period end, YYYYMMDD), `qtrs` (duration in
  quarters, 0 for an instant), `uom`, `value`; dimensions as `segments` (older sets) or
  `dimn`/`dimh` (the Notes sets); `coreg` names a co-registrant (not the filer: skipped).
- `txt`: one row per note text block, same keys plus `value` (the text).
- `tag`: `tag`, `version`, `custom` (1: extension), `abstract`, `datatype`, `tlabel` (label).

What is kept: standard-taxonomy concepts of the shared statement catalog
(`steward.sources.xbrl_concepts`), text blocks of the stored note topics, 10-K / 10-Q / 20-F /
40-F and their amendments. Company extensions and co-registrants are not kept. The facts get
the same classification, fiscal period, reconciliation and lineage as the live path; the one
check this source cannot run is the companyfacts comparison.

Not verified against a live download (the file was not fetched in development): the file
extension (`.tsv` in the Notes sets, `.txt` in the older Financial Statement sets, both are
accepted), the zip file name (`{year}q{n}_notes.zip`), and rare columns. The fixture follows
the documentation exactly.
"""

from __future__ import annotations

import contextlib
import csv
import dataclasses
import datetime as dt
import hashlib
import io
import os
import tempfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, BinaryIO
from zoneinfo import ZoneInfo

import asyncpg
import httpx

from steward import __version__
from steward.fundamentals.statements import (
    StatementFact,
    apply_reconciliation,
    basis_of,
    filing_evidence,
    internal_checks,
    resolve_issuer_id,
    sec_fiscal_period,
    store_statement_facts,
)
from steward.jobs.runner import JobContext, JobFailed
from steward.settings import get_settings
from steward.sources.edgar import pad_cik
from steward.sources.xbrl_concepts import (
    STATEMENT_CONCEPTS,
    classify,
    currency_of,
    is_wanted_text_block,
)

FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A"}
SOURCE = "sec_fsds"
URL = "https://www.sec.gov/files/dera/data/financial-statement-and-notes-data-sets/{quarter}_notes.zip"
MAX_ZIP_BYTES = 1500 * 1024 * 1024
TEXT_BLOCK_CAP = 20_000
EASTERN = ZoneInfo("America/New_York")
csv.field_size_limit(50_000_000)


class FsdsError(Exception):
    pass


class FsdsNotPublished(FsdsError):
    """The data set for a quarter is not on the SEC site (HTTP 404)."""


def _date(text: str) -> dt.date | None:
    text = (text or "").strip()
    if len(text) == 8 and text.isdigit():
        try:
            return dt.date(int(text[:4]), int(text[4:6]), int(text[6:]))
        except ValueError:
            return None
    return None


def accepted_utc(text: str, filed: dt.date) -> dt.datetime:
    """The acceptance moment in UTC (the file writes US Eastern wall time); without it the end
    of the filing day, never midnight."""
    try:
        naive = dt.datetime.strptime((text or "").strip()[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return dt.datetime.combine(filed, dt.time(23, 59, 59), dt.UTC)
    return naive.replace(tzinfo=EASTERN).astimezone(dt.UTC)


def period_start(end: dt.date, quarters: int) -> dt.date | None:
    """First day of the duration ending `end` that spans `quarters` quarters."""
    if quarters <= 0:
        return None
    month_end = (end + dt.timedelta(days=1)).day == 1
    if month_end:
        total = end.year * 12 + (end.month - 1) - 3 * quarters + 1
        return dt.date(total // 12, total % 12 + 1, 1)
    return end - dt.timedelta(days=91 * quarters - 1)


def accession_dashed(adsh: str) -> str:
    adsh = adsh.strip()
    if "-" not in adsh and len(adsh) == 18 and adsh.isdigit():
        return f"{adsh[:10]}-{adsh[10:12]}-{adsh[12:]}"
    return adsh


@dataclass
class FsdsFiling:
    adsh: str
    cik: str
    form: str
    period: dt.date | None
    fy: int | None
    fp: str | None
    filed: dt.date
    accepted_at: dt.datetime
    superseded: bool
    fye_month: int | None
    facts: list[StatementFact] = field(default_factory=list)


def _rows(archive: zipfile.ZipFile, stem: str) -> Iterator[dict[str, str]]:
    """Rows of `stem.tsv` (or `.txt`) as dicts, streamed; a missing file yields nothing."""
    for suffix in (".tsv", ".txt"):
        name = f"{stem}{suffix}"
        if name in archive.namelist():
            with archive.open(name) as handle:
                reader = csv.DictReader(
                    io.TextIOWrapper(handle, encoding="utf-8", errors="replace", newline=""),
                    delimiter="\t",
                    quoting=csv.QUOTE_NONE,
                )
                for row in reader:
                    yield {k: (v or "") for k, v in row.items() if k is not None}
            return


def _concept(tag: str, version: str, custom: bool) -> str | None:
    if custom or "/" not in version:
        return None  # a company extension: version is the accession number
    taxonomy = version.split("/", 1)[0].lower()
    if taxonomy in ("us-gaap", "ifrs-full", "dei", "srt"):
        return f"{taxonomy}:{tag}"
    if taxonomy == "ifrs":
        return f"ifrs-full:{tag}"
    return None


def _is_dimensional(row: dict[str, str]) -> bool:
    if row.get("segments", "").strip():
        return True
    if row.get("dimn", "0").strip() not in ("", "0"):
        return True
    dimh = row.get("dimh", "").strip()
    return dimh not in ("", "0x00000000", "0")


def read_filings(
    source: BinaryIO | Path | str,
    *,
    ciks: set[str] | None = None,
) -> list[FsdsFiling]:
    """Filings (with their statement facts) of a Financial Statement and Notes zip, for the
    given CIKs (padded to ten digits) or all of them. Memory holds only the wanted filings."""
    try:
        archive = zipfile.ZipFile(source)
    except zipfile.BadZipFile as exc:
        raise FsdsError("not a zip file") from exc
    with archive:
        filings: dict[str, FsdsFiling] = {}
        for row in _rows(archive, "sub"):
            form = row.get("form", "").strip().upper()
            cik = row.get("cik", "").strip()
            filed = _date(row.get("filed", ""))
            if form not in FORMS or not cik.isdigit() or filed is None:
                continue
            padded = pad_cik(cik)
            if ciks is not None and padded not in ciks:
                continue
            fy = row.get("fy", "").strip()
            fye = row.get("fye", "").strip()
            filings[row["adsh"].strip()] = FsdsFiling(
                adsh=row["adsh"].strip(),
                cik=padded,
                form=form,
                period=_date(row.get("period", "")),
                fy=int(fy) if fy.isdigit() else None,
                fp=row.get("fp", "").strip().upper() or None,
                filed=filed,
                accepted_at=accepted_utc(row.get("accepted", ""), filed),
                superseded=row.get("prevrpt", "").strip() == "1",
                fye_month=int(fye[:2]) if len(fye) == 4 and fye.isdigit() else None,
            )
        if not filings:
            return []
        customs = {
            (r.get("tag", ""), r.get("version", ""))
            for r in _rows(archive, "tag")
            if r.get("custom", "").strip() == "1"
        }
        labels = {
            (r["tag"], r.get("version", "")): r.get("tlabel", "")
            for r in _rows(archive, "tag")
            if r.get("tag")
        }
        seen: set[tuple[Any, ...]] = set()
        for row in _rows(archive, "num"):
            filing = filings.get(row.get("adsh", "").strip())
            if filing is None or row.get("coreg", "").strip():
                continue
            tag, version = row.get("tag", ""), row.get("version", "")
            concept = _concept(tag, version, (tag, version) in customs)
            end = _date(row.get("ddate", ""))
            if concept is None or end is None or not row.get("qtrs", "").strip().isdigit():
                continue
            dimensional = _is_dimensional(row)
            if concept not in STATEMENT_CONCEPTS:
                continue  # incl. cover-page facts: those come from the live instance path
            try:
                value = Decimal(row.get("value", "").strip())
            except InvalidOperation:
                continue
            if not value.is_finite():
                continue
            quarters = int(row["qtrs"])
            start = period_start(end, quarters)
            unit = row.get("uom", "").strip() or "pure"
            dims: dict[str, Any] = (
                {"members": [["dimension", row.get("dimh") or row.get("segments") or ""]]}
                if dimensional
                else {"members": []}
            )
            key = (filing.adsh, concept, start, end, unit, str(dims))
            if key in seen:
                continue
            seen.add(key)
            statement, line_item = classify(concept, dimensional=dimensional)
            filing.facts.append(
                _fact(
                    filing,
                    concept,
                    statement,
                    line_item,
                    start,
                    end,
                    unit,
                    value,
                    dims,
                    labels.get((tag, version)),
                    None,
                )
            )
        for row in _rows(archive, "txt"):
            filing = filings.get(row.get("adsh", "").strip())
            if filing is None or row.get("coreg", "").strip():
                continue
            concept = _concept(row.get("tag", ""), row.get("version", ""), False)
            end = _date(row.get("ddate", ""))
            text = row.get("value", "").strip()
            if concept is None or end is None or not text or not is_wanted_text_block(concept):
                continue
            quarters = int(row["qtrs"]) if row.get("qtrs", "").strip().isdigit() else 0
            dims = {"members": []}
            text_key: tuple[Any, ...] = (
                filing.adsh,
                concept,
                end,
                hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest(),
            )
            if text_key in seen:
                continue
            seen.add(text_key)
            filing.facts.append(
                _fact(
                    filing,
                    concept,
                    "note",
                    None,
                    period_start(end, quarters),
                    end,
                    "pure",
                    None,
                    dims,
                    None,
                    text,
                )
            )
    return [f for f in filings.values() if f.facts]


def _fiscal_period(filing: FsdsFiling, start: dt.date | None, end: dt.date) -> str | None:
    return sec_fiscal_period(start, end, filing.period, filing.fy, filing.fp)


def _fact(
    filing: FsdsFiling,
    concept: str,
    statement: str,
    line_item: str | None,
    start: dt.date | None,
    end: dt.date,
    unit: str,
    value: Decimal | None,
    dims: dict[str, Any],
    label: str | None,
    text: str | None,
) -> StatementFact:
    adsh = accession_dashed(filing.adsh)
    url = (
        f"https://www.sec.gov/Archives/edgar/data/{int(filing.cik)}/{filing.adsh.replace('-', '')}/"
    )
    validation: dict[str, Any] = {"reviewed": False, "dataset": "financial_statement_and_notes"}
    if filing.superseded:
        validation["superseded_by_amendment"] = True
    block = None
    if text is not None:
        block = text[:TEXT_BLOCK_CAP]
        if len(text) > TEXT_BLOCK_CAP:
            validation["text_truncated"] = True
            validation["text_length"] = len(text)
    return StatementFact(
        ticker="",
        accession=adsh,
        form=filing.form,
        filing_url=url,
        concept=concept,
        statement=statement,
        line_item=line_item,
        period_start=start,
        period_end=end,
        fiscal_period=_fiscal_period(filing, start, end),
        basis=basis_of(concept),
        value=value,
        value_text=None,
        unit=unit,
        currency=currency_of(unit),
        dimensions=dims,
        source=SOURCE,
        source_url=url,
        published_at=dt.datetime.combine(filing.filed, dt.time.min, dt.UTC),
        accepted_at=filing.accepted_at,
        known_at=filing.accepted_at,
        text_block=block,
        label=label or None,
        cik=filing.cik,
        is_amendment=filing.form.endswith("/A"),
        decimals=None,
        validation=validation,
    )


def amends_map(filings: list[FsdsFiling]) -> dict[str, str]:
    """accession -> accession of the original it amends, within the data set: the latest
    earlier filing of the base form for the same CIK and period."""
    out: dict[str, str] = {}
    for amendment in filings:
        if not amendment.form.endswith("/A"):
            continue
        base = amendment.form.removesuffix("/A")
        originals = [
            f
            for f in filings
            if f.cik == amendment.cik
            and f.form == base
            and f.period == amendment.period
            and (f.filed, f.adsh) < (amendment.filed, amendment.adsh)
        ]
        if originals:
            out[amendment.adsh] = accession_dashed(
                max(originals, key=lambda f: (f.filed, f.adsh)).adsh
            )
    return out


# ---------------------------------------------------------------------------------- the job


async def store_filing(
    pool: asyncpg.Pool, ticker: str, filing: FsdsFiling, amends: str | None
) -> dict[str, int]:
    accession = accession_dashed(filing.adsh)
    async with pool.acquire() as conn, conn.transaction():
        if await conn.fetchval(
            "select exists(select 1 from public.financial_statement_fact where ticker=$1"
            " and accession=$2 and source='sec_xbrl')",
            ticker,
            accession,
        ):
            return {"rows": 0, "skipped_live": 1}
        evidence_id = await filing_evidence(
            conn,
            regulator="SEC",
            external_id=accession,
            stable_url=filing.facts[0].filing_url,
            document_type=filing.form,
            content_hash=hashlib.sha256(
                f"fsds:{accession}:{len(filing.facts)}".encode()
            ).hexdigest(),
            filed_at=filing.filed,
            accepted_at=filing.accepted_at,
            known_at=filing.accepted_at,
            quote=(
                f"{filing.form} {accession} filed {filing.filed}: "
                f"SEC financial statement data ({ticker})"
            ),
            amends_external_id=amends,
        )
        issuer_id = await resolve_issuer_id(conn, ticker, filing.cik)
        facts = [
            dataclasses.replace(
                f,
                ticker=ticker,
                evidence_id=evidence_id,
                issuer_id=issuer_id,
                amends_accession=amends,
            )
            for f in filing.facts
        ]
        facts = apply_reconciliation(facts, internal_checks(facts))
        result = await store_statement_facts(conn, ticker, SOURCE, accession, facts)
        return {**result, "skipped_live": 0}


def _remove(name: str | Path) -> None:
    with contextlib.suppress(OSError):
        os.remove(name)


def recent_quarters(today: dt.date, count: int = 3) -> list[str]:
    """The last `count` COMPLETED calendar quarters as `2025q3`, newest first. The SEC posts a
    quarter's data set some weeks after it closes, so the newest one may not exist yet."""
    year, quarter = today.year, (today.month - 1) // 3 + 1
    out: list[str] = []
    for _ in range(count):
        quarter -= 1
        if quarter == 0:
            year, quarter = year - 1, 4
        out.append(f"{year}q{quarter}")
    return out


async def download(http: httpx.AsyncClient, url: str, headers: dict[str, str]) -> Path:
    handle, name = tempfile.mkstemp(suffix=".zip", prefix="fsds-")
    size = 0
    try:
        with os.fdopen(handle, "wb") as out:
            async with http.stream("GET", url, headers=headers, follow_redirects=True) as response:
                if response.status_code == 404:
                    raise FsdsNotPublished(f"{url} returned HTTP 404")
                if response.status_code != 200:
                    raise FsdsError(f"{url} returned HTTP {response.status_code}")
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_ZIP_BYTES:
                        raise FsdsError(f"{url} is larger than {MAX_ZIP_BYTES} bytes")
                    out.write(chunk)
    except BaseException:
        _remove(name)
        raise
    return Path(name)


async def run_fsds_backfill(
    ctx: JobContext, http: httpx.AsyncClient | None, *, archive: Path | BinaryIO | None = None
) -> None:
    """Backfill from one data set. `archive` (tests) replaces the download."""
    quarter = str(ctx.params.get("quarter") or "")
    explicit = bool(quarter or ctx.params.get("url"))
    url = str(ctx.params.get("url") or (URL.format(quarter=quarter) if quarter else ""))
    only = ctx.params.get("ticker")
    async with ctx.step("select_holdings") as step:
        rows = await ctx.pool.fetch(
            """select t212_ticker, cik from public.instrument
               where cik is not null and ($1::text is null or t212_ticker=$1)""",
            only if isinstance(only, str) and only else None,
        )
        by_cik = {pad_cik(r["cik"]): r["t212_ticker"] for r in rows}
        step.detail.update(
            holdings=len(by_cik),
            source=url or ("latest published quarter" if archive is None else "supplied archive"),
        )
    if not by_cik:
        return
    temp: Path | None = None
    try:
        if archive is None:
            if http is None:
                raise JobFailed("no HTTP client")
            agent = get_settings().sec_user_agent
            if not agent or "@" not in agent:
                raise JobFailed("SEC_USER_AGENT must include a contact email address")
            async with ctx.step("download") as step:
                headers = {"User-Agent": agent}
                if explicit:
                    temp = await download(http, url, headers)
                else:
                    # No quarter given (the scheduled run): the latest quarter whose data set is
                    # already published.
                    for candidate in recent_quarters(dt.datetime.now(dt.UTC).date()):
                        url = URL.format(quarter=candidate)
                        try:
                            temp = await download(http, url, headers)
                        except FsdsNotPublished:
                            continue
                        step.detail["quarter"] = candidate
                        break
                    else:
                        raise JobFailed("no recent quarter's data set is published")
                step.detail["bytes"] = temp.stat().st_size
            archive = temp
        async with ctx.step("parse") as step:
            filings = read_filings(archive, ciks=set(by_cik))
            step.detail["filings"] = len(filings)
        amends = amends_map(filings)
        stored = skipped = failed = 0
        for filing in filings:
            async with ctx.step(f"store:{by_cik[filing.cik]}:{filing.adsh}") as step:
                try:
                    result = await store_filing(
                        ctx.pool, by_cik[filing.cik], filing, amends.get(filing.adsh)
                    )
                except asyncpg.PostgresError as exc:
                    step.detail["error"] = f"{type(exc).__name__}: {exc}"[:300]
                    failed += 1
                    continue
                step.detail.update(result)
                if result.get("skipped_live"):
                    skipped += 1
                else:
                    stored += 1
        async with ctx.step("summary") as step:
            step.detail.update(stored=stored, skipped_live=skipped, failed=failed)
    finally:
        if temp is not None:
            _remove(temp)


async def sec_fsds_backfill_handler(ctx: JobContext) -> None:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, read=600.0),
        headers={"User-Agent": f"StewardAI/{__version__}"},
        follow_redirects=True,
    ) as http:
        await run_fsds_backfill(ctx, http)


__all__ = [
    "FsdsError",
    "FsdsFiling",
    "FsdsNotPublished",
    "amends_map",
    "read_filings",
    "recent_quarters",
    "run_fsds_backfill",
    "sec_fsds_backfill_handler",
]
