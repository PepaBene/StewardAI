"""Fill the country mapping of a holding's geographic revenue automatically (autonomy plan C3).

A geographic member that the filing labels with a country name ("Germany", "United States of
America") is that country, so the mapping the owner would type is mechanical: member to
country, share 1. Anything that is not exactly a country (regions such as "Europe", "Rest of
World", "Americas") is left for the owner, because splitting it would be an estimate.

Rules that keep the owner's say:
- a member that already has any row for the country context, including one the owner zeroed to
  remove it, is never written again;
- subtotals and eliminations are skipped;
- each row's note says it was matched automatically.
"""

import logging
from typing import Any

import asyncpg

from steward.company.segments import ELIMINATION, UNALLOCATED, MapIn
from steward.fundamentals.read import SegmentRow, holding_segments
from steward.lookthrough.classify import country_code, country_name

logger = logging.getLogger("steward.company")

NOTE = "Matched automatically: the filing names this country."


def country_maps(rows: list[SegmentRow], taken: set[str]) -> list[MapIn]:
    """Mappings for the members that are exactly one country and are not mapped yet. `taken`
    holds the casefolded member labels that already have a country row."""
    out: list[MapIn] = []
    seen = set(taken)
    for r in rows:
        label = r.label.strip()
        key = label.casefold()
        if r.subtotal or key in seen or ELIMINATION.search(label) or UNALLOCATED.search(label):
            continue
        code = country_code(label)
        name = country_name(code)
        if code is None or name is None:
            continue
        seen.add(key)
        out.append(
            MapIn.model_validate(
                {
                    "breakdown": "geographic",
                    "member_label": label,
                    "context": "country",
                    "value": name,
                    "share": 1,
                    "note": NOTE,
                }
            )
        )
    return out


async def fill_country_maps(
    pool: asyncpg.Pool, owner_id: Any, tickers: list[str]
) -> dict[str, Any]:
    written: dict[str, list[str]] = {}
    for ticker in tickers:
        segments = await holding_segments(pool, ticker, None)
        if not segments.geographic:
            continue
        taken = {
            r["member_label"].casefold()
            for r in await pool.fetch(
                "select member_label from public.company_segment_map"
                " where owner_id=$1 and ticker=$2 and breakdown='geographic' and context='country'",
                owner_id,
                ticker,
            )
        }
        maps = country_maps(segments.geographic, taken)
        for m in maps:
            await pool.execute(
                "insert into public.company_segment_map"
                " (owner_id, ticker, breakdown, member_label, context, value, share, note)"
                " values ($1,$2,$3,$4,$5,$6,$7,$8)",
                owner_id,
                ticker,
                m.breakdown,
                m.member_label,
                m.context,
                m.value,
                m.share,
                m.note,
            )
        if maps:
            written[ticker] = [m.member_label for m in maps]
    return {"mapped": written}
