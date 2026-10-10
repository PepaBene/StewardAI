"""P0.5: the coverage baseline, read from the deployed database before the disclosure layer grows.

Run it against the real database and paste the output into the migration map
(`docs/data/public-disclosure-migration-map.md`):

    uv run python -m steward.disclosure.baseline

It only reads. Every figure is a count or a share the database can state; the one figure it cannot
state, the false-positive rate of the current relationship suggestions, needs a person's labels, so
the report points at the accuracy-check page instead of estimating one.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import asyncpg

type Pool = asyncpg.Pool | asyncpg.Connection

#: (label, table) pairs counted for the migration map. A table that does not exist yet is reported
#: as absent, never as zero.
TABLES: tuple[tuple[str, str], ...] = (
    ("Source documents", "source_document"),
    ("Document versions", "source_document_version"),
    ("Evidence spans", "source_evidence"),
    ("13F filings", "form13f_filing"),
    ("13F holdings", "institutional_holding"),
    ("13D/G filings", "control_stake_filing"),
    ("Fund (N-PORT) filings", "nport_filing"),
    ("Insider filings", "insider_filing"),
    ("Insider transactions", "insider_transaction"),
    ("UK PDMR notices", "pdmr_notification"),
    ("Relationship disclosures", "relationship_disclosure"),
    ("Financial statement facts", "financial_statement_fact"),
    ("Disclosure alerts", "disclosure_alert"),
    ("Adjudication labels", "disclosure_adjudication"),
)


@dataclass(frozen=True)
class Inventory:
    counts: dict[str, int | None]  # None: the table does not exist in this database
    by_regulator: dict[str, int]
    relationship_status: dict[str, int]
    instruments: int
    linked_instruments: int

    @property
    def link_share(self) -> float | None:
        return None if self.instruments == 0 else self.linked_instruments / self.instruments


async def _exists(pool: Pool, table: str) -> bool:
    return bool(await pool.fetchval("select to_regclass($1) is not null", f"public.{table}"))


async def inventory(pool: Pool) -> Inventory:
    counts: dict[str, int | None] = {}
    for label, table in TABLES:
        counts[label] = (
            int(await pool.fetchval(f"select count(*) from public.{table}"))  # noqa: S608
            if await _exists(pool, table)
            else None
        )
    by_regulator: dict[str, int] = {}
    if await _exists(pool, "source_document"):
        rows = await pool.fetch(
            "select regulator, count(*) n from public.source_document group by 1 order by 2 desc"
        )
        by_regulator = {r["regulator"]: int(r["n"]) for r in rows}
    statuses: dict[str, int] = {}
    if await _exists(pool, "relationship_disclosure"):
        rows = await pool.fetch(
            "select status, count(*) n from public.relationship_disclosure group by 1"
        )
        statuses = {r["status"]: int(r["n"]) for r in rows}
    instruments = linked = 0
    if await _exists(pool, "instrument") and await _exists(pool, "instrument_security_link"):
        instruments = int(await pool.fetchval("select count(*) from public.instrument"))
        linked = int(
            await pool.fetchval(
                "select count(*) from public.instrument_security_link where status = 'confirmed'"
            )
        )
    return Inventory(counts, by_regulator, statuses, instruments, linked)


def render(inv: Inventory) -> str:
    lines = ["| Table | Rows |", "|---|---|"]
    for label, n in inv.counts.items():
        lines.append(f"| {label} | {'not in this database' if n is None else f'{n:,}'} |")
    lines += ["", "Documents by regulator:"]
    lines += [f"- {k}: {v:,}" for k, v in inv.by_regulator.items()] or ["- none yet"]
    lines += ["", "Relationship suggestions by review state:"]
    lines += [f"- {k}: {v:,}" for k, v in sorted(inv.relationship_status.items())] or ["- none yet"]
    share = inv.link_share
    lines += [
        "",
        "Instruments with a confirmed security link (the gate for joining disclosures to the "
        "portfolio): "
        + (
            "no instruments"
            if share is None
            else f"{inv.linked_instruments:,} of {inv.instruments:,} ({share:.0%})"
        ),
        "",
        "False-positive rate of current suggestions: needs a person's labels. Label a sample on "
        "the Accuracy checks page; this script does not estimate it.",
    ]
    return "\n".join(lines)


async def _main() -> None:
    from steward.db import create_pool
    from steward.settings import get_settings

    settings = get_settings()
    if settings.database_url is None:
        raise SystemExit("DATABASE_URL is not set: this reads the deployed database.")
    pool = await create_pool(settings)
    try:
        print(render(await inventory(pool)))
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(_main())
