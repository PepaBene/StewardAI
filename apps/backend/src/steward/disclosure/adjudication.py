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
from dataclasses import dataclass, replace
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
    "deal_state": (30, Decimal("0.95")),
}

#: Who made a label. The gate reads a person's own labels and suggestions a person accepted; a
#: label written by a model alone never counts.
GATE_KINDS = ("person", "accepted_suggestion")
ADJUDICATOR_KINDS = (*GATE_KINDS, "model")

#: A deal-state label: the state the document supports, or that it is not a deal at all.
DEAL_STATE_LABELS = (
    "rumored",
    "announced",
    "definitive",
    "approved",
    "closed",
    "terminated",
    "contested",
    "not_a_deal",
)

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
    """`n` real claims plus the planted false positives on top, so a sample of the gate's size
    can meet it. Claims a person already rejected are not drawn: the machine no longer asserts
    them, so labelling one would count a known miss twice."""
    rows = await pool.fetch(
        "select id, passage, status from public.relationship_disclosure"
        " where status <> 'rejected' order by id"
    )
    by_id = {str(r["id"]): r for r in rows}
    real = pick(list(by_id), max(0, n), seed)
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


async def deal_state_sample(pool: Pool, n: int = 30, seed: str = "p6.8") -> list[SampleItem]:
    """Stored deal states with the passage each one rests on. The person reads the passage and
    names the state it supports (or says it is not a deal); the chronology is shown so an
    out-of-order state can be caught."""
    rows = await pool.fetch(
        """select t.id, t.state, t.effective_at, t.known_at, e.quote, d.title,
                  (select string_agg(p.state, ' > ' order by p.known_at, p.id)
                     from public.deal_state_transition p where p.deal_id = t.deal_id) as chain
             from public.deal_state_transition t
             join public.deal_event d on d.id = t.deal_id
             join public.source_evidence e on e.id = t.evidence_id
            order by t.id"""
    )
    by_ref = {f"transition:{r['id']}": r for r in rows}
    return [
        SampleItem(
            ref,
            f"{r['title'] or 'Deal'}: “{(r['quote'] or '').strip()[:600]}”",
            r["state"],
            (f"effective {r['effective_at']:%Y-%m-%d}", f"states: {r['chain'] or r['state']}"),
        )
        for ref, r in ((ref, by_ref[ref]) for ref in pick(list(by_ref), n, seed))
    ]


async def machine_value_for(pool: Pool, dataset: str, sample_key: str, item_ref: str) -> str | None:
    """What the machine said about an item, read from the server's own data and never from the
    client, so a label cannot be made "correct" by sending a convenient machine value. Raises
    ValueError for an item that does not exist."""
    stored = await pool.fetchrow(
        """select machine_value from public.disclosure_adjudication_suggestion
            where dataset = $1 and sample_key = $2 and item_ref = $3""",
        dataset,
        sample_key,
        item_ref,
    )
    if stored is not None:
        machine: str | None = stored["machine_value"]
        return machine
    kind, _, ident = item_ref.partition(":")
    if dataset == "relationship_claim":
        if kind == "planted" and ident.isdigit() and int(ident) < len(PLANTED_RELATIONSHIP_PHRASES):
            return "true_relationship"
        status = (
            await pool.fetchval(
                "select status from public.relationship_disclosure where id = $1", int(ident)
            )
            if kind == "claim" and ident.isdigit()
            else None
        )
        if status is None:
            raise ValueError(f"no such item {item_ref!r}")
        return "true_relationship" if status == "confirmed" else "suggested"
    if dataset == "insider_class":
        accession, _, row_index = item_ref.rpartition(":")
        found = (
            await pool.fetchrow(
                "select economic_class from public.insider_transaction"
                " where accession = $1 and row_index = $2",
                accession,
                int(row_index),
            )
            if row_index.isdigit()
            else None
        )
        if found is None:
            raise ValueError(f"no such item {item_ref!r}")
        economic: str | None = found["economic_class"]
        return economic
    if dataset == "ownership_match":
        return "same_company"
    if dataset == "deal_state":
        state = (
            await pool.fetchval(
                "select state from public.deal_state_transition where id = $1", int(ident)
            )
            if kind == "transition" and ident.isdigit()
            else None
        )
        if state is None:
            raise ValueError(f"no such item {item_ref!r}")
        return str(state)
    return None


