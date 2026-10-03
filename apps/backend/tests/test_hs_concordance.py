"""HS concordance at 6-digit level from the UNSD correlation tables (audit: chapter level only)."""

from __future__ import annotations

import datetime as dt
import zipfile
from io import BytesIO
from xml.sax.saxutils import escape

import asyncpg
import pytest

from steward.reference.hs_concordance import (
    SOURCES,
    ConcordanceFormatError,
    CorrelationTable,
    check_consistency,
    equivalence,
    load_table,
    parse_correlation_xlsx,
    seed_sql,
)

URL = "https://unstats.example/HS.xlsx"


def workbook(
    header: list[tuple[str, str]], rows: list[list[str]], *, sheet: str = "Correlations"
) -> bytes:
    """A minimal .xlsx with the layout of the UNSD files: a conversion sheet, then a
    correlation sheet with a 'Between' line and a header row (cells keyed by column letter)."""
    strings: list[str] = []

    def sid(text: str) -> int:
        if text not in strings:
            strings.append(text)
        return strings.index(text)

    def row(n: int, cells: list[tuple[str, str]]) -> str:
        body = "".join(f'<c r="{col}{n}" t="s"><v>{sid(v)}</v></c>' for col, v in cells)
        return f'<row r="{n}">{body}</row>'

    sheet2 = [row(1, [("A", "Between")]), row(2, header)]
    letters = [c for c, _ in header]
    for i, values in enumerate(rows, start=3):
        sheet2.append(row(i, list(zip(letters, values, strict=False))))
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rid = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    out = BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr(
            "xl/workbook.xml",
            f'<workbook {ns} {rid}><sheets><sheet name="Conversions" sheetId="1" r:id="rId1"/>'
            f'<sheet name="{sheet}" sheetId="2" r:id="rId2"/></sheets></workbook>',
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Target="worksheets/sheet2.xml"/></Relationships>',
        )
        z.writestr("xl/worksheets/sheet1.xml", f"<worksheet {ns}><sheetData/></worksheet>")
        z.writestr(
            "xl/worksheets/sheet2.xml",
            f"<worksheet {ns}><sheetData>{''.join(sheet2)}</sheetData></worksheet>",
        )
        z.writestr(
            "xl/sharedStrings.xml",
            f"<sst {ns}>" + "".join(f"<si><t>{escape(s)}</t></si>" for s in strings) + "</sst>",
        )
    return out.getvalue()


ROWS = [
    ["010121", "010121", "1:1"],
    ["970610", "970600", "n:1"],
    ["970690", "970600", "n:1"],
    ["030721", "030721", "1:n"],
    ["030721", "030791", "n:n"],
    ["030729", "030791", "n:1"],
    ["999999", "999999", "1:1"],
]


def test_parser_reads_both_published_layouts_and_skips_the_placeholder() -> None:
    # HS2022 -> HS2017 file: revisions in A and B (the second with a leading blank).
    a = parse_correlation_xlsx(
        workbook([("A", "HS2022"), ("B", " HS2017"), ("C", "Relationship")], ROWS),
        "H6",
        "hs 2017",
        source_url=URL,
    )
    # HS2017 -> HS2012 file: revisions in A and C, relationship in D.
    b = parse_correlation_xlsx(
        workbook(
            [("A", "HS 2017"), ("C", "HS 2012"), ("D", "Relationship")],
            ROWS,
        ),
        "HS2017",
        "HS2012",
        source_url=URL,
    )
    assert (a.from_revision, a.to_revision) == ("HS2022", "HS2017")
    assert (b.from_revision, b.to_revision) == ("HS2017", "HS2012")
    assert a.pairs == b.pairs and ("999999", "999999", "1:1") not in a.pairs
    assert len(a.pairs) == 6 and len(a.source_sha256) == 64
    assert check_consistency(a) == []


def test_parser_refuses_a_swapped_or_malformed_workbook() -> None:
    header = [("A", "HS2022"), ("B", "HS2017"), ("C", "Relationship")]
    with pytest.raises(ConcordanceFormatError, match="expected HS2017 -> HS2022"):
        parse_correlation_xlsx(workbook(header, ROWS), "HS2017", "HS2022", source_url=URL)
    with pytest.raises(ConcordanceFormatError, match="unexpected row"):
        parse_correlation_xlsx(
            workbook(header, [["01012", "010121", "1:1"]]), "HS2022", "HS2017", source_url=URL
        )
    with pytest.raises(ConcordanceFormatError, match="unexpected row"):
        parse_correlation_xlsx(
            workbook(header, [["010121", "010121", "1:2"]]), "HS2022", "HS2017", source_url=URL
        )
    with pytest.raises(ConcordanceFormatError, match="no Correlation sheet"):
        parse_correlation_xlsx(
            workbook(header, ROWS, sheet="Other"), "HS2022", "HS2017", source_url=URL
        )
    with pytest.raises(ConcordanceFormatError, match="not a readable workbook"):
        parse_correlation_xlsx(b"not a zip", "HS2022", "HS2017", source_url=URL)


