"""Cost lever C6: a chat turn the cheaper model could not answer is asked again on the stronger
one, and only then; each attempt is priced as a step of its own model."""

import datetime as dt
import json

import asyncpg
import pytest

from steward.ai import Usage
from steward.chat.assistant import (
    AnswerSentence,
    AssistantResult,
    ChatAnswerDraft,
    ChatDeps,
    needs_escalation,
    run_chat,
)
from steward.jobs.runner import JobContext, create_job, run_job


def sourced() -> ChatAnswerDraft:
    return ChatAnswerDraft(sentences=[AnswerSentence(text="Cash is 10.", source_ids=["S1"])])


def test_the_signals_that_ask_a_turn_again() -> None:
    usage = Usage(model="m")
    assert needs_escalation(AssistantResult(None, usage, {}, "the step cap was reached"))
    assert needs_escalation(
        AssistantResult(
            ChatAnswerDraft(sentences=[], insufficient_evidence="Nothing found."), usage
        )
    )
    unsourced = ChatAnswerDraft(sentences=[AnswerSentence(text="It will probably go up.")])
    assert needs_escalation(AssistantResult(unsourced, usage))
    assert not needs_escalation(AssistantResult(sourced(), usage))
    linked = ChatAnswerDraft(
        sentences=[AnswerSentence(text="Reported.", urls=["https://www.reuters.com/a"])]
    )
    assert not needs_escalation(AssistantResult(linked, usage))


class Scripted:
    def __init__(self, model: str, draft: ChatAnswerDraft | None) -> None:
        self.model, self.draft, self.calls = model, draft, 0

    async def answer(self, history, question, tools, today) -> AssistantResult:  # type: ignore[no-untyped-def]
        self.calls += 1
        usage = Usage(model=self.model, input_tokens=100, output_tokens=10)
        return AssistantResult(self.draft, usage, {}, None if self.draft else "no parseable answer")


async def ask(pool: asyncpg.Pool, cheap: Scripted, strong: Scripted | None) -> int:
    conversation = await pool.fetchval(
        "insert into public.chat_conversation default values returning id"
    )
    message = await pool.fetchval(
        "insert into public.chat_message (conversation_id, role, content, status)"
        " values ($1, 'user', 'What is my cash?', 'pending') returning id",
        conversation,
    )
    job = await create_job(
        pool, "chat", "chat", params={"conversation_id": conversation, "message_id": message}
    )

    async def handler(ctx: JobContext) -> None:
        deps = ChatDeps(assistant=cheap, now=dt.datetime.now(dt.UTC), escalation=strong)  # type: ignore[arg-type]
        await run_chat(ctx, deps)

    assert await run_job(pool, "chat", handler, trigger="chat", job_id=job) == "succeeded"
    return int(job)


async def steps(pool: asyncpg.Pool, job: int) -> list[tuple[str, str | None]]:
    rows = await pool.fetch(
        "select name, model from public.job_step where job_id = $1 order by step_index", job
    )
    return [(r["name"], r["model"]) for r in rows]


@pytest.mark.db
async def test_a_failed_cheap_attempt_is_asked_again_on_the_stronger_model(
    backend_pool: asyncpg.Pool,
) -> None:
    cheap, strong = Scripted("gpt-6-luna", None), Scripted("gpt-6.1-sol", sourced())
    job = await ask(backend_pool, cheap, strong)
    assert (cheap.calls, strong.calls) == (1, 1)
    named = dict(await steps(backend_pool, job))
    assert named["first_try"] == "gpt-6-luna" and named["escalate"] == "gpt-6.1-sol"
    answer = await backend_pool.fetchval(
        "select body from public.chat_message where role = 'assistant' and job_id = $1", job
    )
    assert json.loads(answer)["model"] == "gpt-6.1-sol"


@pytest.mark.db
async def test_a_good_cheap_answer_is_not_asked_again(backend_pool: asyncpg.Pool) -> None:
    cheap, strong = Scripted("gpt-6-luna", sourced()), Scripted("gpt-6.1-sol", sourced())
    job = await ask(backend_pool, cheap, strong)
    assert (cheap.calls, strong.calls) == (1, 0)
    assert [name for name, _ in await steps(backend_pool, job)].count("escalate") == 0


@pytest.mark.db
async def test_without_an_escalation_model_the_turn_runs_as_before(
    backend_pool: asyncpg.Pool,
) -> None:
    cheap = Scripted("gpt-6-luna", None)
    job = await ask(backend_pool, cheap, None)
    names = [name for name, _ in await steps(backend_pool, job)]
    assert cheap.calls == 1 and "first_try" not in names and "escalate" not in names
