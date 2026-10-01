"""Retention, restore and rollback tooling (plan D8.4).

* `RetentionPolicy` / `prune_raw_payloads`: raw provider payloads are evidence for a bounded
  time.  After the horizon the normalized, point-in-time observation (value, unit, period,
  source URL, vintage, known_at) stays - derived metrics are recomputed from those - and the
  bulky raw copy is dropped.  Open conflicts keep their raw evidence until resolved.
* `catalog_snapshot` / `missing_rollbacks`: a database catalog fingerprint and the list of
  migrations lacking a rollback file, so a rollback script can be proven to restore the schema
  its migration changed (tests/test_lifecycle_phase8.py applies each migration, rolls it back
  and compares fingerprints).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, fields
from pathlib import Path

import asyncpg


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """Days a raw payload is kept.  Defaults follow the plan: no indefinite retention."""

    source_observation_raw_days: int = 400
    macro_payload_days: int = 180
    fetch_audit_days: int = 400

    def validate(self) -> None:
        for item in fields(self):
            if getattr(self, item.name) < 30:
                raise ValueError(f"{item.name} must be at least 30 days")


# Open conflicts keep their raw evidence until they are resolved.
COUNT_ELIGIBLE_OBSERVATIONS = """
select count(*) from public.source_observation o
 where o.known_at < $1 and o.raw <> '{}'::jsonb
   and not exists (
     select 1 from public.source_conflict c
      where c.status = 'open'
        and o.id in (c.preferred_observation_id, c.alternative_observation_id))"""
PRUNE_OBSERVATION_RAW = """
with pruned as (
  update public.source_observation o
     set raw = '{}'::jsonb,
         validation = o.validation || jsonb_build_object('raw_pruned_at', $2::text)
   where o.known_at < $1 and o.raw <> '{}'::jsonb
     and not exists (
       select 1 from public.source_conflict c
        where c.status = 'open'
          and o.id in (c.preferred_observation_id, c.alternative_observation_id))
  returning 1)
select count(*) from pruned"""


async def prune_raw_payloads(
    pool: asyncpg.Pool,
    policy: RetentionPolicy | None = None,
    *,
    now: dt.datetime | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Drop expired raw copies; normalized observations are never deleted or altered."""
    policy = policy or RetentionPolicy()
    policy.validate()
    now = now or dt.datetime.now(dt.UTC)
    obs_cutoff = now - dt.timedelta(days=policy.source_observation_raw_days)
    macro_cutoff = now - dt.timedelta(days=policy.macro_payload_days)
    audit_cutoff = now - dt.timedelta(days=policy.fetch_audit_days)
    counts: dict[str, int] = {}
    async with pool.acquire() as conn, conn.transaction():
        if dry_run:
            counts["source_observation_raw"] = await conn.fetchval(
                COUNT_ELIGIBLE_OBSERVATIONS, obs_cutoff
            )
            counts["macro_payload_archive"] = await conn.fetchval(
                "select count(*) from public.macro_payload_archive"
                " where known_at < $1 and payload is not null",
                macro_cutoff,
            )
            counts["provider_fetch_audit"] = await conn.fetchval(
                "select count(*) from public.provider_fetch_audit where attempted_at < $1",
                audit_cutoff,
            )
            return counts
        counts["source_observation_raw"] = await conn.fetchval(
            PRUNE_OBSERVATION_RAW, obs_cutoff, now.isoformat()
        )
        counts["macro_payload_archive"] = await conn.fetchval(
            """with pruned as (
                 update public.macro_payload_archive set payload = null
                  where known_at < $1 and payload is not null returning 1)
               select count(*) from pruned""",
            macro_cutoff,
        )
        counts["provider_fetch_audit"] = await conn.fetchval(
            """with gone as (
                 delete from public.provider_fetch_audit where attempted_at < $1 returning 1)
               select count(*) from gone""",
            audit_cutoff,
        )
    return counts


# ----------------------------------------------------------------------------- rollbacks
MIGRATION_ROLLBACK_START = "20260930182226"  # first free-data-sources migration
NOT_FREE_SOURCE_MIGRATIONS = frozenset({"20261001100000_transaction_fees.sql"})
ROLLBACK_SUFFIX = ".down.sql"


