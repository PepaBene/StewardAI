"""The evals gate in CI (Defence layer 7; Phase 4 AC: a deliberately broken prompt fails CI).

    uv run python -m steward.verify.evals_gate                   # CI: check
    uv run python -m steward.verify.evals_gate --write-baseline  # after a passing live run

Three model-facing components each have a fingerprint: the source of the code that builds
the prompt and parses the answer, the default model, the settings and data the prompt is
built from (the extraction effort, the event type descriptions), and the evaluation set
(for earnings: each case's labels and its source document).

| Component      | Code                          | Set                                 | Target |
| -------------- | ----------------------------- | ----------------------------------- | ------ |
| earnings       | earnings/extract.py, schema.py | tests/golden/earnings              | 98 %   |
| classification | news/classify.py              | tests/news/news_classification.json | 85 %   |
| claims         | verify/claims.py              | tests/verify/injected_claims.json   | 100 %  |

`evals/baseline.json` records, per component, the fingerprint a live run passed at and its
score. The check:

- fingerprint unchanged: nothing the model sees changed; pass without an API call;
- changed, GOOGLE_API_KEY set: run that component's live eval; fail below the target or
  more than one point below the recorded score (a regression);
- changed, no key: fail, because a prompt, model or schema change must not merge unmeasured.
  Run `--write-baseline` with a key (it writes the baseline only if every changed component
  passes) and commit the file.

The evals run on Gemini (`EVAL_MODEL`, default `gemini-3.8-flash`, override with the
EVALS_MODEL environment variable), never on a Claude model: the app does not call Claude, and
the routing client refuses to. The model is part of each fingerprint, so changing it re-measures.

With no baseline file at all (a fresh repository), a run without a key warns and passes:
no eval runs, so the model-facing code is unverified until a run with a key records one.
"""

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from steward.config.jobs import EarningsCheckSettings
from steward.config.verification import ClaimVerifierSettings

SRC = Path(__file__).resolve().parents[1]
BACKEND = SRC.parents[1]
BASELINE = BACKEND / "evals" / "baseline.json"
REGRESSION_TOLERANCE = 0.01
KEY_NAME = "GOOGLE_API_KEY"


def _eval_model() -> str:
    """The model every live eval runs on: a Gemini model, whatever the app's job configs say."""
    model = os.environ.get("EVALS_MODEL") or "gemini-3.8-flash"
    if not model.startswith("gemini-"):
        raise SystemExit(f"EVALS_MODEL must be a gemini-* model, not {model!r}")
    return model


@dataclass(frozen=True)
class Component:
    name: str
    code: tuple[Path, ...]
    data: tuple[Path, ...]
    model: Callable[[], str]
    target: float
    # Settings or data the prompt is built from that live outside `code` (hashed as text).
    extra: Callable[[], str] = lambda: ""


# The gate scores a small, fixed sample of the golden set (one case per company with a short
# document, different layouts) to keep a live run to a few minutes and a few cents; `--full`
# scores all of it (about $2-3). Both modes use the same fingerprint rule: only the cases
# that are scored are hashed, so a baseline written in one mode is re-run in the other.
GATE_EARNINGS_CASES = ("aapl-fy2026-q1", "nvda-fy2027-q1", "sap-fy2026-q1")
FULL_EARNINGS = False


def _golden_files() -> tuple[Path, ...]:
    """Each scored golden case's labels and the source document it references."""
    files: list[Path] = []
    for case in sorted((BACKEND / "tests" / "golden" / "earnings").glob("*/case.json")):
        if not FULL_EARNINGS and case.parent.name not in GATE_EARNINGS_CASES:
            continue
        files.append(case)
        document = json.loads(case.read_text(encoding="utf-8")).get("document")
        if document:
            files.append(case.parent / document)
    return tuple(files)


def _event_descriptions() -> str:
    from steward.config.event_rules import CLASSIFIABLE_EVENT_TYPES, EVENT_DESCRIPTIONS

    return json.dumps({name: EVENT_DESCRIPTIONS[name] for name in CLASSIFIABLE_EVENT_TYPES})


COMPONENTS: tuple[Component, ...] = (
    Component(
        "earnings",
        (SRC / "earnings" / "extract.py", SRC / "earnings" / "schema.py"),
        (),  # the golden cases, listed at run time
        _eval_model,
        0.98,
        lambda: f"effort={EarningsCheckSettings().effort}",
    ),
    Component(
        "classification",
        (SRC / "news" / "classify.py",),
        (BACKEND / "tests" / "news" / "news_classification.json",),
        _eval_model,
        0.85,
        _event_descriptions,
    ),
    Component(
        "claims",
        (SRC / "verify" / "claims.py",),
        (BACKEND / "tests" / "verify" / "injected_claims.json",),
        _eval_model,
        1.0,
    ),
)


