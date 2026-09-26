"""SEC Form 13F data sets (tier 1): https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets

Each data set is a zip of tab-separated files covering the 13F filings received in a
three-month window (so one report period's filings, and their amendments, can span two
sets). Used here:

- SUBMISSION.tsv: accession, filing date, type (13F-HR, 13F-HR/A, 13F-NT...), filer CIK,
  report period;
- COVERPAGE.tsv: manager name, amendment type (RESTATEMENT replaces a filing, NEW HOLDINGS
  adds positions to it);
- INFOTABLE.tsv: one row per position: issuer, class, CUSIP, value (US dollars since 2023),
  shares or principal amount and its type (SH/PRN), put/call, discretion, voting authority.

The information table is about 400 MB uncompressed, so it is streamed and only rows for
the wanted CUSIPs are kept. Notice filings (13F-NT, no holdings) are skipped.
"""

import csv
import datetime as dt
import io
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import IO
from urllib.parse import urljoin

HOLDINGS_TYPES = {"13F-HR", "13F-HR/A"}
_LINK = re.compile(r'href="([^"]+_form13f\.zip)"', re.IGNORECASE)


def dataset_links(html: str, base_url: str) -> list[str]:
    """Absolute URLs of every data set on the SEC's index page, newest first (as listed)."""
    return list(dict.fromkeys(urljoin(base_url, m) for m in _LINK.findall(html)))


def cusip_of_isin(isin: str | None) -> str | None:
    """A US or Canadian ISIN embeds the 9-character CUSIP (characters 3-11)."""
    if isin and len(isin) == 12 and isin[:2] in ("US", "CA"):
        return isin[2:11].upper()
    return None


@dataclass(frozen=True)
class Filing13F:
    accession: str
    cik: str
    manager_name: str | None
    period: dt.date
    filed: dt.date
    submission_type: str
    amendment_type: str | None


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


def parse_dataset(source: Path | IO[bytes], cusips: set[str]) -> Dataset:
    """Every holdings filing, and the positions in the wanted CUSIPs."""
    wanted = {c.upper() for c in cusips}
    with zipfile.ZipFile(source) as archive:
        with archive.open("SUBMISSION.tsv") as f:
            submissions = {
                r["ACCESSION_NUMBER"]: r
                for r in _rows(f)
                if r.get("SUBMISSIONTYPE", "").strip() in HOLDINGS_TYPES
            }
        with archive.open("COVERPAGE.tsv") as f:
            covers = {r["ACCESSION_NUMBER"]: r for r in _rows(f)}
        positions: list[Position13F] = []
        with archive.open("INFOTABLE.tsv") as f:
            for r in _rows(f):
                cusip = r.get("CUSIP", "").strip().upper()
                if cusip not in wanted or r["ACCESSION_NUMBER"] not in submissions:
                    continue
                positions.append(
                    Position13F(
                        accession=r["ACCESSION_NUMBER"],
                        infotable_sk=int(r["INFOTABLE_SK"]),
                        cusip=cusip,
                        issuer_name=r.get("NAMEOFISSUER", "").strip(),
                        title_of_class=r.get("TITLEOFCLASS", "").strip(),
                        value_usd=_num(r.get("VALUE", "")),
                        shares=_num(r.get("SSHPRNAMT", "")),
                        shares_type=r.get("SSHPRNAMTTYPE", "").strip(),
                        put_call=r.get("PUTCALL", "").strip() or None,
                        discretion=r.get("INVESTMENTDISCRETION", "").strip() or None,
                        voting_sole=_num(r.get("VOTING_AUTH_SOLE", "")),
                        voting_shared=_num(r.get("VOTING_AUTH_SHARED", "")),
                        voting_none=_num(r.get("VOTING_AUTH_NONE", "")),
                    )
                )
    filings = []
    for accession, r in submissions.items():
        # Every holdings filing is kept, with or without a wanted position: a restatement
        # that drops a position must replace the original that had it, and a tracked
        # manager that sold out must still be seen.
        cik = r["CIK"].strip().zfill(10)
        cover = covers.get(accession, {})
        filings.append(
            Filing13F(
                accession=accession,
                cik=cik,
                manager_name=cover.get("FILINGMANAGER_NAME", "").strip() or None,
                period=_date(r["PERIODOFREPORT"]),
                filed=_date(r["FILING_DATE"]),
                submission_type=r["SUBMISSIONTYPE"].strip(),
                amendment_type=cover.get("AMENDMENTTYPE", "").strip() or None,
            )
        )
    return Dataset(filings, positions)
