"""SEC Form 13F data sets (tier 1): https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets

Each data set is a zip of tab-separated files covering the 13F filings received in a
three-month window (so one report period's filings, and their amendments, can span two
sets). Used here:

- SUBMISSION.tsv: accession, filing date, type (13F-HR, 13F-HR/A, 13F-NT...), filer CIK,
  report period;
- COVERPAGE.tsv: manager name, amendment type (RESTATEMENT replaces a filing, NEW HOLDINGS
  adds positions to it);
- INFOTABLE.tsv: one row per position: issuer, class, CUSIP, value (US dollars for filings
  from 2023-01-03, thousands of dollars before; stored here in dollars either way),
  shares or principal amount and its type (SH/PRN), put/call, discretion, voting authority.

The information table is about 400 MB uncompressed, so it is streamed and only rows for
the wanted CUSIPs are kept. Notice filings (13F-NT, no holdings) are skipped.
"""

import csv
import datetime as dt
import io
import re
import zipfile
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import IO
from urllib.parse import urljoin

HOLDINGS_TYPES = {"13F-HR", "13F-HR/A"}
# Filings before this date report VALUE in thousands of US dollars (SEC 13F FAQ).
VALUE_IN_DOLLARS_FROM = dt.date(2023, 1, 3)
_LINK = re.compile(r'href="([^"]+_form13f\.zip)"', re.IGNORECASE)


def dataset_links(html: str, base_url: str) -> list[str]:
    """Absolute URLs of every data set on the SEC's index page, newest first (as listed)."""
    return list(dict.fromkeys(urljoin(base_url, m) for m in _LINK.findall(html)))


def _cusip_check_digit_ok(code: str) -> bool:
    total = 0
    for i, char in enumerate(code[:8]):
        if char.isdigit():
            value = int(char)
        elif char.isalpha():
            value = ord(char) - ord("A") + 10
        else:
            value = {"*": 36, "@": 37, "#": 38}.get(char, -1)
            if value < 0:
                return False
        if i % 2:
            value *= 2
        total += value // 10 + value % 10
    return code[8:].isdigit() and int(code[8]) == (10 - total % 10) % 10


def cusip_of_isin(isin: str | None) -> str | None:
    """A US or Canadian ISIN embeds the 9-character CUSIP (characters 3-11). An offshore
    issuer's ISIN (Cayman, Bermuda...) may embed a CINS instead: a CUSIP starting with a
    letter, which 13F filers report (e.g. KYG6683N1034 -> G6683N103)."""
    if not isin or len(isin) != 12:
        return None
    nsin = isin[2:11].upper()
    if isin[:2].upper() in ("US", "CA"):
        return nsin
    if nsin[0].isalpha() and nsin.isalnum() and _cusip_check_digit_ok(nsin):
        return nsin
    return None


# Words dropped when matching a holding's name to a 13F issuer name.
_ISSUER_NOISE = frozenset(
    "PLC INC LTD LIMITED CORP CORPORATION CO COMPANY SA NV AG SE ASA AB SPA HLDGS HOLDINGS"
    " HOLDING GROUP GRP THE ADR ADS SPONSORED SPONS SPON NEW CL CLASS".split()
)
_DEPOSITARY = re.compile(r"\b(ADR|ADRS|ADS|SPON|SPONS|SPONSORED|DEPOSITARY|DEPOSITORY)\b")
# A name-matched CUSIP must be reported by at least this many filings in the data set.
MIN_NAME_MATCH_FILINGS = 10


def issuer_key(name: str | None) -> str:
    """Upper-case name without punctuation and legal-form words ("AstraZeneca" and
    "ASTRAZENECA PLC" give the same key)."""
    words = re.sub(r"[^A-Z0-9]+", " ", (name or "").upper()).split()
    return " ".join(w for w in words if w not in _ISSUER_NOISE)


