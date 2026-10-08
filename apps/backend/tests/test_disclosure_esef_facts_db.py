"""P2.5: facts read out of stored ESEF reports become observations tied to their document version,
known when the version was known, written once and never edited. Skipped without
STEWARD_TEST_DATABASE_URL."""

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest

from steward.disclosure.esef_facts import (
    ESEF_PARSER_VERSION,
    parse_esef_facts,
    read_pending_esef_versions,
    store_esef_facts,
)
from steward.disclosure.ingest import DocumentRecord, ingest_document_version
from steward.disclosure.registry import seed_sources

pytestmark = pytest.mark.db

FIXTURE = Path(__file__).parent / "fixtures" / "disclosure" / "esef_sample.json"
LEI = "ACMELEI0000000000001"
KNOWN = dt.datetime(2026, 3, 10, 9, 20, 1, tzinfo=dt.UTC)
BEFORE = dt.datetime(2026, 3, 1, tzinfo=dt.UTC)
AFTER = dt.datetime(2026, 4, 1, tzinfo=dt.UTC)


class FakeStore:
    """An evidence store holding bytes by archive path; a path in `broken` fails to read."""

    def __init__(self, files: dict[str, bytes], broken: set[str] | None = None) -> None:
        self.files = files
        self.broken = broken or set()
        self.reads: list[str] = []

    async def put(self, path: str, data: bytes, content_type: str) -> None:
        self.files[path] = data

    async def get(self, path: str) -> bytes:
        self.reads.append(path)
        if path in self.broken:
            raise OSError("archive unavailable")
        return self.files[path]

    async def signed_url(self, path: str, expires_in: int) -> str:
        return path


async def issuer(pool: asyncpg.Pool, lei: str | None = LEI) -> int:
    return int(
        await pool.fetchval(
            "insert into public.issuer(legal_name, lei, country) values('Acme N.V.', $1, 'NL')"
            " returning id",
            lei,
        )
    )


async def esef_version(
    pool: asyncpg.Pool,
    issuer_id: int | None,
    content: bytes,
    *,
    external_id: str = "esef-1",
    archive_path: str | None = "aa/report.json",
    quarantine_reason: str | None = None,
) -> int:
    await seed_sources(pool)
    out = await ingest_document_version(
        pool,
        DocumentRecord(
            regulator="ESEF",
            external_id=external_id,
            stable_url=f"https://filings.xbrl.org/{external_id}.json",
            form="ESEF annual report",
            known_at=KNOWN,
            issuer_id=issuer_id,
            fiscal_period="2025-12-31",
            source_key="esef.filings_xbrl_org",
            published_at=KNOWN,
        ),
        content,
        archive_path=archive_path,
        quarantine_reason=quarantine_reason,
    )
    assert out.created
    return out.version_id


async def observations(pool: asyncpg.Pool, version_id: int) -> list[asyncpg.Record]:
    return list(
        await pool.fetch(
            """select * from public.disclosure_observation
                where document_version_id = $1 order by metric, period_end, id""",
            version_id,
        )
    )


