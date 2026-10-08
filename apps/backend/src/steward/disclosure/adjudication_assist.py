"""Hybrid accuracy check: a model pre-labels a sample, a person confirms it.

The model's label is a suggestion and is stored apart from the person's labels, so the release gate
(`adjudication.measure`) can never count it. A person must label an item themselves when the model
disagrees with the extractor, when the item is a planted false positive, when the model is unsure,
or when it falls in a fixed random share of the rest (so a model that agrees too easily is still
checked). The remaining suggestions can be accepted in one step: that records the person's label,
with a note saying it was accepted.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import asyncpg
from pydantic import BaseModel

from steward.disclosure import adjudication
from steward.disclosure.adjudication import SampleItem, valid_label

type Pool = asyncpg.Pool | asyncpg.Connection

RANDOM_REVIEW_SHARE = 20  # percent of the unflagged items a person labels anyway
ACCEPT_NOTE = "accepted the model suggestion"

SYSTEM = {
    "relationship_claim": (
        "You label one passage from a company filing. Say whether it states a CURRENT customer or "
        "supplier relationship of the filer. Labels: true_relationship, historical_only, "
        "hypothetical, negated, unrelated_third_party, ambiguous. A risk-factor 'if we lose' "
        "sentence is hypothetical; 'no customer accounted for 10%' is negated; another company's "
        "customers are unrelated_third_party; a region or 'all other' is not a counterparty "
        "(ambiguous)."
    ),
    "insider_class": (
        "You label one insider transaction line from a Form 4. Choose the economic class that "
        "best fits the transaction code and text."
    ),
    "ownership_match": (
        "You compare the manager-filed issuer name with the company it was matched to. Labels: "
        "same_company, different_company, cannot_tell. Use cannot_tell when unsure."
    ),
}


class Suggestion(BaseModel):
    label: str
    reason: str
    unsure: bool = False


@dataclass(frozen=True)
class Review:
    must_review: bool
    reasons: tuple[str, ...]


def review_requirement(
    item_ref: str, planted: bool, machine: str | None, suggested: str, unsure: bool, seed: str
) -> Review:
    """Why a person must label this item themselves, or none."""
    reasons: list[str] = []
    if planted:
        reasons.append("planted")
    if unsure:
        reasons.append("model unsure")
    if machine is not None and machine != suggested:
        reasons.append("model disagrees with the extractor")
    digest = hashlib.sha256(f"{seed}:{item_ref}".encode()).digest()
    if digest[0] * 100 // 256 < RANDOM_REVIEW_SHARE:
        reasons.append("random check")
    return Review(bool(reasons), tuple(reasons))


async def suggest(
    llm: Any, model: str, dataset: str, item: SampleItem, labels: Sequence[str]
) -> Suggestion | None:
    """The model's label for one item, or None when it cannot be reached or answers off-list."""
    from steward.llm.types import LLMError, Message, Request

    system = SYSTEM.get(dataset)
    if system is None or not llm.configured(model):
        return None
    prompt = f"Allowed labels: {', '.join(labels)}.\n\nItem:\n{item.text}"
    try:
        done = await llm.complete(
            Request(
                model=model,
                system=system,
                messages=(Message(role="user", text=prompt),),
                schema=Suggestion,
                max_tokens=400,
            )
        )
    except (LLMError, ValueError):
        return None
    parsed = done.parsed
    if not isinstance(parsed, Suggestion) or not valid_label(dataset, parsed.label):
        return None
    return parsed


async def prelabel(
    pool: Pool,
    llm: Any,
    model: str,
    dataset: str,
    sample_key: str,
    items: Sequence[SampleItem],
    labels: Sequence[str],
) -> dict[str, int]:
    """Store a suggestion for every item the model can label. Items it cannot label are counted
    and left for the person alone."""
    stored = unreachable = 0
    for item in items:
        got = await suggest(llm, model, dataset, item, labels)
        if got is None:
            unreachable += 1
            continue
        need = review_requirement(
            item.item_ref, item.planted, item.machine_value, got.label, got.unsure, sample_key
        )
        await pool.execute(
            """insert into public.disclosure_adjudication_suggestion
                 (dataset, sample_key, item_ref, machine_value, suggested_value, rationale, model,
                  must_review, review_reasons)
               values ($1,$2,$3,$4,$5,$6,$7,$8,$9)
               on conflict (dataset, sample_key, item_ref) do update set
                 machine_value = excluded.machine_value, suggested_value = excluded.suggested_value,
                 rationale = excluded.rationale, model = excluded.model,
                 must_review = excluded.must_review, review_reasons = excluded.review_reasons,
                 created_at = now()""",
            dataset,
            sample_key,
            item.item_ref,
            item.machine_value,
            got.label,
            got.reason[:500],
            model,
            need.must_review,
            list(need.reasons),
        )
        stored += 1
    return {"suggested": stored, "left_to_person": unreachable}


async def suggestions(pool: Pool, dataset: str, sample_key: str) -> dict[str, asyncpg.Record]:
    rows = await pool.fetch(
        """select item_ref, suggested_value, rationale, model, must_review, review_reasons
             from public.disclosure_adjudication_suggestion
            where dataset = $1 and sample_key = $2""",
        dataset,
        sample_key,
    )
    return {r["item_ref"]: r for r in rows}


async def accept_unflagged(pool: Pool, dataset: str, sample_key: str, by: str) -> int:
    """Record the person's label for every suggestion that needs no individual look and that this
    person has not labelled yet. Returns how many were recorded."""
    rows = await pool.fetch(
        """select s.item_ref, s.machine_value, s.suggested_value
             from public.disclosure_adjudication_suggestion s
            where s.dataset = $1 and s.sample_key = $2 and not s.must_review
              and not exists (
                select 1 from public.disclosure_adjudication a
                 where a.dataset = s.dataset and a.sample_key = s.sample_key
                   and a.item_ref = s.item_ref and a.adjudicated_by = $3)""",
        dataset,
        sample_key,
        by,
    )
    for r in rows:
        await adjudication.record(
            pool,
            dataset=dataset,
            sample_key=sample_key,
            item_ref=r["item_ref"],
            machine_value=r["machine_value"],
            human_value=r["suggested_value"],
            by=by,
            note=ACCEPT_NOTE,
        )
    return len(rows)
