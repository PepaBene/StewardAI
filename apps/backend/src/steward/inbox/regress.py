"""Check an agent change against the owner's inbox feedback (AG5).

    python -m steward.inbox.regress [--cases evals/inbox_feedback_cases.json]

`python -m steward.inbox.feedback` exports the items the owner rated. This replays each rated
trigger through the current agent (nothing is stored) and judges the new item against the
rating: a `keep` case must still produce a cited statement and the same kind of action (or none
if there was none); an `avoid` case must not repeat the rejected statements or the rejected
action. A pass is a floor, not proof the item is good; the owner still reads the failures.
"""

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import asyncpg

from steward.chat.owner import owner_of
from steward.config.jobs import InboxSettings, JobsConfig
from steward.config.store import load_active
from steward.inbox import triggers
from steward.inbox.agent import InboxAgent
from steward.inbox.run import build_item


def _kind(action: Any) -> str | None:
    return action.get("kind") if isinstance(action, dict) else None


def judge(case: dict[str, Any], item: dict[str, Any]) -> tuple[bool, str]:
    """(passed, reason) for one case against the item the current agent builds."""
    statements = [s.get("text", "") for s in item["body"]["statements"]]
    new_action = _kind(item["action"])
    old_action = _kind(case.get("agent_action"))
    if case["expected"] == "keep":
        if not statements:
            return False, "no cited statement any more"
        if new_action != old_action:
            return False, f"action changed from {old_action} to {new_action}"
        return True, "still cited, same action"
    repeated = [s for s in statements if s in (case.get("agent_statements") or [])]
    if repeated:
        return False, f"repeats {len(repeated)} rejected statement(s)"
    if old_action is not None and new_action == old_action:
        return False, f"proposes the rejected action ({old_action}) again"
    return True, "differs from the rejected item"


async def regress(
    pool: asyncpg.Pool,
    agent: InboxAgent | None,
    cases: list[dict[str, Any]],
    settings: InboxSettings | None = None,
) -> dict[str, Any]:
    stored = await load_active(pool, "jobs", JobsConfig)
    cfg = settings or (stored.body if stored else JobsConfig()).inbox_settings
    owner = await owner_of(pool)
    now = dt.datetime.now(dt.UTC)
    results: list[dict[str, Any]] = []
    for case in cases:
        t = case.get("trigger", {})
        trigger = await triggers.load(pool, str(t.get("kind", "")), int(t.get("ref", 0)))
        if trigger is None:
            results.append({"id": case["id"], "passed": None, "reason": "trigger no longer exists"})
            continue
        item = await build_item(pool, agent, trigger, cfg, now, owner)
        ok, reason = judge(case, item)
        results.append({"id": case["id"], "passed": ok, "reason": reason})
    judged = [r for r in results if r["passed"] is not None]
    return {
        "cases": len(results),
        "passed": sum(1 for r in judged if r["passed"]),
        "failed": sum(1 for r in judged if not r["passed"]),
        "skipped": len(results) - len(judged),
        "results": results,
    }


async def _main(path: Path) -> dict[str, Any]:
    from steward.ai import AINotConfigured
    from steward.db import create_pool
    from steward.inbox.agent import LLMInboxAgent
    from steward.llm.client import create_llm
    from steward.settings import get_settings

    text = await asyncio.to_thread(path.read_text, encoding="utf-8")
    cases = json.loads(text).get("cases", [])
    settings = get_settings()
    pool = await create_pool(settings)
    try:
        agent: InboxAgent | None
        try:
            agent = LLMInboxAgent(create_llm(settings))
        except AINotConfigured:
            agent = None
        return await regress(pool, agent, cases)
    finally:
        await pool.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("evals/inbox_feedback_cases.json"))
    args = parser.parse_args(argv)
    result = asyncio.run(_main(args.cases))
    print(json.dumps(result, indent=2))
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
