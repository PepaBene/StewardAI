"""Historical issuer lifecycle: former names, successor links and as-of readers (plan D1.9).

GLEIF and ESMA FIRDS ingestion keeps the *current* state in `legal_entity` / `listing`; this
module writes the history that an as-of screen needs:

- `issuer` (canonical, keyed by LEI), `issuer_alias` (legal, former, trading and translated
  names with valid windows) and `issuer_relationship` (`merged_into` from a GLEIF successor LEI,
  with a valid-from date), all stamped with `known_at`;
- instrument names from FIRDS record history (a changed `gnr_full_name` closes the old name's
  window; kind `trading`).  `legal` aliases are the issuer's legal name over time (a rename
  closes the old row's `valid_to`, it never rewrites its kind); `former` is a name GLEIF
  itself labels previous, with no known dates.  `valid_to_known_at` says when a close was
  recorded, so a replay dated earlier still sees the window open.

Readers (`aliases_as_of`, `legal_name_as_of`, `relationships_as_of`, `issuer_snapshot_as_of`)
answer "what was true on this date, as we knew it on that date": the valid window decides what
applied, `known_at` hides anything recorded after the replay cutoff.  Window ends (`valid_to`)
are inclusive: the last day the fact applied.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import asyncpg

from steward.sources.esma import FirdsRecord
from steward.sources.gleif import LegalEntity

GLEIF_NAME_KINDS = {
    "PREVIOUS_LEGAL_NAME": "former",
    "TRADING_OR_OPERATING_NAME": "trading",
    "ALTERNATIVE_LANGUAGE_LEGAL_NAME": "translated",
    "AUTO_ASCII_TRANSLITERATED_LEGAL_NAME": "translated",
    "PREFERRED_ASCII_TRANSLITERATED_LEGAL_NAME": "translated",
}


def name_key(name: str) -> str:
    """Case, spacing and punctuation-insensitive form used to decide a name did not change."""
    return re.sub(r"[\W_]+", " ", name.casefold()).strip()


@dataclass(frozen=True, slots=True)
class IssuerSync:
    issuer_id: int
    renamed_from: str | None = None
    aliases_written: int = 0
    successor_issuer_id: int | None = None


def gleif_other_names(entity: LegalEntity) -> list[tuple[str, str]]:
    """(name, alias kind) for the other and transliterated names GLEIF carries, by its type."""
    attrs = (entity.raw.get("attributes") or {}).get("entity") or {}
    found: list[tuple[str, str]] = []
    for block in ("otherNames", "transliteratedOtherNames"):
        for item in attrs.get(block) or []:
            name = (item.get("name") or "").strip()
            if not name:
                continue
            kind = GLEIF_NAME_KINDS.get(str(item.get("type") or ""), "trading")
            found.append((name, kind))
    return list(dict.fromkeys(found))


async def upsert_issuer(
    conn: asyncpg.Connection | asyncpg.Pool, entity: LegalEntity, *, cik: str | None = None
) -> int:
    """The canonical issuer for the entity's LEI (created on first sight)."""
    country = entity.hq_country if entity.hq_country and len(entity.hq_country) == 2 else None
    issuer_id: int = await conn.fetchval(
        "insert into public.issuer (legal_name, lei, country) values ($1, $2, $3)"
        " on conflict (lei) where lei is not null do update set legal_name = excluded.legal_name,"
        " country = coalesce(issuer.country, excluded.country) returning id",
        entity.legal_name,
        entity.lei,
        country if country and country.isalpha() and country.isupper() else None,
    )
    if cik:
        # CIK is unique across issuers; never steal one another issuer already holds.
        await conn.execute(
            "update public.issuer set cik = $2 where id = $1 and cik is null"
            " and not exists (select 1 from public.issuer where cik = $2)",
            issuer_id,
            cik,
        )
    return issuer_id


