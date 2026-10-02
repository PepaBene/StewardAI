"""Builders for the fundamentals-stream tests: a synthetic XBRL instance, an xlsx, a pdf."""

from __future__ import annotations

import datetime as dt
import io
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal
from xml.sax.saxutils import escape

NS = (
    'xmlns="http://www.xbrl.org/2003/instance" '
    'xmlns:xbrli="http://www.xbrl.org/2003/instance" '
    'xmlns:xbrldi="http://xbrl.org/2006/xbrldi" '
    'xmlns:us-gaap="http://fasb.org/us-gaap/2025" '
    'xmlns:srt="http://fasb.org/srt/2025" '
    'xmlns:dei="http://xbrl.sec.gov/dei/2025" '
    'xmlns:iso4217="http://www.xbrl.org/2003/iso4217" '
    'xmlns:co="http://example.test/co"'
)


@dataclass
class Ctx:
    id: str
    start: dt.date | None
    end: dt.date
    members: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Fact:
    concept: str
    ctx: str
    value: str | Decimal | int
    unit: str | None = "USD"
    decimals: str | None = "-3"


def instance_xml(contexts: list[Ctx], facts: list[Fact], units: set[str] | None = None) -> bytes:
    out = [f'<?xml version="1.0" encoding="utf-8"?><xbrl {NS}>']
    for c in contexts:
        period = (
            f"<startDate>{c.start}</startDate><endDate>{c.end}</endDate>"
            if c.start
            else f"<instant>{c.end}</instant>"
        )
        segment = ""
        if c.members:
            members = "".join(
                f'<xbrldi:explicitMember dimension="{a}">{m}</xbrldi:explicitMember>'
                for a, m in c.members
            )
            segment = f"<segment>{members}</segment>"
        out.append(
            f'<context id="{c.id}"><entity><identifier scheme="http://www.sec.gov/CIK">'
            f"0000000001</identifier>{segment}</entity><period>{period}</period></context>"
        )
    for unit in sorted(units or {"USD", "shares", "USD/shares", "pure"}):
        if "/" in unit:
            top, bottom = unit.split("/")
            out.append(
                f'<unit id="{unit.replace("/", "_")}"><divide><unitNumerator>'
                f"<measure>iso4217:{top}</measure></unitNumerator><unitDenominator>"
                f"<measure>xbrli:{bottom}</measure></unitDenominator></divide></unit>"
            )
        elif unit == "shares" or unit == "pure":
            out.append(f'<unit id="{unit}"><measure>xbrli:{unit}</measure></unit>')
        else:
            out.append(f'<unit id="{unit}"><measure>iso4217:{unit}</measure></unit>')
    for f in facts:
        attrs = f'contextRef="{f.ctx}"'
        if f.unit:
            attrs += f' unitRef="{f.unit.replace("/", "_")}"'
        if f.decimals is not None and f.unit:
            attrs += f' decimals="{f.decimals}"'
        out.append(f"<{f.concept} {attrs}>{escape(str(f.value))}</{f.concept}>")
    out.append("</xbrl>")
    return "".join(out).encode()


def xlsx_bytes(rows: list[list[str | int | float]]) -> bytes:
    """A minimal workbook: shared strings for text, numbers as numbers."""
    strings: list[str] = []

    def sid(text: str) -> int:
        if text not in strings:
            strings.append(text)
        return strings.index(text)

    sheet_rows = []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(row):
            ref = f"{chr(65 + c)}{r}"
            if isinstance(value, str):
                cells.append(f'<c r="{ref}" t="s"><v>{sid(value)}</v></c>')
            else:
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
        sheet_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    sheet = (
        f'<?xml version="1.0"?><worksheet xmlns="{main}"><sheetData>'
        f"{''.join(sheet_rows)}</sheetData></worksheet>"
    )
    shared = (
        f'<?xml version="1.0"?><sst xmlns="{main}" count="{len(strings)}" '
        f'uniqueCount="{len(strings)}">'
        + "".join(f"<si><t>{escape(s)}</t></si>" for s in strings)
        + "</sst>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/'
            'content-types"/>',
        )
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
        archive.writestr("xl/sharedStrings.xml", shared)
    return buffer.getvalue()


def pdf_bytes(lines: list[str]) -> bytes:
    """A one-page PDF drawing each line with Helvetica (uncompressed content stream)."""
    stream_lines = ["BT", "/F1 11 Tf", "14 TL", "50 780 Td"]
    for line in lines:
        text = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream_lines.append(f"({text}) Tj T*")
    stream_lines.append("ET")
    content = "\n".join(stream_lines).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)