# Phase 0-6 free-source migrations end here; later phases ship their own rollback files.
MIGRATION_ROLLBACK_END = "20261003999999"


def free_source_migrations(
    migrations_dir: Path, *, through: str = MIGRATION_ROLLBACK_END
) -> list[Path]:
    return [
        p
        for p in sorted(migrations_dir.glob("*.sql"))
        if MIGRATION_ROLLBACK_START <= p.name.split("_", 1)[0] <= through
        and p.name not in NOT_FREE_SOURCE_MIGRATIONS
    ]


def rollback_path(rollback_dir: Path, migration: Path) -> Path:
    return rollback_dir / (migration.stem + ROLLBACK_SUFFIX)


def missing_rollbacks(migrations_dir: Path, rollback_dir: Path) -> list[str]:
    return [
        m.name
        for m in free_source_migrations(migrations_dir)
        if not rollback_path(rollback_dir, m).exists()
    ]


_CATALOG_QUERIES: dict[str, str] = {
    "columns": """
        select table_name||'.'||column_name||' '||data_type||' null='||is_nullable||
               ' default='||coalesce(column_default,'')
          from information_schema.columns where table_schema='public'""",
    "constraints": """
        select c.conrelid::regclass::text||' '||c.conname||' '||pg_get_constraintdef(c.oid)
          from pg_constraint c join pg_namespace n on n.oid=c.connamespace
         where n.nspname='public'""",
    "indexes": "select indexdef from pg_indexes where schemaname='public'",
    "functions": """
        select pg_get_functiondef(p.oid) from pg_proc p join pg_namespace n on n.oid=p.pronamespace
         where n.nspname='public' and p.prokind in ('f','p')""",
    "triggers": """
        select pg_get_triggerdef(t.oid) from pg_trigger t join pg_class c on c.oid=t.tgrelid
          join pg_namespace n on n.oid=c.relnamespace
         where n.nspname='public' and not t.tgisinternal""",
    "policies": """
        select schemaname||'.'||tablename||' '||policyname||' '||cmd||' '||roles::text||' '||
               coalesce(qual,'')||' '||coalesce(with_check,'')
          from pg_policies where schemaname='public'""",
    "rls": """
        select c.relname||' rls='||c.relrowsecurity
          from pg_class c join pg_namespace n on n.oid=c.relnamespace
         where n.nspname='public' and c.relkind='r'""",
    "grants": """
        select table_name||' '||grantee||' '||privilege_type
          from information_schema.role_table_grants
         where table_schema='public' and grantee in ('anon','authenticated','app_backend')""",
    "views": """
        select schemaname||'.'||viewname||' '||definition
          from pg_views where schemaname='public'""",
}

# Reference data the free-source migrations seed; rows are fingerprinted too.
SEEDED_TABLES: dict[str, str] = {
    "provider_access": "provider, endpoint",
    "provider_endpoint_manifest": "provider, endpoint",
}


async def catalog_snapshot(conn: asyncpg.Connection) -> dict[str, str]:
    """Stable per-category fingerprints of the public schema and the seeded reference rows."""
    snapshot: dict[str, str] = {}
    for name, sql in _CATALOG_QUERIES.items():
        rows = sorted(str(r[0]) for r in await conn.fetch(sql))
        snapshot[name] = hashlib.sha256("\n".join(rows).encode()).hexdigest()
    for table, order in SEEDED_TABLES.items():
        exists = await conn.fetchval("select to_regclass($1) is not null", f"public.{table}")
        if not exists:
            continue
        rows = await conn.fetch(
            f"select row_to_json(t)::text from public.{table} t order by {order}"  # noqa: S608
        )
        snapshot[f"rows:{table}"] = hashlib.sha256(
            json.dumps([r[0] for r in rows]).encode()
        ).hexdigest()
    return snapshot


async def catalog_lines(conn: asyncpg.Connection, category: str) -> list[str]:
    return sorted(str(r[0]) for r in await conn.fetch(_CATALOG_QUERIES[category]))


def diff_snapshots(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))


def rollback_notes(path: Path) -> str:
    """The leading comment block of a rollback file (what it undoes, what it cannot restore)."""
    lines = []
    for line in path.read_text().splitlines():
        if not line.startswith("--"):
            break
        lines.append(line.lstrip("- ").rstrip())
    return "\n".join(lines)