async def test_facts_are_stored_as_observations_known_when_the_version_was_known(
    backend_pool: asyncpg.Pool,
) -> None:
    acme = await issuer(backend_pool)
    version = await esef_version(backend_pool, acme, FIXTURE.read_bytes())
    facts = parse_esef_facts(FIXTURE.read_bytes())

    first = await store_esef_facts(backend_pool, version, facts, lei=LEI)
    assert first.extracted == 10 and first.unchanged == 0
    assert first.skipped["other_entity"] == 1  # one fact names a different reporting entity
    assert first.skipped["conflicting_duplicates"] == 2 and first.skipped["nil_value"] == 1

    rows = await observations(backend_pool, version)
    assert len(rows) == 10
    assert {r["known_at"] for r in rows} == {KNOWN}
    assert {r["issuer_id"] for r in rows} == {acme}
    assert {r["parser_version"] for r in rows} == {ESEF_PARSER_VERSION}
    assert {r["review_state"] for r in rows} == {"machine"}
    assert {r["value_state"] for r in rows} == {"reported"}

    revenue = [r for r in rows if r["metric"] == "revenue" and r["period_end"].year == 2025]
    consolidated = next(r for r in revenue if json.loads(r["dimensions"]) == {})
    segment = next(r for r in revenue if json.loads(r["dimensions"]) != {})
    assert consolidated["value"] == Decimal("37417000000")
    assert (consolidated["unit"], consolidated["currency"], consolidated["scale"]) == (
        "EUR",
        "EUR",
        6,
    )
    assert consolidated["period_start"] == dt.date(2025, 1, 1) and not consolidated["is_instant"]
    assert json.loads(segment["dimensions"]) == {"ifrs-full:SegmentsAxis": "acme:EuropeMember"}
    assert segment["value"] == Decimal("20100000000")

    assets = next(r for r in rows if r["metric"] == "total_assets")
    assert assets["is_instant"] and assets["period_start"] is None
    assert assets["period_end"] == dt.date(2025, 12, 31)
    metrics = {r["metric"] for r in rows}
    assert "borrowings_noncurrent" not in metrics and "total_liabilities" not in metrics
    assert "borrowings_current" not in metrics  # only another entity stated it

    evidence = await backend_pool.fetch(
        """select e.row_ref, e.extraction_method, e.quote
             from public.disclosure_observation o
             join public.source_evidence e on e.id = o.evidence_id
            where o.document_version_id = $1""",
        version,
    )
    assert len(evidence) == 10
    assert {e["extraction_method"] for e in evidence} == {"xbrl-json"}
    assert all(json.loads(e["quote"])["fact"] == e["row_ref"] for e in evidence)


async def test_observations_are_visible_only_from_the_versions_known_at(
    backend_pool: asyncpg.Pool,
) -> None:
    acme = await issuer(backend_pool)
    version = await esef_version(backend_pool, acme, FIXTURE.read_bytes())
    await store_esef_facts(backend_pool, version, parse_esef_facts(FIXTURE.read_bytes()), lei=LEI)

    async def visible(as_of: dt.datetime) -> int:
        return int(
            await backend_pool.fetchval(
                "select count(*) from public.disclosure_observation"
                " where issuer_id = $1 and known_at <= $2",
                acme,
                as_of,
            )
        )

    assert await visible(BEFORE) == 0
    assert await visible(KNOWN) == 10
    assert await visible(AFTER) == 10


async def test_running_again_adds_nothing(backend_pool: asyncpg.Pool) -> None:
    acme = await issuer(backend_pool)
    version = await esef_version(backend_pool, acme, FIXTURE.read_bytes())
    facts = parse_esef_facts(FIXTURE.read_bytes())
    await store_esef_facts(backend_pool, version, facts, lei=LEI)

    again = await store_esef_facts(backend_pool, version, facts, lei=LEI)
    assert again.extracted == 0 and again.unchanged == 10
    assert len(await observations(backend_pool, version)) == 10
    assert await backend_pool.fetchval("select count(*) from public.source_evidence") == 10


async def test_a_new_parser_version_adds_rows_beside_the_old_ones(
    backend_pool: asyncpg.Pool,
) -> None:
    acme = await issuer(backend_pool)
    version = await esef_version(backend_pool, acme, FIXTURE.read_bytes())
    facts = parse_esef_facts(FIXTURE.read_bytes())
    await store_esef_facts(backend_pool, version, facts, lei=LEI)

    later = await store_esef_facts(backend_pool, version, facts, lei=LEI, parser_version="next")
    assert later.extracted == 10
    rows = await observations(backend_pool, version)
    assert len(rows) == 20 and {r["parser_version"] for r in rows} == {ESEF_PARSER_VERSION, "next"}


async def test_stored_observations_cannot_be_edited(backend_pool: asyncpg.Pool) -> None:
    acme = await issuer(backend_pool)
    version = await esef_version(backend_pool, acme, FIXTURE.read_bytes())
    await store_esef_facts(backend_pool, version, parse_esef_facts(FIXTURE.read_bytes()), lei=LEI)
    row = (await observations(backend_pool, version))[0]
    with pytest.raises(asyncpg.RestrictViolationError):
        await backend_pool.execute(
            "update public.disclosure_observation set value = 1 where id = $1", row["id"]
        )
    with pytest.raises(asyncpg.RestrictViolationError):
        await backend_pool.execute(
            "delete from public.disclosure_observation where id = $1", row["id"]
        )


