"""Compare two stored research reports of the same company: the fixed pipeline and the agentic
one (agentic workflows and RAG plan, AG3).

    python -m steward.research.compare FIXED.json AGENTIC.json [--max-cost-usd 5]

Each file is a report body as stored (`select body from report where id = ...`). The numbers are
what the code can read from the report itself: sources found and how many are tier 1 or 2,
cited sentences kept, the claim check's verdict on them, steps and searches used, and the cost
the trace recorded. Whether the extra evidence is *correct* still needs the owner's reading;
this only shows whether deep mode found more checkable evidence, and what it cost.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from steward.research.run import ResearchBody


def measure(body: ResearchBody) -> dict[str, Any]:
    cited = sum(1 for s in body.sections for sentence in s.sentences if sentence.source_ids)
    claims = body.claims
    cost = [c.cost_usd for c in body.trace.model_calls] if body.trace else []
    return {
        "engine": body.engine,
        "profile": body.profile_id,
        "sources": len(body.sources),
        "sources_tier_1_2": sum(1 for s in body.sources if s.tier in (1, 2)),
        "web_sources": sum(1 for s in body.sources if s.kind == "search"),
        "cited_sentences": cited,
        "supported_claims": claims.supported if claims else None,
        "unsupported_claims": claims.unsupported if claims else None,
        "removed_sentences": body.dropped_sentences,
        "steps": body.steps_used,
        "searches": body.searches_used,
        "model_calls": len(cost),
        "cost_usd": round(sum(c for c in cost if c is not None), 4) if cost else None,
        "unpriced_calls": sum(1 for c in cost if c is None),
        "workers": {a.lens: a.outcome for a in body.agents},
    }


def compare(
    fixed: ResearchBody, agentic: ResearchBody, *, max_cost_usd: float | None = None
) -> dict[str, Any]:
    a, b = measure(fixed), measure(agentic)
    gains = {
        key: (b[key] - a[key]) if a[key] is not None and b[key] is not None else None
        for key in (
            "sources",
            "sources_tier_1_2",
            "cited_sentences",
            "supported_claims",
            "unsupported_claims",
            "cost_usd",
        )
    }
    more_evidence = (gains["sources_tier_1_2"] or 0) > 0 and (gains["supported_claims"] or 0) >= 0
    no_worse_claims = (gains["unsupported_claims"] or 0) <= 0
    within_budget = (
        True if max_cost_usd is None or b["cost_usd"] is None else b["cost_usd"] <= max_cost_usd
    )
    return {
        "fixed": a,
        "agentic": b,
        "difference": gains,
        "agentic_found_more_checkable_evidence": more_evidence and no_worse_claims,
        "agentic_within_cost_limit": within_budget,
        "note": "Correctness of the extra evidence is for the owner to judge; see the report.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixed", type=Path)
    parser.add_argument("agentic", type=Path)
    parser.add_argument("--max-cost-usd", type=float, default=None)
    args = parser.parse_args(argv)
    bodies = [
        ResearchBody.model_validate(json.loads(p.read_text(encoding="utf-8")))
        for p in (args.fixed, args.agentic)
    ]
    print(json.dumps(compare(*bodies, max_cost_usd=args.max_cost_usd), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
