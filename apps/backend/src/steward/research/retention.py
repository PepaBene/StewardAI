"""Retention and deletion of what a research run keeps beyond the report (Phase 5).

A run leaves four kinds of working record besides the report's cited sentences and sources:
the checkpoints that let it resume (the raw output of each step, including search titles),
the step details (for chat, a trace of the tool calls), the URLs the search returned, and the
text snippets of web sources. None of it is needed once the report is stored and shown.

- `purge_report_trace` removes them for one report on request: the discovered-URL list and
  web-source snippets inside the stored report, the job's checkpoints, and the tool trace in
  its step details. The report's sentences, sources (title, link, date, tier), claim ledger and
  snapshot digest stay, so the report still reads and its claims still resolve.
- `purge_old_traces` does the same for every job that succeeded before a cutoff, for the
  checkpoint and trace parts (it never touches a job that is not finished).
"""

import datetime as dt
import json
from typing import Any

import asyncpg


def scrub_body(body: dict[str, Any], now: dt.datetime) -> dict[str, Any]:
    """The stored report with its working records removed. Counts stay in `search_summary`."""
    out = dict(body)
    out["discovered_urls"] = []
    out["sources"] = [
        {**s, "snippet": None} if s.get("kind") == "search" else s for s in body.get("sources", [])
    ]
    out["trace_purged_at"] = now.isoformat(timespec="seconds")
    return out


async def purge_report_trace(
    pool: asyncpg.Pool, report_id: int, now: dt.datetime
) -> dict[str, int]:
    row = await pool.fetchrow(
        "select id, job_id, body from public.report where id = $1 and type = 'research'", report_id
    )
    if row is None:
        raise LookupError(f"No research report {report_id}")
    body = json.loads(row["body"]) if isinstance(row["body"], str) else dict(row["body"])
    cleared = len(body.get("discovered_urls", []))
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "update public.report set body = $2::jsonb where id = $1",
            report_id,
            json.dumps(scrub_body(body, now)),
        )
        checkpoints = 0
        traces = 0
        if row["job_id"] is not None:
            done = await conn.fetchval(
                "select status = 'succeeded' from public.job where id = $1", row["job_id"]
            )
            if done:
                checkpoints = int(
                    await conn.fetchval(
                        "with d as (delete from public.job_checkpoint where job_id = $1"
                        " returning 1) select count(*) from d",
                        row["job_id"],
                    )
                )
                traces = int(
                    await conn.fetchval(
                        "with u as (update public.job_step set detail = detail - 'trace'"
                        " where job_id = $1 and detail ? 'trace' returning 1)"
                        " select count(*) from u",
                        row["job_id"],
                    )
                )
    return {"urls": cleared, "checkpoints": checkpoints, "traces": traces}


async def purge_old_traces(
    pool: asyncpg.Pool, older_than_days: int, now: dt.datetime
) -> dict[str, int]:
    """Checkpoints and tool traces of succeeded jobs finished more than N days ago."""
    cutoff = now - dt.timedelta(days=older_than_days)
    async with pool.acquire() as conn, conn.transaction():
        checkpoints = int(
            await conn.fetchval(
                "with d as (delete from public.job_checkpoint c using public.job j"
                " where c.job_id = j.id and j.status = 'succeeded' and j.finished_at < $1"
                " returning 1) select count(*) from d",
                cutoff,
            )
        )
        traces = int(
            await conn.fetchval(
                "with u as (update public.job_step s set detail = s.detail - 'trace'"
                " from public.job j where s.job_id = j.id and j.status = 'succeeded'"
                " and j.finished_at < $1 and s.detail ? 'trace' returning 1)"
                " select count(*) from u",
                cutoff,
            )
        )
    return {"checkpoints": checkpoints, "traces": traces}


REVIEW_STATES = ("owner_confirmed", "owner_rejected", "unreviewed")


def set_claim_review(body: dict[str, Any], claim_id: str, state: str) -> dict[str, Any]:
    """The stored report with one claim's review state set by the owner."""
    if state not in REVIEW_STATES:
        raise ValueError(f"review state must be one of {', '.join(REVIEW_STATES)}")
    ledger: dict[str, Any] = body.get("ledger") or {}
    claims: list[dict[str, Any]] = ledger.get("claims", [])
    if not any(c.get("id") == claim_id for c in claims):
        raise LookupError(f"No claim {claim_id} in this report")
    out = dict(body)
    out["ledger"] = {
        **ledger,
        "claims": [{**c, "review": state} if c.get("id") == claim_id else c for c in claims],
    }
    return out
