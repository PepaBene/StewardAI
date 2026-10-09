"""Store parsed UK TR-1 notices (P3.4). Idempotent by announcement URL; a reviewed row is kept.

Storing a notice does not turn the national voting-rights lane on: that lane stays `not_covered`
for an issuer until 20 reviewed examples reach the enablement bar (see `ownership.py`).
"""

from __future__ import annotations

import asyncpg

from steward.sources.tr1_uk import Tr1Notice

type Pool = asyncpg.Pool | asyncpg.Connection


async def store_tr1_notice(
    pool: Pool, notice: Tr1Notice, *, source_url: str, issuer_ticker: str | None
) -> bool:
    """Insert the notice once; returns True when a new row was written. A repeat read of the same
    announcement changes nothing, and a notice a person already reviewed is never overwritten."""
    new = await pool.fetchval(
        """insert into public.uk_tr1_notice
             (source_url, issuer_ticker, issuer_name, holder, holder_office,
              shareholder_if_different, percent_shares_after, percent_instruments_after,
              percent_total_after, voting_rights_after, percent_total_before, thresholds_crossed,
              direction, date_crossed, date_issuer_notified, known_at, gap)
           values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
           on conflict (source_url) do nothing
           returning source_url""",
        source_url,
        issuer_ticker,
        notice.issuer,
        notice.holder,
        notice.holder_office,
        notice.shareholder_if_different,
        notice.percent_shares_after,
        notice.percent_instruments_after,
        notice.percent_total_after,
        notice.voting_rights_after,
        notice.percent_total_before,
        None if notice.thresholds_crossed is None else list(notice.thresholds_crossed),
        notice.direction,
        notice.date_crossed,
        notice.date_issuer_notified,
        notice.known_at,
        notice.gap,
    )
    return new is not None
