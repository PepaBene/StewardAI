"""Golden cases for the news techniques that run in code (span verification, thread claim diffs,
novelty) and for what the code keeps of the model techniques' output (extraction, framing,
claim checks, sentiment, impact chains and notes, thesis links, briefs).

    uv run python -m steward.news.eval_analysis

They need no model or database, so they run in CI and gate any change to those functions. The
model cases fix a model's answer and check the verification around it, not the model itself.
"""

import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from steward.config.thesis import ThesisConfig
from steward.news.analysis import ItemText, StoryMaterial, verify_span
from steward.news.brief import BriefLine, Candidate, WrittenLine, build_lines, rank_score
from steward.news.brief import sections as brief_sections
from steward.news.claims import ClaimVerdicts
from steward.news.claims import checked as claims_checked
from steward.news.extract import StoryExtraction, verified
from steward.news.framing import FramingComparison
from steward.news.framing import verified as framing_verified
from steward.news.impact import ImpactNotes, apply_notes, event_state, graph_chains
from steward.news.novelty import classify_novelty
from steward.news.sentiment import RawSentiment, bounded
from steward.news.thesis_link import ThesisLinks
from steward.news.thesis_link import checked as thesis_checked
from steward.news.threads import claim_diff
from steward.xray.supply_chain import SupplyChain, SupplyEdge, SupplyNode

GOLDEN_PATH = Path(__file__).resolve().parents[3] / "tests" / "news" / "analysis_golden.json"
MODEL_GOLDEN_PATH = GOLDEN_PATH.with_name("model_golden.json")
REPEAT_AT = 0.88
UPDATE_AT = 0.72
NOW = dt.datetime(2026, 10, 10, 12, 0, tzinfo=dt.UTC)


def load_golden(path: Path = GOLDEN_PATH) -> dict[str, Any]:
    golden: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return golden


def run_spans(cases: list[dict[str, Any]]) -> list[str]:
    return [
        f"span {c['id']}: expected {c['ok']}"
        for c in cases
        if verify_span(c["span"], c["texts"]) != c["ok"]
    ]


def run_diffs(cases: list[dict[str, Any]]) -> list[str]:
    failures = []
    for c in cases:
        diff = claim_diff(c["prior"], c["now"], c.get("corrections", ()))
        got = (len(diff.new), len(diff.changed), len(diff.retracted))
        want = (c["new"], c["changed"], c.get("retracted", 0))
        if got != want:
            failures.append(f"diff {c['id']}: new/changed/retracted {got}, expected {want}")
    return failures


def run_novelty(cases: list[dict[str, Any]]) -> list[str]:
    failures = []
    for c in cases:
        result = classify_novelty(
            c["own"],
            {int(k): v for k, v in c["earlier"].items()},
            recycled=c["recycled"],
            event_date=(NOW - dt.timedelta(days=c["age_days"])).date() if c["age_days"] else None,
            first_seen=NOW,
            repeat_at=REPEAT_AT,
            update_at=UPDATE_AT,
        )
        if result.novelty != c["expect"]:
            failures.append(f"novelty {c['id']}: {result.novelty}, expected {c['expect']}")
    return failures


def _story(
    items: list[dict[str, Any]],
    ticker: str | None = None,
    lifecycle: str = "open",
    severity: int = 1,
    cluster_id: int = 1,
) -> StoryMaterial:
    return StoryMaterial(
        cluster_id=cluster_id,
        ticker=ticker,
        headline=items[0]["headline"],
        event_type=None,
        severity=severity,
        lifecycle=lifecycle,
        recycled=False,
        event_date=None,
        first_seen=NOW,
        last_seen=NOW,
        thread_id=None,
        items=tuple(
            ItemText(
                id=i["id"],
                url=f"https://example.test/{i['id']}",
                outlet="example.test",
                outlet_name=None,
                tier=2,
                region=None,
                state_media=False,
                evidence_kind=i.get("evidence_kind"),
                independence_key=None,
                is_correction=False,
                published_at=NOW,
                language="en",
                headline=i["headline"],
                summary=i.get("summary"),
                body=i.get("body"),
            )
            for i in items
        ),
    )


