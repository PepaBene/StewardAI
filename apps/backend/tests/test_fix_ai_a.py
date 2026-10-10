"""Group AI-A: owner prompt overrides reach the earnings, research, claim verifier and
monthly review calls; the research writer caches the shared material; the monthly review
goes through the Batch API by default."""

import datetime as dt
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest

from steward.config.holding_profiles import HoldingProfile
from steward.config.jobs import EarningsCalendarSettings, JobsConfig, ReviewSettings
from steward.config.prompts import PromptsConfig
from steward.config.store import save_config
from steward.documents import fetch as fetch_module
from steward.earnings.calendar import CALENDAR_SYSTEM, dates_from_ir_page
from steward.earnings.extract import SYSTEM_PROMPT, ExtractionRequest, LLMExtractor
from steward.earnings.locate import SEARCH_SYSTEM, LLMSearchLocator, SearchRequest
from steward.jobs.runner import run_job
from steward.llm.types import Completion, LLMError, Request
from steward.research.run import added_material_text
from steward.research.sources import Registry
from steward.research.writer import SYSTEM as WRITER_SYSTEM
from steward.research.writer import LLMResearchWriter, SectionRequest
from steward.review import monthly
from steward.review.monthly import SYSTEM as REVIEW_SYSTEM
from steward.review.monthly import LLMReviewWriter
from steward.verify.claims import SYSTEM as VERIFIER_SYSTEM
from steward.verify.claims import Claim, LLMClaimVerifier

OVERRIDE = "You are the owner's own prompt. Follow these instructions exactly."


def _usage(**extra: int) -> Any:
    return SimpleNamespace(
        input_tokens=100,
        output_tokens=10,
        cache_creation_input_tokens=extra.get("write", 0),
        cache_read_input_tokens=extra.get("read", 0),
        server_tool_use=None,
    )


def _response(parsed: Any, stop_reason: str = "end_turn", **usage: int) -> Any:
    return SimpleNamespace(
        usage=_usage(**usage), stop_reason=stop_reason, parsed_output=parsed, content=[]
    )


class _Messages:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.responses[min(len(self.calls), len(self.responses)) - 1]


class _Client:
    def __init__(self, *responses: Any) -> None:
        self.messages = _Messages(list(responses))


def _system_text(system: Any) -> str:
    return system if isinstance(system, str) else "".join(b["text"] for b in system)


# ---------------------------------------------------------------------------
# Prompt overrides reach the request (and the default is sent without one)
# ---------------------------------------------------------------------------


REQUEST = ExtractionRequest("AAPL", "Apple", dt.date(2025, 7, 31), 9, (2025, "Q3"), [], "USD")


async def test_extraction_prompt_override_reaches_the_request() -> None:
    doc = fetch_module.Document("https://x.test", fetch_module.TEXT, b"text")
    result = SimpleNamespace(figures=[])
    default: Any = _Client(_response(result))
    await LLMExtractor(default, "m", "low").extract(doc, "text", REQUEST)
    assert _system_text(default.messages.calls[0]["system"]) == SYSTEM_PROMPT
    client: Any = _Client(_response(result))
    await LLMExtractor(client, "m", "low", OVERRIDE).extract(doc, "text", REQUEST)
    assert _system_text(client.messages.calls[0]["system"]) == OVERRIDE


async def test_locate_prompt_override_reaches_the_request() -> None:
    request = SearchRequest("AAPL", "Apple", dt.date(2025, 7, 31), (2025, "Q3"), ["x.test"], [])
    answer = SimpleNamespace(url=None, reason="none")
    default: Any = _Client(_response(answer))
    await LLMSearchLocator(default, "m", 3).locate(request)
    assert default.messages.calls[0]["system"] == SEARCH_SYSTEM
    client: Any = _Client(_response(answer))
    await LLMSearchLocator(client, "m", 3, OVERRIDE).locate(request)
    assert client.messages.calls[0]["system"] == OVERRIDE
    # The runtime part (company, period, domains) is still sent by code after it.
    assert "Apple" in client.messages.calls[0]["messages"][0]["content"]


