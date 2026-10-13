"""Side-by-side gate for the digest writer's model and effort (cost levers C2 and C5).

    uv run python -m steward.news.eval_digest gemini-3.8-flash gpt-6-luna:low gpt-6-luna:medium

Each argument is `model` or `model:effort`. For every candidate the same stored window is
written with direct (not batch) calls, each written sentence is checked by the claim verifier
(configured model), and the report compares what the owner would notice: sentences kept after
the citation filter and the claim check, sentences dropped, and the tokens used. Nothing is
stored or sent. A candidate passes when it keeps at least as many supported sentences as the
first (the baseline) less a small margin, and flags no more unsupported ones.
"""

import argparse
import asyncio
import datetime as dt
import json
import sys
from dataclasses import dataclass

from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.jobs import DigestSettings, JobsConfig
from steward.config.store import load_active
from steward.config.thesis import ThesesConfig
from steward.config.verification import VerificationConfig
from steward.db import create_pool
from steward.llm.client import create_llm
from steward.llm.routing import routing_client
from steward.news.digest import LLMDigestWriter, build_digest, verify_digest_claims
from steward.settings import get_settings
from steward.verify.claims import LLMClaimVerifier

MARGIN = 0.9  # a candidate may keep 90% of the baseline's supported sentences


@dataclass
class Row:
    name: str
    sentences: int
    supported: int
    unsupported: int
    dropped_uncited: int
    input_tokens: int
    output_tokens: int


def parse(spec: str) -> tuple[str, str | None]:
    model, _, effort = spec.partition(":")
    return model, effort or None


async def run(specs: list[str], kind: str, days_back: int) -> list[Row]:
    settings = get_settings()
    pool = await create_pool(settings)
    try:
        jobs = await load_active(pool, "jobs", JobsConfig)
        digest_cfg = (jobs.body if jobs else JobsConfig()).digest
        profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
        theses = await load_active(pool, "theses", ThesesConfig)
        verification = await load_active(pool, "verification", VerificationConfig)
        claim_cfg = (verification.body if verification else VerificationConfig()).claim_verifier
        llm = create_llm(settings)
        verifier = LLMClaimVerifier(routing_client(llm), claim_cfg.model, effort=claim_cfg.effort)
        end = dt.datetime.now(dt.UTC) - dt.timedelta(days=days_back)
        rows: list[Row] = []
        for spec in specs:
            model, effort = parse(spec)
            cfg: DigestSettings = digest_cfg.model_copy(
                update={"model": model, "effort": effort, "use_batch": False}
            )
            body, usage, note = await build_digest(
                pool,
                kind,  # type: ignore[arg-type]
                end,
                profiles.body if profiles else HoldingProfilesConfig(),
                cfg,
                LLMDigestWriter(llm, cfg),
                theses.body if theses else None,
            )
            written = len(body.overview) + sum(len(s.summary) for s in body.sections)
            outcome = await verify_digest_claims(body, claim_cfg, verifier, end.date())
            unsupported = sum(1 for r in outcome.results.values() if r.status == "unsupported")
            rows.append(
                Row(
                    spec,
                    written,
                    written - unsupported,
                    unsupported,
                    body.dropped_sentences,
                    usage.input_tokens if usage else 0,
                    usage.output_tokens if usage else 0,
                )
            )
            if note:
                print(f"{spec}: {note}")
        return rows
    finally:
        await pool.close()


def verdicts(rows: list[Row]) -> list[str]:
    base = rows[0]
    out = []
    for r in rows[1:]:
        ok = r.supported >= base.supported * MARGIN and r.unsupported <= base.unsupported
        out.append(f"{r.name}: {'PASS' if ok else 'FAIL'} against {base.name}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "candidates", nargs="+", help="model or model:effort; the first is the baseline"
    )
    parser.add_argument("--kind", choices=["daily", "weekly"], default="daily")
    parser.add_argument(
        "--days-back", type=int, default=0, help="end the window this many days ago"
    )
    args = parser.parse_args(argv)
    rows = asyncio.run(run(args.candidates, args.kind, args.days_back))
    print(json.dumps([r.__dict__ for r in rows], indent=1))
    lines = verdicts(rows) if len(rows) > 1 else []
    print("\n".join(lines))
    return 0 if all("PASS" in line for line in lines) else 1


if __name__ == "__main__":
    sys.exit(main())
