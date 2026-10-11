"""The weekly theme narrative: what code accepts of a model's paragraph (no database)."""

from types import SimpleNamespace
from typing import Any

from steward.ai import Usage
from steward.news import theme_narrative as tn

ITEMS = [
    tn.ThemeItem(1, "Fed holds rates at 4.5%", "Officials see slower cuts", "US", 1),
    tn.ThemeItem(2, "ECB signals patience on cuts", None, "EU", 1),
    tn.ThemeItem(3, "BoJ keeps policy rate unchanged", "Yen weakens", "JP", 2),
]


class Llm:
    def __init__(self, narrative: tn.Narrative) -> None:
        self.narrative = narrative
        self.calls = 0

    async def complete(self, request: Any) -> SimpleNamespace:
        self.calls += 1
        return SimpleNamespace(
            parsed=self.narrative,
            citations=[],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=100, output_tokens=40),
        )


def test_a_paragraph_citing_two_theme_items_with_known_numbers_is_accepted() -> None:
    n = tn.Narrative(text="The Fed held at 4.5% while the ECB signalled patience.", item_ids=[1, 2])
    assert tn.accepted(ITEMS, n)


def test_a_paragraph_needs_two_distinct_cited_items() -> None:
    assert not tn.accepted(ITEMS, tn.Narrative(text="Rates held.", item_ids=[1]))
    assert not tn.accepted(ITEMS, tn.Narrative(text="Rates held.", item_ids=[1, 1]))


def test_a_paragraph_citing_an_item_outside_the_theme_is_rejected() -> None:
    assert not tn.accepted(ITEMS, tn.Narrative(text="Rates held.", item_ids=[1, 99]))


def test_a_number_not_in_the_cited_items_is_rejected() -> None:
    # 3.5% appears in no cited item; 4.5% appears only in item 1, which is cited.
    assert not tn.accepted(ITEMS, tn.Narrative(text="The Fed cut to 3.5%.", item_ids=[1, 2]))
    # A number found only in an item that is not cited does not count.
    assert not tn.accepted(ITEMS, tn.Narrative(text="Rates at 4.5%.", item_ids=[2, 3]))


async def test_write_narrative_returns_none_for_a_rejected_paragraph() -> None:
    bad = tn.Narrative(text="The Fed cut to 3.5%.", item_ids=[1, 2])
    llm = Llm(bad)
    usage = Usage(model="m")
    assert await tn.write_narrative(llm, "m", "Rates", ITEMS, usage) is None  # type: ignore[arg-type]
    assert llm.calls == 1 and usage.input_tokens == 100


async def test_write_narrative_returns_an_accepted_paragraph_as_a_brief_body() -> None:
    good = tn.Narrative(text="The Fed held at 4.5%; the ECB signalled patience.", item_ids=[2, 1])
    result = await tn.write_narrative(Llm(good), "m", "Rates", ITEMS, Usage(model="m"))  # type: ignore[arg-type]
    assert result is not None
    body = tn.as_body("Rates", result)
    line = body.sections[0].lines[0]
    assert body.sections[0].title == "Rates"
    assert line.item_ids == [1, 2] and line.written_by_model
