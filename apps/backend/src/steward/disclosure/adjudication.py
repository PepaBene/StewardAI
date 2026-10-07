"""P0.6, P4.3, P5.1, P5.6: draw adjudication samples, record a person's label, measure the gate.

The release thresholds (`docs/data/public-disclosure-release-thresholds.md`) are measured from a
person's labels, never from the classifier's own output. A sample is reproducible (same ids and
seed, same items), includes deliberate false positives that nobody is told about, and a gate is met
only when the sample is big enough, every planted false positive was caught and the rate clears
the bar. Nothing here changes a stored claim or class: it measures.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import asyncpg

from steward.disclosure.dependencies import AUDIT_LABELS, audit_precision, screen_passage
from steward.disclosure.insiders import ECONOMIC_CLASSES

type Pool = asyncpg.Pool | asyncpg.Connection

#: dataset -> (minimum sample, minimum rate), from the release thresholds table
GATES: Mapping[str, tuple[int, Decimal]] = {
    "relationship_claim": (100, Decimal("0.95")),
    "insider_class": (50, Decimal("0.98")),
    "ownership_match": (50, Decimal("0.98")),
    "statement_note": (30, Decimal("0.98")),
    "document_sample": (10, Decimal("0.98")),
}

#: Passages that are NOT current reported relationships. They go into a relationship sample unmarked
#: so a reviewer who labels one `true_relationship` is caught.
PLANTED_RELATIONSHIP_PHRASES: tuple[tuple[str, str], ...] = (
    ("negated", "No single customer accounted for 10% or more of our revenue in fiscal 2025."),
    ("hypothetical", "If we were to lose Customer A, our revenue could be materially reduced."),
    ("historical", "Prior to 2019, Northwind Traders was our largest customer."),
    ("third_party", "Companies in our industry depend on a small number of large customers."),
    ("negated", "We did not derive more than 5% of revenue from any distributor."),
    ("hypothetical", "Should a major supplier terminate, we may be adversely affected."),
    (
        "third_party",
        "Analysts note that our competitors rely on a single customer for 40% of sales.",
    ),
)


@dataclass(frozen=True)
class SampleItem:
    item_ref: str
    text: str
    machine_value: str | None
    hints: tuple[str, ...]
    planted: bool = False


def pick(ids: Sequence[str], n: int, seed: str) -> list[str]:
    """The same ids and seed always give the same items, in the same order."""
    return sorted(ids, key=lambda i: hashlib.sha256(f"{seed}:{i}".encode()).hexdigest())[:n]


def planted_items(seed: str, n: int = 7) -> list[SampleItem]:
    chosen = pick([str(i) for i in range(len(PLANTED_RELATIONSHIP_PHRASES))], n, seed)
    out = []
    for i in chosen:
        kind, text = PLANTED_RELATIONSHIP_PHRASES[int(i)]
        # a planted item looks like any other claim: the machine value is "true_relationship"
        out.append(SampleItem(f"planted:{i}", text, "true_relationship", (kind,), True))
    return out


def shuffle_in(items: list[SampleItem], planted: list[SampleItem], seed: str) -> list[SampleItem]:
    """Planted items are mixed in by a hash, so position gives nothing away."""
    mixed = [*items, *planted]
    return sorted(
        mixed, key=lambda s: hashlib.sha256(f"{seed}:mix:{s.item_ref}".encode()).hexdigest()
    )


async def relationship_sample(pool: Pool, n: int = 100, seed: str = "p5.1") -> list[SampleItem]:
    rows = await pool.fetch(
        "select id, passage, status from public.relationship_disclosure order by id"
    )
    by_id = {str(r["id"]): r for r in rows}
    real = pick(list(by_id), max(0, n - 7), seed)
    items = [
        SampleItem(
            f"claim:{i}",
            by_id[i]["passage"],
            "true_relationship" if by_id[i]["status"] == "confirmed" else "suggested",
            tuple(screen_passage(by_id[i]["passage"])),
        )
        for i in real
    ]
    return shuffle_in(items, planted_items(seed), seed)


async def insider_sample(pool: Pool, n: int = 50, seed: str = "p4.3") -> list[SampleItem]:
    """Classified transaction rows, drawn across the classes, with the footnote the class used."""
    rows = await pool.fetch(
        """select accession, row_index, transaction_code, economic_class, classification_basis,
                  footnote_text, shares, price_per_share, derivative
             from public.insider_transaction where economic_class is not null"""
    )
    by_ref = {f"{r['accession']}:{r['row_index']}": r for r in rows}
    by_class: dict[str, list[str]] = defaultdict(list)
    for ref, r in by_ref.items():
        by_class[r["economic_class"]].append(ref)
    # Take a share of every class so rare classes are examined, not only the common ones.
    per = max(1, n // max(1, len(by_class)))
    chosen: list[str] = []
    for cls in sorted(by_class):
        chosen += pick(by_class[cls], per, f"{seed}:{cls}")
    chosen = pick(chosen, n, seed)
    return [
        SampleItem(
            ref,
            f"code {by_ref[ref]['transaction_code']}"
            f"{' (derivative)' if by_ref[ref]['derivative'] else ''}, "
            f"{by_ref[ref]['shares']} @ {by_ref[ref]['price_per_share']}; "
            f"footnote: {by_ref[ref]['footnote_text'] or 'none'}",
            by_ref[ref]["economic_class"],
            (by_ref[ref]["classification_basis"] or "no basis",),
        )
        for ref in chosen
    ]


OWNERSHIP_LABELS = ("same_company", "different_company", "cannot_tell")


async def ownership_sample(pool: Pool, n: int = 50, seed: str = "p3.7") -> list[SampleItem]:
    """13F lines the app matched to a ticker by CUSIP. The person reads the name the manager
    filed and the name of the ticker it was matched to, and says whether they are one company.
    One line per CUSIP and ticker pair, so a popular name is not sampled fifty times."""
    rows = await pool.fetch(
        """select distinct on (h.cusip, h.ticker) h.accession, h.infotable_sk, h.cusip, h.ticker,
                  h.issuer_name, h.title_of_class, i.name as instrument_name
             from public.institutional_holding h
             left join public.instrument i on i.t212_ticker = h.ticker
            where h.ticker is not null
            order by h.cusip, h.ticker, h.accession"""
    )
    by_ref = {f"{r['accession']}:{r['infotable_sk']}": r for r in rows}
    return [
        SampleItem(
            ref,
            f"Manager filed “{r['issuer_name'] or 'no name'}” "
            f"({r['title_of_class'] or 'no class'}, CUSIP {r['cusip']}); matched to {r['ticker']}"
            f"{' (' + r['instrument_name'] + ')' if r['instrument_name'] else ''}.",
            "same_company",
            ("matched by CUSIP",),
        )
        for ref, r in ((ref, by_ref[ref]) for ref in pick(list(by_ref), n, seed))
    ]


def valid_label(dataset: str, human_value: str) -> bool:
    if dataset == "relationship_claim":
        return human_value in AUDIT_LABELS
    if dataset == "insider_class":
        return human_value in ECONOMIC_CLASSES
    if dataset == "ownership_match":
        return human_value in OWNERSHIP_LABELS
    return bool(human_value.strip())


def is_correct(dataset: str, machine_value: str | None, human_value: str) -> bool:
    """A relationship suggestion is correct only if it IS a true relationship; any other label is
    a miss. Other datasets are correct when the machine value equals the person's."""
    if dataset == "relationship_claim":
        return (
            machine_value in ("true_relationship", "suggested")
            and human_value == "true_relationship"
        )
    return machine_value == human_value


