"""Group AI-A: owner prompt overrides reach the earnings, research, claim verifier and
monthly review calls; the research writer caches the shared material; the monthly review
goes through the Batch API by default."""

import asyncio
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
from steward.earnings.extract import SYSTEM_PROMPT, ClaudeExtractor, ExtractionRequest
from steward.earnings.locate import SEARCH_SYSTEM, ClaudeSearchLocator, SearchRequest
from steward.jobs.runner import run_job
from steward.research.run import added_material_text
from steward.research.sources import Registry
from steward.research.writer import SYSTEM as WRITER_SYSTEM
from steward.research.writer import ClaudeResearchWriter, SectionRequest
from steward.review import monthly
from steward.review.monthly import SYSTEM as REVIEW_SYSTEM
from steward.review.monthly import ClaudeReviewWriter
from steward.verify.claims import SYSTEM as VERIFIER_SYSTEM
from steward.verify.claims import Claim, ClaudeClaimVerifier

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
    await ClaudeExtractor(default, "m", "low").extract(doc, "text", REQUEST)
    assert _system_text(default.messages.calls[0]["system"]) == SYSTEM_PROMPT
    client: Any = _Client(_response(result))
    await ClaudeExtractor(client, "m", "low", OVERRIDE).extract(doc, "text", REQUEST)
    assert _system_text(client.messages.calls[0]["system"]) == OVERRIDE


async def test_locate_prompt_override_reaches_the_request() -> None:
    request = SearchRequest("AAPL", "Apple", dt.date(2025, 7, 31), (2025, "Q3"), ["x.test"], [])
    answer = SimpleNamespace(url=None, reason="none")
    default: Any = _Client(_response(answer))
    await ClaudeSearchLocator(default, "m", 3).locate(request)
    assert default.messages.calls[0]["system"] == SEARCH_SYSTEM
    client: Any = _Client(_response(answer))
    await ClaudeSearchLocator(client, "m", 3, OVERRIDE).locate(request)
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
    await ClaudeClaimVerifier(default, "m").check(claims, dt.date(2026, 9, 1))
    assert [c["system"] for c in default.messages.calls] == [VERIFIER_SYSTEM] * 2
    client: Any = _Client(_response(verdicts))
    await ClaudeClaimVerifier(client, "m", OVERRIDE).check(claims, dt.date(2026, 9, 1))
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
    await ClaudeResearchWriter(default, "m", "high").write(_section("business"))
    assert _system_text(default.messages.calls[0]["system"]) == WRITER_SYSTEM

    client: Any = _Client(_response(draft, write=900), _response(draft, read=900))
    writer = ClaudeResearchWriter(client, "m", "high", OVERRIDE)
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
    # The shared material is the first block, with the breakpoint, byte-identical across
    # sections; the section's task and the sources search added come after it.
    assert one[0] == two[0] and one[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in one[1] and "Moat and risks" in two[1]["text"]
    assert "[S2]" in two[1]["text"] and "[S2]" not in two[0]["text"]
    # Cache writes and reads are recorded for the cost.
    assert first.usage.cache_write_tokens == 900 and second.usage.cache_read_tokens == 900


def test_added_material_is_empty_without_search_sources() -> None:
    reg = Registry.load([{"id": "S1", "kind": "fact", "ref": "1", "title": "Revenue"}])
    assert added_material_text(reg, 1) == ""


# ---------------------------------------------------------------------------
# Monthly review: override, Batch API, fallback to direct calls
# ---------------------------------------------------------------------------


class _Batches:
    def __init__(self, result: Any, status: list[str]) -> None:
        self.result = result
        self.status = status
        self.created: list[dict[str, Any]] = []
        self.cancelled: list[str] = []

    async def create(self, requests: list[dict[str, Any]]) -> Any:
        self.created.extend(requests)
        return SimpleNamespace(id="b1", processing_status=self.status.pop(0))

    async def retrieve(self, batch_id: str) -> Any:
        return SimpleNamespace(id=batch_id, processing_status=self.status.pop(0))

    async def cancel(self, batch_id: str) -> None:
        self.cancelled.append(batch_id)

    async def results(self, batch_id: str) -> Any:
        async def gen() -> Any:
            yield SimpleNamespace(custom_id="review", result=self.result)

        return gen()


def _batch_success(text: str) -> Any:
    message = SimpleNamespace(
        usage=_usage(),
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=text)],
    )
    return SimpleNamespace(type="succeeded", message=message)


