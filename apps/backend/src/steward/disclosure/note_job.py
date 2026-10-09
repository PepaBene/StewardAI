"""P2.4 wiring: read the note text blocks of one parsed filing and store the extracted tables.

A filing's financial-statement notes are inline-XBRL text blocks. This picks the blocks that
describe debt, leases and share repurchases, runs the note extractors over each block's own text,
and stores the buckets once (`store_note_tables` is idempotent), with offsets moved to the whole
document so a reviewer can find the passage. It never guesses:

  * a block that matches no schedule stores nothing;
  * a schedule that came back as `review` is stored as `review`, and the validators then say
    `needs_review` instead of passing it;
  * the currency is read from the filing's own monetary unit. When the filing has none, nothing
    is stored for it, because note text does not carry a currency.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import asyncpg

from steward.disclosure.note_store import store_note_tables
from steward.disclosure.note_tables import (
    RepurchaseTable,
    Schedule,
    extract_debt_maturities,
    extract_lease_maturities,
    extract_repurchases,
)
from steward.disclosure.parse import ParsedDocument, TextBlock

type Pool = asyncpg.Pool | asyncpg.Connection

#: fragments of the text-block concept name that identify each kind of note
_DEBT = ("debtdisclosure", "longtermdebt", "debtinstrument", "borrowings")
_LEASE = ("leasesdisclosure", "leasedisclosure", "leaseliabilit", "lessee")
_REPURCHASE = ("stockrepurchase", "sharerepurchase", "treasurystock", "issuerpurchase")


@dataclass(frozen=True, slots=True)
class NoteJobResult:
    blocks_read: int
    stored: int
    skipped: tuple[str, ...]


def _kind(name: str) -> str | None:
    low = name.split(":", 1)[-1].lower()
    for kind, needles in (("debt", _DEBT), ("lease", _LEASE), ("repurchase", _REPURCHASE)):
        if any(n in low for n in needles):
            return kind
    return None


def monetary_currency(doc: ParsedDocument) -> str | None:
    """The currency most facts are reported in; None if the filing declares none. The parser keeps
    only the local part of a unit measure, so a currency is a bare three-letter code (`USD`); the
    prefixed form (`iso4217:USD`) is accepted too. Ratios (`USD/shares`), `shares` and `pure` are
    not currencies."""
    counts: Counter[str] = Counter()
    for fact in doc.facts:
        measure = doc.units.get(fact.unit_ref or "", "").split(":")[-1]
        if len(measure) == 3 and measure.isalpha() and measure.isupper():
            counts[measure] += 1
    return counts.most_common(1)[0][0] if counts else None


def _stated_period_end(doc: ParsedDocument) -> dt.date | None:
    """The cover page's `dei:DocumentPeriodEndDate`, when it was read as a fact."""
    for f in doc.facts:
        if not f.name.endswith("DocumentPeriodEndDate"):
            continue
        text = " ".join(f.raw_text.split())
        for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%d %B %Y"):
            try:
                return dt.datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    return None


def period_end_of(doc: ParsedDocument) -> dt.date | None:
    """The reporting period end: the cover page's stated date, else the duration end that most
    of the filing's facts use. Not the latest end: a forward-dated context (a commitment or a
    guidance period) would otherwise become the reporting period."""
    stated = _stated_period_end(doc)
    if stated is not None:
        return stated
    plain = {k: c.end for k, c in doc.contexts.items() if c.end is not None and not c.dimensions}
    used = Counter(
        plain[ref] for f in doc.facts if (ref := f.context_ref) is not None and ref in plain
    )
    if used:
        top = max(used.values())
        return max(end for end, n in used.items() if n == top)
    return max(plain.values()) if plain else None


def _blocks(doc: ParsedDocument) -> list[tuple[str, TextBlock]]:
    return [(k, b) for b in doc.text_blocks if (k := _kind(b.name)) is not None]


def block_text(doc: ParsedDocument, block: TextBlock) -> tuple[str, Callable[[int], int]]:
    """The block's text, joined from its tagged span and its `continuedAt` spans (one newline
    between spans), and a function that maps a position in that text back to the document."""
    pieces: list[tuple[int, int, int]] = []  # joined start, document start, length
    parts: list[str] = []
    cursor = 0
    for start, end in block.segments:
        pieces.append((cursor, start, end - start))
        parts.append(doc.text[start:end])
        cursor += end - start + 1  # the joining newline
    joined = "\n".join(parts)

    def to_document(position: int) -> int:
        for joined_start, doc_start, length in reversed(pieces):
            if position >= joined_start:
                return doc_start + min(position - joined_start, length)
        return pieces[0][1] + position

    return joined, to_document


async def extract_notes_for_document(
    pool: Pool,
    *,
    issuer_id: int,
    ticker: str | None,
    accession: str,
    form: str | None,
    known_at: dt.datetime,
    source_url: str,
    doc: ParsedDocument,
    evidence_id: int | None = None,
) -> NoteJobResult:
    """Extract and store the debt, lease and repurchase tables of one parsed filing."""
    skipped: list[str] = []
    currency = monetary_currency(doc)
    period_end = period_end_of(doc)
    if currency is None:
        return NoteJobResult(0, 0, ("the filing declares no monetary unit",))
    if period_end is None:
        return NoteJobResult(0, 0, ("the filing has no reporting period end",))
    blocks = _blocks(doc)
    stored = 0
    for kind, block in blocks:
        text, to_document = block_text(doc, block)
        debt: Schedule | None = None
        lease: Schedule | None = None
        repurchases: RepurchaseTable | None = None
        if kind == "debt":
            debt = extract_debt_maturities(text)
        elif kind == "lease":
            lease = extract_lease_maturities(text)
        else:
            repurchases = extract_repurchases(text)
        if not any((debt and debt.rows, lease and lease.rows, repurchases and repurchases.rows)):
            skipped.append(f"{block.name}: no table found")
            continue
        stored += await store_note_tables(
            pool,
            issuer_id=issuer_id,
            ticker=ticker,
            accession=accession,
            form=form,
            period_end=period_end,
            known_at=known_at,
            source_url=source_url,
            currency=currency,
            debt=debt,
            lease=lease,
            repurchases=repurchases,
            evidence_id=evidence_id,
            offset=to_document,
        )
    return NoteJobResult(len(blocks), stored, tuple(skipped))


def summarise(results: Sequence[NoteJobResult]) -> str:
    stored = sum(r.stored for r in results)
    blocks = sum(r.blocks_read for r in results)
    return f"read {blocks} note blocks, stored {stored} rows"
