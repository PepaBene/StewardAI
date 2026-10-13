"""Draft company case items from the owner's own thesis (autonomy plan C3).

The thesis already says why the stock is owned (the bull case), what would make it lose 40%
(the bear case, from the pre-mortem) and what would end it (risks, from the exit conditions).
Each becomes a *draft* case item with the thesis wording, low confidence and no evidence, so
the company page starts filled and the owner confirms or rewrites. Nothing is written as
confirmed and no wording is invented.

A statement that already exists for the holding, archived or not, is never added again, so an
item the owner removed stays removed.
"""

import logging
from typing import Any

import asyncpg

from steward.config.thesis import ThesesConfig, ThesisConfig

logger = logging.getLogger("steward.company")

MAX_LEN = 2000


def drafts(thesis: ThesisConfig) -> list[tuple[str, str]]:
    """(kind, statement) the thesis supports."""
    out: list[tuple[str, str]] = [("bull", thesis.why_owned)]
    out += [("bear", line) for line in thesis.pre_mortem]
    out += [("risk", line) for line in thesis.exit_conditions]
    return [(kind, text.strip()[:MAX_LEN]) for kind, text in out if len(text.strip()) >= 3]


async def fill_case_items(
    pool: asyncpg.Pool, owner_id: Any, theses: ThesesConfig, held: set[str]
) -> dict[str, Any]:
    written: dict[str, int] = {}
    for thesis in theses.theses:
        if thesis.ticker not in held:
            continue
        existing = {
            r["statement"].strip().casefold()
            for r in await pool.fetch(
                "select statement from public.company_case_item where owner_id=$1 and ticker=$2",
                owner_id,
                thesis.ticker,
            )
        }
        added = 0
        for kind, statement in drafts(thesis):
            if statement.casefold() in existing:
                continue
            existing.add(statement.casefold())
            await pool.execute(
                "insert into public.company_case_item"
                " (owner_id, ticker, kind, statement, evidence, confidence, status)"
                " values ($1, $2, $3, $4, '[]'::jsonb, 'low', 'draft')",
                owner_id,
                thesis.ticker,
                kind,
                statement,
            )
            added += 1
        if added:
            written[thesis.ticker] = added
    return {"drafted": written}
