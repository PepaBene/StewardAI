"""The live benchmark runner: replays the labelled cases through the chat assistant path
against a model, stores each run as JSON and compares runs.

    python -m steward.chat.benchmark_runner --model gpt-6.1-sol --out evals/runs
    python -m steward.chat.benchmark_runner compare RUN_A RUN_B
    python -m steward.chat.benchmark_runner review RUN CASE --analysis 2 --citation 1
    python -m steward.chat.benchmark_runner add-case "question ..." --group holding

The model client is injected (`ChatAssistant`, normally `LLMChatAssistant` over the routing
layer's `create_llm`), and so is the tool factory, so tests run the same path with fakes and
no network. Code scores what it can see (retrieval from the trace, citation presence, figures
no source contains, an empty or failed render); the strategy stage and the quality of the
others are hand-scored in a review file kept next to the run and merged into its summary.
"""

import argparse
import asyncio
import datetime as dt
import json
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from steward.chat import benchmark, catalog
from steward.chat.assistant import (
    ChatAssistant,
    ChatSentence,
    answer_shape,
    finish_answer,
    mark_unsupported_figures,
)
from steward.research.sources import Registry

EVALS_DIR = Path(__file__).resolve().parents[3] / "evals"
RUNS_DIR = Path(os.environ.get("STEWARD_BENCH_RUNS_DIR", EVALS_DIR / "runs"))
EXTRA_CASES_PATH = EVALS_DIR / "cases_extra.json"
STAGES = ("retrieval", "analysis", "citation", "strategy", "rendering")
MANUAL_STAGES = ("analysis", "citation", "strategy", "rendering")
StageResult = Literal["pass", "fail", "unreviewed"]


class ToolsLike(Protocol):
    """What the runner needs of `ChatTools`: calls, a trace and the source registry."""

    registry: Registry
    trace: list[dict[str, Any]]

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]: ...


ToolsFactory = Callable[[], ToolsLike]


class CaseResult(BaseModel):
    case: str
    category: str
    question: str
    tools_called: list[str]
    datasets_touched: list[str]
    score: benchmark.Score
    stages: dict[str, StageResult]
    failure_stage: benchmark.FailureStage | None
    passed: bool
    sentences: int = 0
    cited_sentences: int = 0
    unsupported_sentences: int = 0
    shape: str | None = None
    note: str | None = None
    answer: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    seconds: float = 0.0
    error: str | None = None


class RunRecord(BaseModel):
    run_id: str
    created_at: str
    git_sha: str | None
    model: str
    cases: list[CaseResult]
    aggregate: dict[str, Any] = Field(default_factory=dict)


class CaseReview(BaseModel):
    """Hand scores per failure stage: 0 broken, 1 partly right, 2 right."""

    analysis: int | None = Field(default=None, ge=0, le=2)
    citation: int | None = Field(default=None, ge=0, le=2)
    strategy: int | None = Field(default=None, ge=0, le=2)
    rendering: int | None = Field(default=None, ge=0, le=2)
    notes: str = ""


class ReviewRecord(BaseModel):
    run_id: str
    cases: dict[str, CaseReview] = Field(default_factory=dict)


# ------------------------------------------------------------------ running


def git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).parent,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def datasets_touched(tools_called: Sequence[str]) -> list[str]:
    seen: dict[str, None] = {}
    for tool in tools_called:
        info = catalog.TOOLS.get(tool)
        for d in info.datasets if info else ():
            seen[d] = None
    return list(seen)


def _stages(
    score: benchmark.Score, sentences: list[ChatSentence], shape: str | None, answered: bool
) -> dict[str, StageResult]:
    cited = sum(1 for s in sentences if not s.no_source)
    return {
        "retrieval": "fail" if score.failure == "retrieval" else "pass",
        "analysis": "fail" if any(s.unsupported for s in sentences) else "pass",
        "citation": "pass" if cited else "fail",
        "strategy": "unreviewed",
        "rendering": "fail" if not answered or shape is None else "pass",
    }


def _first_failure(stages: dict[str, StageResult]) -> benchmark.FailureStage | None:
    for stage in STAGES:
        if stages.get(stage) == "fail":
            return stage  # type: ignore[return-value]
    return None


