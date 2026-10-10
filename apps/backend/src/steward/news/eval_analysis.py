"""Golden cases for the news techniques that run in code: span verification, thread claim
diffs and novelty.

    uv run python -m steward.news.eval_analysis

They need no model or database, so they run in CI and gate any change to those functions. The
model techniques (extraction, impact notes, framing, claim checks, thesis links, briefs) have
no golden set yet; see the plan's N8 gaps.
"""

import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from steward.news.analysis import verify_span
from steward.news.novelty import classify_novelty
from steward.news.threads import claim_diff

GOLDEN_PATH = Path(__file__).resolve().parents[3] / "tests" / "news" / "analysis_golden.json"
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


def run_all(golden: dict[str, Any] | None = None) -> tuple[int, list[str]]:
    """The number of cases and the failures."""
    data = golden or load_golden()
    failures = run_spans(data["spans"]) + run_diffs(data["diffs"]) + run_novelty(data["novelty"])
    return len(data["spans"]) + len(data["diffs"]) + len(data["novelty"]), failures


def main() -> None:
    total, failures = run_all()
    print(f"{total - len(failures)}/{total} golden cases pass")
    for failure in failures:
        print(f"  FAIL {failure}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
