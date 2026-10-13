"""Cost levers D2 to D8 of the deep research run: write order, cheap-first sections with
escalation, scouts, the flex tier and the batched claim checks. No network, no database."""

import datetime as dt
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from steward.config.research import ResearchProfile
from steward.llm import wire
from steward.llm.client import LLM
from steward.llm.types import LLMError, Message, Request, normalized_usage
from steward.research.gather import Gathered
from steward.research.layout import SectionSpec
from steward.research.run import (
    LATE_SECTIONS,
    late_material,
    merge_attempts,
    write_order,
)
from steward.research.scouts import (
    Extract,
    LLMScout,
    ScoutDraft,
    ScoutRequest,
    question_for,
    snippet_of,
)
from steward.research.sources import Registry
from steward.verify.claims import Claim, ClaimVerdict, ClaimVerdicts, LLMClaimVerifier

TODAY = dt.date(2026, 10, 13)


def _specs(*ids: str) -> list[SectionSpec]:
    return [SectionSpec(id=i, title=i, written_by="model") for i in ids]


# ------------------------------------------------------------------------------------ D4


def test_summary_last_moves_only_the_late_sections_and_keeps_their_order() -> None:
    specs = _specs("executive_summary", "business", "scenarios", "thesis_kpis", "open_questions")
    ordered = [s.id for s in write_order(specs, "summary_last")]
    assert ordered == [
        "business",
        "scenarios",
        "executive_summary",
        "thesis_kpis",
        "open_questions",
    ]
    assert all(i in LATE_SECTIONS for i in ordered[2:])


def test_template_order_is_the_default_and_changes_nothing() -> None:
    specs = _specs("executive_summary", "business")
    assert write_order(specs, "template") == specs
    assert ResearchProfile(id="p", name="p", sections=["business"]).write_order == "template"


def _gathered() -> Gathered:
    return Gathered.model_validate(
        {
            "ticker": "AAPL_US_EQ",
            "name": "Apple",
            "owned": True,
            "currency": "USD",
            "thesis_source": None,
            "strategy_source": None,
            "news_source_ids": [],
            "context": "Apple makes phones.",
            "sources": [
                {"id": "S1", "kind": "fact", "ref": "a", "title": "10-K", "snippet": "cited"},
                {"id": "S2", "kind": "fact", "ref": "b", "title": "Unused", "snippet": "unused"},
            ],
            "sections": {},
            "valuation": [],
            "peers": [],
        }
    )


def test_a_late_section_reads_only_what_was_written_and_the_sources_it_cites() -> None:
    gathered = _gathered()
    reg = Registry.load(gathered.sources)
    written = {"business": {"sentences": [{"text": "It sells phones.", "source_ids": ["S1"]}]}}
    text = late_material(gathered, reg, written)
    assert "It sells phones. [S1]" in text and "10-K" in text
    assert "Unused" not in text  # a source nothing cited is left out


def test_a_late_section_falls_back_to_the_whole_material_when_nothing_was_written() -> None:
    gathered = _gathered()
    assert late_material(gathered, Registry.load(gathered.sources), {"business": {}}) == ""


# --------------------------------------------------------------------------------- D3, D6


def test_escalation_keeps_the_cost_and_the_trace_of_both_attempts() -> None:
    cheap = {
        "sentences": [],
        "note": "not written: x",
        "steps": 2,
        "searches": 1,
        "new_sources": [{"id": "S9"}],
        "trace": {"model": {"model": "gpt-6-luna"}},
    }
    strong = {
        "sentences": [{"text": "A."}],
        "steps": 3,
        "searches": 2,
        "new_sources": [],
        "trace": {"model": {"model": "gpt-6.1-sol"}},
    }
    merged = merge_attempts(cheap, strong)
    assert merged["sentences"] == [{"text": "A."}] and "note" not in merged
    assert (merged["steps"], merged["searches"]) == (5, 3)
    assert merged["new_sources"] == [{"id": "S9"}]
    assert merged["extra_traces"] == [{"model": {"model": "gpt-6-luna"}}]
    assert merged["trace"] == {"model": {"model": "gpt-6.1-sol"}}


def test_every_new_profile_setting_defaults_to_todays_behaviour() -> None:
    p = ResearchProfile(id="p", name="p", sections=["business"])
    assert (p.write_order, p.cheap_sections, p.follow_up_cheap) == ("template", [], False)
    assert (p.search_mode, p.priority) == ("model", "standard")


def test_a_cheap_section_listed_twice_is_refused() -> None:
    with pytest.raises(ValueError, match="cheap section"):
        ResearchProfile(
            id="p", name="p", sections=["business"], cheap_sections=["business", "business"]
        )


# ------------------------------------------------------------------------------------ D2


