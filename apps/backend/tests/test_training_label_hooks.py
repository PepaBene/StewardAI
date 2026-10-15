"""What the owner says about the engine's work is kept as a training label."""

import asyncpg
import pytest

from steward.news.read_analysis import NewsFeedbackIn, add_feedback


@pytest.mark.db
async def test_news_feedback_is_also_a_training_label(backend_pool: asyncpg.Pool) -> None:
    await add_feedback(backend_pool, NewsFeedbackIn(target="story", target_id="7", kind="useful"))
    await add_feedback(
        backend_pool,
        NewsFeedbackIn(target="item", target_id="9", kind="wrong_entity", note="wrong company"),
    )
    rows = await backend_pool.fetch(
        "select subject_kind, subject_ref, label, by_owner, detail->>'kind' as kind"
        " from public.training_label order by id"
    )
    assert [
        (r["subject_kind"], r["subject_ref"], r["label"], r["by_owner"], r["kind"]) for r in rows
    ] == [
        ("news_story", "story:7", "right", True, "useful"),
        ("news_item", "item:9", "wrong", True, "wrong_entity"),
    ]