def valid_label(dataset: str, human_value: str) -> bool:
    if dataset == "relationship_claim":
        return human_value in AUDIT_LABELS
    if dataset == "insider_class":
        return human_value in ECONOMIC_CLASSES
    if dataset == "ownership_match":
        return human_value in OWNERSHIP_LABELS
    if dataset == "deal_state":
        return human_value in DEAL_STATE_LABELS
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
    kind: str = "person",
) -> int:
    """Store one label. `kind` says who made it: `person` (their own reading),
    `accepted_suggestion` (they accepted a model's label in bulk) or `model` (no person)."""
    if dataset not in GATES:
        raise ValueError(f"unknown dataset {dataset!r}")
    if not valid_label(dataset, human_value):
        raise ValueError(f"{human_value!r} is not a label for {dataset}")
    if kind not in ADJUDICATOR_KINDS:
        raise ValueError(f"unknown adjudicator kind {kind!r}")
    planted = item_ref.startswith("planted:")
    row_id = await pool.fetchval(
        """insert into public.disclosure_adjudication
             (dataset, sample_key, item_ref, machine_value, human_value, correct,
              planted_false_positive, note, adjudicated_by, adjudicator_kind)
           values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
           on conflict (dataset, sample_key, item_ref, adjudicated_by) do update set
             human_value = excluded.human_value, correct = excluded.correct, note = excluded.note,
             adjudicator_kind = excluded.adjudicator_kind, adjudicated_at = now()
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
        kind,
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


#: At most this share of the labels the gate reads may be suggestions accepted in bulk. Items on
#: which the model and the extractor agree are the easy ones, so counting them without a look
#: would lift the rate; the rest must be the reviewer's own reading.
MAX_ACCEPTED_SHARE = Decimal("0.5")


def measure_labels(dataset: str, rows: Sequence[Mapping[str, Any]], sample_key: str = "") -> Gate:
    """The gate for one dataset from labelled rows. One label per item: the latest wins. A gate
    that would be met only because most labels were bulk-accepted suggestions is not met."""
    gate = _measure_labels(dataset, rows, sample_key)
    latest = {r["item_ref"]: r for r in rows}
    accepted = sum(1 for r in latest.values() if r.get("adjudicator_kind") == "accepted_suggestion")
    if gate.met and latest and Decimal(accepted) / len(latest) > MAX_ACCEPTED_SHARE:
        return replace(
            gate,
            met=False,
            reason=f"{accepted} of {len(latest)} labels were accepted suggestions; "
            "the reviewer's own readings must be at least half",
        )
    return gate


def _measure_labels(dataset: str, rows: Sequence[Mapping[str, Any]], sample_key: str = "") -> Gate:
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


async def saved_labels(pool: Pool, dataset: str, sample_key: str) -> dict[str, tuple[str, str]]:
    """Each item's newest gate-counting label as (human_value, adjudicator_kind), so a reloaded
    page shows what the gate already counts instead of asking for the label again."""
    rows = await pool.fetch(
        """select item_ref, human_value, adjudicator_kind
             from public.disclosure_adjudication
            where dataset = $1 and sample_key = $2 and adjudicator_kind = any($3::text[])
            order by adjudicated_at, id""",
        dataset,
        sample_key,
        list(GATE_KINDS),
    )
    return {r["item_ref"]: (r["human_value"], r["adjudicator_kind"]) for r in rows}


async def measure(pool: Pool, dataset: str, sample_key: str) -> Gate:
    rows = await pool.fetch(
        """select item_ref, correct, planted_false_positive, human_value, adjudicator_kind
             from public.disclosure_adjudication
            where dataset = $1 and sample_key = $2 and adjudicator_kind = any($3::text[])
            order by adjudicated_at, id""",
        dataset,
        sample_key,
        list(GATE_KINDS),
    )
    return measure_labels(dataset, [dict(r) for r in rows], sample_key)