async def run_case(
    case: benchmark.Case,
    assistant: ChatAssistant,
    tools_factory: ToolsFactory,
    *,
    today: dt.date,
    usd_per_mtok: tuple[float, float] | None = None,
    tier_of: Callable[[str], int | None] = lambda _host: None,
) -> CaseResult:
    """One question through the assistant, the tools and the same answer checks the chat job
    applies (citations kept only if checkable, figures no source contains flagged)."""
    tools = tools_factory()
    started = time.monotonic()
    error: str | None = None
    sentences: list[ChatSentence] = []
    note: str | None = None
    shape: str | None = None
    in_tok = out_tok = 0
    try:
        result = await assistant.answer([], case.question, tools, today)  # type: ignore[arg-type]
        in_tok = result.usage.input_tokens + result.usage.cache_read_tokens
        out_tok = result.usage.output_tokens
        note = result.note
        if result.draft is not None:
            sentences = finish_answer(result.draft, tools.registry, result.titles, tier_of)
            await mark_unsupported_figures(sentences, tools.registry)
            shape = answer_shape(sentences, result.draft.insufficient_evidence)
    except Exception as exc:  # a failing case must not end the run
        error = f"{type(exc).__name__}: {exc}"[:300]
    seconds = time.monotonic() - started
    trace = list(tools.trace)
    score = benchmark.score_trace(case, trace)
    stages = _stages(score, sentences, shape, answered=bool(sentences) and error is None)
    called = [str(t.get("tool")) for t in trace]
    cost = (
        round((in_tok * usd_per_mtok[0] + out_tok * usd_per_mtok[1]) / 1_000_000, 6)
        if usd_per_mtok
        else None
    )
    failure = _first_failure(stages)
    return CaseResult(
        case=case.id,
        category=case.category,
        question=case.question,
        tools_called=called,
        datasets_touched=datasets_touched(called),
        score=score,
        stages=stages,
        failure_stage=failure,
        passed=failure is None,
        sentences=len(sentences),
        cited_sentences=sum(1 for s in sentences if not s.no_source),
        unsupported_sentences=sum(1 for s in sentences if s.unsupported),
        shape=shape,
        note=note,
        answer=" ".join(s.text for s in sentences)[:4000],
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=cost,
        seconds=round(seconds, 3),
        error=error,
    )


def aggregate(results: Sequence[CaseResult]) -> dict[str, Any]:
    n = len(results)
    sentences = sum(r.sentences for r in results)
    costs = [r.cost_usd for r in results if r.cost_usd is not None]
    failures: dict[str, int] = {}
    for r in results:
        if r.failure_stage:
            failures[r.failure_stage] = failures.get(r.failure_stage, 0) + 1
    return {
        "cases": n,
        "passed": sum(1 for r in results if r.passed),
        "tool_group_recall": round(sum(r.score.tool_recall for r in results) / n, 4) if n else None,
        "unsupported_figure_rate": round(
            sum(r.unsupported_sentences for r in results) / sentences, 4
        )
        if sentences
        else None,
        "citation_presence": round(sum(r.cited_sentences for r in results) / sentences, 4)
        if sentences
        else None,
        "cost_usd": round(sum(costs), 6) if costs else None,
        "seconds": round(sum(r.seconds for r in results), 3),
        "input_tokens": sum(r.input_tokens for r in results),
        "output_tokens": sum(r.output_tokens for r in results),
        "failures_by_stage": failures,
        "errors": sum(1 for r in results if r.error),
    }


async def run_cases(
    cases: Sequence[benchmark.Case],
    assistant: ChatAssistant,
    tools_factory: ToolsFactory,
    *,
    model: str,
    today: dt.date | None = None,
    usd_per_mtok: tuple[float, float] | None = None,
    run_id: str | None = None,
    now: dt.datetime | None = None,
) -> RunRecord:
    results = [
        await run_case(
            c, assistant, tools_factory, today=today or dt.date.today(), usd_per_mtok=usd_per_mtok
        )
        for c in cases
    ]
    moment = now or dt.datetime.now(dt.UTC)
    return RunRecord(
        run_id=run_id or f"{moment:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}",
        created_at=moment.isoformat(timespec="seconds"),
        git_sha=git_sha(),
        model=model,
        cases=results,
        aggregate=aggregate(results),
    )


# ------------------------------------------------------------------ storage


def run_path(directory: Path, run_id: str) -> Path:
    return directory / f"{run_id}.json"


def review_path(directory: Path, run_id: str) -> Path:
    return directory / f"{run_id}.review.json"


