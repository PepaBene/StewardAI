"""P2.4 wiring: pick a filing's note blocks and store their tables at document offsets."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from steward.disclosure.note_job import (
    extract_notes_for_document,
    monetary_currency,
    period_end_of,
)
from steward.disclosure.parse import IxContext, IxFact, ParsedDocument, TextBlock

UTC = dt.UTC

DEBT = (
    "Scheduled maturities of long-term debt (in millions)\n"
    "2027 100\n2028 200\n2029 300\n2030 400\n2031 500\nThereafter 1,500\nTotal 3,000\n"
)
PREFIX = "Annual report front matter. "


def fact(unit: str | None) -> IxFact:
    return IxFact(
        "us-gaap:Revenues", "c1", unit, None, 0, "", "1", Decimal(1), None, "f", False, 0, 1
    )


def document(*, unit_measure: str | None = "iso4217:USD", with_end: bool = True) -> ParsedDocument:
    text = PREFIX + DEBT
    return ParsedDocument(
        text=text,
        facts=[fact("u1")],
        text_blocks=[
            TextBlock("us-gaap:DebtDisclosureTextBlock", "c1", len(PREFIX), len(text)),
            TextBlock("us-gaap:IncomeTaxDisclosureTextBlock", "c1", 0, 5),
        ],
        contexts={
            "c1": IxContext(
                "c1", dt.date(2025, 1, 1), dt.date(2025, 12, 31) if with_end else None, None, ()
            )
        },
        units={"u1": unit_measure} if unit_measure else {},
    )


class RecordingPool:
    def __init__(self) -> None:
        self.args: list[tuple[Any, ...]] = []

    async def fetchval(self, _sql: str, *args: Any) -> int:
        self.args.append(args)
        return 1


def test_currency_and_period_come_from_the_filing_itself() -> None:
    doc = document()
    assert monetary_currency(doc) == "USD"
    assert period_end_of(doc) == dt.date(2025, 12, 31)


def test_the_parsers_bare_unit_names_are_read_as_currencies_and_ratios_are_not() -> None:
    assert monetary_currency(document(unit_measure="USD")) == "USD"
    assert monetary_currency(document(unit_measure="USD/shares")) is None
    assert monetary_currency(document(unit_measure="pure")) is None


def test_a_share_unit_is_not_a_currency() -> None:
    assert monetary_currency(document(unit_measure="xbrli:shares")) is None


@pytest.mark.asyncio
async def test_a_debt_block_is_stored_with_offsets_in_the_whole_document() -> None:
    pool = RecordingPool()
    result = await extract_notes_for_document(
        pool,  # type: ignore[arg-type]
        issuer_id=1,
        ticker="AAA",
        accession="0001-26-000001",
        form="10-K",
        known_at=dt.datetime(2026, 2, 20, tzinfo=UTC),
        source_url="https://sec.example/x",
        doc=document(),
    )
    assert result.blocks_read == 1 and result.stored > 0
    starts = [int(a[15]) for a in pool.args]  # $16 is the note start offset
    assert min(starts) >= len(PREFIX)  # block-relative offsets were moved into document space


@pytest.mark.asyncio
async def test_no_monetary_unit_means_nothing_is_stored_and_the_reason_is_given() -> None:
    pool = RecordingPool()
    result = await extract_notes_for_document(
        pool,  # type: ignore[arg-type]
        issuer_id=1,
        ticker=None,
        accession="a",
        form=None,
        known_at=dt.datetime(2026, 2, 20, tzinfo=UTC),
        source_url="u",
        doc=document(unit_measure=None),
    )
    assert result.stored == 0 and pool.args == []
    assert result.skipped == ("the filing declares no monetary unit",)
