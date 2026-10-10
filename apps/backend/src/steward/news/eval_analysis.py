"""Golden cases for the news techniques that run in code (span verification, thread claim diffs,
novelty) and for what the code keeps of the model techniques' output (extraction, sentiment).

    uv run python -m steward.news.eval_analysis

They need no model or database, so they run in CI and gate any change to those functions. The
model cases fix a model's answer and check the verification around it, not the model itself;
impact notes, framing, claim checks, thesis links and briefs have no golden set yet.
"""

import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from steward.news.analysis import ItemText, StoryMaterial, verify_span
from steward.news.extract import StoryExtraction, verified
from steward.news.novelty import classify_novelty
from steward.news.sentiment import RawSentiment, bounded
from steward.news.threads import claim_diff

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


def _story(items: list[dict[str, Any]]) -> StoryMaterial:
    return StoryMaterial(
        cluster_id=1,
        ticker=None,
        headline=items[0]["headline"],
        event_type=None,
        severity=1,
        lifecycle="open",
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
                evidence_kind=None,
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
        + run_sentiment(model["sentiment"])
    )
    total = sum(len(data[k]) for k in ("spans", "diffs", "novelty")) + sum(
        len(model[k]) for k in ("extraction", "sentiment")
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