async def test_calendar_prompt_override_reaches_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch(*_: Any, **__: Any) -> fetch_module.Document:
        return fetch_module.Document("https://ir.test/cal", fetch_module.TEXT, b"calendar")

    monkeypatch.setattr("steward.earnings.calendar.fetch_document", fake_fetch)
    profile = HoldingProfile(ticker="X_US_EQ", financial_calendar_url="https://ir.test/cal")
    page = SimpleNamespace(entries=[])
    today = dt.date(2026, 9, 1)
    for system, expected in ((None, CALENDAR_SYSTEM), (OVERRIDE, OVERRIDE)):
        client: Any = _Client(_response(page))
        args: tuple[Any, ...] = (client, None, profile, EarningsCalendarSettings(), today)
        await dates_from_ir_page(*args, *([system] if system else []))
        assert client.messages.calls[0]["system"] == expected


async def test_claim_verifier_prompt_override_reaches_every_batch() -> None:
    claims = [Claim(i, f"Sentence {i}.", ("Snippet.",)) for i in range(45)]
    verdicts = SimpleNamespace(verdicts=[])
    default: Any = _Client(_response(verdicts))
    await LLMClaimVerifier(default, "m").check(claims, dt.date(2026, 9, 1))
    assert [c["system"] for c in default.messages.calls] == [VERIFIER_SYSTEM] * 2
    client: Any = _Client(_response(verdicts))
    await LLMClaimVerifier(client, "m", OVERRIDE).check(claims, dt.date(2026, 9, 1))
    assert [c["system"] for c in client.messages.calls] == [OVERRIDE] * 2


def _section(section: str, added: str = "") -> SectionRequest:
    return SectionRequest(
        section=section,
        name="Apple",
        ticker="AAPL_US_EQ",
        material="Company context: x\n\nSources:\n[S1] (fact, tier 1, ) Revenue: 94,036",
        language="en",
        max_sentences=3,
        domains=(),
        max_searches=0,
        peers=[],
        today=dt.date(2026, 9, 1),
        added_material=added,
    )


async def test_research_writer_override_and_cached_shared_material() -> None:
    from steward.research.writer import SectionDraft

    draft = SectionDraft(sentences=[])
    default: Any = _Client(_response(draft))
    await LLMResearchWriter(default, "m", "high").write(_section("business"))
    assert _system_text(default.messages.calls[0]["system"]) == WRITER_SYSTEM

    client: Any = _Client(_response(draft, write=900), _response(draft, read=900))
    writer = LLMResearchWriter(client, "m", "high", OVERRIDE)
    first = await writer.write(_section("business"))
    added = added_material_text(
        Registry.load(
            [
                {"id": "S1", "kind": "fact", "ref": "1", "title": "Revenue"},
                {"id": "S2", "kind": "search", "ref": "u", "title": "Reuters", "tier": 2},
            ]
        ),
        1,
    )
    second = await writer.write(_section("moat_risks", added))
    assert _system_text(client.messages.calls[0]["system"]) == OVERRIDE

    one, two = (c["messages"][0]["content"] for c in client.messages.calls)
    # The shared material comes first, byte-identical across sections, so the provider's
    # prefix cache lands; the section's task and the sources search added come after it.
    material = _section("business").material
    assert one.startswith(material) and two.startswith(material)
    assert "Moat and risks" in two[len(material) :]
    assert "[S2]" in two[len(material) :] and "[S2]" not in one
    # Cache writes and reads are recorded for the cost.
    assert first.usage.cache_write_tokens == 900 and second.usage.cache_read_tokens == 900


def test_added_material_is_empty_without_search_sources() -> None:
    reg = Registry.load([{"id": "S1", "kind": "fact", "ref": "1", "title": "Revenue"}])
    assert added_material_text(reg, 1) == ""


# ---------------------------------------------------------------------------
# Monthly review: override, Batch API, fallback to direct calls
# ---------------------------------------------------------------------------


ACTIONS = '{"actions": [{"action": "Trim X", "rationale": "r", "rule_ids": ["max-single"]}]}'


class _FakeLLM:
    """Stands in for `LLM`: `complete_batch` returns the canned completions (None = the batch
    did not finish in time), or raises `LLMError` when `fail` is set."""

    def __init__(self, answers: list[Any] | None = None, fail: bool = False) -> None:
        self.answers = answers or [None]
        self.fail = fail
        self.batches: list[list[Request]] = []
        self.kwargs: list[dict[str, Any]] = []

    async def complete_batch(self, requests: list[Request], **kwargs: Any) -> list[Any]:
        self.batches.append(requests)
        self.kwargs.append(kwargs)
        if self.fail:
            raise LLMError("batch failed")
        return self.answers


