"""Labels: an owner or audit judgement on an autopilot decision becomes one training label."""

import asyncpg
import pytest

from steward.autopilot import ledger
from steward.training import labels


def test_the_source_follows_who_judged_and_how() -> None:
    assert labels.source_for("owner", "applied") == ("owner_review", True)
    assert labels.source_for("owner", "reverted") == ("owner_revert", True)
    assert labels.source_for("audit:gpt-5", "applied") == ("audit", False)


async def decision(pool: asyncpg.Pool, status: str = "applied") -> int:
    return await ledger.record(
        pool,
        kind="fact",
        subject_ref="fact:1",
        ticker=None,
        decision="accept",
        confidence=0.95,
        decided_by="rule:x",
        summary="s",
        evidence={"prompt": "p"},
        status=status,  # type: ignore[arg-type]
    )


@pytest.mark.db
async def test_reviews_write_labels_and_unreviewed_decisions_write_none(
    backend_pool: asyncpg.Pool,
) -> None:
    first = await decision(backend_pool)
    assert await backend_pool.fetchval("select count(*) from public.training_label") == 0

    assert await ledger.set_review(backend_pool, first, "wrong", "owner")
    row = await backend_pool.fetchrow("select * from public.training_label")
    assert (row["subject_kind"], row["subject_ref"], row["label"]) == ("fact", "fact:1", "wrong")
    assert row["by_owner"] and row["source"] == "owner_review" and row["decision_id"] == first

    assert await ledger.set_review(backend_pool, first, "ok", "audit:gpt-5")
    audit = await backend_pool.fetchrow("select * from public.training_label order by id desc")
    assert audit["source"] == "audit" and not audit["by_owner"] and audit["label"] == "right"

    assert not await ledger.set_review(backend_pool, 999999, "ok", "owner")  # nothing to label
    assert await backend_pool.fetchval("select count(*) from public.training_label") == 2


@pytest.mark.db
async def test_labels_cannot_be_changed_afterwards(backend_pool: asyncpg.Pool) -> None:
    await labels.record(
        backend_pool,
        source="owner_review",
        subject_kind="fact",
        subject_ref="fact:2",
        label="right",
        by_owner=True,
    )
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await backend_pool.execute("update public.training_label set label = 'wrong'")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await backend_pool.execute("delete from public.training_label")
