"""P9.4: an issuer rename is visible to name matching and succession only from the moment it was
known. Skipped without STEWARD_TEST_DATABASE_URL."""

import datetime as dt

import asyncpg
import pytest

from steward.reference import disclosure_identity as ident
from tests.test_disclosure_db import issuer

pytestmark = pytest.mark.db

UTC = dt.UTC


def at(month: int, day: int, hour: int = 12) -> dt.datetime:
    return dt.datetime(2026, month, day, hour, tzinfo=UTC)


async def ids_for(pool: asyncpg.Pool, name: str, when: dt.datetime) -> set[int]:
    found = await ident.candidates_for(pool, name, as_of=when)
    return {c.issuer_id for c in found}


async def test_a_former_name_matches_until_the_rename_is_known(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    acme = await issuer(pool, "Newco Holdings")
    await pool.execute(
        """insert into public.issuer_alias
             (issuer_id, name, kind, valid_to, valid_to_known_at, known_at)
           values ($1, 'Oldco Industries', 'former', '2026-03-31', $2, $3)""",
        acme,
        at(4, 2),
        at(1, 1),
    )
    assert acme in await ids_for(pool, "Oldco Industries", at(3, 15))  # the old name in force
    # the rename took effect on 1 April but nobody knew until 2 April: the old name still matches
    assert acme in await ids_for(pool, "Oldco Industries", at(4, 1))
    assert acme not in await ids_for(pool, "Oldco Industries", at(5, 1))
    assert acme in await ids_for(pool, "Newco Holdings", at(5, 1))


async def test_succession_is_followed_only_once_it_was_known_and_in_force(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    old = await issuer(pool, "Oldco Industries")
    new = await issuer(pool, "Newco Holdings")
    await pool.execute(
        """insert into public.issuer_relationship
             (predecessor_id, successor_id, relationship, valid_from, known_at)
           values ($1, $2, 'renamed_to', '2026-04-01', $3)""",
        old,
        new,
        at(4, 2),
    )
    assert await ident.successors(pool, old, at(4, 1)) == []  # in force, but not yet known
    assert await ident.successors(pool, old, at(3, 1)) == []  # before it took effect
    assert await ident.successors(pool, old, at(4, 3)) == [new]
