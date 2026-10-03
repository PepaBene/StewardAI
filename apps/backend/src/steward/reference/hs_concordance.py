"""HS revision correspondence at 6-digit level, from the UN Statistics Division tables.

Source (free, official, no key): the "Conversion and Correlation Tables" the UN Statistics
Division publishes at https://unstats.un.org/unsd/classifications/Econ/ (the tables UN Comtrade
itself documents).  One workbook per revision pair; its "Correlation" sheet lists every
(from code, to code, relationship) pair at 6 digits, relationship one of `1:1`, `1:n`, `n:1`,
`n:n`, read from the first revision to the second.

This module is the reproducible path from that file to `public.hs_concordance`:

    python -m steward.reference.hs_concordance fetch HS2022 HS2017 --emit-sql seed.sql
    python -m steward.reference.hs_concordance fetch HS2022 HS2017 --load    # DATABASE_URL

`--emit-sql` is how migration 20261007160000 was produced; the source URL, byte hash and
fetch date are stored on every row (`source_url`, `source_sha256`), so a loaded row says exactly
which file it came from.  Nothing is inferred: a code with no row has no known counterpart
(`unknown`), never "the same".

Reading (`equivalence`): a 6-digit code is `identical` only when its single row is `1:1`.  A
2- or 4-digit chapter/heading is `aggregate_identical` when every 6-digit row touching it, on
either side, stays inside it (subheadings may be re-cut within a heading and the heading's
trade still adds up the same).  Anything else is `related` (the codes it maps to are listed,
never summed) or `unknown`.  Revisions are not chained: HS2022 -> HS2012 is answered only from
a table that was loaded for exactly that pair.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree  # element types only; parsing goes through xml_guard

import asyncpg

from steward.macro.hs import normalize_hs_revision
from steward.sources.xml_guard import safe_fromstring

UNSD_TABLES = "https://unstats.un.org/unsd/classifications/Econ/tables/"
# (from revision, to revision) -> published workbook.  Add a pair here and load it; nothing else
# in the code needs to know which pairs exist.
SOURCES: dict[tuple[str, str], str] = {
    ("HS2022", "HS2017"): UNSD_TABLES + "HS2022toHS2017ConversionAndCorrelationTables.xlsx",
    ("HS2017", "HS2012"): UNSD_TABLES + "HS2017toHS2012ConversionAndCorrelationTables.xlsx",
}
RELATIONS = ("1:1", "1:n", "n:1", "n:n")
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_CODE = re.compile(r"^[0-9]{6}$")
# 999999 is the tables' placeholder for "no HS code"; it is not a commodity.
_PLACEHOLDER = "999999"


class ConcordanceFormatError(ValueError):
    """The workbook is not a UNSD correlation table this parser understands."""


@dataclass(frozen=True, slots=True)
class CorrelationTable:
    from_revision: str
    to_revision: str
    pairs: tuple[tuple[str, str, str], ...]  # (from_code, to_code, relation), sorted
    source_url: str
    source_sha256: str


def _cell_values(sheet: bytes, strings: list[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row in safe_fromstring(sheet).iter(f"{{{_NS['m']}}}row"):
        values: dict[str, str] = {}
        for cell in row.findall("m:c", _NS):
            v = cell.find("m:v", _NS)
            if v is None or v.text is None:
                continue
            column = re.match(r"[A-Z]+", cell.get("r", ""))
            if column is None:
                continue
            values[column.group()] = strings[int(v.text)] if cell.get("t") == "s" else v.text
        rows.append(values)
    return rows


def _sheet_paths(book: zipfile.ZipFile) -> dict[str, str]:
    """Sheet name -> archive path, through the workbook relationships."""
    workbook = safe_fromstring(book.read("xl/workbook.xml"))
    rels = safe_fromstring(book.read("xl/_rels/workbook.xml.rels"))
    targets = {r.get("Id", ""): r.get("Target", "") for r in rels}
    rid = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    out: dict[str, str] = {}
    for sheet in workbook.iter(f"{{{_NS['m']}}}sheet"):
        target = targets.get(sheet.get(rid, ""), "")
        out[sheet.get("name", "")] = (
            target.lstrip("/") if target.startswith("/") else "xl/" + target
        )
    return out


def parse_correlation_xlsx(
    data: bytes, from_revision: str, to_revision: str, *, source_url: str
) -> CorrelationTable:
    """Read the "Correlation" sheet of a UNSD workbook.

    The header row names the two revisions and the `Relationship` column; the revisions named
    there must be the ones the caller expects (a swapped file would silently invert every
    `1:n`).  Rows that are not a 6-digit pair with a known relationship are an error, except the
    `999999` placeholder, which is skipped."""
    a, b = normalize_hs_revision(from_revision), normalize_hs_revision(to_revision)
    if a is None or b is None:
        raise ConcordanceFormatError("both revisions are required")
    try:
        book = zipfile.ZipFile(BytesIO(data))
        strings = [
            "".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t"))
            for si in safe_fromstring(book.read("xl/sharedStrings.xml")).findall("m:si", _NS)
        ]
        paths = _sheet_paths(book)
        name = next((n for n in paths if "correlation" in n.casefold()), None)
        if name is None:
            raise ConcordanceFormatError(f"no Correlation sheet in {sorted(paths)}")
        rows = _cell_values(book.read(paths[name]), strings)
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, ValueError) as exc:
        raise ConcordanceFormatError(f"not a readable workbook: {exc}") from exc

    header_at = next(
        (i for i, r in enumerate(rows) if any(v.strip() == "Relationship" for v in r.values())),
        None,
    )
    if header_at is None:
        raise ConcordanceFormatError("no header row with a Relationship column")
    header = rows[header_at]
    relation_col = next(c for c, v in header.items() if v.strip() == "Relationship")
    revision_cols = sorted(
        (c for c, v in header.items() if c != relation_col and normalize_hs_revision(v)),
        key=lambda c: (len(c), c),
    )
    if len(revision_cols) != 2:
        raise ConcordanceFormatError(f"expected two revision columns, found {header}")
    named = [normalize_hs_revision(header[c]) for c in revision_cols]
    if named != [a, b]:
        raise ConcordanceFormatError(f"workbook is {named[0]} -> {named[1]}, expected {a} -> {b}")
    pairs: set[tuple[str, str, str]] = set()
    for row in rows[header_at + 1 :]:
        if not row:
            continue
        src, dst = row.get(revision_cols[0], "").strip(), row.get(revision_cols[1], "").strip()
        relation = row.get(relation_col, "").strip()
        if src == _PLACEHOLDER and dst == _PLACEHOLDER:
            continue
        if not (_CODE.match(src) and _CODE.match(dst) and relation in RELATIONS):
            raise ConcordanceFormatError(f"unexpected row {row}")
        pairs.add((src, dst, relation))
    if not pairs:
        raise ConcordanceFormatError("the correlation sheet has no rows")
    return CorrelationTable(
        a, b, tuple(sorted(pairs)), source_url, hashlib.sha256(data).hexdigest()
    )


def check_consistency(table: CorrelationTable) -> list[str]:
    """Problems that mean the table cannot be trusted: a `1:1` pair whose codes also appear in
    other pairs, or a relation label that disagrees with the pairs' own degree."""
    by_src: dict[str, set[str]] = {}
    by_dst: dict[str, set[str]] = {}
    for src, dst, _ in table.pairs:
        by_src.setdefault(src, set()).add(dst)
        by_dst.setdefault(dst, set()).add(src)
    problems: list[str] = []
    for src, dst, relation in table.pairs:
        one_to_many = len(by_src[src]) > 1
        many_to_one = len(by_dst[dst]) > 1
        expected = (
            "n:n"
            if one_to_many and many_to_one
            else "1:n"
            if one_to_many
            else "n:1"
            if many_to_one
            else "1:1"
        )
        if relation != expected:
            problems.append(f"{src}->{dst} is labelled {relation}, its degree says {expected}")
    return problems


