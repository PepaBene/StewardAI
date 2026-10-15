"""The disclosure judges: the registry, prompts, label vocabulary, ref parsing, and (with a
database) apply, revert, outcome and is_open against the real queue, adjudication and
insider-override tables."""

import datetime as dt
from decimal import Decimal
from typing import Any

import asyncpg
import pytest

from steward.autopilot.judge import ApplyError, Candidate
from steward.autopilot.kinds import disclosure
from steward.autopilot.kinds.disclosure import (
    ADJUDICATION,
    DATASETS,
    DISCLOSURE_REVIEW,
    INSIDER_OVERRIDE,
    KINDS,
    adjudication_prompt,
    insider_prompt,
    labels_for,
    review_prompt,
)
from steward.disclosure import adjudication, review
from steward.disclosure.insiders import ECONOMIC_CLASSES

NOTE = "Autopilot (model:x, 0.95): the passage settles it"


# --------------------------------------------------------------------------- the registry


def test_kinds_are_well_formed() -> None:
    assert [k.name for k in KINDS] == ["disclosure_review", "adjudication", "insider_override"]
    for kind in KINDS:
        assert "defer" not in kind.actionable
        decisions = set(kind.output_model.model_fields["decision"].annotation.__args__)  # type: ignore[union-attr]
        assert "defer" in decisions
        assert kind.actionable < decisions
        assert kind.revert is not None and kind.outcome is not None
        assert kind.expire_decision is None  # none of the three has a safe, reversible expiry


def test_adjudication_vocabulary_covers_every_dataset_label() -> None:
    for dataset in DATASETS:
        labels = labels_for(dataset)
        assert labels
        for label in labels:
            assert label in ADJUDICATION.actionable
            assert adjudication.valid_label(dataset, label)
    assert labels_for("statement_note") == ()


def test_insider_vocabulary_is_the_classes_but_a_snapshot() -> None:
    assert INSIDER_OVERRIDE.actionable == set(ECONOMIC_CLASSES) - {"holding_snapshot"}


def test_review_vocabulary_is_the_endpoints_but_not_deferred() -> None:
    assert DISCLOSURE_REVIEW.actionable == {"approved", "rejected", "superseded"}


# ----------------------------------------------------------------------------- prompts


def test_review_prompt_holds_the_reason_the_candidate_and_the_subject() -> None:
    row = {
        "reason": "entity_ambiguity",
        "subject_type": "identity_mapping",
        "subject_id": 4,
        "created_at": dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
    }
    prompt = review_prompt(row, {"mention": "Acme", "ticker": "ACME_US_EQ"}, "Mention: Acme")
    assert "Reason held: entity_ambiguity" in prompt
    assert '"mention": "Acme"' in prompt
    assert "<subject>\nMention: Acme\n</subject>" in prompt
    long = review_prompt(row, {"x": "y" * 5000}, "z" * 9000)
    assert len(long) <= disclosure.PROMPT_CAP


def test_adjudication_prompt_lists_the_labels_and_hides_that_an_item_is_planted() -> None:
    item = adjudication.SampleItem("planted:1", "We may lose X.", "suggested", ("hedged",), True)
    suggestion = {
        "suggested_value": "hypothetical",
        "rationale": "an if-clause",
        "review_reasons": ["planted", "model unsure"],
    }
    prompt = adjudication_prompt(
        "relationship_claim", labels_for("relationship_claim"), item, suggestion
    )
    assert "Allowed labels: true_relationship" in prompt
    assert "planted" not in prompt
    assert "model unsure" in prompt
    assert "Label: hypothetical" in prompt