def save_run(run: RunRecord, directory: Path = RUNS_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = run_path(directory, run.run_id)
    path.write_text(run.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_run(path_or_id: str | Path, directory: Path = RUNS_DIR) -> RunRecord:
    path = Path(path_or_id)
    if not path.is_file():
        path = run_path(directory, str(path_or_id))
    return RunRecord.model_validate_json(path.read_text(encoding="utf-8"))


def load_review(run_id: str, directory: Path = RUNS_DIR) -> ReviewRecord:
    path = review_path(directory, run_id)
    if not path.is_file():
        return ReviewRecord(run_id=run_id)
    return ReviewRecord.model_validate_json(path.read_text(encoding="utf-8"))


def save_review(review: ReviewRecord, directory: Path = RUNS_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = review_path(directory, review.run_id)
    path.write_text(review.model_dump_json(indent=2), encoding="utf-8")
    return path


def merge_review(run: RunRecord, review: ReviewRecord) -> RunRecord:
    """The run with the hand scores summarised under `aggregate["manual"]`: per stage the mean
    score (0-2) and how many cases were scored, plus the cases scored 0 at any stage. Reviews
    of cases not in the run are ignored."""
    ids = {r.case for r in run.cases}
    manual: dict[str, Any] = {"reviewed_cases": 0, "stages": {}, "broken": []}
    scored = {cid: rv for cid, rv in review.cases.items() if cid in ids}
    manual["reviewed_cases"] = len(scored)
    for stage in MANUAL_STAGES:
        values = [getattr(rv, stage) for rv in scored.values() if getattr(rv, stage) is not None]
        manual["stages"][stage] = {
            "mean": round(sum(values) / len(values), 3) if values else None,
            "scored": len(values),
        }
    manual["broken"] = sorted(
        {
            f"{cid}:{stage}"
            for cid, rv in scored.items()
            for stage in MANUAL_STAGES
            if getattr(rv, stage) == 0
        }
    )
    return run.model_copy(update={"aggregate": {**run.aggregate, "manual": manual}})


def record_review(
    run_id: str, case_id: str, scores: dict[str, int | None], notes: str, directory: Path
) -> RunRecord:
    """Adds or updates one case's hand scores, stores the review file and the merged run."""
    run = load_run(run_id, directory)
    if case_id not in {r.case for r in run.cases}:
        raise ValueError(f"case {case_id} is not in run {run_id}")
    review = load_review(run_id, directory)
    current = review.cases.get(case_id, CaseReview())
    update = {k: v for k, v in scores.items() if v is not None and k in MANUAL_STAGES}
    review.cases[case_id] = CaseReview.model_validate(
        {**current.model_dump(), **update, "notes": notes or current.notes}
    )
    save_review(review, directory)
    merged = merge_review(run, review)
    save_run(merged, directory)
    return merged


# ------------------------------------------------------------------ comparing


_HIGHER_IS_BETTER = {"tool_group_recall": True, "citation_presence": True, "passed": True}
_LOWER_IS_BETTER = ("unsupported_figure_rate", "cost_usd", "seconds", "errors")


def _delta(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else round(b - a, 6)


def compare_runs(a: RunRecord, b: RunRecord) -> dict[str, Any]:
    """Per-case and aggregate deltas from run `a` to run `b`, and the regressions: a case that
    passed and now fails, a lower tool recall, a new failure stage, and an aggregate that got
    worse (recall or citation down; unsupported rate, cost, time or errors up)."""
    ca = {r.case: r for r in a.cases}
    cb = {r.case: r for r in b.cases}
    per_case: list[dict[str, Any]] = []
    regressions: list[str] = []
    for cid in sorted(ca.keys() & cb.keys()):
        x, y = ca[cid], cb[cid]
        row = {
            "case": cid,
            "recall": _delta(x.score.tool_recall, y.score.tool_recall),
            "passed": [x.passed, y.passed],
            "failure_stage": [x.failure_stage, y.failure_stage],
            "cost_usd": _delta(x.cost_usd, y.cost_usd),
            "seconds": _delta(x.seconds, y.seconds),
            "tokens": (y.input_tokens + y.output_tokens) - (x.input_tokens + x.output_tokens),
        }
        per_case.append(row)
        if x.passed and not y.passed:
            regressions.append(f"{cid}: passed before, now fails at {y.failure_stage}")
        elif y.score.tool_recall < x.score.tool_recall:
            regressions.append(f"{cid}: tool recall {x.score.tool_recall} -> {y.score.tool_recall}")
        elif y.failure_stage and y.failure_stage != x.failure_stage and not x.passed:
            regressions.append(f"{cid}: failure moved {x.failure_stage} -> {y.failure_stage}")
    agg: dict[str, Any] = {}
    for key in (*_HIGHER_IS_BETTER, *_LOWER_IS_BETTER, "input_tokens", "output_tokens"):
        va, vb = a.aggregate.get(key), b.aggregate.get(key)
        d = _delta(va, vb)
        agg[key] = {"a": va, "b": vb, "delta": d}
        if d is None or d == 0:
            continue
        if key in _HIGHER_IS_BETTER and d < 0:
            regressions.append(f"aggregate {key}: {va} -> {vb}")
        if key in _LOWER_IS_BETTER and d > 0 and key in ("unsupported_figure_rate", "errors"):
            regressions.append(f"aggregate {key}: {va} -> {vb}")
    return {
        "a": a.run_id,
        "b": b.run_id,
        "cases_only_in_a": sorted(ca.keys() - cb.keys()),
        "cases_only_in_b": sorted(cb.keys() - ca.keys()),
        "per_case": per_case,
        "aggregate": agg,
        "regressions": regressions,
    }


# ------------------------------------------------------------------ cases from real failures


def load_extra(path: Path = EXTRA_CASES_PATH) -> list[benchmark.Case]:
    if not path.is_file():
        return []
    return benchmark.load_cases(path)


def load_all_cases(extra: Path | None = EXTRA_CASES_PATH) -> list[benchmark.Case]:
    """The built-in cases followed by the user's, with duplicate ids refused."""
    cases = benchmark.load_cases() + (load_extra(extra) if extra else [])
    seen: set[str] = set()
    for c in cases:
        if c.id in seen:
            raise ValueError(f"duplicate case id {c.id}")
        seen.add(c.id)
    return cases


def append_case(
    question: str,
    tool_groups: Sequence[Sequence[str]],
    traps: Sequence[str],
    *,
    path: Path = EXTRA_CASES_PATH,
    category: str = "general_research",
    datasets: Sequence[str] | None = None,
    case_id: str | None = None,
    must_admit: Sequence[str] = (),
) -> benchmark.Case:
    """Files a real failure as a new case: the question, the tool groups a good run must reach
    and the claims it must not make. Datasets default to those of the named tools. Raises
    ValueError for an unknown tool, a duplicate id or an invalid case; nothing is written then."""
    groups = [list(g) for g in tool_groups]
    unknown = sorted({t for g in groups for t in g} - catalog.TOOLS.keys() - {"propose_job"})
    if unknown:
        raise ValueError(f"unknown tools {unknown}")
    derived = datasets or sorted({d for g in groups for t in g for d in catalog.TOOLS[t].datasets})
    existing = json.loads(path.read_text(encoding="utf-8"))["cases"] if path.is_file() else []
    taken = {c["id"] for c in existing} | {c.id for c in benchmark.load_cases()}
    new_id = case_id or f"real-{len(existing) + 1:02d}"
    if new_id in taken:
        raise ValueError(f"duplicate case id {new_id}")
    case = benchmark.Case(
        id=new_id,
        category=category,
        question=question.strip(),
        tool_groups=groups,
        datasets=list(derived),
        must_admit=list(must_admit),
        unsupported_traps=list(traps),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"cases": [*existing, case.model_dump(mode="json")]}, indent=2) + "\n",
        encoding="utf-8",
    )
    return case


# ------------------------------------------------------------------ CLI


def _select(
    cases: list[benchmark.Case], only: Sequence[str], category: str | None, limit: int | None
) -> list[benchmark.Case]:
    if only:
        cases = [c for c in cases if c.id in set(only)]
    if category:
        cases = [c for c in cases if c.category == category]
    return cases[:limit] if limit else cases


async def _live_run(args: argparse.Namespace) -> RunRecord:
    """Builds the real client and tools (database, routing layer) and runs the cases."""
    from steward.ai import AINotConfigured
    from steward.chat.assistant import SYSTEM, LLMChatAssistant
    from steward.chat.tools import ChatTools
    from steward.config.jobs import JobsConfig
    from steward.config.news_sources import NewsSourcesConfig
    from steward.config.store import load_active
    from steward.db import create_pool
    from steward.llm.client import create_llm
    from steward.settings import get_settings

    settings = get_settings()
    pool = await create_pool(settings)
    try:
        jobs = await load_active(pool, "jobs", JobsConfig)
        sources = await load_active(pool, "news_sources", NewsSourcesConfig)
        chat = (jobs.body if jobs else JobsConfig()).chat.model_copy(update={"model": args.model})
        registry = sources.body if sources else NewsSourcesConfig()
        domains = tuple(d.domain for d in registry.domain_tiers if d.tier in chat.source_tiers)[:60]
        try:
            llm = create_llm(settings)
        except AINotConfigured as exc:
            raise SystemExit(f"no model key is configured: {exc}") from exc
        assistant = LLMChatAssistant(llm, chat, domains if args.web else (), SYSTEM)
        owner = args.owner or await pool.fetchval(
            "select created_by::text from public.chat_conversation order by id limit 1"
        )
        cases = _select(
            load_all_cases(Path(args.cases) if args.cases else EXTRA_CASES_PATH),
            args.only,
            args.category,
            args.limit,
        )
        price = (args.usd_in, args.usd_out) if args.usd_in or args.usd_out else None
        return await run_cases(
            cases,
            assistant,
            lambda: ChatTools(pool, Registry(), None, None, owner_id=owner),
            model=args.model,
            usd_per_mtok=price,
        )
    finally:
        await pool.close()


def _print_compare(diff: dict[str, Any]) -> None:
    print(f"compare {diff['a']} -> {diff['b']}")
    for key, v in diff["aggregate"].items():
        print(f"  {key}: {v['a']} -> {v['b']} ({v['delta']})")
    print(f"regressions: {len(diff['regressions'])}")
    for line in diff["regressions"]:
        print(f"  - {line}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="benchmark_runner")
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="replay the cases against a model")
    run.add_argument("--model", required=True)
    run.add_argument("--cases", help="extra cases file (default evals/cases_extra.json)")
    run.add_argument("--out", default=str(RUNS_DIR), help="directory for the run file")
    run.add_argument("--only", nargs="*", default=[], help="case ids")
    run.add_argument("--category")
    run.add_argument("--limit", type=int)
    run.add_argument("--owner", help="owner uuid for owner-scoped tools")
    run.add_argument("--web", action="store_true", help="allow provider web search")
    run.add_argument("--usd-in", type=float, default=0.0, help="USD per million input tokens")
    run.add_argument("--usd-out", type=float, default=0.0, help="USD per million output tokens")
    cmp_ = sub.add_parser("compare", help="deltas and regressions between two runs")
    cmp_.add_argument("a")
    cmp_.add_argument("b")
    cmp_.add_argument("--out", default=str(RUNS_DIR))
    rev = sub.add_parser("review", help="store hand scores (0-2) for one case of a run")
    rev.add_argument("run")
    rev.add_argument("case")
    for stage in MANUAL_STAGES:
        rev.add_argument(f"--{stage}", type=int, choices=(0, 1, 2))
    rev.add_argument("--notes", default="")
    rev.add_argument("--out", default=str(RUNS_DIR))
    add = sub.add_parser("add-case", help="file a real failure as a new case")
    add.add_argument("question")
    add.add_argument("--group", action="append", required=True, help="tools, comma separated")
    add.add_argument("--trap", action="append", required=True)
    add.add_argument("--category", default="general_research")
    add.add_argument("--id")
    add.add_argument("--file", default=str(EXTRA_CASES_PATH))
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args_in = list(sys.argv[1:] if argv is None else argv)
    if args_in and args_in[0] not in ("run", "compare", "review", "add-case", "-h", "--help"):
        args_in = ["run", *args_in]
    args = build_parser().parse_args(args_in)
    if args.command == "run":
        run = asyncio.run(_live_run(args))
        path = save_run(run, Path(args.out))
        print(json.dumps({"saved": str(path), **run.aggregate}, indent=2))
    elif args.command == "compare":
        diff = compare_runs(load_run(args.a, Path(args.out)), load_run(args.b, Path(args.out)))
        _print_compare(diff)
    elif args.command == "review":
        scores = {s: getattr(args, s) for s in MANUAL_STAGES}
        merged = record_review(args.run, args.case, scores, args.notes, Path(args.out))
        print(json.dumps(merged.aggregate.get("manual"), indent=2))
    else:
        case = append_case(
            args.question,
            [g.split(",") for g in args.group],
            args.trap,
            path=Path(args.file),
            category=args.category,
            case_id=args.id,
        )
        print(f"added {case.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
