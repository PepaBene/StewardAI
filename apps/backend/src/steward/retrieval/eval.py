"""Retrieval evaluation: does hybrid search find the right passages, and is it better than
keywords alone? (AG2 gate: keep the vectors only if they earn their cost.)

`evals/retrieval_cases.json` labels, per query, the documents (kind and ref) a good search
must surface. A document counts as found at rank r when any of its passages is the r-th hit.
Recall@k is the share of labelled documents found in the top k, averaged over queries; MRR
is the mean reciprocal rank of the first relevant document. Labels name stored records, so the
set is written against the owner's real corpus; run it after the index job.

    python -m steward.retrieval.eval --k 5            # keyword, vector and hybrid side by side
    python -m steward.retrieval.eval --k 5 --out run.json
"""

import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from steward.news.embeddings import Embedder
from steward.retrieval.search import Filters, Mode, search

CASES_PATH = Path(__file__).resolve().parents[3] / "evals" / "retrieval_cases.json"
MODES: tuple[Mode, ...] = ("keyword", "vector", "hybrid")


class Relevant(BaseModel):
    kind: str
    ref: str


class RetrievalCase(BaseModel):
    id: str
    query: str = Field(min_length=3)
    relevant: list[Relevant] = Field(min_length=1)
    ticker: str | None = None
    kinds: list[str] = Field(default_factory=list)


def load_cases(path: Path = CASES_PATH) -> list[RetrievalCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [RetrievalCase.model_validate(c) for c in raw["cases"]]


def score_case(
    found: Sequence[tuple[str, str]], relevant: Sequence[Relevant], k: int
) -> dict[str, float]:
    """Recall@k and reciprocal rank for one query. `found` is the (kind, ref) of each hit,
    best first; repeated documents count once, at their first position."""
    seen: list[tuple[str, str]] = []
    for item in found:
        if item not in seen:
            seen.append(item)
    wanted = {(r.kind, r.ref) for r in relevant}
    top = seen[:k]
    hit = wanted & set(top)
    first = next((i for i, d in enumerate(seen, start=1) if d in wanted), None)
    return {"recall": len(hit) / len(wanted), "rr": 1.0 / first if first else 0.0}


def summarise(per_case: Sequence[dict[str, float]]) -> dict[str, float | None]:
    if not per_case:
        return {"recall": None, "mrr": None}
    return {
        "recall": round(sum(c["recall"] for c in per_case) / len(per_case), 3),
        "mrr": round(sum(c["rr"] for c in per_case) / len(per_case), 3),
    }


async def evaluate(
    pool: Any, embedder: Embedder | None, cases: Sequence[RetrievalCase], k: int
) -> dict[str, Any]:
    """Every case under keyword, vector and hybrid search, with the summary per mode and the
    cases where hybrid does worse than keywords alone."""
    scores: dict[str, list[dict[str, float]]] = {m: [] for m in MODES}
    worse: list[str] = []
    for case in cases:
        filters = Filters(tickers=(case.ticker,) if case.ticker else (), kinds=tuple(case.kinds))
        per_mode: dict[str, dict[str, float]] = {}
        for mode in MODES:
            if mode != "keyword" and embedder is None:
                continue
            result = await search(pool, embedder, case.query, filters=filters, limit=20, mode=mode)
            per_mode[mode] = score_case([(h.kind, h.ref) for h in result.hits], case.relevant, k)
            scores[mode].append(per_mode[mode])
        if "hybrid" in per_mode and per_mode["hybrid"]["recall"] < per_mode["keyword"]["recall"]:
            worse.append(case.id)
    return {
        "k": k,
        "cases": len(cases),
        "modes": {m: summarise(s) for m, s in scores.items() if s},
        "hybrid_worse_than_keyword": worse,
    }


async def _main(args: argparse.Namespace) -> dict[str, Any]:
    from steward.config.jobs import JobsConfig
    from steward.config.store import load_active
    from steward.db import create_pool
    from steward.news.embeddings import GeminiEmbedder
    from steward.settings import get_settings

    cases = load_cases(Path(args.cases))
    if not cases:
        raise SystemExit(f"{args.cases} has no cases: label queries against the real corpus first")
    settings = get_settings()
    pool = await create_pool(settings)
    try:
        stored = await load_active(pool, "jobs", JobsConfig)
        cfg = (stored.body if stored else JobsConfig()).document_index_settings
        async with httpx.AsyncClient(timeout=30.0) as http:
            embedder = (
                GeminiEmbedder(
                    http,
                    settings.google_api_key.get_secret_value(),
                    cfg.embedding_model,
                    cfg.embedding_dimensions,
                )
                if settings.google_api_key
                else None
            )
            return await evaluate(pool, embedder, cases, args.k)
    finally:
        await pool.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(CASES_PATH))
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--out", help="write the result as JSON to this file")
    args = parser.parse_args(argv)
    result = asyncio.run(_main(args))
    text = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