def fetch_workbook(url: str) -> bytes:
    """Download a table through the configured proxy (HTTPS_PROXY / CA bundle are honoured)."""
    import httpx

    response = httpx.get(url, timeout=120, follow_redirects=True)
    response.raise_for_status()
    return response.content


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def seed_sql(table: CorrelationTable, *, fetched: dt.date) -> str:
    """The statement a migration runs to seed one table (deterministic for a given workbook).

    Rows are carried as `from|to|relation` lines in one dollar-quoted string, so the migration
    stays small and a diff of two loads is a diff of lines."""
    lines = "\n".join(f"{s}|{d}|{r}" for s, d, r in table.pairs)
    note = f"UNSD correlation table, fetched {fetched.isoformat()}"
    return (
        "insert into public.hs_concordance"
        "(from_revision, from_code, to_revision, to_code, relation, note, source_url,"
        " source_sha256)\n"
        f"select {_quote(table.from_revision)}, split_part(l, '|', 1), "
        f"{_quote(table.to_revision)}, split_part(l, '|', 2),\n"
        f"       split_part(l, '|', 3), {_quote(note)}, {_quote(table.source_url)}, "
        f"{_quote(table.source_sha256)}\n"
        f"  from regexp_split_to_table($hs${lines}\n$hs$, E'\\n') l\n"
        " where l <> ''\n"
        "on conflict (from_revision, from_code, to_revision, to_code) do nothing;\n"
    )


