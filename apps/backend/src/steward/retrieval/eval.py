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
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from steward.news.embeddings import Embedder
from steward.retrieval.search import Filters, Hit, Mode, search

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
    # Phrases the supporting passages must contain (case-insensitive). Support@k: every phrase
    # appears in the top k passages of the labelled documents, i.e. the answer is retrievable,
    # not just the right document.
    must_contain: list[str] = Field(default_factory=list)


def supported(
    hits: Sequence[Hit], relevant: Sequence[Relevant], phrases: Sequence[str], k: int
) -> bool | None:
    """Whether the top-k passages of the labelled documents hold every phrase; None when the
    case names no phrases."""
    if not phrases:
        return None
    wanted = {(r.kind, r.ref) for r in relevant}
    text = " ".join(h.text.lower() for h in hits[:k] if (h.kind, h.ref) in wanted)
    return all(p.lower() in text for p in phrases)


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


Reranker = Callable[[str, list[Hit]], Awaitable[list[Hit]]]


async def evaluate(
    pool: Any,
    embedder: Embedder | None,
    cases: Sequence[RetrievalCase],
    k: int,
    reranker: Reranker | None = None,
) -> dict[str, Any]:
    """Every case under keyword, vector and hybrid search (and hybrid plus rerank when a
    reranker is given), with the summary per mode, the support@k of the cases that name
    phrases, and the cases where hybrid does worse than keywords alone."""
    modes: list[str] = [*MODES, *(["hybrid+rerank"] if reranker else [])]
    scores: dict[str, list[dict[str, float]]] = {m: [] for m in modes}
    support: dict[str, list[bool]] = {m: [] for m in modes}
    worse: list[str] = []
    for case in cases:
        filters = Filters(tickers=(case.ticker,) if case.ticker else (), kinds=tuple(case.kinds))
        per_mode: dict[str, dict[str, float]] = {}
        for mode in modes:
            base: Mode = "hybrid" if mode == "hybrid+rerank" else mode  # type: ignore[assignment]
            if base != "keyword" and embedder is None:
                continue
            result = await search(pool, embedder, case.query, filters=filters, limit=20, mode=base)
            hits = result.hits
            if mode == "hybrid+rerank" and reranker is not None:
                hits = await reranker(case.query, hits)
            per_mode[mode] = score_case([(h.kind, h.ref) for h in hits], case.relevant, k)
            scores[mode].append(per_mode[mode])
            if (ok := supported(hits, case.relevant, case.must_contain, k)) is not None:
                support[mode].append(ok)
        if "hybrid" in per_mode and per_mode["hybrid"]["recall"] < per_mode["keyword"]["recall"]:
            worse.append(case.id)
    return {
        "k": k,
        "cases": len(cases),
        "modes": {
            m: {
                **summarise(s),
                "support": round(sum(support[m]) / len(support[m]), 3) if support[m] else None,
            }
            for m, s in scores.items()
            if s
        },
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
            reranker: Reranker | None = None
            if args.rerank_model:
                from steward.llm.client import create_llm
                from steward.retrieval.rerank import rerank

                llm = create_llm(settings)

                async def reranker(query: str, hits: list[Hit]) -> list[Hit]:
                    return (await rerank(llm, args.rerank_model, query, hits))[0]

            return await evaluate(pool, embedder, cases, args.k, reranker)
    finally:
        await pool.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(CASES_PATH))
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--out", help="write the result as JSON to this file")
    parser.add_argument(
        "--rerank-model", help="also score hybrid + a model rerank of the top 20, e.g. gpt-6-luna"
    )
    args = parser.parse_args(argv)
    result = asyncio.run(_main(args))
    text = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
