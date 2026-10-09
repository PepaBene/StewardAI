"""The thesis draft keeps only lines that rest on a fact the app gave the model."""

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from steward.llm.types import Completion
from steward.thesis import draft
from steward.thesis.draft import DraftLine, DraftUnavailable, DraftVariant, ModelDraft

FACTS = [f"fact {i}" for i in range(1, 8)]
FACTS[1] = "fact 2: operating margin 20%"


def test_lines_without_a_real_fact_are_dropped_and_counted() -> None:
    model = ModelDraft(
        why_owned=DraftLine(text="Steady margins.", facts=[1, 99]),
        exit_conditions=[
            DraftLine(text="Margin below 20%.", facts=[2]),
            DraftLine(text="Invented event.", facts=[]),
            DraftLine(text="Out of range.", facts=[40]),
        ],
        pre_mortem=[DraftLine(text=f"risk {i}", facts=[i]) for i in range(1, 6)],
        variant_perception=DraftVariant(belief="b", disproof="d", facts=[0]),
    )
    out = draft.resolve(model, FACTS, "m", "AAPL_US_EQ")
    assert out.why_owned is not None and out.why_owned.facts == ["fact 1"]
    assert [x.text for x in out.exit_conditions] == ["Margin below 20%."]
    assert len(out.pre_mortem) == 3
    assert out.variant_perception is None
    assert out.dropped == 3  # two exit lines and the variant
    assert "base-rate" in out.left_to_owner[0]


def test_a_short_pre_mortem_is_said_to_be_short() -> None:
    model = ModelDraft(pre_mortem=[DraftLine(text="only", facts=[1])])
    out = draft.resolve(model, FACTS, None, "X")
    assert out.notes and "1 of the three" in out.notes[0]


def test_facts_come_from_research_figures_segments_and_conflicts() -> None:
    report: Any = SimpleNamespace(
        research=SimpleNamespace(
            body=SimpleNamespace(
                sections=[
                    SimpleNamespace(
                        title="Business", sentences=[SimpleNamespace(text="Sells phones.")]
                    )
                ]
            )
        ),
        key_figures=[
            SimpleNamespace(
                label="ROIC", period="2025", value=Decimal("0.3"), unit="%", own_median_5y=None
            ),
            SimpleNamespace(label="PE", period=None, value=None, unit="x", own_median_5y=None),
        ],
        segments=[
            SimpleNamespace(
                breakdown="product", period="2025", lines=[("Phones", Decimal("0.6")), ("X", None)]
            )
        ],
        conflicts=[SimpleNamespace(area="research", text="older than the filing")],
    )
    facts = draft.facts_for(report)
    assert facts[0] == "Research, Business: Sells phones."
    assert any(f.startswith("Figure, ROIC") for f in facts)
    assert not any("PE" in f for f in facts)
    assert "Phones 60%" in facts[2]
    assert facts[-1] == "Conflict (research): older than the filing"


class _FakeLLM:
    def __init__(self, parsed: Any, configured: bool = True) -> None:
        self.parsed = parsed
        self._configured = configured

    def configured(self, model: str) -> bool:
        return self._configured

    async def complete(self, req: Any) -> Completion:
        return Completion("m", "openai", "", self.parsed, "end", None)


def _report(n: int) -> Any:
    return SimpleNamespace(
        ticker="X",
        research=SimpleNamespace(
            body=SimpleNamespace(
                sections=[
                    SimpleNamespace(
                        title="T", sentences=[SimpleNamespace(text=f"s{i}") for i in range(n)]
                    )
                ]
            )
        ),
        key_figures=[],
        segments=[],
        conflicts=[],
    )


@pytest.mark.asyncio
async def test_too_little_material_gives_no_draft() -> None:
    with pytest.raises(DraftUnavailable, match="Too little"):
        await draft.draft_thesis(_FakeLLM(ModelDraft()), "gpt-5", _report(2))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_an_unconfigured_model_gives_no_draft() -> None:
    with pytest.raises(DraftUnavailable, match="not configured"):
        await draft.draft_thesis(_FakeLLM(None, configured=False), "gpt-5", _report(8))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_draft_is_built_from_the_model_answer() -> None:
    parsed = ModelDraft(why_owned=DraftLine(text="Because.", facts=[3]))
    out = await draft.draft_thesis(_FakeLLM(parsed), "gpt-5", _report(8))  # type: ignore[arg-type]
    assert out.why_owned is not None and out.why_owned.facts == ["Research, T: s2"]
    assert out.facts_used == 8