async def record(
    pool: Pool,
    *,
    dataset: str,
    sample_key: str,
    item_ref: str,
    machine_value: str | None,
    human_value: str,
    by: str,
    note: str | None = None,
) -> int:
    if dataset not in GATES:
        raise ValueError(f"unknown dataset {dataset!r}")
    if not valid_label(dataset, human_value):
        raise ValueError(f"{human_value!r} is not a label for {dataset}")
    planted = item_ref.startswith("planted:")
    row_id = await pool.fetchval(
        """insert into public.disclosure_adjudication
             (dataset, sample_key, item_ref, machine_value, human_value, correct,
              planted_false_positive, note, adjudicated_by)
           values ($1,$2,$3,$4,$5,$6,$7,$8,$9)
           on conflict (dataset, sample_key, item_ref, adjudicated_by) do update set
             human_value = excluded.human_value, correct = excluded.correct, note = excluded.note,
             adjudicated_at = now()
           returning id""",
        dataset,
        sample_key,
        item_ref,
        machine_value,
        human_value,
        is_correct(dataset, machine_value, human_value),
        planted,
        note,
        by,
    )
    return int(row_id)


@dataclass(frozen=True)
class Gate:
    dataset: str
    sample_key: str
    labelled: int
    needed: int
    correct: int
    rate: Decimal | None
    bar: Decimal
    planted: int
    planted_caught: int
    met: bool
    reason: str


