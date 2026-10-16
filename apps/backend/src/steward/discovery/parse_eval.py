# ruff: noqa: E501 - report text stays on one line each
"""Evaluation of the plain-English screen parser (insight plan I10.4).

Scores a parser over `evals/screen_parse_cases.json` for exact filter match, unit correctness
and refusals, and records a pass in `evals/screen_parse_gate.json` with the metric registry's
hash; a changed registry makes the recorded pass stale, which the parse endpoint reports.

Run against the configured model (costs a few cents):
    uv run python -m steward.discovery.parse_eval --record
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from steward.discovery.metrics import STOCK_CATALOGUE
from steward.discovery.parse import ScreenDraft, registry_hash

EVALS = Path(__file__).resolve().parents[3] / "evals"
CASES = EVALS / "screen_parse_cases.json"
GATE = EVALS / "screen_parse_gate.json"
TOLERANCE = Decimal("0.0001")

Parser = Callable[[str, list[str], list[str]], Awaitable[ScreenDraft]]


def load_cases(path: Path = CASES) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _key(f: tuple[str, str, Any]) -> tuple[str, str]:
    return f[0], f[1]


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, list) or isinstance(b, list):
        return sorted(map(str, a)) == sorted(map(str, b))
    return abs(Decimal(str(a)) - Decimal(str(b))) <= TOLERANCE * max(
        Decimal(1), abs(Decimal(str(b)))
    )


@dataclass
class CaseResult:
    id: str
    exact: bool
    units_ok: bool
    refusal_ok: bool
    problems: list[str] = field(default_factory=list)


def score_case(case: dict[str, Any], draft: ScreenDraft) -> CaseResult:
    problems: list[str] = []
    refused = bool(case.get("refused"))
    refusal_ok = draft.refused == refused
    if not refusal_ok:
        problems.append("should be refused" if refused else "refused a supported request")
    want = {_key(tuple(f)): f[2] for f in case.get("filters", [])}
    got = {(f.metric_id, f.op): f.value for f in draft.filters}
    units_ok = True
    for k, v in want.items():
        if k not in got:
            problems.append(f"missing {k[0]} {k[1]} {v}")
        elif not _same(got[k], v):
            problems.append(f"{k[0]} {k[1]} is {got[k]}, expected {v}")
            m = STOCK_CATALOGUE.get(k[0])
            # A fraction off by exactly 100x is a unit error, not a threshold choice.
            if m is not None and m.unit == "fraction" and not isinstance(got[k], list):
                ratio = (
                    Decimal(str(got[k])) / Decimal(str(v)) if Decimal(str(v)) != 0 else Decimal(0)
                )
                if abs(ratio - 100) < 1 or abs(ratio - Decimal("0.01")) < Decimal("0.001"):
                    units_ok = False
    for k in got.keys() - want.keys():
        problems.append(f"unexpected {k[0]} {k[1]} {got[k]}")
    if sorted(case.get("markets", [])) != sorted(draft.markets):
        problems.append(f"markets {draft.markets}, expected {case.get('markets', [])}")
    if sorted(case.get("industries", [])) != sorted(draft.industries):
        problems.append(f"industries {draft.industries}, expected {case.get('industries', [])}")
    if "sort" in case:
        want_sort = case["sort"]
        if draft.sort is None or [draft.sort.metric_id, draft.sort.descending] != want_sort:
            problems.append(f"sort {draft.sort}, expected {want_sort}")
    for phrase in case.get("unmatched", []):
        if not any(phrase.lower() in u.lower() for u in draft.unmatched_phrases):
            problems.append(f"'{phrase}' not reported as unmatched")
    exact = not problems if not refused else refusal_ok and not draft.filters
    return CaseResult(case["id"], exact, units_ok, refusal_ok, problems)


@dataclass
class Report:
    exact: float
    units: float
    refusals: float
    passed: bool
    registry_hash: str
    cases: list[CaseResult]


async def evaluate(parser: Parser, data: dict[str, Any] | None = None) -> Report:
    data = data or load_cases()
    results = []
    for case in data["cases"]:
        draft = await parser(case["text"], data.get("sectors", []), data.get("countries", []))
        results.append(score_case(case, draft))
    n = len(results)
    refusal_cases = [r for r, c in zip(results, data["cases"], strict=True) if c.get("refused")]
    exact = sum(r.exact for r in results) / n
    units = sum(r.units_ok for r in results) / n
    refusals = (
        sum(r.refusal_ok for r in refusal_cases) / len(refusal_cases) if refusal_cases else 1.0
    )
    t = data["threshold"]
    passed = exact >= t["exact"] and units >= t["units"] and refusals >= t["refusals"]
    return Report(exact, units, refusals, passed, registry_hash(), results)


def gate_status(path: Path = GATE) -> str:
    """One line for the parse endpoint: whether the evaluation passed for this registry."""
    try:
        gate = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "The parser has not been evaluated yet."
    if gate.get("registry_hash") != registry_hash():
        return f"Evaluated on {gate.get('recorded_at', '?')[:10]} for an earlier metric registry; re-run the evaluation."
    return f"Evaluation passed on {gate.get('recorded_at', '?')[:10]} ({gate.get('exact', 0):.0%} exact)."


def record(report: Report, path: Path = GATE) -> None:
    path.write_text(
        json.dumps(
            {
                "description": "Last passing run of the screen parse evaluation; see steward/discovery/parse_eval.py.",
                "recorded_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                "registry_hash": report.registry_hash,
                "exact": round(report.exact, 4),
                "units": round(report.units, 4),
                "refusals": round(report.refusals, 4),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


async def _model_parser() -> Parser:
    from steward.config.jobs import JobsConfig
    from steward.discovery.parse import SYSTEM, ModelDraft, prompt, validate_draft
    from steward.llm.client import create_llm
    from steward.llm.types import Message, Request
    from steward.settings import get_settings

    llm = create_llm(get_settings())
    model = JobsConfig().chat.model

    async def parse(text: str, sectors: list[str], countries: list[str]) -> ScreenDraft:
        done = await llm.complete(
            Request(
                model=model,
                system=SYSTEM,
                messages=(Message(role="user", text=prompt(text, sectors, countries)),),
                schema=ModelDraft,
                max_tokens=1500,
            )
        )
        if not isinstance(done.parsed, ModelDraft):
            return ScreenDraft(unmatched_phrases=["(no usable answer)"])
        return validate_draft(done.parsed, sectors)

    return parse


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--record", action="store_true", help="Write the gate file when the run passes."
    )
    args = ap.parse_args()
    report = asyncio.run(_run())
    for r in report.cases:
        if r.problems:
            print(f"{r.id}: {'; '.join(r.problems)}")
    print(
        f"exact {report.exact:.0%}, units {report.units:.0%}, refusals {report.refusals:.0%}: {'PASS' if report.passed else 'FAIL'}"
    )
    if args.record and report.passed:
        record(report)
    raise SystemExit(0 if report.passed else 1)


async def _run() -> Report:
    return await evaluate(await _model_parser())


if __name__ == "__main__":
    main()