def test_insider_prompt_carries_the_footnote_and_the_classifier_basis() -> None:
    row: dict[str, Any] = {
        "ticker": "ACME_US_EQ",
        "filed": dt.date(2026, 11, 2),
        "is_officer": False,
        "is_director": True,
        "is_ten_percent_owner": False,
        "officer_title": None,
        "transaction_code": "J",
        "transaction_date": dt.date(2026, 10, 30),
        "acquired_disposed": "D",
        "security_title": "Common Stock",
        "derivative": False,
        "shares": Decimal("500"),
        "price_per_share": None,
        "rule_10b5_1_plan": None,
        "economic_class": "other",
        "classification_basis": "code J without a clarifying footnote",
        "footnote_text": "Shares transferred to a family trust.",
    }
    prompt = insider_prompt(row)
    assert "Role: director" in prompt
    assert "Rule 10b5-1 plan: not stated" in prompt
    assert "<footnote>\nShares transferred to a family trust.\n</footnote>" in prompt


def test_refs_round_trip() -> None:
    ref = disclosure._insider_ref("0000099999-26-000001", 3)
    assert disclosure._insider_key(ref) == ("0000099999-26-000001", 3)
    assert disclosure._id_of("review:12") == 12


async def test_a_malformed_sample_key_rebuilds_nothing() -> None:
    assert await disclosure._regenerate(None, "ownership_match", "nonsense") == {}
    assert await disclosure._regenerate(None, "ownership_match", "ownership_match:x:big") == {}


# ----------------------------------------------------------------- disclosure_review (db)


async def _mapping_review(pool: asyncpg.Pool, mention: str) -> tuple[int, int]:
    mapping_id: int = await pool.fetchval(
        "insert into public.disclosure_entity_mapping (mention, mention_kind, anonymous_label)"
        " values ($1, 'counterparty', 'a large customer') returning id",
        mention,
    )
    review_id = await review.enqueue(
        pool, "identity_mapping", mapping_id, "entity_ambiguity", {"mention": mention}
    )
    assert review_id is not None
    return review_id, mapping_id


