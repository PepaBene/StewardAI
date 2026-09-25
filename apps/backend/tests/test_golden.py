"""The earnings golden set in CI (Phase 2 AC: stored as fixtures with expected values and
runnable in CI in under 2 minutes).

CI has no API key, so it runs the oracle extractor: the labels themselves go through the
validator and the verdict logic. That proves every correct figure in the real releases
passes validation (no false rejections), and that every KPI gets a verdict with no
unhandled exception. Extraction accuracy against a live model is measured with
`uv run python -m steward.earnings.golden --live` (needs ANTHROPIC_API_KEY).
"""

import importlib.util
import time
from types import ModuleType

from steward.earnings.golden import (
    DEFAULT_DIR,
    OracleExtractor,
    default_kpis,
    evaluate,
    load_cases,
    summarise,
)


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "golden_check_cases", DEFAULT_DIR / "check_cases.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_golden_case_is_consistent_with_its_document() -> None:
    checker = _checker()
    case_dirs = sorted(p.parent for p in DEFAULT_DIR.glob("*/case.json"))
    assert len(case_dirs) >= 20
    failures = {d.name: checker.check_case(d) for d in case_dirs}
    assert {k: v for k, v in failures.items() if v} == {}


async def test_golden_set_passes_validation_and_every_kpi_gets_a_verdict() -> None:
    started = time.monotonic()
    cases = load_cases()
    report = summarise(await evaluate(cases, OracleExtractor(cases)))
    elapsed = time.monotonic() - started

    assert report["errors"] == {}
    problems = {
        cid: c["missing"] + c["wrong"] + c["rejected_labels"]
        for cid, c in report["per_case"].items()
        if c["accuracy"] < 1
    }
    assert problems == {}
    assert report["accuracy"] == 1.0

    kpi_ids = {k.id for k in default_kpis("USD")}
    for cid, case in report["per_case"].items():
        judged = {name.split()[0] for name in case["verdicts"]}
        assert judged == kpi_ids, cid
        # A single release never holds four quarters: TTM must be unknown, not guessed.
        ttm = [s for name, s in case["verdicts"].items() if name.startswith("revenue-ttm")]
        assert ttm and set(ttm) == {"unknown"}, cid
    assert elapsed < 120