async def load_table(
    pool: asyncpg.Pool, table: CorrelationTable, *, fetched: dt.date | None = None
) -> int:
    """Upsert one table; returns the number of rows written.  Rows of the same revision pair
    from an earlier load that the new file no longer lists are removed."""
    note = f"UNSD correlation table, fetched {(fetched or dt.date.today()).isoformat()}"
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "delete from public.hs_concordance where from_revision=$1 and to_revision=$2"
            " and source_url is not null",
            table.from_revision,
            table.to_revision,
        )
        await conn.executemany(
            "insert into public.hs_concordance(from_revision, from_code, to_revision, to_code,"
            " relation, note, source_url, source_sha256) values ($1,$2,$3,$4,$5,$6,$7,$8)"
            " on conflict (from_revision, from_code, to_revision, to_code) do update set"
            " relation=excluded.relation, note=excluded.note, source_url=excluded.source_url,"
            " source_sha256=excluded.source_sha256",
            [
                (
                    table.from_revision,
                    s,
                    table.to_revision,
                    d,
                    r,
                    note,
                    table.source_url,
                    table.source_sha256,
                )
                for s, d, r in table.pairs
            ],
        )
    return len(table.pairs)


# ----------------------------------------------------------------------------- reading
Status = Literal["same_revision", "identical", "aggregate_identical", "related", "unknown"]


@dataclass(frozen=True, slots=True)
class Equivalence:
    """What a code of one revision is in another revision.

    * `same_revision`, `identical`, `aggregate_identical`: `codes` is the code to read trade
      for in the target revision (the same code for an aggregate);
    * `related`: `codes` are the target codes the code maps to or from under `relation`
      (`1:n`, `n:1`, `n:n`, or `moved` for a heading whose subheadings left it); they are NOT
      equivalent, and their values must not be added to or compared with the original's;
    * `unknown`: no loaded table covers the code for this revision pair."""

    status: Status
    codes: tuple[str, ...]
    relation: str | None
    basis: str

    @property
    def comparable(self) -> bool:
        return self.status in ("same_revision", "identical", "aggregate_identical")


_TOUCHING = """
select from_code as src, to_code as dst, relation, source_url is not null as table_row
  from public.hs_concordance
 where from_revision=$1 and to_revision=$2 and (from_code like $3 or to_code like $3)
union all
select to_code, from_code,
       case relation when '1:n' then 'n:1' when 'n:1' then '1:n' else relation end,
       source_url is not null
  from public.hs_concordance
 where from_revision=$2 and to_revision=$1 and (from_code like $3 or to_code like $3)
"""


