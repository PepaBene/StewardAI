"""The machine's second look can fail a release feature but never pass one (engine plan, 5.3).

The data audit (`engine.data_audit`) opens a notice for every price, fund-holding or macro figure
that looks wrong. While such a notice is open, the features that show those figures read as
audit "mismatch" and cannot be activated. No result here ever adds a "match" row to the
ledger: only a person comparing a screen with its source can do that.
"""

import asyncpg

KIND = "data_audit"  # engine.data_audit.KIND

# Notice subject prefix (see data_audit) -> the release features that show that data.
FEATURES_BY_PREFIX: dict[str, tuple[str, ...]] = {
    "price": ("company", "performance", "risk"),
    "jump": ("company", "performance", "risk"),
    "stale": ("company", "performance", "risk"),
    "portfolio": ("performance", "risk", "xray", "journal"),
    "fund": ("funds", "xray"),
    "macro": ("macro",),
    "statement": ("company",),
    "graph": ("ownership",),
}


def blocked(subjects: list[str]) -> dict[str, int]:
    """Feature id -> number of open findings that block it."""
    out: dict[str, int] = {}
    for subject in subjects:
        for feature in FEATURES_BY_PREFIX.get(subject.split(":", 1)[0], ()):
            out[feature] = out.get(feature, 0) + 1
    return out


async def open_blocks(pool: asyncpg.Pool) -> dict[str, int]:
    rows = await pool.fetch(
        "select subject from public.ops_task where kind = $1 and status <> 'resolved'", KIND
    )
    return blocked([r["subject"] for r in rows])
