"""Plain-English screen parsing (insight plan I10.1-I10.4): validation of the model's draft
against the metric registry, and the evaluation scorer."""

import json
from decimal import Decimal
from typing import Any

from steward.discovery import parse_eval
from steward.discovery.metrics import STOCK_CATALOGUE
from steward.discovery.parse import (
    DraftFilter,
    DraftSort,
    ModelDraft,
    ModelFilter,
    ScreenDraft,
    registry_hash,
    to_stock_filters,
    validate_draft,
)

SECTORS = ["Technology", "Utilities"]


def raw(filters: list[ModelFilter], **kw: Any) -> ModelDraft:
    base: dict[str, Any] = {
        "refused": False,
        "refusal_reason": None,
        "markets": [],
        "industries": [],
        "keywords": [],
        "filters": filters,
        "sort": None,
        "unmatched_phrases": [],
    }
    return ModelDraft(**{**base, **kw})


def f(
    metric: str, op: str, number: float | None = None, values: list[str] | None = None
) -> ModelFilter:
    return ModelFilter(metric_id=metric, op=op, number=number, values=values or [], phrase=metric)  # type: ignore[arg-type]


def test_acceptance_sentence_becomes_visible_filters() -> None:
    d = validate_draft(
        raw(
            [
                f("dividend_yield", "gte", 4),
                f("net_debt_to_equity", "lte", 0.5),
                f("forward_eps_growth", "gte", 0),
            ],
            markets=["us"],
        ),
        SECTORS,
    )
    assert d.markets == ["US"] and not d.refused
    got = {(x.metric_id, x.op): x.value for x in d.filters}
    # "4" for a yield is read as 4 % and says so.
    assert got[("dividend_yield", "gte")] == Decimal("0.04")
    assert next(x for x in d.filters if x.metric_id == "dividend_yield").note
    assert got[("net_debt_to_equity", "lte")] == Decimal("0.5")
    assert to_stock_filters(d)[0] == {"metric": "country", "op": "in", "value": ["US"]}


def test_unknown_or_implausible_filters_are_reported_not_guessed() -> None:
    d = validate_draft(
        raw(
            [
                f("esg_rating", "gte", 3),
                f("pe_ratio", "gte", 99999),
                f("pe_ratio", "in", None, ["x"]),
                f("pe_ratio", "lte", 15),
                f("pe_ratio", "lte", 12),
            ],
            industries=["Technology", "Crypto"],
            unmatched_phrases=["run by founders"],
        ),
        SECTORS,
    )
    assert [(x.metric_id, x.op) for x in d.filters] == [("pe_ratio", "lte")]
    text = " | ".join(d.unmatched_phrases)
    for bit in ("esg_rating", "plausible", "needs a number", "second", "Crypto", "run by founders"):
        assert bit in text
    assert d.industries == ["Technology"]


def test_category_filters_fold_into_markets_and_industries() -> None:
    d = validate_draft(
        raw([f("country", "in", None, ["gb", "DE"]), f("sector", "in", None, ["utilities"])]),
        SECTORS,
    )
    assert d.markets == ["DE", "GB"] and d.industries == ["Utilities"] and d.filters == []


def test_refusal_carries_no_filters() -> None:
    d = validate_draft(
        raw([f("pe_ratio", "lte", 10)], refused=True, refusal_reason="a forecast"), SECTORS
    )
    assert d.refused and d.filters == [] and d.refusal_reason == "a forecast"


def test_full_percent_is_not_shrunk() -> None:
    d = validate_draft(raw([f("revenue_growth", "gte", 1)]), SECTORS)
    assert d.filters[0].value == Decimal(1) and d.filters[0].note is None


def test_registry_has_ticks_in_order_and_a_stable_hash() -> None:
    for m in STOCK_CATALOGUE.values():
        if m.ticks:
            assert list(m.ticks) == sorted(m.ticks), m.id
    assert registry_hash() == registry_hash() and len(registry_hash()) == 16


def test_evaluation_cases_name_real_metrics_and_score() -> None:
    data = parse_eval.load_cases()
    assert len(data["cases"]) == 40
    assert sum(1 for c in data["cases"] if c.get("refused")) >= 3
    for c in data["cases"]:
        for metric, op, value in c.get("filters", []):
            m = STOCK_CATALOGUE[metric]
            assert op in ("gte", "lte") and m.kind == "numeric", c["id"]
            Decimal(value)
        for s in c.get("industries", []):
            assert s in data["sectors"], c["id"]

    def expected(case: dict[str, Any]) -> ScreenDraft:
        return ScreenDraft(
            markets=case.get("markets", []),
            industries=case.get("industries", []),
            filters=[
                DraftFilter(metric_id=m, op=o, value=Decimal(v))
                for m, o, v in case.get("filters", [])
            ],
            sort=DraftSort(metric_id=case["sort"][0], descending=case["sort"][1])
            if "sort" in case
            else None,
            unmatched_phrases=[f"{p} (not a metric)" for p in case.get("unmatched", [])],
            refused=bool(case.get("refused")),
        )

    by_text = {c["text"]: c for c in data["cases"]}

    async def perfect(text: str, _s: list[str], _c: list[str]) -> ScreenDraft:
        return expected(by_text[text])

    import asyncio

    report = asyncio.run(parse_eval.evaluate(perfect, data))
    assert report.passed and report.exact == 1.0

    async def percent_slip(text: str, _s: list[str], _c: list[str]) -> ScreenDraft:
        d = expected(by_text[text])
        for x in d.filters:
            if STOCK_CATALOGUE[x.metric_id].unit == "fraction" and x.value != 0:
                x.value = Decimal(str(x.value)) * 100
        if d.refused:
            d.refused = False
        return d

    bad = asyncio.run(parse_eval.evaluate(percent_slip, data))
    assert not bad.passed and bad.units < 1 and bad.refusals == 0


def test_gate_status_reports_a_stale_registry() -> None:
    import tempfile
    from pathlib import Path

    gate = Path(tempfile.mkdtemp()) / "gate.json"
    assert "not been evaluated" in parse_eval.gate_status(gate)
    gate.write_text('{"registry_hash": "old", "recorded_at": "2026-10-01T00:00:00+00:00"}')
    assert "re-run" in parse_eval.gate_status(gate)
    gate.write_text(
        json.dumps({"registry_hash": registry_hash(), "recorded_at": "2026-10-01", "exact": 0.9})
    )
    assert parse_eval.gate_status(gate).startswith("Evaluation passed")