class _ScoutClient:
    def __init__(self, extracts: list[Extract], *, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self._extracts = extracts
        self._fail = fail
        self.messages = SimpleNamespace(parse=self.parse)

    async def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._fail:
            raise LLMError("boom")
        result = SimpleNamespace(
            type="web_search_tool_result",
            content=[SimpleNamespace(url=e.url, title=e.title) for e in self._extracts],
        )
        return SimpleNamespace(
            usage=normalized_usage(input_tokens=100, output_tokens=20, searches=2),
            parsed_output=ScoutDraft(extracts=self._extracts),
            stop_reason="end_turn",
            content=[result],
        )


def _scout_request() -> ScoutRequest:
    return ScoutRequest(
        name="Apple",
        ticker="AAPL_US_EQ",
        question="Section: Catalysts.",
        domains=("sec.gov",),
        max_searches=4,
    )


async def test_a_scout_searches_with_its_own_model_and_returns_the_extracts() -> None:
    extract = Extract(url="https://sec.gov/a", title="10-K", facts=["Revenue grew 12%."])
    client = _ScoutClient([extract])
    scout = LLMScout(client, "gpt-6-luna", "low", cache_key="k", priority="flex")  # type: ignore[arg-type]
    result = await scout.scout(_scout_request())
    call = client.calls[0]
    assert call["model"] == "gpt-6-luna" and call["priority"] == "flex"
    assert call["tools"][0]["max_uses"] == 4 and call["tools"][0]["allowed_domains"] == ["sec.gov"]
    assert result.extracts == [extract] and result.titles == {"https://sec.gov/a": "10-K"}
    assert result.usage.searches == 2
    assert "2026" not in call["messages"][0]["content"]  # no date: the answer can be reused


async def test_a_failed_scout_returns_a_note_so_the_writer_can_search_itself() -> None:
    scout = LLMScout(_ScoutClient([], fail=True), "gpt-6-luna", "low")  # type: ignore[arg-type]
    result = await scout.scout(_scout_request())
    assert result.extracts == [] and result.note and "failed" in result.note


def test_a_scout_extract_is_stored_as_its_dated_facts() -> None:
    plain = Extract(url="u", title="t", facts=[" A. ", "B.", "", "C.", "D."])
    assert snippet_of(plain) == "A. B. C."
    dated = Extract(url="u", title="t", date="2026-10-01", facts=["A."])
    assert snippet_of(dated) == "(2026-10-01) A."
    assert snippet_of(Extract(url="u", title="t")) == ""


def test_the_counter_case_scout_is_told_to_look_for_the_opposing_evidence() -> None:
    assert "disconfirming" in question_for("T", "task", "", counter=True)
    assert "disconfirming" not in question_for("T", "task", "", counter=False)


# ------------------------------------------------------------------------------------ D5


def _req(**kw: Any) -> Request:
    return Request(model="gpt-6.1-sol", system="s", messages=(Message("user", "q"),), **kw)


def test_the_flex_tier_is_asked_for_only_when_requested() -> None:
    assert "service_tier" not in wire.openai_body(_req())
    assert wire.openai_body(_req(priority="flex"))["service_tier"] == "flex"


class _Http:
    """Refuses flex calls when `refuse_flex`, answers everything else."""

    def __init__(self, *, refuse_flex: bool) -> None:
        self.refuse_flex = refuse_flex
        self.tiers: list[str | None] = []

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        body = kwargs["json"]
        self.tiers.append(body.get("service_tier"))
        if body.get("service_tier") == "flex" and self.refuse_flex:
            return httpx.Response(429, json={"error": {"message": "Resource Unavailable"}})
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "hi"}],
                    }
                ],
                "usage": {"input_tokens": 3, "output_tokens": 1},
            },
        )


async def test_a_refused_flex_call_is_answered_at_the_standard_tier() -> None:
    http = _Http(refuse_flex=True)
    llm = LLM(openai_key="k", http=http, retries=0)  # type: ignore[arg-type]
    done = await llm.complete(_req(priority="flex"))
    assert done.text == "hi"
    assert http.tiers == ["flex", None]


async def test_an_accepted_flex_call_is_not_repeated() -> None:
    http = _Http(refuse_flex=False)
    llm = LLM(openai_key="k", http=http, retries=0)  # type: ignore[arg-type]
    await llm.complete(_req(priority="flex"))
    assert http.tiers == ["flex"]


# ------------------------------------------------------------------------------------ D8


class _VerifierClient:
    def __init__(self) -> None:
        self.asked = 0
        self.messages = SimpleNamespace(parse=self.parse)

    async def parse(self, **kwargs: Any) -> Any:
        self.asked += 1
        prompt = kwargs["messages"][0]["content"]
        indexes = [int(p.split('"')[0]) for p in prompt.split('<claim index="')[1:]]
        return SimpleNamespace(
            usage=normalized_usage(input_tokens=10, output_tokens=5),
            parsed_output=ClaimVerdicts(
                verdicts=[ClaimVerdict(index=i, supported=True, reason="r") for i in indexes]
            ),
        )


async def test_the_verifier_still_answers_every_claim_through_the_shared_call_path() -> None:
    client = _VerifierClient()
    verifier = LLMClaimVerifier(client, "gpt-6-luna", use_batch=False)  # type: ignore[arg-type]
    claims = [Claim(n, f"Claim {n}.", (f"snippet {n}",)) for n in range(90)]
    verdicts, usage = await verifier.check(claims, TODAY)
    assert sorted(v.index for v in verdicts) == list(range(90))
    assert client.asked >= 2  # more claims than one call holds
    assert usage.input_tokens == 10 * client.asked