def _completion(parsed: Any) -> Completion:
    return Completion(
        model="m",
        provider="openai",
        text="",
        parsed=parsed,
        stop_reason="end",
        usage=_usage(),
    )


def _batched_client(*direct: Any, llm: _FakeLLM) -> Any:
    client: Any = _Client(*direct)
    client.llm = llm
    return client


async def test_the_monthly_review_uses_the_batch_api_by_default() -> None:
    from steward.review.monthly import ReviewDraft

    assert ReviewSettings().use_batch is True  # the digest's default
    parsed = ReviewDraft.model_validate_json(ACTIONS)
    llm = _FakeLLM([_completion(parsed)])
    client = _batched_client(llm=llm)
    writer = LLMReviewWriter(client, ReviewSettings(), OVERRIDE, poll_seconds=0)
    draft, usage, note = await writer.propose("material", dt.date(2026, 9, 1))
    assert draft is not None and draft.actions[0].rule_ids == ["max-single"] and note is None
    assert usage.batch is True and usage.input_tokens == 100
    assert client.messages.calls == []  # no direct call
    [[request]] = llm.batches
    assert request.system == OVERRIDE
    assert request.effort == ReviewSettings().effort
    assert request.schema is ReviewDraft
    assert "material" in request.messages[0].text


async def test_a_failed_review_batch_falls_back_to_a_direct_call() -> None:
    from steward.review.monthly import ReviewDraft

    client = _batched_client(_response(ReviewDraft(actions=[])), llm=_FakeLLM(fail=True))
    writer = LLMReviewWriter(client, ReviewSettings(), OVERRIDE, poll_seconds=0)
    draft, usage, _ = await writer.propose("material", dt.date(2026, 9, 1))
    assert draft is not None and usage.batch is False
    assert client.messages.calls[0]["system"] == OVERRIDE


async def test_a_late_review_batch_is_written_directly() -> None:
    from steward.review.monthly import ReviewDraft

    # `complete_batch` answers None for a request the batch did not finish in time.
    llm = _FakeLLM([None])
    client = _batched_client(_response(ReviewDraft(actions=[])), llm=llm)
    writer = LLMReviewWriter(client, ReviewSettings(batch_wait_minutes=1), OVERRIDE, poll_seconds=0)
    draft, usage, _ = await writer.propose("material", dt.date(2026, 9, 1))
    assert llm.kwargs[0]["wait_minutes"] == 1
    assert draft is not None and usage.batch is False
    assert client.messages.calls[0]["system"] == OVERRIDE


async def test_direct_review_without_batch() -> None:
    from steward.review.monthly import ReviewDraft

    client: Any = _Client(_response(ReviewDraft(actions=[])))
    writer = LLMReviewWriter(client, ReviewSettings(use_batch=False))
    draft, usage, _ = await writer.propose("material", dt.date(2026, 9, 1))
    assert draft is not None and usage.batch is False
    assert client.messages.calls[0]["system"] == REVIEW_SYSTEM


def test_saved_jobs_configs_without_the_new_settings_stay_valid() -> None:
    old = JobsConfig().model_dump(mode="json")
    for key in ("use_batch", "batch_wait_minutes"):
        old["review"].pop(key)
    assert JobsConfig.model_validate(old).review.use_batch is True


async def test_the_monthly_review_handler_sends_the_saved_override(
    backend_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with backend_pool.acquire() as conn:
        await save_config(
            conn,
            "prompts",
            PromptsConfig(overrides={"monthly_review": OVERRIDE}),
            created_by=None,
        )
    from steward.review.monthly import ReviewDraft

    llm = _FakeLLM([_completion(ReviewDraft(actions=[]))])
    monkeypatch.setattr(monthly, "create_llm", lambda _settings: llm)
    status = await run_job(
        backend_pool, "monthly_review", monthly.monthly_review_handler, trigger="manual"
    )
    assert status == "succeeded"
    assert llm.batches[0][0].system == OVERRIDE
    step = await backend_pool.fetchrow(
        "select batch, input_tokens from job_step where name = 'review'"
    )
    assert step["batch"] is True and step["input_tokens"] == 100
