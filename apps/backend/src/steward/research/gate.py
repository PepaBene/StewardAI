"""The research regression gate (Phase 5): a change to what the research pipeline shows a
model, may carry out, or rates as grounded must pass a fixed suite before it merges.

    uv run python -m steward.research.gate                   # CI: check
    uv run python -m steward.research.gate --write-baseline  # after reviewing a change

Two parts:

- the **suite** (`evals/research_regression.json`): injection text that must be caught and
  ordinary prose that must not, outbound links that must be removed, and claims whose type,
  confidence and checks are fixed. It needs no model and no database, so it always runs and
  every score must be 1.0. Add a case for each real failure.
- the **fingerprint** of everything a model sees or a template fixes: the writer and
  verifier prompts, the section tasks and plan questions, the source-order text, the tool
  catalog, the default profiles and templates, the benchmark cases, and the code and data of
  the suite. `evals/research_gate.json` records the fingerprint the suite last passed at. A
  change to any of it fails the gate until someone runs `--write-baseline`, which records a
  new fingerprint only if the suite passes. The live benchmark (model answers scored by a
  reviewer) is a separate step and is not part of this gate.
"""

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from steward.chat import catalog
from steward.config.research import ResearchProfilesConfig
from steward.research import ledger, plan, source_policy, untrusted, writer
from steward.research.sources import CitedSentence, Registry
from steward.verify.claims import RESEARCH_SYSTEM as VERIFIER_SYSTEM

SRC = Path(__file__).resolve().parents[1]
BACKEND = SRC.parents[1]
SUITE = BACKEND / "evals" / "research_regression.json"
CASES = BACKEND / "evals" / "research_cases.json"
BASELINE = BACKEND / "evals" / "research_gate.json"
TARGET = 1.0
NOW = dt.datetime(2026, 10, 11, 12, 0, tzinfo=dt.UTC)


def fingerprint() -> str:
    """A digest of every text, table and default the research pipeline's behaviour rests on."""
    parts: list[str] = [
        writer.SYSTEM,
        VERIFIER_SYSTEM,
        json.dumps(writer.SECTION_TITLES, sort_keys=True),
        json.dumps(writer.SECTION_TASKS, sort_keys=True),
        json.dumps(plan.SECTION_PLANS, sort_keys=True, default=str),
        json.dumps(plan.MODE_QUESTIONS, sort_keys=True, default=str),
        source_policy.PRIORITY_TEXT,
        json.dumps(
            {
                name: [catalog.TOOLS[name].service, catalog.TOOLS[name].bounds]
                for name in sorted(catalog.TOOLS)
            }
        ),
        ResearchProfilesConfig().model_dump_json(),
    ]
    digest = hashlib.sha256("\0".join(parts).encode())
    for path in (
        CASES,
        SUITE,
        Path(ledger.__file__),
        Path(untrusted.__file__),
        Path(source_policy.__file__),
    ):
        digest.update(path.name.encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def score_injection(cases: list[dict[str, Any]]) -> tuple[float, list[str]]:
    failed = [c["text"] for c in cases if untrusted.instruction_like(c["text"]) != c["injected"]]
    return 1 - len(failed) / len(cases), failed


def score_outbound(cases: list[dict[str, Any]]) -> tuple[float, list[str]]:
    failed: list[str] = []
    for c in cases:
        text, removed = untrusted.strip_outbound(c["text"], set(c["allowed"]))
        if removed != c["removed"] or any(a in text for a in c["absent"]):
            failed.append(c["text"])
    return 1 - len(failed) / len(cases), failed


def score_ledger(cases: list[dict[str, Any]]) -> tuple[float, list[str]]:
    failed: list[str] = []
    for c in cases:
        reg = Registry()
        for s in c["sources"]:
            reg.add(**s)
        untrusted.flag_sources(reg.sources)
        sentence = CitedSentence(
            text=c["text"],
            source_ids=c["source_ids"],
            written_by=c["written_by"],
            check=c["check"],
        )
        section = ledger.SectionInput(
            id=c["section"], written_by=c["written_by"], sentences=[sentence]
        )
        got = ledger.build_ledger([section], reg, [], NOW)
        claim = got.claims[0]
        expect = c["expect"]
        codes = sorted({k.code for k in got.checks})
        if (claim.type, claim.confidence, codes) != (
            expect["type"],
            expect["confidence"],
            sorted(expect["checks"]),
        ):
            failed.append(f"{c['id']}: got {claim.type}, {claim.confidence}, {codes}")
    return 1 - len(failed) / len(cases), failed


def run_suite() -> dict[str, tuple[float, list[str]]]:
    data = json.loads(SUITE.read_text(encoding="utf-8"))
    return {
        "injection": score_injection(data["injection"]),
        "outbound": score_outbound(data["outbound"]),
        "ledger": score_ledger(data["ledger"]),
    }


def check(baseline: dict[str, Any] | None) -> tuple[bool, list[str]]:
    lines: list[str] = []
    ok = True
    for name, (score, failed) in run_suite().items():
        passed = score >= TARGET
        ok &= passed
        lines.append(f"{'ok  ' if passed else 'FAIL'} {name}: {score:.0%}")
        lines += [f"       - {f}" for f in failed]
    now = fingerprint()
    if baseline is None:
        lines.append("warn no baseline recorded: run with --write-baseline")
    elif baseline.get("fingerprint") != now:
        ok = False
        lines.append(
            "FAIL a prompt, section task, plan question, tool, default template or suite file "
            "changed since the baseline. Review it, then run with --write-baseline."
        )
    else:
        lines.append("ok   fingerprint matches the baseline")
    return ok, lines


def load_baseline(path: Path = BASELINE) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args(argv)
    # A baseline being rewritten is the point of --write-baseline: judge the suite alone.
    ok, lines = check(None if args.write_baseline else load_baseline())
    if args.write_baseline:
        ok = not any(line.startswith("FAIL") for line in lines)
    print("\n".join(lines))
    if not ok:
        return 1
    if args.write_baseline:
        BASELINE.write_text(
            json.dumps(
                {
                    "description": "Fingerprint the research regression suite last passed at; "
                    "see steward/research/gate.py.",
                    "recorded_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                    "fingerprint": fingerprint(),
                    "suite": {k: v[0] for k, v in run_suite().items()},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"wrote {BASELINE.relative_to(BACKEND)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