async def equivalence(
    pool: asyncpg.Pool | asyncpg.Connection, code: str, from_revision: str, to_revision: str
) -> Equivalence:
    """Resolve `code` (2, 4 or 6 digits, as `from_revision` defines it) in `to_revision`."""
    a, b = normalize_hs_revision(from_revision), normalize_hs_revision(to_revision)
    if a is None or b is None or not re.fullmatch(r"[0-9]{2}|[0-9]{4}|[0-9]{6}", code):
        return Equivalence("unknown", (), None, "revision or code is not valid")
    if a == b:
        return Equivalence("same_revision", (code,), None, "same revision")
    rows = await pool.fetch(_TOUCHING, a, b, code + "%")
    detailed = [r for r in rows if r["table_row"]]
    if len(code) == 6:
        mine = [r for r in detailed if r["src"] == code]
        if not mine:
            return Equivalence("unknown", (), None, f"no {a}->{b} table row for {code}")
        if len(mine) == 1 and mine[0]["relation"] == "1:1":
            return Equivalence(
                "identical", (mine[0]["dst"],), "1:1", "UNSD correlation table, 1:1 row"
            )
        relations = {r["relation"] for r in mine}
        relation = relations.pop() if len(relations) == 1 else "n:n"
        return Equivalence(
            "related",
            tuple(sorted({r["dst"] for r in mine})),
            relation,
            "UNSD correlation table: not a 1:1 correspondence",
        )
    if detailed:
        outside = {
            c[: len(code)] for r in detailed for c in (r["src"], r["dst"]) if not c.startswith(code)
        }
        if not outside:
            return Equivalence(
                "aggregate_identical",
                (code,),
                None,
                f"every 6-digit row touching {code} stays inside it (UNSD correlation table)",
            )
        return Equivalence(
            "related",
            tuple(sorted(outside)),
            "moved",
            f"subheadings moved between {code} and these codes (UNSD correlation table)",
        )
    if len(code) == 2:
        chapter = [r for r in rows if r["src"] == code and r["dst"] == code]
        if chapter and chapter[0]["relation"] == "chapter":
            return Equivalence(
                "aggregate_identical", (code,), "chapter", "chapter exists in both revisions"
            )
    return Equivalence("unknown", (), None, f"no {a}->{b} table row under {code}")


# ----------------------------------------------------------------------------- command line
def _argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch", help="download one revision pair and load or emit it")
    fetch.add_argument("from_revision")
    fetch.add_argument("to_revision")
    fetch.add_argument("--file", help="a workbook already downloaded (skips the download)")
    fetch.add_argument("--emit-sql", help="write the seed statement to this path")
    fetch.add_argument("--load", action="store_true", help="load into DATABASE_URL")
    return parser


def _prepare(args: argparse.Namespace) -> CorrelationTable:
    a, b = normalize_hs_revision(args.from_revision), normalize_hs_revision(args.to_revision)
    url = SOURCES.get((a or "", b or ""))
    if url is None:
        raise SystemExit(f"no source registered for {a} -> {b}; known: {sorted(SOURCES)}")
    data = Path(args.file).read_bytes() if args.file else fetch_workbook(url)
    table = parse_correlation_xlsx(data, a or "", b or "", source_url=url)
    problems = check_consistency(table)
    if problems:
        raise SystemExit("inconsistent table:\n" + "\n".join(problems[:20]))
    print(f"{table.from_revision} -> {table.to_revision}: {len(table.pairs)} pairs")
    print(f"sha256 {table.source_sha256}")
    if args.emit_sql:
        Path(args.emit_sql).write_text(seed_sql(table, fetched=dt.date.today()), encoding="utf-8")
    return table


async def _load(table: CorrelationTable) -> None:
    import os

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=1)
    try:
        print(f"loaded {await load_table(pool, table)} rows")
    finally:
        await pool.close()


if __name__ == "__main__":
    _args = _argparser().parse_args()
    _table = _prepare(_args)
    if _args.load:
        asyncio.run(_load(_table))