@pytest.mark.db
async def test_review_apply_revert_and_outcome(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    review_id, mapping_id = await _mapping_review(pool, "DB Autopilot Customer A")
    ref = f"review:{review_id}"
    cand = next(c for c in await disclosure.review_load(pool, 100) if c.ref == ref)
    assert "DB Autopilot Customer A" in cand.prompt
    assert await disclosure.review_is_open(pool, [ref]) == {ref}

    with pytest.raises(ApplyError):  # a mapping cannot be superseded
        await disclosure.review_apply(pool, cand, "superseded", NOTE)
    payload = await disclosure.review_apply(pool, cand, "approved", NOTE)
    assert payload is not None
    status, decided_by = await pool.fetchrow(
        "select status, decided_by from public.quality_review where id = $1", review_id
    )
    assert (status, decided_by) == ("approved", "ai:autopilot")
    assert (
        await pool.fetchval(
            "select status from public.disclosure_entity_mapping where id = $1", mapping_id
        )
        == "confirmed"
    )
    assert await disclosure.review_is_open(pool, [ref]) == set()
    assert await disclosure.review_outcome(pool, ref) is None  # our own decision
    with pytest.raises(ApplyError):
        await disclosure.review_apply(pool, cand, "rejected", NOTE)

    await disclosure.review_revert(pool, payload)
    assert await disclosure.review_is_open(pool, [ref]) == {ref}
    assert (
        await pool.fetchval(
            "select status from public.disclosure_entity_mapping where id = $1", mapping_id
        )
        == "suggested"
    )
    with pytest.raises(ApplyError):  # nothing left to revert
        await disclosure.review_revert(pool, payload)


@pytest.mark.db
async def test_review_an_owner_decision_is_final(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    review_id, _ = await _mapping_review(pool, "DB Autopilot Customer B")
    ref = f"review:{review_id}"
    cand = next(c for c in await disclosure.review_load(pool, 100) if c.ref == ref)
    await review.decide(pool, review_id, "rejected", "owner:u1", "a different company")
    assert await disclosure.review_outcome(pool, ref) == "rejected"
    assert await disclosure.review_is_open(pool, [ref]) == set()
    with pytest.raises(ApplyError):
        await disclosure.review_apply(pool, cand, "approved", NOTE)


@pytest.mark.db
async def test_review_of_a_subject_without_state_is_left_to_the_owner(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    review_id, _ = await _mapping_review(pool, "DB Autopilot Customer C")
    await pool.execute(
        "update public.quality_review set subject_type = 'estimate_value' where id = $1", review_id
    )
    cand = Candidate(f"review:{review_id}", None, "", {"review_id": review_id})
    with pytest.raises(ApplyError):
        await disclosure.review_apply(pool, cand, "approved", NOTE)
    assert review_id not in {
        c.evidence["review_id"] for c in await disclosure.review_load(pool, 100)
    }


# ------------------------------------------------------------------- adjudication (db)


async def _suggestion(pool: asyncpg.Pool, item_ref: str) -> int:
    sid: int = await pool.fetchval(
        "insert into public.disclosure_adjudication_suggestion"
        " (dataset, sample_key, item_ref, machine_value, suggested_value, rationale, model,"
        " must_review) values ('ownership_match', 'ownership_match:dbauto:10', $1,"
        " 'same_company', 'same_company', 'same name', 'gpt-test', false) returning id",
        item_ref,
    )
    return sid


def _adj_cand(sid: int) -> Candidate:
    return Candidate(f"adjudication:{sid}", None, "", {"suggestion_id": sid})


@pytest.mark.db
async def test_adjudication_label_revert_and_a_person_label_is_final(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    sid = await _suggestion(pool, "dbauto:1")
    ref = f"adjudication:{sid}"
    assert await disclosure.adjudication_is_open(pool, [ref]) == {ref}
    with pytest.raises(ApplyError):  # a label from another dataset
        await disclosure.adjudication_apply(pool, _adj_cand(sid), "negated", NOTE)

    payload = await disclosure.adjudication_apply(pool, _adj_cand(sid), "different_company", NOTE)
    assert payload is not None
    row = await pool.fetchrow(
        "select adjudicator_kind, adjudicated_by, correct, human_value"
        " from public.disclosure_adjudication where id = $1",
        payload["adjudication_id"],
    )
    assert (row["adjudicator_kind"], row["adjudicated_by"]) == ("model", "ai:autopilot")
    assert (row["human_value"], row["correct"]) == ("different_company", False)
    assert await disclosure.adjudication_is_open(pool, [ref]) == set()
    assert await disclosure.adjudication_outcome(pool, ref) is None  # nobody human yet
    gate = await adjudication.measure(pool, "ownership_match", "ownership_match:dbauto:10")
    assert gate.labelled == 0  # a model label never reaches the gate
    with pytest.raises(ApplyError):
        await disclosure.adjudication_apply(pool, _adj_cand(sid), "same_company", NOTE)

    await disclosure.adjudication_revert(pool, payload)
    assert await disclosure.adjudication_is_open(pool, [ref]) == {ref}
    with pytest.raises(ApplyError):
        await disclosure.adjudication_revert(pool, payload)

    await adjudication.record(
        pool,
        dataset="ownership_match",
        sample_key="ownership_match:dbauto:10",
        item_ref="dbauto:1",
        machine_value="same_company",
        human_value="same_company",
        by="owner:u1",
    )
    assert await disclosure.adjudication_outcome(pool, ref) == "same_company"
    assert await disclosure.adjudication_is_open(pool, [ref]) == set()
    with pytest.raises(ApplyError):
        await disclosure.adjudication_apply(pool, _adj_cand(sid), "different_company", NOTE)


@pytest.mark.db
async def test_adjudication_a_bulk_accepted_suggestion_is_not_an_outcome(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    sid = await _suggestion(pool, "dbauto:2")
    await adjudication.record(
        pool,
        dataset="ownership_match",
        sample_key="ownership_match:dbauto:10",
        item_ref="dbauto:2",
        machine_value="same_company",
        human_value="same_company",
        by="owner:u1",
        kind="accepted_suggestion",
    )
    assert await disclosure.adjudication_outcome(pool, f"adjudication:{sid}") is None
    assert await disclosure.adjudication_is_open(pool, [f"adjudication:{sid}"]) == set()


# --------------------------------------------------------------- insider_override (db)

ACC = "0000099998-26-000001"


async def _insider_line(pool: asyncpg.Pool, accession: str, footnote: str | None) -> None:
    await pool.execute(
        "insert into public.insider_filing (accession, ticker, issuer_cik, form, filed,"
        " source_url) values ($1, 'DBINS_US_EQ', '0000011111', '4', $2, 'https://sec.example/x')",
        accession,
        dt.date(2026, 11, 2),
    )
    await pool.execute(
        "insert into public.insider_transaction (accession, row_index, reporting_owner_cik,"
        " owner_name, is_director, transaction_date, transaction_code, shares, price_per_share,"
        " direct_indirect, derivative, economic_class, classification_basis, footnote_text)"
        " values ($1, 0, '0000000007', 'Pat Director', true, $2, 'J', 500, null, 'D', false,"
        " 'other', 'code J without a clarifying footnote', $3)",
        accession,
        dt.date(2026, 10, 30),
        footnote,
    )


@pytest.mark.db
async def test_insider_override_apply_revert_and_outcome(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    await _insider_line(pool, ACC, "Shares gifted to a family member.")
    ref = disclosure._insider_ref(ACC, 0)
    cand = next(c for c in await disclosure.insider_load(pool, 100) if c.ref == ref)
    assert cand.ticker == "DBINS_US_EQ"
    assert "gifted to a family member" in cand.prompt
    assert await disclosure.insider_is_open(pool, [ref]) == {ref}

    with pytest.raises(ApplyError):
        await disclosure.insider_apply(pool, cand, "holding_snapshot", NOTE)
    payload = await disclosure.insider_apply(pool, cand, "gift", NOTE)
    assert payload is not None and payload["original_class"] == "other"
    assert await disclosure.insider_is_open(pool, [ref]) == set()
    assert await disclosure.insider_outcome(pool, ref) is None  # our own override
    with pytest.raises(ApplyError):
        await disclosure.insider_apply(pool, cand, "tax_withholding", NOTE)

    await disclosure.insider_revert(pool, payload)
    latest = await pool.fetchrow(
        "select new_class, decided_by from public.insider_classification_override"
        " where accession = $1 order by id desc limit 1",
        ACC,
    )
    assert (latest["new_class"], latest["decided_by"]) == ("other", "ai:autopilot")
    with pytest.raises(ApplyError):  # the latest override is no longer the one to revert
        await disclosure.insider_revert(pool, payload)


@pytest.mark.db
async def test_insider_owner_override_is_final_and_is_the_outcome(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    acc = "0000099998-26-000002"
    await _insider_line(pool, acc, "Gift to a charity.")
    ref = disclosure._insider_ref(acc, 0)
    cand = next(c for c in await disclosure.insider_load(pool, 100) if c.ref == ref)
    from steward.disclosure.insider_analysis import record_override

    await record_override(pool, acc, 0, "gift", "gift to a charity per footnote", "owner:u1")
    assert await disclosure.insider_outcome(pool, ref) == "gift"
    with pytest.raises(ApplyError):
        await disclosure.insider_apply(pool, cand, "other", NOTE)


@pytest.mark.db
async def test_insider_line_without_a_footnote_is_not_offered(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    acc = "0000099998-26-000003"
    await _insider_line(pool, acc, None)
    ref = disclosure._insider_ref(acc, 0)
    assert ref not in {c.ref for c in await disclosure.insider_load(pool, 200)}
    assert await disclosure.insider_is_open(pool, [ref]) == set()