def fingerprint(component: Component) -> str:
    digest = hashlib.sha256()
    data = component.data or _golden_files()
    for path in (*component.code, *data):
        name = path.relative_to(BACKEND) if path.is_relative_to(BACKEND) else Path(path.name)
        digest.update(name.as_posix().encode())
        digest.update(b"\0")
        # Line endings differ between a Windows checkout (CRLF) and CI (LF): hash LF.
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    digest.update(component.model().encode())
    extra = component.extra()
    if extra:
        digest.update(b"\0")
        digest.update(extra.encode())
    return digest.hexdigest()


async def live_score(name: str) -> float:
    """Run one component's live eval; returns its score (0..1)."""
    from steward.llm.routing import create_routing_client
    from steward.settings import get_settings

    client, llm = create_routing_client(get_settings())
    if name == "earnings":
        from steward.earnings.extract import ClaudeExtractor
        from steward.earnings.golden import evaluate, load_cases, summarise

        settings = EarningsCheckSettings()
        cases = [c for c in load_cases() if FULL_EARNINGS or c.id in GATE_EARNINGS_CASES]
        extractor = ClaudeExtractor(client, _eval_model(), settings.effort)
        return float(summarise(await evaluate(cases, extractor))["accuracy"])
    if name == "classification":
        from steward.news.classify import ClaudeClassifier
        from steward.news.eval import evaluate as evaluate_news
        from steward.news.eval import load_set

        classifier = ClaudeClassifier(client, _eval_model(), llm=llm)
        return (await evaluate_news(classifier, load_set())).accuracy
    from steward.verify.claims import ClaudeClaimVerifier
    from steward.verify.eval import load_cases as load_claims
    from steward.verify.eval import score

    settings_c = ClaimVerifierSettings()
    result = await score(load_claims(), ClaudeClaimVerifier(client, _eval_model()), settings_c)
    # A verifier that flags faithful sentences is not passing, whatever it catches.
    return result.catch_rate if not result.faithful_flagged else 0.0


def load_baseline(path: Path = BASELINE) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


@dataclass
class GateResult:
    ok: bool
    lines: list[str]
    scores: dict[str, dict[str, Any]]


async def run_gate(
    baseline: dict[str, Any] | None,
    have_key: bool,
    scorer: Callable[[str], Any] = live_score,
    components: tuple[Component, ...] = COMPONENTS,
) -> GateResult:
    lines: list[str] = []
    scores: dict[str, dict[str, Any]] = {}
    ok = True
    recorded = (baseline or {}).get("components", {})
    for component in components:
        current = fingerprint(component)
        before = recorded.get(component.name)
        if before and before.get("fingerprint") == current:
            lines.append(f"{component.name}: unchanged since the recorded live run, not re-run")
            scores[component.name] = before
            continue
        if not have_key:
            if baseline is None:
                lines.append(
                    f"::warning::{component.name}: NO EVALS RAN. There is no baseline and no "
                    f"{KEY_NAME}, so the live eval was skipped and this component's "
                    f"prompt, model and schema are unverified. Set {KEY_NAME} and run "
                    "`--write-baseline` to measure them."
                )
                continue
            ok = False
            lines.append(
                f"{component.name}: prompt, model, schema or eval set changed since the last "
                f"live run, and no {KEY_NAME} is available to measure it. Run "
                "`uv run python -m steward.verify.evals_gate --write-baseline` with a key and "
                "commit evals/baseline.json."
            )
            continue
        value = float(await scorer(component.name))
        floor = component.target
        if before and before.get("score") is not None:
            floor = max(floor, float(before["score"]) - REGRESSION_TOLERANCE)
        passed = value >= floor
        ok = ok and passed
        lines.append(
            f"{component.name}: {value:.1%} (needs {floor:.1%}: target {component.target:.0%}"
            + (f", recorded {float(before['score']):.1%}" if before else "")
            + f") {'ok' if passed else 'FAIL'}"
        )
        scores[component.name] = {
            "fingerprint": current,
            "score": round(value, 4),
            "model": component.model(),
            "passed": passed,
        }
    return GateResult(ok, lines, scores)


def write_baseline(result: GateResult, path: Path = BASELINE) -> None:
    data = {
        "description": "Live eval results per model-facing component; see "
        "steward/verify/evals_gate.py. Written by --write-baseline, only when every "
        "changed component passed.",
        "recorded_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "components": {
            name: {k: v for k, v in entry.items() if k != "passed"}
            for name, entry in sorted(result.scores.items())
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument(
        "--full", action="store_true", help="score every golden earnings case (about $2-3)"
    )
    args = parser.parse_args(argv)
    global FULL_EARNINGS
    FULL_EARNINGS = args.full
    have_key = bool(os.environ.get(KEY_NAME))
    if args.write_baseline and not have_key:
        print(f"--write-baseline needs {KEY_NAME}: it records live results")
        return 1
    result = asyncio.run(run_gate(load_baseline(), have_key))
    print("\n".join(result.lines))
    if not result.ok:
        return 1
    if args.write_baseline:
        write_baseline(result)
        print(f"wrote {BASELINE.relative_to(BACKEND)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
