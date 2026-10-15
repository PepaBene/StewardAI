"""Learn which web domains deserve their source tier from what you did with their facts
(autonomy plan B2).

A domain the AI judge promoted to tier 2 or 3 is trusted automatically afterwards. If you keep
rejecting the facts that come from it, that promotion was wrong. The rule is one-way, like the
learned confidence floors: it only ever demotes an AI-placed domain to `rejected`, never promotes
one, and never touches a domain you placed yourself. The demotion is a notice you can undo from
the web-domains list (setting a tier yourself is final).
"""

import datetime as dt

import asyncpg

from steward.ops import tasks
from steward.research.domains import domain_key

KIND = "domain_learning"
WINDOW_DAYS = 180
MIN_REJECTED = 3  # owner rejections before a domain is doubted
MAX_APPROVED_SHARE = 0.5  # of the facts you decided, at most this share may be approvals


def should_demote(approved: int, rejected: int) -> bool:
    """Three or more of your rejections, and fewer than half of your decisions were approvals."""
    decided = approved + rejected
    return rejected >= MIN_REJECTED and approved / decided < MAX_APPROVED_SHARE


async def learn(pool: asyncpg.Pool, now: dt.datetime) -> dict[str, list[str]]:
    placed = {
        r["domain"]
        for r in await pool.fetch(
            "select domain from public.web_domain_tier where decided_by like 'ai:%'"
            " and tier in ('T2', 'T3')"
        )
    }
    if not placed:
        return {"demoted": []}
    tally: dict[str, list[int]] = {}
    for r in await pool.fetch(
        "select host, status from public.web_fact where decided_by like 'owner:%'"
        " and status in ('approved', 'rejected') and retrieved_at >= $1",
        now - dt.timedelta(days=WINDOW_DAYS),
    ):
        domain = domain_key(r["host"])
        if domain in placed:
            counts = tally.setdefault(domain, [0, 0])
            counts[0 if r["status"] == "approved" else 1] += 1
    demoted: list[str] = []
    for domain, (approved, rejected) in sorted(tally.items()):
        if not should_demote(approved, rejected):
            continue
        done = await pool.execute(
            "update public.web_domain_tier set tier = 'rejected', decided_by = 'engine:outcomes',"
            " reasons = $2, decided_at = now() where domain = $1 and decided_by like 'ai:%'",
            domain,
            f"you rejected {rejected} and approved {approved} of its facts in {WINDOW_DAYS} days"[
                :500
            ],
        )
        if done.endswith(" 0"):
            continue
        demoted.append(domain)
        await tasks.see(
            pool,
            KIND,
            domain,
            title=f"{domain} is no longer trusted as a source",
            detail=f"You rejected {rejected} and approved {approved} of the facts it gave, so "
            "the app stopped using it. Set its tier yourself in the web-domains list to undo.",
            href="/holdings",
            weight=0.2,
            status="fixing",
        )
    return {"demoted": demoted}