async def test_a_version_without_an_issuer_or_a_quarantined_one_stores_nothing(
    backend_pool: asyncpg.Pool,
) -> None:
    facts = parse_esef_facts(FIXTURE.read_bytes())
    orphan = await esef_version(backend_pool, None, FIXTURE.read_bytes(), external_id="orphan")
    with pytest.raises(ValueError, match="no issuer"):
        await store_esef_facts(backend_pool, orphan, facts)
    acme = await issuer(backend_pool)
    held = await esef_version(
        backend_pool, acme, b"{}", external_id="held", quarantine_reason="unreadable"
    )
    with pytest.raises(ValueError, match="quarantined"):
        await store_esef_facts(backend_pool, held, facts)
    with pytest.raises(ValueError, match="no document version"):
        await store_esef_facts(backend_pool, 987654321, facts)
    assert await backend_pool.fetchval("select count(*) from public.disclosure_observation") == 0


async def test_pending_versions_are_read_once_and_counted(backend_pool: asyncpg.Pool) -> None:
    acme = await issuer(backend_pool)
    good = await esef_version(
        backend_pool, acme, FIXTURE.read_bytes(), external_id="good", archive_path="aa/good.json"
    )
    garbage = await esef_version(
        backend_pool, acme, b"not json", external_id="garbage", archive_path="bb/garbage.json"
    )
    store = FakeStore({"aa/good.json": FIXTURE.read_bytes(), "bb/garbage.json": b"not json"})

    detail = await read_pending_esef_versions(backend_pool, store)
    assert detail["versions"] == 2 and detail["extracted"] == 10 and detail["unchanged"] == 0
    assert detail["failed"] == 1 and detail["errors"] == [f"version {garbage}: EsefParseError"]
    assert detail["skipped"]["unreadable_report"] == 1
    assert detail["skipped"]["other_entity"] == 1 and detail["ignored"] == 3
    assert len(await observations(backend_pool, good)) == 10
    assert await observations(backend_pool, garbage) == []

    versions = {
        r["id"]: r["parser_version"]
        for r in await backend_pool.fetch(
            "select id, parser_version from public.source_document_version"
        )
    }
    assert versions == {good: ESEF_PARSER_VERSION, garbage: ESEF_PARSER_VERSION}

    store.reads.clear()
    again = await read_pending_esef_versions(backend_pool, store)
    assert again["versions"] == 0 and store.reads == []  # nothing is read twice


async def test_an_archive_that_cannot_be_read_is_retried_next_run(
    backend_pool: asyncpg.Pool,
) -> None:
    acme = await issuer(backend_pool)
    version = await esef_version(backend_pool, acme, FIXTURE.read_bytes())
    store = FakeStore({"aa/report.json": FIXTURE.read_bytes()}, broken={"aa/report.json"})

    down = await read_pending_esef_versions(backend_pool, store)
    assert down["failed"] == 1 and down["errors"] == [f"version {version}: OSError"]
    assert await observations(backend_pool, version) == []

    store.broken.clear()
    up = await read_pending_esef_versions(backend_pool, store)
    assert up["versions"] == 1 and up["extracted"] == 10 and up["failed"] == 0


async def test_versions_of_other_regulators_and_unarchived_ones_are_left_alone(
    backend_pool: asyncpg.Pool,
) -> None:
    acme = await issuer(backend_pool)
    await esef_version(
        backend_pool, acme, FIXTURE.read_bytes(), external_id="no-archive", archive_path=None
    )
    await ingest_document_version(
        backend_pool,
        DocumentRecord(
            regulator="EDINET",
            external_id="S100X",
            stable_url="edinet:S100X",
            form="annual",
            known_at=KNOWN,
            issuer_id=acme,
        ),
        FIXTURE.read_bytes(),
        archive_path="cc/other.json",
    )
    store = FakeStore({"cc/other.json": FIXTURE.read_bytes()})
    detail = await read_pending_esef_versions(backend_pool, store)
    assert detail["versions"] == 0 and store.reads == []
    none = await read_pending_esef_versions(backend_pool, None)
    assert none["skipped_reason"] == "no evidence store configured"
