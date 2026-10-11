"""Turn the owner's inbox feedback into candidate evaluation cases (AG5).

    python -m steward.inbox.feedback [--out evals/inbox_feedback_cases.json]

An item the owner marked `not_useful` becomes a case the next agent change must not repeat
(what the trigger was, what the agent said, why the owner rejected it); one marked `useful`
becomes a case it must keep getting right. The cases are *candidates*: they are written to a
separate file and merged into an eval set by hand, so one bad day of feedback cannot change
the benchmark on its own.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import asyncpg

from steward.inbox.service import list_items


def case_from(item: Any) -> dict[str, Any]:
    body = item.body
    return {
        "id": f"inbox-{item.id}",
        "kind": "inbox_item",
        "expected": "keep" if item.feedback == "useful" else "avoid",
        "trigger": body.get("trigger", {}),
        "ticker": item.ticker,
        "agent_headline": item.headline,
        "agent_statements": [s.get("text") for s in body.get("statements", [])],
        "agent_action": item.proposed_action,
        "owner_note": item.feedback_note,
    }


async def export_cases(pool: asyncpg.Pool, limit: int = 500) -> list[dict[str, Any]]:
    items = await list_items(pool, None, None, limit)
    return [case_from(i) for i in items if i.feedback]


async def _main(out: Path) -> int:
    from steward.db import create_pool  # imported late: needs the database settings
    from steward.settings import get_settings

    pool = await create_pool(get_settings())
    try:
        cases = await export_cases(pool)
    finally:
        await pool.close()
    text = json.dumps({"version": 1, "cases": cases}, indent=2)
    await asyncio.to_thread(out.write_text, text, encoding="utf-8")
    print(f"wrote {len(cases)} candidate cases to {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("evals/inbox_feedback_cases.json"))
    args = parser.parse_args(argv)
    return asyncio.run(_main(args.out))


if __name__ == "__main__":
    sys.exit(main())