@dataclass(frozen=True)
class Filing13F:
    accession: str
    cik: str
    manager_name: str | None
    period: dt.date
    filed: dt.date
    submission_type: str
    amendment_type: str | None
    # From the filing's own summary page: how many lines the manager reported and their total
    # value, so a total can be reconciled even though only some lines are stored. None when the
    # dataset has no summary page; never zero by default.
    reported_line_count: int | None = None
    reported_total_value_usd: Decimal | None = None
    # True when the cover says holdings were omitted under a confidential treatment request.
    omitted_holdings_notice: bool | None = None


@dataclass(frozen=True)
class Position13F:
    accession: str
    infotable_sk: int
    cusip: str
    issuer_name: str
    title_of_class: str
    value_usd: Decimal | None
    shares: Decimal | None
    shares_type: str
    put_call: str | None
    discretion: str | None
    voting_sole: Decimal | None
    voting_shared: Decimal | None
    voting_none: Decimal | None


def _date(text: str) -> dt.date:
    return dt.datetime.strptime(text.strip(), "%d-%b-%Y").date()


def _num(text: str) -> Decimal | None:
    text = text.strip().replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _yes_no(text: str | None) -> bool | None:
    """The dataset's Y/N flags. A blank is unknown, not 'no'."""
    value = (text or "").strip().upper()
    if value in {"Y", "YES", "TRUE", "1"}:
        return True
    if value in {"N", "NO", "FALSE", "0"}:
        return False
    return None


def _value_usd(text: str, filed: dt.date) -> Decimal | None:
    value = _num(text)
    if value is not None and filed < VALUE_IN_DOLLARS_FROM:
        value *= 1000
    return value


def _rows(handle: IO[bytes]) -> Iterator[dict[str, str]]:
    text = io.TextIOWrapper(handle, encoding="utf-8", errors="replace", newline="")
    reader = csv.reader(text, delimiter="\t", quoting=csv.QUOTE_NONE)
    header = next(reader, None)
    if header is None:
        return
    names = [h.strip().upper() for h in header]
    for row in reader:
        if len(row) < len(names):
            row = row + [""] * (len(names) - len(row))
        yield dict(zip(names, row, strict=False))


@dataclass
class Dataset:
    filings: list[Filing13F]
    positions: list[Position13F]
    # CUSIP -> ticker for depositary receipts found by issuer name (no CUSIP was known).
    name_matched: dict[str, str] = field(default_factory=dict)


def _member(archive: zipfile.ZipFile, name: str) -> str:
    """The archive entry named `name`, wherever it sits.

    Most quarters put the TSVs flat at the zip root; some (e.g. 01jun2025-31aug2025) nest
    them under a folder named after the archive instead, e.g.
    "01JUN2025-31AUG2025_form13f/SUBMISSION.tsv". Match by file name only, case-insensitive.
    """
    for entry in archive.namelist():
        if entry.strip("/").rsplit("/", 1)[-1].upper() == name.upper():
            return entry
    raise KeyError(f"no {name} in the archive (has: {archive.namelist()})")


def _position(r: dict[str, str], cusip: str, filed: dt.date) -> Position13F:
    return Position13F(
        accession=r["ACCESSION_NUMBER"],
        infotable_sk=int(r["INFOTABLE_SK"]),
        cusip=cusip,
        issuer_name=r.get("NAMEOFISSUER", "").strip(),
        title_of_class=r.get("TITLEOFCLASS", "").strip(),
        value_usd=_value_usd(r.get("VALUE", ""), filed),
        shares=_num(r.get("SSHPRNAMT", "")),
        shares_type=r.get("SSHPRNAMTTYPE", "").strip(),
        put_call=r.get("PUTCALL", "").strip() or None,
        discretion=r.get("INVESTMENTDISCRETION", "").strip() or None,
        voting_sole=_num(r.get("VOTING_AUTH_SOLE", "")),
        voting_shared=_num(r.get("VOTING_AUTH_SHARED", "")),
        voting_none=_num(r.get("VOTING_AUTH_NONE", "")),
    )