def run_extraction(cases: list[dict[str, Any]]) -> list[str]:
    """What the code keeps of a model's extraction: claims with a real quote, a main number that
    is in the text, a known event type and well-formed dates."""
    failures = []
    for c in cases:
        out = verified(_story(c["items"]), StoryExtraction.model_validate(c["raw"]))
        want = c["expect"]
        got: dict[str, Any] = {"claims": len(out.key_claims) if out else 0}
        if out is not None:
            got.update(
                magnitude=out.magnitude is not None,
                event_type=out.event_type,
                effective=out.effective,
                occurred=out.occurred,
            )
        wrong = {k: (got.get(k), v) for k, v in want.items() if got.get(k) != v}
        if wrong:
            failures.append(f"extraction {c['id']}: (got, expected) {wrong}")
    return failures


def run_framing(cases: list[dict[str, Any]]) -> list[str]:
    failures = []
    for c in cases:
        out = framing_verified(_story(c["items"]), FramingComparison.model_validate(c["raw"]))
        want = c["expect"]
        if want.get("none"):
            got: dict[str, Any] = {"none": out is None}
        elif out is None:
            got = {"none": True}
        else:
            got = {
                "outlets": len(out.outlets),
                "agreement": out.agreement,
                "quotes": [len(o.quotes) for o in out.outlets],
                "numbers": [len(o.numbers_cited) for o in out.outlets],
            }
        wrong = {k: (got.get(k), v) for k, v in want.items() if got.get(k) != v}
        if wrong:
            failures.append(f"framing {c['id']}: (got, expected) {wrong}")
    return failures


def run_claims(cases: list[dict[str, Any]]) -> list[str]:
    failures = []
    for c in cases:
        raw = ClaimVerdicts.model_validate({"verdicts": c["raw"]})
        got = [x.verdict for x in claims_checked(_story(c["items"]), c["claims"], raw)]
        if got != c["expect"]:
            failures.append(f"claims {c['id']}: {got}, expected {c['expect']}")
    return failures


def run_sentiment(cases: list[dict[str, Any]]) -> list[str]:
    failures = []
    for c in cases:
        raw = RawSentiment.model_validate({"aspects": c["raw"]})
        out = bounded(_story(c["items"]), raw)
        got = {"aspects": len(out.aspects), "intensity": round(out.intensity, 3)}
        wrong = {k: (got[k], v) for k, v in c["expect"].items() if got[k] != v}
        if wrong:
            failures.append(f"sentiment {c['id']}: (got, expected) {wrong}")
    return failures


def _graph(spec: dict[str, Any]) -> SupplyChain:
    asof = dt.date(2026, 6, 1)
    return SupplyChain(
        nodes=[
            SupplyNode(
                symbol=n["symbol"], name=None, weight=n.get("weight", 0.0),
                held=n.get("weight", 0.0) > 0, direct_weight=n.get("weight", 0.0), level=0,
            )
            for n in spec["nodes"]
        ],
        edges=[
            SupplyEdge(
                id=None, supplier=e["supplier"], customer=e["customer"], product=e["product"],
                customer_share=e.get("share"), source_url="https://example.test/filing",
                as_of=dt.date.fromisoformat(e.get("as_of", asof.isoformat())),
                origin=e.get("origin", "owner"),
            )
            for e in spec["edges"]
        ],
        linked=[], held_in_graph=sum(n.get("weight", 0.0) for n in spec["nodes"]), notes=[],
    )  # fmt: skip


def run_impact(cases: list[dict[str, Any]]) -> list[str]:
    """The chains the code builds from the graph and the owner's classifications, and what it
    keeps of a model's path sentences and hypotheses."""
    failures = []
    for c in cases:
        graph = _graph(c["graph"])
        story = _story(
            [{"id": 1, "headline": c["headline"]}],
            ticker=c.get("ticker"),
            lifecycle=c.get("lifecycle", "open"),
        )
        extraction = (
            StoryExtraction.model_validate(c["extraction"]) if c.get("extraction") else None
        )
        result, paths = graph_chains(
            graph, story, extraction, today=NOW.date(), attributes=c.get("attributes")
        )
        if c.get("notes"):
            result = apply_notes(result, paths, ImpactNotes.model_validate(c["notes"]), graph)
        got: dict[str, Any] = {
            "chains": [[ch.relation, ch.holding, ch.confidence] for ch in result.chains],
            "direct": round(result.direct_weight, 4),
            "reached": round(result.reached_weight, 4),
            "attribute": round(result.attribute_weight, 4),
            "state": event_state(extraction, story.lifecycle),
            "weights": [ch.weight for ch in result.chains],
            "notes": [ch.note for ch in result.chains],
        }
        wrong = {k: (got[k], v) for k, v in c["expect"].items() if got[k] != v}
        if wrong:
            failures.append(f"impact {c['id']}: (got, expected) {wrong}")
    return failures


