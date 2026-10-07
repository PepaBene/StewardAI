"""P5.2/P5.3: store extracted drafts as SUGGESTED relationship claims.

Nothing stored here is ever confirmed: a draft enters the review queue with its shape, denominator,
period and the filing passage. Re-reading a note updates the same claim (the claim key includes the
passage), and a claim a reviewer already decided keeps its decision.
"""

from __future__ import annotations

import asyncpg

from steward.disclosure.concentration_extract import Draft
from steward.graph.relationships import Direction, upsert_disclosure

_SHARE_BASIS = {
    "total_revenue": "issuer_revenue",
    "segment_revenue": "issuer_revenue",
    "receivables": "other",
    "purchases": "issuer_purchases",
}


async def store_drafts(
    pool: asyncpg.Pool, *, ticker: str, drafts: list[Draft], source_url: str, source: str
) -> list[int]:
    ids: list[int] = []
    for d in drafts:
        anonymous = d.shape in ("anonymous", "group")
        direction: Direction = (
            "issuer_sells_to" if d.relationship == "customer" else "issuer_buys_from"
        )
        row_id = await upsert_disclosure(
            pool,
            ticker=ticker,
            relationship=d.relationship,  # type: ignore[arg-type]
            direction=direction,
            passage=d.passage,
            source_url=source_url,
            source=source,
            counterparty_name=d.name,
            anonymous_counterparty=anonymous,
            reported_share=d.share,
            share_basis=_SHARE_BASIS.get(d.denominator or "", None),
            fiscal_period=d.fiscal_period,
            origin="structured_extract",
            claim_label=d.label if anonymous else None,
        )
        # A decided claim keeps its figures, so only a still-suggested one takes the new shape.
        await pool.execute(
            """update public.relationship_disclosure
                  set claim_shape = $2, counterparty_count = $3, denominator_kind = $4
                where id = $1 and status = 'suggested'""",
            row_id,
            d.shape,
            d.count,
            d.denominator,
        )
        ids.append(row_id)
    return ids