def parse_dataset(
    source: Path | IO[bytes],
    cusips: set[str],
    tracked_ciks: set[str] | None = None,
    depositary_names: dict[str, str] | None = None,
) -> Dataset:
    """Every holdings filing, and rows for portfolio CUSIPs or tracked managers.

    `depositary_names` maps `issuer_key` names to tickers of US-listed holdings with no known
    CUSIP: depositary-receipt rows (ADR/ADS class) of that issuer are kept under the one CUSIP
    most filings report, when at least MIN_NAME_MATCH_FILINGS do."""
    wanted = {c.upper() for c in cusips}
    managers = {c.zfill(10) for c in (tracked_ciks or set())}
    by_name = {k: t for k, t in (depositary_names or {}).items() if len(k) >= 3}
    candidates: dict[tuple[str, str], list[Position13F]] = defaultdict(list)
    with zipfile.ZipFile(source) as archive:
        with archive.open(_member(archive, "SUBMISSION.tsv")) as f:
            submissions = {
                r["ACCESSION_NUMBER"]: r
                for r in _rows(f)
                if r.get("SUBMISSIONTYPE", "").strip() in HOLDINGS_TYPES
            }
        tracked_accessions = {
            accession
            for accession, row in submissions.items()
            if row.get("CIK", "").strip().zfill(10) in managers
        }
        with archive.open(_member(archive, "COVERPAGE.tsv")) as f:
            covers = {r["ACCESSION_NUMBER"]: r for r in _rows(f)}
        try:
            with archive.open(_member(archive, "SUMMARYPAGE.tsv")) as f:
                summaries = {r["ACCESSION_NUMBER"]: r for r in _rows(f)}
        except KeyError:
            summaries = {}  # older archives: no summary page, so the totals stay unknown
        positions: list[Position13F] = []
        with archive.open(_member(archive, "INFOTABLE.tsv")) as f:
            for r in _rows(f):
                cusip = r.get("CUSIP", "").strip().upper()
                accession = r["ACCESSION_NUMBER"]
                if accession not in submissions:
                    continue
                if cusip in wanted or accession in tracked_accessions:
                    positions.append(
                        _position(r, cusip, _date(submissions[accession]["FILING_DATE"]))
                    )
                elif (
                    by_name
                    and cusip
                    and _DEPOSITARY.search(r.get("TITLEOFCLASS", "").upper())
                    and (ticker := by_name.get(issuer_key(r.get("NAMEOFISSUER"))))
                ):
                    candidates[(ticker, cusip)].append(
                        _position(r, cusip, _date(submissions[accession]["FILING_DATE"]))
                    )
        name_matched: dict[str, str] = {}
        for ticker in {t for t, _ in candidates}:
            reported_by, cusip = max(
                (len({p.accession for p in rows}), c)
                for (t, c), rows in candidates.items()
                if t == ticker
            )
            if reported_by >= MIN_NAME_MATCH_FILINGS:
                name_matched[cusip] = ticker
                positions.extend(
                    p for p in candidates[(ticker, cusip)] if p.accession not in tracked_accessions
                )
    filings = []
    for accession, r in submissions.items():
        # Every holdings filing is kept, with or without a wanted position: a restatement
        # that drops a position must replace the original that had it, and a tracked
        # manager that sold out must still be seen.
        cik = r["CIK"].strip().zfill(10)
        cover = covers.get(accession, {})
        summary = summaries.get(accession, {})
        line_count = _num(summary.get("TABLEENTRYTOTAL", ""))
        filed = _date(r["FILING_DATE"])
        filings.append(
            Filing13F(
                accession=accession,
                cik=cik,
                manager_name=cover.get("FILINGMANAGER_NAME", "").strip() or None,
                period=_date(r["PERIODOFREPORT"]),
                filed=filed,
                submission_type=r["SUBMISSIONTYPE"].strip(),
                amendment_type=cover.get("AMENDMENTTYPE", "").strip() or None,
                reported_line_count=None if line_count is None else int(line_count),
                reported_total_value_usd=_value_usd(summary.get("TABLEVALUETOTAL", ""), filed),
                omitted_holdings_notice=_yes_no(
                    cover.get("ISCONFIDENTIALOMITTED") or summary.get("ISCONFIDENTIALOMITTED")
                ),
            )
        )
    return Dataset(filings, positions, name_matched)