def test_consistency_check_catches_a_label_that_contradicts_the_pairs() -> None:
    table = CorrelationTable(
        "HS2022",
        "HS2017",
        (("010121", "010121", "1:1"), ("010121", "010129", "1:1")),
        URL,
        "0" * 64,
    )
    assert len(check_consistency(table)) == 2
    assert all(url.startswith("https://unstats.un.org/") for url in SOURCES.values())


async def _count(pool: asyncpg.Pool, a: str, b: str) -> int:
    return int(
        await pool.fetchval(
            "select count(*) from public.hs_concordance where from_revision=$1 and to_revision=$2"
            " and source_url is not null",
            a,
            b,
        )
    )


@pytest.mark.db
async def test_migration_seeds_the_unsd_tables_with_provenance(backend_pool: asyncpg.Pool) -> None:
    assert await _count(backend_pool, "HS2022", "HS2017") == 15656
    assert await _count(backend_pool, "HS2017", "HS2012") == 6140
    rows = await backend_pool.fetch(
        "select distinct source_url, source_sha256 from public.hs_concordance"
        " where source_url is not null order by 1"
    )
    assert [r["source_url"] for r in rows] == sorted(SOURCES.values())
    assert all(len(r["source_sha256"]) == 64 for r in rows)
    # The existing chapter rows are untouched and carry no table provenance.
    assert (
        await backend_pool.fetchval(
            "select count(*) from public.hs_concordance where relation='chapter'"
            " and source_url is null"
        )
        == 192
    )
    bad = await backend_pool.fetchval(
        "select count(*) from public.hs_concordance where source_url is not null"
        " and (from_code !~ '^[0-9]{6}$' or to_code !~ '^[0-9]{6}$'"
        "      or relation not in ('1:1','1:n','n:1','n:n'))"
    )
    assert bad == 0


