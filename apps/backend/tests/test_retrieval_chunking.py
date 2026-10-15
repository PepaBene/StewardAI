"""Chunking keeps structure: headings start sections, tables are never cut mid-row, sizes hold."""

from steward.retrieval import chunking

FILING = """Annual report
PART I
ITEM 1. BUSINESS
We design phones and sell them through carriers and retailers. Our largest customer
accounted for 21% of net sales in fiscal 2025.

ITEM 1A. RISK FACTORS
We rely on a small number of suppliers for key components. A disruption at one of them
could delay shipments. Some components are single-sourced.

ITEM 7. RESULTS
Segment | 2025 | 2024
Phones | 190 | 180
Services | 90 | 80
Wearables | 40 | 41
"""


def test_headings_start_sections_with_a_path() -> None:
    result = chunking.chunk(FILING, "Annual report")
    paths = [p.section_path for p in result.parents]
    assert any("ITEM 1A. RISK FACTORS" in p.upper() for p in paths)
    assert all(p.startswith("Annual report") for p in paths)
    risk = next(c for c in result.children if "single-sourced" in c.text)
    assert "RISK FACTORS" in risk.section_path.upper()
    assert "dependence" not in result.parents[risk.parent].text  # parent is that section only
    assert "single-sourced" in result.parents[risk.parent].text


def test_a_table_stays_whole_and_apart_from_prose() -> None:
    result = chunking.chunk(FILING, "Annual report")
    table = next(c for c in result.children if c.text.startswith("Segment |"))
    assert table.text.count("\n") == 3 and "Wearables | 40 | 41" in table.text


def test_a_long_table_splits_between_rows_and_repeats_its_header() -> None:
    header = "Item | Q1 | Q2 | Q3 | Q4"
    rows = [f"Line {i} | {i} | {i + 1} | {i + 2} | {i + 3}" for i in range(120)]
    result = chunking.chunk("RESULTS\n" + "\n".join([header, *rows]), "T")
    pieces = [c.text for c in result.children]
    assert len(pieces) > 1
    assert all(p.startswith(header) for p in pieces)
    assert all(len(p) <= chunking.CHILD_MAX + 80 for p in pieces)
    body = [line for p in pieces for line in p.split("\n")[1:]]
    assert body == rows  # no row lost, none duplicated, order kept


def test_long_prose_splits_at_sentence_ends_within_the_limit() -> None:
    prose = " ".join(
        f"Sentence number {i} says something specific about topic {i}." for i in range(80)
    )
    result = chunking.chunk(prose, "Doc")
    assert len(result.children) > 2
    assert all(len(c.text) <= chunking.CHILD_MAX for c in result.children)
    assert all(c.text.rstrip().endswith(".") for c in result.children)


def test_no_headings_is_one_section_named_after_the_document() -> None:
    result = chunking.chunk("Just a paragraph of text about margins.", "Memo")
    assert [p.section_path for p in result.parents] == ["Memo"]
    assert len(result.children) == 1


def test_unstructured_is_a_single_child_without_a_parent() -> None:
    result = chunking.chunk("Headline. Summary lead.", "Headline", structured=False)
    assert result.parents == [] and len(result.children) == 1
    assert result.children[0].parent == -1


def test_empty_text_has_no_chunks() -> None:
    assert chunking.chunk("   \n ", "x") == chunking.Chunked([], [])


def test_content_hash_changes_with_the_text() -> None:
    assert chunking.content_hash("a") != chunking.content_hash("b")
    assert chunking.content_hash("a") == chunking.content_hash("a")
