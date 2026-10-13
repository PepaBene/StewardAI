"""Training-data capture: redacted, switchable, append-only, and never able to break a call."""

import asyncpg
import pytest

from steward.config.jobs import JobsConfig, TrainingCaptureSettings
from steward.config.store import save_config
from steward.llm import capture
from steward.llm.types import Completion, Message, Request, normalized_usage


def completion(text: str = "ok") -> Completion:
    return Completion(
        model="gpt-5",
        provider="openai",
        text=text,
        parsed=None,
        stop_reason="end",
        usage=normalized_usage(input_tokens=10, output_tokens=5, cache_read_tokens=2),
    )


def request(text: str) -> Request:
    return Request(model="gpt-5", system="You are careful.", messages=(Message("user", text),))


def test_personal_identifiers_and_keys_are_removed() -> None:
    raw = (
        "Mail me at owner@example.com, account 1234567890123, IBAN CZ65 0800 0000 1920 0014 5399,"
        " key sk-abcdefghijklmnopqrstuvwx and Authorization: Bearer abcdefghijklmnop12345678."
    )
    out = capture.redact(raw)
    for leaked in ("owner@example.com", "1234567890123", "1920", "sk-abcdef", "abcdefghijklmnop12"):
        assert leaked not in out
    assert "<EMAIL>" in out and "<IBAN>" in out and "<KEY>" in out and "<TOKEN>" in out


def test_ordinary_numbers_and_prices_survive_redaction() -> None:
    text = "Revenue was 4,200.5 million, up 12.3% on 2025-03-31; price 187.42 EUR."
    assert capture.redact(text) == text


def test_long_text_is_cut() -> None:
    assert capture.clip("a" * 50, 10).endswith("…[cut]")
    assert capture.clip("short", 10) == "short"


@pytest.mark.db
async def test_a_call_is_stored_redacted_and_the_switch_stops_it(
    backend_pool: asyncpg.Pool,
) -> None:
    capture.reset_cache()
    await capture.record(backend_pool, 1, "news_story", request("mail a@b.co"), completion())
    row = await backend_pool.fetchrow("select * from public.llm_call")
    assert row is not None and row["job_type"] == "news_story" and row["input_tokens"] == 10
    assert "a@b.co" not in str(row["request"]) and "<EMAIL>" in str(row["request"])
    assert row["redaction"] == capture.REDACTION_VERSION

    await capture.record(backend_pool, 2, "chat", request("hello"), completion())  # excluded
    assert await backend_pool.fetchval("select count(*) from public.llm_call") == 1

    body = JobsConfig(training_capture=TrainingCaptureSettings(enabled=False))
    async with backend_pool.acquire() as conn:
        await save_config(conn, "jobs", body, created_by=None, note="off")
    capture.reset_cache()
    await capture.record(backend_pool, 3, "news_story", request("again"), completion())
    assert await backend_pool.fetchval("select count(*) from public.llm_call") == 1
    capture.reset_cache()


@pytest.mark.db
async def test_a_stored_call_cannot_be_changed_or_deleted(backend_pool: asyncpg.Pool) -> None:
    capture.reset_cache()
    await capture.record(backend_pool, 1, "news_story", request("x"), completion())
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await backend_pool.execute("update public.llm_call set model = 'x'")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await backend_pool.execute("delete from public.llm_call")


async def test_a_capture_failure_never_raises() -> None:
    class Broken:
        async def execute(self, *args: object) -> None:
            raise RuntimeError("down")

        async def fetchval(self, *args: object) -> None:
            raise RuntimeError("down")

    capture.reset_cache()
    await capture.record(Broken(), 1, "news_story", request("x"), completion())  # type: ignore[arg-type]