@pytest.mark.db
async def test_equivalence_distinguishes_identical_related_and_unknown(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    same = await equivalence(pool, "854231", "H6", "HS2022")
    assert same.status == "same_revision" and same.comparable

    one = await equivalence(pool, "010121", "HS2022", "HS2017")
    assert (one.status, one.codes, one.relation) == ("identical", ("010121",), "1:1")
    assert one.comparable

    # Two HS2022 codes fold into one HS2017 code: not comparable code by code.
    merged = await equivalence(pool, "970610", "HS2022", "HS2017")
    assert merged.status == "related" and merged.relation == "n:1" and merged.codes == ("970600",)
    assert not merged.comparable
    # Read the other way round the same pair is 1:n and lists both HS2022 codes.
    split = await equivalence(pool, "970600", "HS2017", "HS2022")
    assert split.status == "related" and split.relation == "1:n"
    assert split.codes == ("970610", "970690")

    # A heading whose subheadings stay inside it is identical as an aggregate even when its
    # subheadings are re-cut; one that lost subheadings to other headings is not.
    heading = await equivalence(pool, "0101", "HS2022", "HS2017")
    assert heading.status == "aggregate_identical" and heading.codes == ("0101",)
    moved = await equivalence(pool, "0403", "HS2022", "HS2017")
    assert moved.status == "related" and moved.relation == "moved" and "19" + "01" in moved.codes
    chapter = await equivalence(pool, "04", "HS2022", "HS2017")
    assert chapter.status == "related" and "19" in chapter.codes

    # Revisions are not chained and an absent code is unknown, never "same".
    assert (await equivalence(pool, "010121", "HS2022", "HS2012")).status == "unknown"
    assert (await equivalence(pool, "999998", "HS2022", "HS2017")).status == "unknown"
    assert (await equivalence(pool, "01", "HS2022", "HS2012")).status == "unknown"
    assert (await equivalence(pool, "0101x", "HS2022", "HS2017")).status == "unknown"
    older = await equivalence(pool, "010121", "HS2017", "HS2012")
    assert older.status == "identical"


@pytest.mark.db
async def test_seed_sql_and_loader_produce_the_same_rows(backend_pool: asyncpg.Pool) -> None:
    table = parse_correlation_xlsx(
        workbook([("A", "HS2007"), ("B", "HS2002"), ("C", "Relationship")], ROWS),
        "HS2007",
        "HS2002",
        source_url="https://unstats.example/test.xlsx",
    )
    pool = backend_pool
    await pool.execute(seed_sql(table, fetched=dt.date(2026, 10, 3)))
    seeded = await pool.fetch(
        "select * from public.hs_concordance where from_revision='HS2007'"
        " order by from_code, to_code"
    )
    assert len(seeded) == 6 and seeded[0]["note"] == "UNSD correlation table, fetched 2026-10-03"
    # Re-running the seed is a no-op; loading the same table rewrites the same pairs.
    await pool.execute(seed_sql(table, fetched=dt.date(2026, 10, 3)))
    assert await load_table(pool, table, fetched=dt.date(2026, 10, 3)) == 6
    again = await pool.fetch(
        "select * from public.hs_concordance where from_revision='HS2007'"
        " order by from_code, to_code"
    )
    assert [dict(r) for r in again] == [dict(r) for r in seeded]
    # A newer file that drops a pair removes it; other revision pairs are untouched.
    smaller = CorrelationTable(
        table.from_revision, table.to_revision, table.pairs[:2], table.source_url, "1" * 64
    )
    await load_table(pool, smaller, fetched=dt.date(2026, 10, 4))
    assert await _count(pool, "HS2007", "HS2002") == 2
    assert await _count(pool, "HS2022", "HS2017") == 15656


@pytest.mark.db
async def test_trade_exposure_reads_other_revisions_only_through_the_concordance(
    backend_pool: asyncpg.Pool,
) -> None:
    """A mapping in HS2022 reads Comtrade rows filed under HS2017 only where the UNSD table says
    the product is the same; a split or merged code is reported, not silently mixed in."""
    from decimal import Decimal
    from types import SimpleNamespace

    from steward.data_context.holding import trade_exposure

    pool = backend_pool
    issuer = await pool.fetchval(
        "insert into issuer (legal_name, country) values ('Concordance Co', 'US') returning id"
    )

    async def mapping(code: str) -> None:
        await pool.execute(
            "insert into exposure_mapping (issuer_id, segment, hs_revision, hs_code, basis,"
            " weight, valid_from)"
            " values ($1, 's' || $2::text, 'HS2022', $2, 'reported', 0.1, '2026-01-01')",
            issuer,
            code,
        )

    async def trade(revision: str, code: str, value: str) -> None:
        await pool.execute(
            "insert into trade_observation (reporter, partner, flow, hs_revision, hs_code, period,"
            " trade_value, known_at, source_url) values ('842', '156', 'import', $1, $2,"
            " '2025-01-01', $3, '2026-06-01', 'https://comtradeplus.un.org/x')",
            revision,
            code,
            Decimal(value),
        )

    when = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)

    def effect(code: str) -> SimpleNamespace:
        return SimpleNamespace(
            issuer_id=issuer, geography=None, hs_code=code, weight=Decimal("0.1"), basis="reported"
        )

    # 010121 is 1:1 between the revisions: the HS2017 row is the same product.
    await mapping("010121")
    await trade("HS2017", "010121", "70")
    await trade("HS2022", "010121", "75")  # same flow in both revisions: the mapping's own wins
    # 970610 was merged into 970600 in HS2017: that row must not be shown for it.
    await mapping("970610")
    await trade("HS2017", "970600", "5")
    # 0101 (heading) is identical as an aggregate; its HS2017 row at 4 digits is the same.
    await mapping("0101")
    await trade("HS2017", "0101", "9")
    # HS2012 rows exist but no table links HS2022 to HS2012.
    await trade("HS2012", "010121", "1")

    out = {
        e.hs_code: e
        for e in await trade_exposure(
            pool, [effect("010121"), effect("970610"), effect("0101")], when
        )
    }
    one = out["010121"]
    assert [(r.hs_revision, r.trade_value) for r in one.rows] == [("HS2022", Decimal("75"))]
    assert any("HS2012" in n and "no correspondence table" in n for n in one.revision_notes)
    merged = out["970610"]
    assert merged.rows == [] and merged.gap
    assert any("n:1" in n and "970600" in n for n in merged.revision_notes)
    heading = out["0101"]
    assert [(r.hs_revision, r.hs_code) for r in heading.rows] == [("HS2017", "0101")]
    assert (
        "correspondence" in heading.rows[0].revision_basis
        or "UNSD" in heading.rows[0].revision_basis
    )

    # When only the HS2017 row exists, the 1:1 correspondence makes it usable and says why.
    await pool.execute("delete from trade_observation where hs_revision in ('HS2022','HS2012')")
    only = (await trade_exposure(pool, [effect("010121")], when))[0]
    assert [(r.hs_revision, r.trade_value) for r in only.rows] == [("HS2017", Decimal("70"))]
    assert "UNSD correlation table, 1:1" in only.rows[0].revision_basis