async def sync_gleif_issuer(
    conn: asyncpg.Connection | asyncpg.Pool,
    entity: LegalEntity,
    *,
    before_name: str | None,
    today: dt.date,
    known_at: dt.datetime,
    successor: LegalEntity | None = None,
    cik: str | None = None,
) -> IssuerSync:
    """Record the issuer, its names over time and a successor link from one GLEIF read.

    `before_name` is the legal name stored before this read (None on first sight).  A changed
    legal name closes the old name's window the day before `today` (the day the change was
    seen) and opens the new one on `today`; a first read opens an unbounded window.
    Idempotent: replaying the same read writes nothing new."""
    issuer_id = await upsert_issuer(conn, entity, cik=cik)
    written = 0
    renamed_from: str | None = None
    current = entity.legal_name.strip()
    open_legal = await conn.fetch(
        "select id, name from public.issuer_alias"
        " where issuer_id = $1 and kind = 'legal' and valid_to is null",
        issuer_id,
    )
    open_names = {name_key(r["name"]) for r in open_legal}
    if name_key(current) not in open_names:
        previous = next((r for r in open_legal), None)
        old_name = previous["name"] if previous else before_name
        opens_on: dt.date | None = None
        if old_name and name_key(old_name) != name_key(current):
            renamed_from = old_name
            opens_on = today
            if previous:
                await conn.execute(
                    "update public.issuer_alias set valid_to = $2, valid_to_known_at = $3"
                    " where id = $1",
                    previous["id"],
                    today - dt.timedelta(days=1),
                    known_at,
                )
            else:
                await conn.execute(
                    "insert into public.issuer_alias"
                    " (issuer_id, name, kind, valid_to, valid_to_known_at, known_at)"
                    " values ($1, $2, 'legal', $3, $4, $4) on conflict do nothing",
                    issuer_id,
                    old_name,
                    today - dt.timedelta(days=1),
                    known_at,
                )
            written += 1
        result = await conn.execute(
            "insert into public.issuer_alias (issuer_id, name, kind, valid_from, known_at)"
            " values ($1, $2, 'legal', $3, $4) on conflict do nothing",
            issuer_id,
            current,
            opens_on,
            known_at,
        )
        written += result.endswith("1")
    for name, kind in gleif_other_names(entity):
        result = await conn.execute(
            "insert into public.issuer_alias (issuer_id, name, kind, known_at)"
            " select $1, $2, $3, $4 where not exists (select 1 from public.issuer_alias"
            "  where issuer_id = $1 and lower(name) = lower($2))",
            issuer_id,
            name,
            kind,
            known_at,
        )
        written += result.endswith("1")
    successor_issuer_id: int | None = None
    if successor is not None and entity.successor_lei == successor.lei:
        successor_issuer_id = await upsert_issuer(conn, successor)
        if successor_issuer_id != issuer_id:
            await conn.execute(
                "insert into public.issuer_relationship"
                " (predecessor_id, successor_id, relationship, valid_from, known_at)"
                " values ($1, $2, 'merged_into', $3, $4) on conflict do nothing",
                issuer_id,
                successor_issuer_id,
                today,
                known_at,
            )
    return IssuerSync(issuer_id, renamed_from, written, successor_issuer_id)


def firds_name_history(
    history: Iterable[FirdsRecord],
) -> list[tuple[str, dt.date | None, dt.date | None]]:
    """(name, valid_from, valid_to) of the instrument names FIRDS published, per ISIN.

    `history` is every published record state in publication order.  A new `full_name` closes
    the previous name the day before its publication date; the last name stays open.  Cancelled
    records do not rename anything."""
    published: list[tuple[str, dt.date | None]] = []
    for record in history:
        if record.status == "CANC" or not record.full_name:
            continue
        stamp = record.raw.get("publication_date")
        day = None
        if stamp:
            try:
                day = dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).date()
            except ValueError:
                day = None
        if published and name_key(published[-1][0]) == name_key(record.full_name):
            continue
        published.append((record.full_name, day))
    spans: list[tuple[str, dt.date | None, dt.date | None]] = []
    for i, (name, start) in enumerate(published):
        nxt = published[i + 1][1] if i + 1 < len(published) else None
        end = nxt - dt.timedelta(days=1) if nxt else None
        begin = None if i == 0 else start
        if begin is not None and end is not None and end < begin:
            end = begin  # replaced on its first day: a one-day window, never an inverted one
        spans.append((name, begin, end))
    return spans


