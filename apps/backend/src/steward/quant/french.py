"""Ken French Data Library factor files (quant plan Q4.1).

The library publishes daily Fama-French five-factor and momentum returns for the Developed,
Europe and US markets as zipped CSV files, in percent and US dollars. This module parses them
(pure) and names the files to fetch; `steward.portfolio.factors_store` does the I/O.

File layout, which the parser relies on and the tests pin down: free text, then a header line
that begins with a comma and names the columns (`,Mkt-RF,SMB,HML,RMW,CMA,RF`), then one line per
trading day (`19630701,  -0.67,  0.01, ...`), then free text again. A value of -99.99 or -999
means missing.

Factors: `MKT_RF` market minus risk-free, `SMB` size, `HML` value, `RMW` profitability, `CMA`
investment, `MOM` momentum (the library names it `Mom` in the US file and `WML` elsewhere) and
`RF` the one-month risk-free rate.
"""

import datetime as dt
import io
import re
import zipfile
from dataclasses import dataclass

Region = str  # "developed" | "europe" | "us"

REGIONS: tuple[Region, ...] = ("developed", "europe", "us")
BASE_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
MISSING = (-99.99, -999.0)

_COLUMN_NAMES = {
    "MKT-RF": "MKT_RF",
    "SMB": "SMB",
    "HML": "HML",
    "RMW": "RMW",
    "CMA": "CMA",
    "RF": "RF",
    "MOM": "MOM",
    "WML": "MOM",
}
_ROW = re.compile(r"^\s*(\d{8})\s*,")


@dataclass(frozen=True)
class FactorFile:
    region: Region
    name: str  # file stem on the library's server, without the _CSV.zip suffix

    @property
    def url(self) -> str:
        return f"{BASE_URL}{self.name}_CSV.zip"


FILES: tuple[FactorFile, ...] = (
    FactorFile("developed", "Developed_5_Factors_Daily"),
    FactorFile("developed", "Developed_Mom_Factor_Daily"),
    FactorFile("europe", "Europe_5_Factors_Daily"),
    FactorFile("europe", "Europe_Mom_Factor_Daily"),
    FactorFile("us", "F-F_Research_Data_5_Factors_2x3_daily"),
    FactorFile("us", "F-F_Momentum_Factor_daily"),
)

# factor -> [(date, fraction per day)]
Factors = dict[str, list[tuple[dt.date, float]]]


class FrenchFormatError(ValueError):
    """The file does not look like a Data Library daily factor file."""


def parse_daily(text: str) -> Factors:
    """Daily factor returns as fractions (the library's percent divided by 100)."""
    columns: list[str | None] | None = None
    out: Factors = {}
    started = False
    for line in text.splitlines():
        if columns is None:
            if line.lstrip().startswith(",") and re.search(r"[A-Za-z]", line):
                columns = [_COLUMN_NAMES.get(c.strip().upper()) for c in line.split(",")[1:]]
                if not any(columns):
                    raise FrenchFormatError(f"unrecognised factor columns: {line.strip()!r}")
            continue
        match = _ROW.match(line)
        if match is None:
            if started and line.strip():
                break  # trailing notes
            continue
        started = True
        day = dt.datetime.strptime(match.group(1), "%Y%m%d").date()
        for name, raw in zip(columns, line.split(",")[1:], strict=False):
            if name is None:
                continue
            try:
                value = float(raw)
            except ValueError:
                continue
            if value in MISSING:
                continue
            out.setdefault(name, []).append((day, value / 100.0))
    if columns is None or not out:
        raise FrenchFormatError("no factor header or no daily rows found")
    return out


def parse_zip(payload: bytes) -> Factors:
    """The factors in the one CSV inside a library zip file."""
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise FrenchFormatError("the zip file holds no CSV")
        return parse_daily(archive.read(names[0]).decode("latin-1"))