def measure_labels(dataset: str, rows: Sequence[Mapping[str, Any]], sample_key: str = "") -> Gate:
    """The gate for one dataset from labelled rows. One label per item: the latest wins."""
    needed, bar = GATES[dataset]
    latest: dict[str, Mapping[str, Any]] = {}
    for r in rows:
        latest[r["item_ref"]] = r
    labelled = len(latest)
    correct = sum(1 for r in latest.values() if r["correct"])
    planted = [r for r in latest.values() if r["planted_false_positive"]]
    caught = sum(1 for r in planted if not r["correct"])
    rate = Decimal(correct) / labelled if labelled else None
    if dataset == "relationship_claim":
        # Planted items are not real claims: they test the reviewer, so they stay out of precision.
        real = {k: v["human_value"] for k, v in latest.items() if not v["planted_false_positive"]}
        keyed = {i: v for i, v in enumerate(real.values())}
        res = audit_precision(
            keyed,
            min_claims=needed,
            min_precision=bar,
            deliberate_false_positive_ids=[],
            min_deliberate=0,
        )
        rate = res.precision
        correct = res.true_relationships
        labelled = res.total
        if len(planted) < 5:
            return Gate(
                dataset,
                sample_key,
                labelled,
                needed,
                correct,
                rate,
                bar,
                len(planted),
                caught,
                False,
                f"only {len(planted)} planted false positives were labelled",
            )
        if caught < len(planted):
            return Gate(
                dataset,
                sample_key,
                labelled,
                needed,
                correct,
                rate,
                bar,
                len(planted),
                caught,
                False,
                "a planted false positive was labelled a true relationship",
            )
        return Gate(
            dataset,
            sample_key,
            labelled,
            needed,
            correct,
            rate,
            bar,
            len(planted),
            caught,
            res.meets_bar,
            res.reason,
        )
    if labelled < needed:
        return Gate(
            dataset,
            sample_key,
            labelled,
            needed,
            correct,
            rate,
            bar,
            len(planted),
            caught,
            False,
            f"{labelled} of {needed} items labelled",
        )
    met = rate is not None and rate >= bar
    return Gate(
        dataset,
        sample_key,
        labelled,
        needed,
        correct,
        rate,
        bar,
        len(planted),
        caught,
        met,
        "meets the bar" if met else f"rate {rate:.3f} is below {bar}",
    )


async def measure(pool: Pool, dataset: str, sample_key: str) -> Gate:
    rows = await pool.fetch(
        """select item_ref, correct, planted_false_positive, human_value
             from public.disclosure_adjudication
            where dataset = $1 and sample_key = $2 order by adjudicated_at, id""",
        dataset,
        sample_key,
    )
    return measure_labels(dataset, [dict(r) for r in rows], sample_key)
