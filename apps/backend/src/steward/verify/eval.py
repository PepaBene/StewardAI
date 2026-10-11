"""The claim verifier's evaluation set: 20 cited sentences with one injected error each.

    uv run python -m steward.verify.eval            # the code check only (no API call)
    uv run python -m steward.verify.eval --live     # the full verifier with the configured model

Phase 4 AC: an injected unsupported sentence is flagged on 100 % of the 20 cases. Every
case also has a faithful version, which must not be flagged: a verifier that flags
everything would pass the first test and fail this one.

Without --live only the numeric check runs: it must catch every numeric case and flag no
faithful sentence; semantic cases are reported as not checked. The live run calls the
model API (about $0.01 with gpt-6-luna) and needs OPENAI_API_KEY or GOOGLE_API_KEY. Exit
status 1 when a target is missed.
"""

import argparse
import asyncio
import datetime as dt
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from steward.config.verification import ClaimVerifierSettings
from steward.llm.routing import create_routing_client
from steward.settings import get_settings
from steward.verify.claims import Claim, ClaimVerifier, LLMClaimVerifier, check_claims

SET_PATH = Path(__file__).resolve().parents[3] / "tests" / "verify" / "injected_claims.json"
TODAY = dt.date(2026, 9, 26)


@dataclass(frozen=True)
class Case:
    id: str
    kind: str
    evidence: tuple[str, ...]
    supported: str
    injected: str


def load_cases(path: Path = SET_PATH) -> list[Case]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        Case(c["id"], c["kind"], tuple(c["evidence"]), c["supported"], c["injected"])
        for c in data["cases"]
    ]


@dataclass
class ClaimScore:
    cases: int = 0
    injected_flagged: list[str] = field(default_factory=list)
    injected_missed: list[str] = field(default_factory=list)
    injected_unchecked: list[str] = field(default_factory=list)
    faithful_flagged: list[str] = field(default_factory=list)

    @property
    def catch_rate(self) -> float:
        return len(self.injected_flagged) / self.cases if self.cases else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "cases": self.cases,
            "injected_flagged": len(self.injected_flagged),
            "catch_rate": round(self.catch_rate, 4),
            "faithful_flagged": len(self.faithful_flagged),
        }

    def report(self) -> str:
        lines = [
            f"injected errors flagged: {len(self.injected_flagged)}/{self.cases} "
            f"= {self.catch_rate:.0%} (target 100 %)",
            f"faithful sentences flagged: {len(self.faithful_flagged)}/{self.cases}",
        ]
        if self.injected_missed:
            lines.append(f"missed: {', '.join(self.injected_missed)}")
        if self.injected_unchecked:
            lines.append(f"not checked (no model): {', '.join(self.injected_unchecked)}")
        if self.faithful_flagged:
            lines.append(f"false alarms: {', '.join(self.faithful_flagged)}")
        return "\n".join(lines)


async def score(
    cases: list[Case], verifier: ClaimVerifier | None, settings: ClaimVerifierSettings
) -> ClaimScore:
    claims: list[Claim] = []
    for n, case in enumerate(cases):
        claims.append(Claim(2 * n, case.supported, case.evidence))
        claims.append(Claim(2 * n + 1, case.injected, case.evidence))
    outcome = await check_claims(claims, settings, verifier, TODAY)
    result = ClaimScore(cases=len(cases))
    for n, case in enumerate(cases):
        faithful, injected = outcome.results[2 * n], outcome.results[2 * n + 1]
        if faithful.status == "unsupported":
            result.faithful_flagged.append(f"{case.id} ({faithful.reason})")
        if injected.status == "unsupported":
            result.injected_flagged.append(case.id)
        elif injected.status == "unchecked":
            result.injected_unchecked.append(case.id)
        else:
            result.injected_missed.append(case.id)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--live", action="store_true", help="call the configured model")
    parser.add_argument("--model", default=ClaimVerifierSettings().model)
    parser.add_argument("--json", action="store_true", help="print the score as JSON")
    args = parser.parse_args(argv)
    cases = load_cases()
    settings = ClaimVerifierSettings(model=args.model)
    verifier = (
        LLMClaimVerifier(create_routing_client(get_settings())[0], args.model)
        if args.live
        else None
    )
    result = asyncio.run(score(cases, verifier, settings))
    print(json.dumps(result.as_dict()) if args.json else result.report())
    if args.live:
        ok = not result.injected_missed and not result.injected_unchecked
        ok = ok and result.catch_rate == 1.0
    else:
        numeric = {c.id for c in cases if c.kind == "numeric"}
        ok = numeric <= set(result.injected_flagged)
    return 0 if ok and not result.faithful_flagged else 1


if __name__ == "__main__":
    sys.exit(main())