ACTIONS = '{"actions": [{"action": "Trim X", "rationale": "r", "rule_ids": ["max-single"]}]}'


async def test_the_monthly_review_uses_the_batch_api_by_default() -> None:
    assert ReviewSettings().use_batch is True  # the digest's default
    client: Any = _Client()
    client.messages.batches = _Batches(_batch_success(ACTIONS), ["in_progress", "ended"])
    writer = ClaudeReviewWriter(client, ReviewSettings(), OVERRIDE, poll_seconds=0)
    draft, usage, note = await writer.propose("material", dt.date(2026, 9, 1))
    assert draft is not None and draft.actions[0].rule_ids == ["max-single"] and note is None
    assert usage.batch is True and usage.input_tokens == 100
    assert client.messages.calls == []  # no direct call
    [request] = client.messages.batches.created
    params = request["params"]
    assert params["system"] == OVERRIDE
    assert params["output_config"]["effort"] == "high"
    assert params["output_config"]["format"]["type"] == "json_schema"
    assert "material" in params["messages"][0]["content"]


async def test_a_failed_review_batch_falls_back_to_a_direct_call() -> None:
    from steward.review.monthly import ReviewDraft

    client: Any = _Client(_response(ReviewDraft(actions=[])))
    client.messages.batches = _Batches(SimpleNamespace(type="errored"), ["ended"])
    writer = ClaudeReviewWriter(client, ReviewSettings(), OVERRIDE, poll_seconds=0)
    draft, usage, _ = await writer.propose("material", dt.date(2026, 9, 1))
    assert draft is not None and usage.batch is False
    assert client.messages.calls[0]["system"] == OVERRIDE


async def test_a_late_review_batch_is_cancelled_and_written_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from steward.review.monthly import ReviewDraft

    client: Any = _Client(_response(ReviewDraft(actions=[])))
    client.messages.batches = _Batches(None, ["in_progress"] * 5)
    writer = ClaudeReviewWriter(
        client, ReviewSettings(batch_wait_minutes=1), OVERRIDE, poll_seconds=0
    )
    loop = asyncio.get_running_loop()
    ticks = iter([0.0])
    # The first reading sets the deadline; every later one is past it.
    monkeypatch.setattr(loop, "time", lambda: next(ticks, 10_000.0))
    draft, usage, _ = await writer.propose("material", dt.date(2026, 9, 1))
    monkeypatch.undo()
    assert client.messages.batches.cancelled == ["b1"]
    assert draft is not None and usage.batch is False
    assert client.messages.calls[0]["system"] == OVERRIDE


async def test_direct_review_without_batch() -> None:
    from steward.review.monthly import ReviewDraft

    client: Any = _Client(_response(ReviewDraft(actions=[])))
    writer = ClaudeReviewWriter(client, ReviewSettings(use_batch=False))
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
    client: Any = _Client()
    monkeypatch.setattr(monthly, "create_client", lambda _settings: client)
    seen: dict[str, Any] = {}

    async def run_review(ctx: Any, deps: Any) -> None:
        # Claude models are never called now, so the Batch API path no longer runs from the
        # handler; what matters here is the system prompt the handler gives the writer.
        seen["system"] = deps.writer._system

    monkeypatch.setattr(monthly, "run_review", run_review)
    status = await run_job(
        backend_pool, "monthly_review", monthly.monthly_review_handler, trigger="manual"
    )
    assert status == "succeeded"
    assert seen["system"] == OVERRIDE