def run_thesis(cases: list[dict[str, Any]]) -> list[str]:
    failures = []
    for c in cases:
        thesis = ThesisConfig.model_validate(c["thesis"])
        story = _story(
            [{"id": 1, "headline": c["headline"]}],
            ticker=thesis.ticker,
            lifecycle=c.get("lifecycle", "open"),
        )
        extraction = StoryExtraction.model_validate(c["extraction"])
        links = thesis_checked(
            story, extraction, thesis, ThesisLinks.model_validate({"links": c["raw"]}),
            owner_confirmed=c.get("owner_confirmed", False),
        )  # fmt: skip
        got = [[x.kind, x.ref, x.status, x.claim] for x in links]
        if got != c["expect"]:
            failures.append(f"thesis {c['id']}: {got}, expected {c['expect']}")
    return failures


def _candidate(spec: dict[str, Any]) -> Candidate:
    items = spec["items"]
    story = _story(
        items, ticker=spec.get("ticker"), lifecycle=spec.get("lifecycle", "open"),
        severity=spec.get("severity", 2), cluster_id=spec["story_id"],
    )  # fmt: skip
    return Candidate(
        story,
        spec.get("exposure", 0.0),
        spec.get("novelty"),
        tuple(spec.get("claims", ())),
        spec.get("score", 1.0),
    )


def run_brief(cases: list[dict[str, Any]]) -> list[str]:
    """Ranking, the checks on a model-written line, the fallback to the plain headline, and the
    regional order of the sections."""
    failures = []
    for c in cases:
        kind = c["kind"]
        if kind == "rank":
            got: Any = [round(rank_score(**r["args"]), 5) for r in c["rows"]]
            want = [r["expect"] for r in c["rows"]]
        elif kind == "lines":
            chosen = [_candidate(x) for x in c["stories"]]
            written = {w["story_id"]: WrittenLine.model_validate(w) for w in c["written"]}
            got = [
                [line.story_id, line.written_by_model, line.text]
                for line in build_lines(chosen, written)
            ]
            want = c["expect"]
        else:
            lines = [
                BriefLine(
                    story_id=i, ticker=t, text=t or "x", item_ids=[i], severity=2,
                    lifecycle="open", score=1.0,
                )
                for i, t in enumerate(c["tickers"], start=1)
            ]  # fmt: skip
            got = [
                [s.title, [line.ticker for line in s.lines]] for s in brief_sections(c["of"], lines)
            ]
            want = c["expect"]
        if got != want:
            failures.append(f"brief {c['id']}: {got}, expected {want}")
    return failures


def run_all(
    golden: dict[str, Any] | None = None, model_golden: dict[str, Any] | None = None
) -> tuple[int, list[str]]:
    """The number of cases and the failures."""
    data = golden or load_golden()
    model = model_golden or load_golden(MODEL_GOLDEN_PATH)
    failures = (
        run_spans(data["spans"])
        + run_diffs(data["diffs"])
        + run_novelty(data["novelty"])
        + run_extraction(model["extraction"])
        + run_framing(model["framing"])
        + run_claims(model["claims"])
        + run_sentiment(model["sentiment"])
        + run_impact(model["impact"])
        + run_thesis(model["thesis"])
        + run_brief(model["brief"])
    )
    total = sum(len(data[k]) for k in ("spans", "diffs", "novelty")) + sum(
        len(model[k])
        for k in ("extraction", "framing", "claims", "sentiment", "impact", "thesis", "brief")
    )
    return total, failures


def main() -> None:
    total, failures = run_all()
    print(f"{total - len(failures)}/{total} golden cases pass")
    for failure in failures:
        print(f"  FAIL {failure}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
