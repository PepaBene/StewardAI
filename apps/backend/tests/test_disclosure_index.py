"""The disclosure register's prose and the owner's agent notes in the passage index, and open
disclosure alerts as an inbox trigger."""

import datetime as dt
import json

import asyncpg
import pytest

from steward.inbox import service, triggers
from steward.retrieval import corpus, index
from steward.retrieval.search import Filters, Hit, search

NOW = dt.datetime(2026, 10, 11, tzinfo=dt.UTC)


async def add_relationship(pool: asyncpg.Pool, status: str, passage: str) -> int:
    return int(
        await pool.fetchval(
            "insert into public.relationship_disclosure (ticker, relationship, direction,"
            " counterparty_name, passage, source_url, source, status, known_at)"
            " values ('AAPL_US_EQ', 'supplier', 'issuer_buys_from', $4, $1,"
            " 'https://example.com/10k', 'sec', $2, $3) returning id",
            passage,
            status,
            NOW,
            passage.split()[0],
        )
    )


async def add_alert(pool: asyncpg.Pool, key: str, headline: str) -> int:
    return int(
        await pool.fetchval(
            "insert into public.disclosure_alert (kind, ticker, dedupe_key, headline, detail,"
            " source_url, known_at) values ('insider_trade', 'AAPL_US_EQ', $1, $2, $3::jsonb,"
            " 'https://example.com/form4', $4) returning id",
            key,
            headline,
            json.dumps({"insider": "J. Doe", "shares": 5000}),
            NOW,
        )
    )


@pytest.mark.db
async def test_relationship_passages_alerts_and_agent_notes_are_indexed_and_pruned(
    backend_pool: asyncpg.Pool,
) -> None:
    kept = await add_relationship(
        backend_pool,
        "confirmed",
        "Foxconn assembles most of our devices and is our largest supplier.",
    )
    rejected = await add_relationship(backend_pool, "rejected", "Zebra Corp supplies cases.")
    alert = await add_alert(backend_pool, "k1", "Director bought shares")
    note = await service.add_note(
        backend_pool, service.NoteIn(ticker="AAPL_US_EQ", text="Watch supplier concentration.")
    )

    disclosures = await corpus.disclosure_docs(backend_pool, 365)
    assert sorted(d.ref for d in disclosures) == sorted([f"relationship:{kept}", f"alert:{alert}"])
    assert f"relationship:{rejected}" not in corpus.refs(disclosures)
    notes = await corpus.agent_note_docs(backend_pool)
    assert corpus.refs(notes) == [str(note.id)]

    await index.index_documents(backend_pool, [*disclosures, *notes])

    async def kind_hits(query: str, kind: str) -> list[Hit]:
        result = await search(backend_pool, None, query, filters=Filters(kinds=(kind,)), limit=5)
        return result.hits

    hits = await kind_hits("largest supplier", "disclosure")
    assert hits and all(h.kind == "disclosure" for h in hits)
    assert any("bought" in h.text for h in await kind_hits("director bought shares", "disclosure"))
    note_hits = await kind_hits("supplier concentration", "agent_note")
    assert note_hits and note_hits[0].kind == "agent_note"

    await service.delete_note(backend_pool, note.id)
    remaining = corpus.refs(await corpus.agent_note_docs(backend_pool))
    await index.prune(backend_pool, "agent_note", remaining)
    assert not await backend_pool.fetchval(
        "select count(*) from public.document_chunk where kind = 'agent_note'"
    )


@pytest.mark.db
async def test_an_open_disclosure_alert_is_a_trigger_once_acknowledged_it_is_not(
    backend_pool: asyncpg.Pool,
) -> None:
    open_id = await add_alert(backend_pool, "k1", "Director bought shares")
    done = await add_alert(backend_pool, "k2", "Holder crossed 5%")
    await backend_pool.execute(
        "update public.disclosure_alert set acknowledged_at = now() where id = $1", done
    )
    found = await triggers.pending(backend_pool, 7, 10, 2)
    assert [(t.kind, t.ref) for t in found] == [("disclosure", open_id)]
    assert "J. Doe" in found[0].material and found[0].key == f"disclosure:{open_id}"
    loaded = await triggers.load(backend_pool, "disclosure", open_id)
    assert loaded is not None and loaded.severity is None