async def sync_firds_names(
    conn: asyncpg.Connection | asyncpg.Pool,
    issuer_id: int,
    history: Iterable[FirdsRecord],
    *,
    known_at: dt.datetime,
) -> int:
    """Former and current instrument names from FIRDS record history, as trading aliases.

    Only names that were *superseded* in the history are windowed; the current one is open.
    Returns the number of alias rows written."""
    written = 0
    for name, valid_from, valid_to in firds_name_history(history):
        if valid_to is not None:
            # A name that has since been replaced closes its open window; the kind stays
            # `trading`, and `valid_to_known_at` keeps earlier replays from seeing the close.
            updated = await conn.execute(
                "update public.issuer_alias set valid_to = greatest($3::date,"
                " coalesce(valid_from, $3::date)), valid_to_known_at = $4"
                " where issuer_id = $1 and lower(name) = lower($2) and kind = 'trading'"
                " and valid_to is null",
                issuer_id,
                name,
                valid_to,
                known_at,
            )
            written += updated.endswith("1")
        result = await conn.execute(
            "insert into public.issuer_alias"
            " (issuer_id, name, kind, valid_from, valid_to, valid_to_known_at, known_at)"
            " select $1, $2, 'trading', $3, $4::date, case when $4::date is null then null"
            " else $5::timestamptz end, $5 where not exists (select 1 from public.issuer_alias"
            "  where issuer_id = $1 and lower(name) = lower($2) and kind = 'trading')"
            " on conflict do nothing",  # the legal row of the same name and window may exist
            issuer_id,
            name,
            valid_from,
            valid_to,
            known_at,
        )
        written += result.endswith("1")
    return written


# ----------------------------------------------------------------------------- as-of readers


async def aliases_as_of(
    pool: asyncpg.Pool | asyncpg.Connection,
    issuer_id: int,
    as_of: dt.date,
    *,
    known_at: dt.datetime | None = None,
) -> list[dict[str, Any]]:
    """The names the issuer had on `as_of`, among the rows known by `known_at`."""
    rows = await pool.fetch(
        """select name, kind, valid_from, valid_to, known_at from public.issuer_alias
            where issuer_id=$1 and known_at <= $3
              and (valid_from is null or valid_from <= $2)
              and (valid_to is null or valid_to >= $2
                   or coalesce(valid_to_known_at, known_at) > $3)
            order by kind, name""",
        issuer_id,
        as_of,
        known_at or dt.datetime.now(dt.UTC),
    )
    return [dict(r) for r in rows]


async def legal_name_as_of(
    pool: asyncpg.Pool | asyncpg.Connection,
    issuer_id: int,
    as_of: dt.date,
    *,
    known_at: dt.datetime | None = None,
) -> str | None:
    """The legal name on `as_of`; falls back to the issuer's current name only when no legal
    alias window is recorded at all (an issuer first seen after the date is not guessed)."""
    cutoff = known_at or dt.datetime.now(dt.UTC)
    names = [
        a["name"]
        for a in await aliases_as_of(pool, issuer_id, as_of, known_at=cutoff)
        if a["kind"] == "legal"
    ]
    if names:
        return str(names[0])
    if await pool.fetchval(
        "select exists (select 1 from public.issuer_alias where issuer_id=$1 and kind = 'legal'"
        " and known_at <= $2)",
        issuer_id,
        cutoff,
    ):
        return None  # windows exist but none covers the date
    row = await pool.fetchval("select legal_name from public.issuer where id=$1", issuer_id)
    return str(row) if row else None


async def relationships_as_of(
    pool: asyncpg.Pool | asyncpg.Connection,
    issuer_id: int,
    as_of: dt.date,
    *,
    known_at: dt.datetime | None = None,
) -> list[dict[str, Any]]:
    """Predecessor / successor links of the issuer in force on `as_of` and known by `known_at`."""
    rows = await pool.fetch(
        """select r.id, r.predecessor_id, r.successor_id, r.relationship, r.valid_from,
                  r.valid_to, r.known_at
             from public.issuer_relationship r
            where $1 in (r.predecessor_id, r.successor_id) and r.known_at <= $3
              and r.valid_from <= $2 and (r.valid_to is null or r.valid_to >= $2)
            order by r.valid_from, r.id""",
        issuer_id,
        as_of,
        known_at or dt.datetime.now(dt.UTC),
    )
    return [dict(r) for r in rows]


async def issuer_snapshot_as_of(
    pool: asyncpg.Pool | asyncpg.Connection, lei: str, known_at: dt.datetime
) -> dict[str, Any] | None:
    """The GLEIF legal-entity state last recorded at or before `known_at` (the entity graph a
    replay may use), never a later snapshot."""
    row = await pool.fetchrow(
        """select lei, known_at, valid_from, valid_to, legal_name, entity_status,
                  registration_status, successor_lei
             from public.legal_entity_snapshot where lei=$1 and known_at <= $2
            order by known_at desc, id desc limit 1""",
        lei,
        known_at,
    )
    return dict(row) if row else None
