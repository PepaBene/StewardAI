# ruff: noqa: E501 - long fixture strings and SQL
"""Graph stream against a real database: decided-row protection, the shared rejection store,
dated patent paths, deal states, the passage review pipeline, collectors and the routes."""

import datetime as dt
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool, get_pool_or_error
from api.graph_routes import router as graph_router
from api.main import create_app
from steward.config import ThesesConfig
from steward.config.store import save_config
from steward.free_source_reads import business_network_payload, patent_trend_rows
from steward.free_sources import store_company_edge, store_patent_family
from steward.graph import provider_gate
from steward.graph.deals import (
    DealTransitionError,
    deal_key,
    deal_timeline,
    record_deal_event,
)
from steward.graph.decisions import GraphDecisionError, decide_disclosure, decide_edge, decide_event
from steward.graph.edges import upsert_edge, upsert_event
from steward.graph.evidence import record_evidence
from steward.graph.keys import normalize_object_key
from steward.graph.kpi import kpi_event_links, validate_thesis_kpi
from steward.graph.passages import review_pending_hits
from steward.graph.patents import attributed_families
from steward.graph.relationships import (
    concentration,
    detect_contradictions,
    review_queue,
    upsert_disclosure,
)
from steward.graph.successors import open_reconciliations, reconcile_parent_change
from steward.graph.ted import collect_ted_awards
from steward.graph.wikidata_statements import (
    current_statements,
    parse_entity_statements,
    sync_statements,
)
from steward.news.feeds import HoldingFeedInfo
from steward.news.pipeline import record_filing_hits
from steward.news.regulatory import collect_edgar_fts
from steward.sources.company_research import (
    collect_alpha_exposure,
    collect_diffbot_candidates,
    collect_epo_patents,
    collect_pdl_company,
    pdl_skip_reason,
    store_research_observation,
)
from steward.sources.edgar import EdgarClient, Filing
from steward.sources.free_market import FreeSourceError

pytestmark = pytest.mark.db

FIXTURES = Path(__file__).parent / "fixtures" / "graph"
TICKER = "EXM_US_EQ"
OWNER = "7f0c3d4e-0000-4000-8000-000000000001"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


async def seed_holding(pool: asyncpg.Pool, ticker: str = TICKER, **extra: str) -> None:
    await pool.execute(
        "insert into instrument (t212_ticker, isin, name, currency, cik) values"
        " ($1, 'US30000A1043', 'Example Industries AG', 'USD', $2)",
        ticker,
        extra.get("cik", "0001234567"),
    )


async def open_gate(pool: asyncpg.Pool, provider: str, endpoint: str) -> None:
    await pool.execute(
        "update provider_access set state='available', hard_blocked=false, retry_at=null,"
        " last_success_at=now() where provider=$1 and endpoint=$2",
        provider,
        endpoint,
    )


async def regulator_evidence(pool: asyncpg.Pool, regulator: str = "sec", n: int = 1) -> int:
    return await record_evidence(
        pool,
        regulator=regulator,
        external_id=f"doc-{regulator}-{n}",
        url=f"https://example.test/{regulator}/{n}",
        document_type="8-K",
        content=f"body {regulator} {n}",
        quote="Example Corp agreed to acquire Target Inc.",
    )


# ------------------------------------------------------------- A22 / G5 / G7 / G8: decisions


async def test_resync_never_overwrites_a_decided_edge_or_downgrades_a_dispute(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    sec_evidence = await regulator_evidence(pool, "sec")
    base = dict(
        subject_key=TICKER, object_key="TGT", object_name="Target Inc", edge_type="acquirer",
        direction="outgoing", source="sec", source_url="https://sec.example/8k",
    )  # fmt: skip
    edge = await upsert_edge(
        pool,
        **base,
        status="confirmed",
        evidence_id=sec_evidence,
        passage="Example agreed to acquire Target.",
    )
    row = await pool.fetchrow(
        "select status, evidence_tier from company_graph_edge where id=$1", edge
    )
    assert (row["status"], row["evidence_tier"]) == ("confirmed", "regulatory")
    # A vendor re-sync of the same row (even asserting a weaker status/evidence) changes nothing.
    again = await upsert_edge(
        pool, **base, status="suggested", detail={"seen": "again"}, valid_from=dt.date(2020, 1, 1)
    )
    assert again == edge
    row = await pool.fetchrow("select * from company_graph_edge where id=$1", edge)
    assert (row["status"], row["evidence_tier"], row["evidence_id"]) == (
        "confirmed",
        "regulatory",
        sec_evidence,
    )
    assert row["valid_from"] is None and json.loads(row["detail"]) == {"seen": "again"}
    # The owner can reject it (previously the confirmed status was sticky and this was ignored).
    rejected = await decide_edge(
        pool, edge, "reject", reason="Deal was withdrawn per 8-K item 1.02"
    )
    assert rejected["status"] == "rejected" and rejected["decided_at"] is not None
    await upsert_edge(pool, **base, status="suggested")
    assert (
        await pool.fetchval("select status from company_graph_edge where id=$1", edge) == "rejected"
    )

    # A disputed row is not downgraded back to a suggestion by a re-sync.
    other = await upsert_edge(
        pool,
        **{
            **base,
            "object_key": "OTH",
            "object_name": "Other Inc",
            "source_url": "https://x.example/1",
        },
    )
    await decide_edge(pool, other, "dispute", reason="Two sources disagree on the counterparty")
    await upsert_edge(
        pool,
        **{
            **base,
            "object_key": "OTH",
            "object_name": "Other Inc",
            "source_url": "https://x.example/1",
        },
    )
    assert (
        await pool.fetchval("select status from company_graph_edge where id=$1", other)
        == "disputed"
    )


async def test_owner_path_transitions_reasons_and_the_status_history(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    edge = await upsert_edge(
        pool, subject_key=TICKER, object_key="PRT", object_name="Partner SA", edge_type="partner",
        direction="symmetric", source="diffbot", source_url="https://d.example/p",
    )  # fmt: skip
    with pytest.raises(GraphDecisionError):  # no reason
        await decide_edge(pool, edge, "reject", reason=" ")
    with pytest.raises(GraphDecisionError):  # confirming needs evidence or a passage
        await decide_edge(pool, edge, "confirm", reason="looks right to me")
    with pytest.raises(GraphDecisionError):  # nothing to reopen
        await decide_edge(pool, edge, "reopen", reason="not rejected at all")
    await pool.execute(
        "update company_graph_edge set passage='Partnership announced.' where id=$1", edge
    )
    confirmed = await decide_edge(pool, edge, "confirm", reason="Owner read the press release")
    assert confirmed["status"] == "confirmed" and confirmed["evidence_tier"] == "owner_review"
    mid = await pool.fetchval("select now()")
    disputed = await decide_edge(pool, edge, "dispute", reason="Partner denies the arrangement")
    assert disputed["status"] == "disputed"
    await decide_edge(pool, edge, "reject", reason="Confirmed false by the partner")
    history = [r["to_status"] for r in await pool.fetch(
        "select to_status from graph_status_log where table_name='company_graph_edge'"
        " and row_id=$1 order by id", edge)]  # fmt: skip
    assert history == ["suggested", "confirmed", "disputed", "rejected"]
    assert (
        await pool.fetchval("select graph_status_as_of('company_graph_edge',$1,$2)", edge, mid)
        == "confirmed"
    )
    network = await business_network_payload(pool, TICKER, mid)
    assert next(e for e in network["edges"] if e["id"] == edge)["status"] == "confirmed"
    network_now = await business_network_payload(pool, TICKER, None)
    assert next(e for e in network_now["edges"] if e["id"] == edge)["status"] == "rejected"


async def test_rejection_is_keyed_on_subject_normalised_object_and_type_across_tables(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    first = await upsert_edge(
        pool, subject_key=TICKER, object_key="Siemens AG", object_name="Siemens AG",
        edge_type="competitor", direction="symmetric", source="diffbot", source_url="https://d.example/1",
    )  # fmt: skip
    await decide_edge(pool, first, "reject", reason="Not a competitor of this holding")
    # Another source, another url, another spelling, a LEI-less key: still rejected.
    second = await upsert_edge(
        pool, subject_key=TICKER, object_key="name:SIEMENS A.G.", object_name="SIEMENS AG",
        edge_type="competitor", direction="symmetric", source="wikidata", source_url="https://w.example/2",
    )  # fmt: skip
    assert (
        await pool.fetchval("select status from company_graph_edge where id=$1", second)
        == "rejected"
    )
    # A different edge type for the same object is not.
    partner = await upsert_edge(
        pool, subject_key=TICKER, object_key="Siemens AG", object_name="Siemens AG",
        edge_type="partner", direction="symmetric", source="diffbot", source_url="https://d.example/3",
    )  # fmt: skip
    assert (
        await pool.fetchval("select status from company_graph_edge where id=$1", partner)
        == "suggested"
    )
    # The legacy relationship table honours it too (a new row arrives rejected)...
    await pool.execute(
        "insert into entity_relationship (ticker, subject, object_key, object_name, type, source, status)"
        " values ($1,$1,'name:siemens','Siemens','competitor','wikidata','suggested')",
        TICKER,
    )
    assert await pool.fetchval(
        "select status from entity_relationship where ticker=$1 and type='competitor'", TICKER) == "rejected"  # fmt: skip
    # ... and a rejection made in the legacy table reaches the new edges (both directions).
    await pool.execute(
        "insert into entity_relationship (ticker, subject, object_key, object_name, type, source, status)"
        " values ($1,$1,'name:acme','Acme','ultimate_parent','gleif','suggested')",
        TICKER,
    )
    await pool.execute(
        "update entity_relationship set status='rejected' where ticker=$1 and object_key='name:acme'",
        TICKER,
    )
    acme = await upsert_edge(
        pool, subject_key=TICKER, object_key="ACME Inc.", object_name="ACME Inc.", edge_type="parent",
        direction="outgoing", source="diffbot", source_url="https://d.example/4",
    )  # fmt: skip
    assert (
        await pool.fetchval("select status from company_graph_edge where id=$1", acme) == "rejected"
    )
    # Reopening lifts the shared rejection.
    await decide_edge(pool, first, "reopen", reason="Owner changed their mind about this one")
    third = await upsert_edge(
        pool, subject_key=TICKER, object_key="Siemens", object_name="Siemens",
        edge_type="competitor", direction="symmetric", source="diffbot", source_url="https://d.example/5",
    )  # fmt: skip
    assert (
        await pool.fetchval("select status from company_graph_edge where id=$1", third)
        == "suggested"
    )


async def test_sql_normalisation_matches_python(backend_pool: asyncpg.Pool) -> None:
    names = ["Siemens AG", "SIEMENS A.G.", "The Coca-Cola Company", "Alpha & Omega Holdings Inc.",
             "name:Foo  Bar", "wd:q42", "5493001KJTIIGC8Y1R12", "O'Brien Ltd", "Nestlé S.A."]  # fmt: skip
    for name in names:
        assert await backend_pool.fetchval(
            "select graph_norm_object_key($1)", name
        ) == normalize_object_key(name), name


async def test_confirmed_events_need_evidence_and_the_tier_comes_from_the_document(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await seed_holding(pool)
    with pytest.raises(GraphDecisionError):
        await upsert_event(
            pool, ticker=TICKER, event_type="leadership", event_state="closed", source="sec",
            source_url="https://sec.example/ceo", status="confirmed",
        )  # fmt: skip
    vendor_doc = await regulator_evidence(pool, "diffbot", 2)
    with pytest.raises(GraphDecisionError, match="only primary or regulatory"):
        await upsert_event(
            pool, ticker=TICKER, event_type="leadership", event_state="closed", source="sec",
            source_url="https://sec.example/ceo", status="confirmed", evidence_id=vendor_doc,
        )  # fmt: skip
    sec_doc = await regulator_evidence(pool, "sec", 3)
    event = await upsert_event(
        pool, ticker=TICKER, event_type="leadership", event_state="closed", source="sec",
        source_url="https://sec.example/ceo", status="confirmed", evidence_id=sec_doc,
    )  # fmt: skip
    row = await pool.fetchrow(
        "select status, evidence_tier, decided_at from company_event where id=$1", event
    )
    assert (row["status"], row["evidence_tier"]) == ("confirmed", "regulatory") and row[
        "decided_at"
    ]
    # The caller cannot launder a vendor assertion into a high tier through the legacy wrapper.
    edge = await store_company_edge(
        pool, subject_key=TICKER, object_key="X", object_name="X", edge_type="partner",
        direction="symmetric", source="diffbot", source_url="https://d.example/x",
        evidence_tier="primary",
    )  # fmt: skip
    assert (
        await pool.fetchval("select evidence_tier from company_graph_edge where id=$1", edge)
        == "vendor"
    )
    # Owner path: confirm/reject/dispute a confirmed event; re-sync keeps the decision.
    await decide_event(pool, event, "dispute", reason="Filing was later restated")
    await upsert_event(
        pool,
        ticker=TICKER,
        event_type="leadership",
        event_state="closed",
        source="sec",
        source_url="https://sec.example/ceo",
        status="suggested",
    )
    assert await pool.fetchval("select status from company_event where id=$1", event) == "disputed"
    reopened = await decide_event(pool, event, "reopen", reason="Restatement was itself withdrawn")
    assert reopened["status"] == "suggested" and reopened["decided_at"] is None
    again = await decide_event(pool, event, "confirm", reason="Original 8-K stands as filed")
    assert again["status"] == "confirmed"
    with pytest.raises(GraphDecisionError):  # a confirmed row cannot be "reopened"
        await decide_event(pool, event, "reopen", reason="Reopening a confirmed event is illegal")
    bare = await upsert_event(
        pool,
        ticker=TICKER,
        event_type="product",
        event_state="announced",
        source="sec",
        source_url="https://sec.example/p2",
    )
    with pytest.raises(GraphDecisionError, match="linked evidence"):
        await decide_event(pool, bare, "confirm", reason="Looks right but nothing is attached")


async def test_company_event_known_at_is_preserved_on_refresh(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    when = dt.datetime(2026, 5, 1, 12, tzinfo=dt.UTC)
    event = await upsert_event(
        pool, ticker=TICKER, event_type="product", event_state="announced", source="sec",
        source_url="https://sec.example/p", known_at=when,
    )  # fmt: skip
    await upsert_event(
        pool, ticker=TICKER, event_type="product", event_state="announced", source="sec",
        source_url="https://sec.example/p", terms={"note": "refreshed"},
    )  # fmt: skip
    row = await pool.fetchrow("select known_at, terms from company_event where id=$1", event)
    assert row["known_at"] == when  # the first time the state was known, not the refresh


# ------------------------------------------------------------------ A21: dated subsidiary path


async def seed_patent(
    pool: asyncpg.Pool, key: str, applicant: str, basis: str, priority: dt.date
) -> int:
    return await store_patent_family(
        pool, family_key=key, priority_date=priority, title="Widget", cpc_codes=["H01L21/02"],
        jurisdictions=["EP"], forward_citations=2, backward_citations=1, legal_status="granted",
        source="epo_ops", source_url="https://worldwide.espacenet.com/" + key, ticker=TICKER,
        applicant=applicant, ownership_confidence=Decimal("0.8"), ownership_basis=basis,
    )  # fmt: skip


async def test_subsidiary_patents_need_a_confirmed_edge_covering_the_priority_date(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    sec_doc = await regulator_evidence(pool, "gleif", 9)
    # No edge at all: refused (a bare valid_from used to be enough).
    with pytest.raises(ValueError, match="confirmed subsidiary edge"):
        await seed_patent(
            pool, "F-NOEDGE", "Example Robotics GmbH", "dated_subsidiary_path", dt.date(2015, 5, 1)
        )
    edge = await upsert_edge(
        pool, subject_key=TICKER, object_key="Example Robotics GmbH", object_name="Example Robotics GmbH",
        edge_type="subsidiary", direction="outgoing", source="gleif", source_url="https://gleif.example/1",
        valid_from=dt.date(2012, 1, 1), valid_to=dt.date(2020, 12, 31), status="confirmed",
        evidence_id=sec_doc, passage="Example Robotics GmbH is a wholly owned subsidiary.",
    )  # fmt: skip
    await seed_patent(
        pool, "F-IN", "Example Robotics GmbH", "dated_subsidiary_path", dt.date(2015, 5, 1)
    )
    with pytest.raises(ValueError, match="covering the priority date"):  # before ownership began
        await seed_patent(
            pool, "F-BEFORE", "Example Robotics GmbH", "dated_subsidiary_path", dt.date(2010, 1, 1)
        )
    with pytest.raises(ValueError, match="covering the priority date"):  # after valid_to
        await seed_patent(
            pool, "F-AFTER", "Example Robotics GmbH", "dated_subsidiary_path", dt.date(2022, 1, 1)
        )
    await seed_patent(pool, "F-DIRECT", "Example Industries AG", "direct", dt.date(2018, 1, 1))
    await seed_patent(pool, "F-SUGGESTED", "Mystery Ltd", "suggested", dt.date(2018, 1, 1))
    keys = {f["family_key"] for f in await attributed_families(pool, TICKER)}
    assert keys == {"F-IN", "F-DIRECT"}  # suggested ownership is never attributed
    trend = await patent_trend_rows(pool, TICKER)
    assert sum(t["families"] for t in trend if t["cpc"] == "H01L21/02") == 2
    # The path is re-validated at read time: ending the relationship removes the family.
    await decide_edge(pool, edge, "dispute", reason="Control of the subsidiary is contested")
    assert {f["family_key"] for f in await attributed_families(pool, TICKER)} == {"F-DIRECT"}
    # ... and an as-of read before the dispute still sees it.
    before = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    assert {f["family_key"] for f in await attributed_families(pool, TICKER, before)} <= {
        "F-DIRECT",
        "F-IN",
    }


# ------------------------------------------------------------- G10: relationship disclosures


async def test_disclosures_dedupe_and_concentration_counts_only_the_right_ones(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    common = dict(
        ticker=TICKER, passage="Customer A accounted for 18% of total revenue for FY2025.",
        source_url="https://sec.example/10k", source="sec", fiscal_period="FY2025",
        valid_from=dt.date(2025, 1, 1), share_basis="issuer_revenue",
    )  # fmt: skip
    anon = await upsert_disclosure(
        pool, **common, relationship="customer", direction="issuer_sells_to",
        anonymous_counterparty=True, reported_share=Decimal("0.18"),
    )  # fmt: skip
    # Re-reading the same filing updates the row instead of duplicating it.
    assert anon == await upsert_disclosure(
        pool, **common, relationship="customer", direction="issuer_sells_to",
        anonymous_counterparty=True, reported_share=Decimal("0.19"),
    )  # fmt: skip
    named = await upsert_disclosure(
        pool, **{**common, "passage": "Northwind Traders accounted for 11% of revenue in FY2025."},
        relationship="customer", direction="issuer_sells_to", counterparty_name="Northwind Traders",
        reported_share=Decimal("0.11"),
    )  # fmt: skip
    unknown_dir = await upsert_disclosure(
        pool, **{**common, "passage": "Some unclear passage about Contoso Ltd at 30%."},
        relationship="customer", direction="unknown", counterparty_name="Contoso Ltd",
        reported_share=Decimal("0.30"),
    )  # fmt: skip
    counterparty_basis = await upsert_disclosure(
        pool,
        **{
            **common,
            "passage": "Holding was 40% of Acme's revenue (the filer's own).",
            "share_basis": "counterparty_revenue",
        },
        relationship="supplier", direction="issuer_buys_from", counterparty_name="Acme Inc",
        reported_share=Decimal("0.40"),
    )  # fmt: skip
    assert await pool.fetchval("select count(*) from relationship_disclosure") == 4
    for did in (anon, named, counterparty_basis):
        await decide_disclosure(pool, did, "confirm", reason="Owner checked the filing text")
    with pytest.raises(GraphDecisionError):  # the owner path refuses an unknown direction ...
        await decide_disclosure(pool, unknown_dir, "confirm", reason="Owner checked the text")
    # ... and a legacy row confirmed some other way still stays out of the concentration.
    await pool.execute(
        "update relationship_disclosure set status='confirmed' where id=$1", unknown_dir
    )
    rows = await concentration(pool, TICKER)
    assert len(rows) == 1
    row = rows[0]
    # Unknown direction and the filer-basis share are excluded; anonymous and named stay apart.
    assert (row["named_count"], row["anonymous_count"]) == (1, 1)
    assert row["named_share_sum"] == Decimal("0.11") and row["anonymous_share_sum"] == Decimal(
        "0.19"
    )
    assert row["reported_share_sum"] == Decimal("0.30")


async def test_confirming_a_disclosure_needs_direction_period_and_start_date(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    did = await upsert_disclosure(
        pool, ticker=TICKER, relationship="customer", direction="unknown",
        passage="Contoso Ltd is mentioned somewhere near the word customer.", source_url="https://s.example/1",
        source="sec", counterparty_name="Contoso Ltd",
    )  # fmt: skip
    with pytest.raises(GraphDecisionError, match="known direction"):
        await decide_disclosure(pool, did, "confirm", reason="I read the passage")
    rejected = await decide_disclosure(
        pool, did, "reject", reason="The passage is about something else"
    )
    assert rejected["status"] == "rejected"


async def test_contradictions_are_recorded_and_the_review_queue_lists_them(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    base = dict(
        ticker=TICKER, relationship="customer", direction="issuer_sells_to", fiscal_period="FY2025",
        valid_from=dt.date(2025, 1, 1), share_basis="issuer_revenue", source="sec",
    )  # fmt: skip
    a = await upsert_disclosure(
        pool, **base, passage="Northwind Traders accounted for 11% of FY2025 revenue (10-K).",
        source_url="https://s.example/10k", counterparty_name="Northwind Traders",
        reported_share=Decimal("0.11"),
    )  # fmt: skip
    b = await upsert_disclosure(
        pool, **base, passage="Northwind Traders accounted for 25% of FY2025 revenue (10-K/A).",
        source_url="https://s.example/10ka", counterparty_name="NORTHWIND TRADERS INC",
        reported_share=Decimal("0.25"),
    )  # fmt: skip
    anon = await upsert_disclosure(
        pool, **base, passage="One customer accounted for 25% of FY2025 revenue (note 3).",
        source_url="https://s.example/note3", anonymous_counterparty=True,
        reported_share=Decimal("0.25"),
    )  # fmt: skip
    wrong_way = await upsert_disclosure(
        pool, ticker=TICKER, relationship="customer", direction="issuer_buys_from",
        passage="A passage whose relationship contradicts its own direction.",
        source_url="https://s.example/x", source="sec", counterparty_name="Odd Co",
    )  # fmt: skip
    await detect_contradictions(pool, a)
    await detect_contradictions(pool, b)
    await detect_contradictions(pool, anon)
    await detect_contradictions(pool, wrong_way)
    kinds = {r["kind"] for r in await pool.fetch("select kind from relationship_contradiction")}
    assert {"share_conflict", "named_vs_anonymous", "direction_conflict"} <= kinds
    queue = await review_queue(pool, TICKER)
    assert queue["contradictions"] and len(queue["suggested_disclosures"]) == 4
    # Rejecting a disclosure resolves the contradictions it takes part in.
    await decide_disclosure(pool, b, "reject", reason="The 10-K/A misprinted the percentage")
    open_after = await pool.fetchval(
        "select count(*) from relationship_contradiction where status='open' and"
        " (disclosure_id=$1 or other_disclosure_id=$1)", b)  # fmt: skip
    assert open_after == 0


# ------------------------------------------------------------------- passage review pipeline


def filing_html(body: str) -> str:
    return (
        "<html><body>"
        + "".join(f"<p>{line}</p>" for line in body.splitlines() if line)
        + "</body></html>"
    )


def edgar_client(pages: dict[str, str]) -> EdgarClient:
    def handler(request: httpx.Request) -> httpx.Response:
        text = pages.get(str(request.url))
        return httpx.Response(200, text=text) if text is not None else httpx.Response(404)

    return EdgarClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)), "Test test@example.com"
    )


async def test_passage_review_resolves_direction_and_queues_the_hit(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await seed_holding(pool)
    url = "https://www.sec.gov/Archives/edgar/data/777/000000777726000001/acme10k.htm"
    for keyword in ("customer", "supplier"):  # the two keyword searches queue the same document
        await pool.execute(
            "insert into filing_hit (ticker, kind, query, matched_term, accession, cik, filer_name,"
            " form, filed_at, file, url) values ($1,'relationship_candidate',$2,$3,'0000000777-26-000001',"
            "'777','Acme Controls Inc','10-K',$4,'acme10k.htm',$5)",
            TICKER, f"name + {keyword}", keyword, dt.date(2026, 2, 20), url,
        )  # fmt: skip
    edgar = edgar_client({url: filing_html(fixture("10k_filer_names_holding.txt"))})
    results = await review_pending_hits(pool, edgar, {TICKER: ["Example Industries AG"]})
    assert len(results) == 1  # one read serves both queued hits
    assert results[0]["state"] == "resolved"
    states = {r["review_state"] for r in await pool.fetch("select review_state from filing_hit")}
    assert states == {"resolved"}
    d = await pool.fetchrow("select * from relationship_disclosure")
    assert (d["relationship"], d["direction"], d["status"], d["origin"]) == (
        "customer",
        "issuer_sells_to",
        "suggested",
        "passage_review",
    )
    assert d["counterparty_key"] == "cik:0000000777" and d["reported_share"] == Decimal("0.14")
    assert d["share_basis"] == "counterparty_purchases" and d["fiscal_period"] == "FY2025"
    assert d["valid_from"] == dt.date(2025, 1, 1) and d["valid_to"] == dt.date(2025, 12, 31)
    quote = await pool.fetchval("select quote from source_evidence where id=$1", d["evidence_id"])
    assert "Example Industries AG" in quote
    # Nothing is confirmed and nothing feeds concentration until the owner decides.
    assert await concentration(pool, TICKER) == []
    queue = await review_queue(pool, TICKER)
    assert queue["filing_hits"] == [] and len(queue["suggested_disclosures"]) == 1


async def test_passage_review_dismisses_boilerplate_and_retries_fetch_failures(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await seed_holding(pool)
    boiler = "https://www.sec.gov/Archives/edgar/data/1/b.htm"
    missing = "https://www.sec.gov/Archives/edgar/data/1/missing.htm"
    for url in (boiler, missing):
        await pool.execute(
            "insert into filing_hit (ticker, kind, query, matched_term, accession, cik, filer_name,"
            " form, filed_at, file, url) values ($1,'relationship_candidate','q','customer',$2,'1','F','10-K',"
            "$3,'f.htm',$4)", TICKER, url[-8:], dt.date(2026, 2, 1), url)  # fmt: skip
    edgar = edgar_client(
        {
            boiler: filing_html(
                "Our customers include many companies. Example Industries AG licenses a patent to us under an unrelated cross-license agreement signed in 2019."
            )
        }
    )
    for _ in range(3):
        await review_pending_hits(pool, edgar, {TICKER: ["Example Industries AG"]})
    rows = {
        r["url"]: r
        for r in await pool.fetch("select url, review_state, attempts, review_note from filing_hit")
    }
    assert rows[boiler]["review_state"] == "dismissed"
    assert rows[missing]["review_state"] == "failed" and rows[missing]["attempts"] == 3
    assert await pool.fetchval("select count(*) from relationship_disclosure") == 0


async def test_edgar_full_text_search_no_longer_assigns_supplier_or_customer(
    backend_pool: asyncpg.Pool,
) -> None:
    """Keyword adjacency is only a reason to read the filing (D5.8)."""
    pool = backend_pool

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"hits": {"total": {"value": 1}, "hits": [{
            "_id": "0000000777-26-000001:acme10k.htm",
            "_source": {"ciks": ["0000000777"], "display_names": ["Acme Controls Inc (CIK 0000000777)"],
                        "file_date": "2026-02-20", "form": "10-K"}}]}})  # fmt: skip

    holding = HoldingFeedInfo(TICKER, ("Example Industries AG",), "0001234567", None)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await collect_edgar_fts(
            http, {"User-Agent": "t t@example.com"}, "fts", [holding],
            dt.date(2026, 1, 1), dt.date(2026, 3, 1),
        )  # fmt: skip
    assert result.relations and {r[1] for r in result.relations} == {"relationship_candidate"}
    await record_filing_hits(pool, result)
    kinds = {r["kind"] for r in await pool.fetch("select kind from filing_hit")}
    assert kinds == {"relationship_candidate"}  # never 'customer' or 'supplier'
    assert await pool.fetchval("select count(*) from entity_relationship") == 0


async def test_ted_awards_become_suggested_procurement_disclosures(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    body = json.loads(fixture("ted_search_response.json"))

    def handler(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        assert "winner-name" in sent["query"] and sent["limit"] == 100
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        stored = await collect_ted_awards(
            pool, http, ticker=TICKER, names=["Example Industries AG"], since=dt.date(2026, 1, 1)
        )
        again = await collect_ted_awards(
            pool, http, ticker=TICKER, names=["Example Industries AG"], since=dt.date(2026, 1, 1)
        )
    assert stored == again == 1  # only the exact legal-name winner; the re-run does not duplicate
    row = await pool.fetchrow("select * from relationship_disclosure")
    assert (row["relationship"], row["direction"], row["status"]) == (
        "procurement_award",
        "award_to_issuer",
        "suggested",
    )
    assert (
        row["counterparty_name"] == "Deutsche Bahn Infrastructure AG"
        and row["fiscal_period"] == "FY2026"
    )
    assert "15250000" in row["passage"] and row["source_url"].endswith("234567-2026")
    assert await pool.fetchval("select count(*) from relationship_disclosure") == 1


# --------------------------------------------------------------------------- deal state machine


async def test_deal_events_validate_transitions_and_preserve_known_at(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    key = deal_key(TICKER, "deal", "Target Software Inc")
    t0 = dt.datetime(2026, 9, 15, 20, tzinfo=dt.UTC)
    ann = await record_deal_event(
        pool, ticker=TICKER, event_type="deal", key=key, state="announced", source="sec",
        source_url="https://sec.example/8k-1", announced_at=dt.date(2026, 9, 15),
        effective_at=dt.date(2026, 9, 15), counterparty="Target Software Inc",
        terms={"price_per_share": "38.5"}, known_at=t0,
    )  # fmt: skip
    # Re-recording the same state refreshes it but leaves known_at where it was.
    await record_deal_event(
        pool, ticker=TICKER, event_type="deal", key=key, state="announced", source="sec",
        source_url="https://sec.example/8k-1", effective_at=dt.date(2026, 9, 15),
        terms={"total_value": "4200000000"},
    )  # fmt: skip
    row = await pool.fetchrow("select known_at, terms from company_event where id=$1", ann)
    assert row["known_at"] == t0 and json.loads(row["terms"]) == {
        "price_per_share": "38.5",
        "total_value": "4200000000",
    }
    for state, day in (("approved", 20), ("closed", 28)):
        await record_deal_event(
            pool, ticker=TICKER, event_type="deal", key=key, state=state, source="sec",
            source_url=f"https://sec.example/{state}", effective_at=dt.date(2026, 10, day),
        )  # fmt: skip
    with pytest.raises(DealTransitionError):  # a closed deal cannot be (re)announced later
        await record_deal_event(
            pool, ticker=TICKER, event_type="deal", key=key, state="announced", source="sec",
            source_url="https://sec.example/late", effective_at=dt.date(2026, 11, 5),
        )  # fmt: skip
    with pytest.raises(DealTransitionError):
        await record_deal_event(
            pool, ticker=TICKER, event_type="deal", key=key, state="terminated", source="sec",
            source_url="https://sec.example/term", effective_at=dt.date(2026, 11, 6),
        )  # fmt: skip
    timeline = (await deal_timeline(pool, TICKER, with_pro_forma=False))[0]
    assert (
        timeline["state"] == "closed" and timeline["confirmed_state"] is None
    )  # nothing confirmed yet
    assert [t["state"] for t in timeline["timeline"]] == ["announced", "approved", "closed"]
    early = await deal_timeline(
        pool, TICKER, dt.datetime(2026, 9, 16, tzinfo=dt.UTC), with_pro_forma=False
    )
    assert early[0]["state"] == "announced"  # the state as known on that date


async def test_dilution_and_leverage_use_confirmed_terms_only(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    await seed_holding(pool)
    for concept, stmt, value in (
        ("dei:EntityCommonStockSharesOutstanding", "shares", 200_000_000),
        ("us-gaap:LongTermDebt", "balance_sheet", 5_000_000_000),
        ("us-gaap:CashAndCashEquivalentsAtCarryingValue", "balance_sheet", 1_000_000_000),
        ("us-gaap:OperatingIncomeLoss", "income", 1_700_000_000),
        ("us-gaap:DepreciationDepletionAndAmortization", "cash_flow", 300_000_000),
    ):
        await pool.execute(
            "insert into financial_statement_fact (ticker, concept, statement, period_end, basis, value,"
            " unit, dimensions, source, source_url, known_at) values ($1,$2,$3,'2025-12-31','reported',$4,"
            "'USD','{\"members\": []}'::jsonb,'sec','https://sec.example/f',$5)",
            TICKER, concept, stmt, Decimal(value), dt.datetime(2026, 2, 1, tzinfo=dt.UTC),
        )  # fmt: skip
    key = deal_key(TICKER, "deal", "Target Software Inc")
    evidence = await regulator_evidence(pool, "sec", 11)
    suggested = await record_deal_event(
        pool, ticker=TICKER, event_type="deal", key=key, state="announced", source="sec",
        source_url="https://sec.example/a", effective_at=dt.date(2026, 9, 15),
        terms={"shares_to_issue": "21000000", "debt_raised": "2800000000", "total_value": "4200000000",
               "cash_pct": "60", "stock_pct": "40"}, evidence_id=evidence,
    )  # fmt: skip
    suggested_only = (await deal_timeline(pool, TICKER))[0]
    assert suggested_only["confirmed_terms"] == {} and suggested_only["pro_forma"] is None
    assert "no confirmed terms" in suggested_only["pro_forma_unavailable"]
    await decide_event(pool, suggested, "confirm", reason="Terms match the signed merger agreement")
    confirmed = (await deal_timeline(pool, TICKER))[0]
    assert confirmed["confirmed_state"] == "announced"
    pf = confirmed["pro_forma"]
    assert pf["share_count_increase"] == Decimal("0.1050")
    assert pf["gross_leverage_before"] == Decimal("2.5000")  # 5.0bn debt / (1.7bn + 0.3bn)
    assert pf["gross_leverage_after"] == Decimal("3.9000")  # 7.8bn / 2.0bn
    assert pf["inputs_missing"] == []
    # A later suggested amendment with different terms changes nothing until confirmed.
    await record_deal_event(
        pool, ticker=TICKER, event_type="deal", key=key, state="amended", source="sec",
        source_url="https://sec.example/b", effective_at=dt.date(2026, 10, 1),
        terms={"shares_to_issue": "30000000"}, evidence_id=evidence,
    )  # fmt: skip
    again = (await deal_timeline(pool, TICKER))[0]
    assert again["pro_forma"]["share_count_increase"] == Decimal("0.1050")
    assert again["state"] == "amended" and again["confirmed_state"] == "announced"


# ---------------------------------------------------------------- G14 / G12 / A20: collectors


async def test_research_observations_dedupe_and_a_changed_value_is_new_history(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    kw = dict(ticker=TICKER, provider="pdl", dataset="company_enrichment", metric="employee_count",
              source_url="https://api.peopledatalabs.com/v5/company/enrich", unit="people")  # fmt: skip
    first = await store_research_observation(pool, **kw, value=Decimal("1200"))
    second = await store_research_observation(pool, **kw, value=Decimal("1200.0"))
    assert first == second  # numerically identical re-fetch
    third = await store_research_observation(pool, **kw, value=Decimal("1300"))
    assert third != first
    await store_research_observation(pool, **kw, text_value="1001-5000")
    assert await pool.fetchval("select count(*) from company_research_observation") == 3
    txt = dict(kw, metric="latest_funding_stage")
    await store_research_observation(pool, **txt, text_value="Series B")
    await store_research_observation(pool, **txt, text_value="Series B")
    assert await pool.fetchval("select count(*) from company_research_observation") == 4


async def test_alpha_exposure_refresh_adds_nothing_twice_and_converts_cents(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await open_gate(pool, "alpha_vantage", "commodity")
    await open_gate(pool, "alpha_vantage", "account_free")
    payload = {
        "name": "Global Price of Sugar", "interval": "daily", "unit": "cents per pound",
        "data": [{"date": "2026-09-30", "value": "21.50"}, {"date": "2026-09-29", "value": "21.00"}],
    }  # fmt: skip

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        kw = dict(ticker=TICKER, function="SUGAR", series_name="sugar", api_key="k")
        first = await collect_alpha_exposure(pool, http, **kw)
        second = await collect_alpha_exposure(pool, http, **kw)
    assert first == 2 and second == 0
    row = await pool.fetchrow(
        "select value, unit, currency from company_research_observation where period_end='2026-09-30'"
    )
    assert (row["value"], row["unit"], row["currency"]) == (
        Decimal("0.2150"),
        "USD per pound",
        "USD",
    )


async def test_alpha_exposure_network_error_refunds_the_credits(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    await open_gate(pool, "alpha_vantage", "commodity")
    await open_gate(pool, "alpha_vantage", "account_free")

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as http:
        with pytest.raises(httpx.ConnectError):
            await collect_alpha_exposure(
                pool, http, ticker=TICKER, function="SUGAR", series_name="sugar", api_key="k"
            )
    used = await pool.fetchval(
        "select coalesce(sum(credits),0) from provider_quota_usage where provider='alpha_vantage'"
        " and window_kind='day'")  # fmt: skip
    assert used == 0


async def test_pdl_cooldown_only_when_unresolved_and_refunds_on_404_and_network_error(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await seed_holding(pool)
    await open_gate(pool, "people_data_labs", "company_enrich")
    calls: list[str] = []
    mode = {"kind": "404"}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(mode["kind"])
        if mode["kind"] == "net":
            raise httpx.ConnectError("down")
        if mode["kind"] == "404":
            return httpx.Response(404, json={"status": 404})
        return httpx.Response(200, json={"likelihood": 9, "website": "example.com", "name": "Example",
                                          "employee_count": 1500, "total_funding_raised": 5_000_000})  # fmt: skip

    async def month_credits() -> Decimal:
        return Decimal(await pool.fetchval(
            "select coalesce(sum(credits),0) from provider_quota_usage where provider='people_data_labs'"
            " and window_kind='month'"))  # fmt: skip

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        kw = dict(ticker=TICKER, domain="www.example.com", api_key="k")
        assert await collect_pdl_company(pool, http, **kw) == 0  # 404: free, and refunded
        assert await month_credits() == 0
        # A 'no match' is on cooldown: no second call is made (or spent).
        assert await collect_pdl_company(pool, http, **kw) == 0 and calls == ["404"]
        assert "cooldown" in (await pdl_skip_reason(pool, TICKER) or "")
        await pool.execute("delete from research_lookup_log")
        mode["kind"] = "net"
        with pytest.raises(httpx.ConnectError):
            await collect_pdl_company(pool, http, **kw)
        assert await month_credits() == 0  # the network error never reached PDL
        await pool.execute("delete from research_lookup_log")
        mode["kind"] = "ok"
        assert await collect_pdl_company(pool, http, **kw) == 1
        assert await month_credits() == 1
        assert await collect_pdl_company(pool, http, **kw) == 0  # matched: reused for 30 days
    assert calls == ["404", "net", "ok"]
    # Identity (LEI), headcount and funding all resolved: nothing for PDL to add even off cooldown.
    await pool.execute("delete from research_lookup_log")
    await pool.execute(
        "update instrument set issuer_lei='5493001KJTIIGC8Y1R12' where t212_ticker=$1", TICKER
    )
    assert "already resolved" in (await pdl_skip_reason(pool, TICKER) or "")


async def test_transient_403_cools_down_and_only_repeated_denials_hard_block(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await seed_holding(pool)
    await open_gate(pool, "diffbot", "organization")

    def forbidden(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Forbidden")

    async def status() -> asyncpg.Record:
        return await pool.fetchrow(
            "select state, hard_blocked, retry_at from provider_access where provider='diffbot'"
            " and endpoint='organization'"
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
        kw = dict(ticker=TICKER, domain="example.com", api_key="k")
        with pytest.raises(FreeSourceError):
            await collect_diffbot_candidates(pool, http, **kw)
        first = await status()
        assert (
            not first["hard_blocked"] and first["retry_at"] is not None
        )  # cooling down, not latched
        # Inside the cooldown the gate is closed without another request.
        with pytest.raises(RuntimeError):
            await collect_diffbot_candidates(pool, http, **kw)
        for _ in range(2):  # time passes (cooldowns elapse) and the denial repeats
            await pool.execute("update provider_access set retry_at=null where provider='diffbot'")
            with pytest.raises(FreeSourceError):
                await collect_diffbot_candidates(pool, http, **kw)
        assert (await status())["hard_blocked"]
    # The owner's reset path lifts it; a previously verified endpoint is available again.
    reset = await provider_gate.reset_block(pool, "diffbot", "organization")
    assert reset["state"] == "available" and not reset["hard_blocked"]
    assert await provider_gate.gate_open(pool, "diffbot", "organization")
    with pytest.raises(LookupError):
        await provider_gate.reset_block(pool, "nope", "nothing")


async def test_diffbot_collector_stores_directional_dated_suggestions_and_keeps_decisions(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await seed_holding(pool)
    await open_gate(pool, "diffbot", "organization")
    entity = {
        "id": "E1", "name": "Example Industries AG", "homepageUri": "https://www.example.com",
        "acquisitions": [{"acquiree": {"name": "Target Software Inc"},
                          "date": {"str": "d2021-06-15", "precision": 3}}],
        "acquiredBy": [{"name": "MegaCorp"}],
    }  # fmt: skip

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"entity": entity}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert (
            await collect_diffbot_candidates(
                pool, http, ticker=TICKER, domain="example.com", api_key="k"
            )
            == 2
        )
        edges = {r["object_name"]: r for r in await pool.fetch("select * from company_graph_edge")}
        assert (
            edges["Target Software Inc"]["edge_type"],
            edges["Target Software Inc"]["direction"],
        ) == ("acquirer", "outgoing")
        assert edges["Target Software Inc"]["valid_from"] == dt.date(2021, 6, 15)
        assert (
            edges["MegaCorp"]["edge_type"],
            edges["MegaCorp"]["direction"],
            edges["MegaCorp"]["valid_from"],
        ) == ("acquired", "incoming", None)
        assert {e["status"] for e in edges.values()} == {"suggested"}
        assert {e["evidence_tier"] for e in edges.values()} == {"vendor"}
        await decide_edge(
            pool, edges["MegaCorp"]["id"], "reject", reason="MegaCorp is a minority investor only"
        )
        await pool.execute("update provider_access set retry_at=null where provider='diffbot'")
        await collect_diffbot_candidates(
            pool, http, ticker=TICKER, domain="example.com", api_key="k"
        )
    after = {
        r["object_name"]: r["status"]
        for r in await pool.fetch("select object_name, status from company_graph_edge")
    }
    assert after == {"Target Software Inc": "suggested", "MegaCorp": "rejected"}
    assert await pool.fetchval("select count(*) from company_graph_edge") == 2


def ops_handler(token_status: int = 200, search_xml: str | None = None):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/auth/accesstoken"):
            return (
                httpx.Response(200, json={"access_token": "tok", "expires_in": "1199"})
                if token_status == 200
                else httpx.Response(token_status, text="invalid_client")
            )
        if "/legal/" in path:
            return httpx.Response(200, text=fixture("ops_legal.xml"))
        return httpx.Response(200, text=search_xml or fixture("ops_biblio_search.xml"))

    return handler


async def test_epo_collector_stores_families_publications_and_attribution(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await seed_holding(pool)
    await open_gate(pool, "epo_ops", "patent_search")
    await pool.execute(
        "insert into entity_alias (ticker, alias, kind, source) values"
        " ($1,'Example Robotics GmbH','subsidiary','gleif')", TICKER)  # fmt: skip
    async with httpx.AsyncClient(transport=httpx.MockTransport(ops_handler())) as http:
        stored = await collect_epo_patents(
            pool, http, ticker=TICKER, applicant_aliases=("Example Industries AG",),
            client_id="id", client_secret="secret", today=dt.date(2026, 10, 1),
        )  # fmt: skip
    assert stored == 2
    fam = await pool.fetchrow("select * from patent_family where family_key='25134712'")
    assert fam["family_id_source"] == "ops_family_id"
    assert fam["jurisdictions"] == ["EP", "US"]
    assert fam["priority_date"] == dt.date(2005, 11, 15) and fam["citations_backward"] == 3
    assert fam["citations_forward"] == 0 and "not provided" in fam["raw"]
    assert fam["grant_date"] == dt.date(2012, 2, 28)
    assert fam["legal_status"] == "expired"  # a lapse event in the legal-service response
    pubs = {
        (r["country"], r["doc_number"], r["kind_code"])
        for r in await pool.fetch("select * from patent_publication")
    }
    assert ("EP", "1840100", "A1") in pubs and ("US", "8123456", "B2") in pubs
    owner = await pool.fetchrow(
        "select * from patent_family_owner o join patent_family f on f.id=o.family_id where f.family_key='25134712'"
    )
    assert (owner["ownership_basis"], owner["applicant"]) == ("direct", "Example Industries AG")
    assert owner["evidence_id"] is not None  # the OPS record is stored as evidence
    # Re-running does not duplicate a family however many offices published it.
    async with httpx.AsyncClient(transport=httpx.MockTransport(ops_handler())) as http:
        await collect_epo_patents(
            pool, http, ticker=TICKER, applicant_aliases=("Example Industries AG",),
            client_id="id", client_secret="secret", today=dt.date(2026, 10, 1),
        )  # fmt: skip
    assert await pool.fetchval("select count(*) from patent_family") == 2
    assert await pool.fetchval("select count(*) from patent_publication") == 3


async def test_epo_oauth_failure_is_recorded_with_its_type(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    await seed_holding(pool)
    await open_gate(pool, "epo_ops", "patent_search")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(ops_handler(token_status=400))
    ) as http:
        with pytest.raises(FreeSourceError):
            await collect_epo_patents(
                pool, http, ticker=TICKER, applicant_aliases=("Example Industries AG",),
                client_id="id", client_secret="bad",
            )  # fmt: skip
    row = await pool.fetchrow(
        "select last_attempt_state, status_code from provider_access where provider='epo_ops'"
    )
    # The first rejection is a cooldown, not a permanent block.
    assert row["last_attempt_state"] == "temporarily_failed" and row["status_code"] == 400


# ----------------------------------------------------------------------- G16 and D5.10


async def test_thesis_kpi_must_exist_and_events_join_verdicts_and_clusters(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    thesis = ThesesConfig.model_validate({"theses": [{
        "ticker": TICKER, "why_owned": "Cloud growth",
        "kpis": [{"id": "rev-growth", "metric": "revenue_growth_yoy", "operator": ">=",
                  "threshold": 10, "unit": "%"}]}]})  # fmt: skip
    async with pool.acquire() as conn:
        stored = await save_config(conn, "theses", thesis, created_by=None)
    await validate_thesis_kpi(pool, TICKER, "rev-growth")
    with pytest.raises(ValueError, match="not in"):
        await validate_thesis_kpi(pool, TICKER, "margin")
    await upsert_event(
        pool, ticker=TICKER, event_type="deal", event_state="announced", source="sec",
        source_url="https://sec.example/e", thesis_kpi="rev-growth",
        effective_at=dt.date(2026, 9, 15),
    )  # fmt: skip
    await pool.execute(
        "insert into story_cluster (ticker, kpi_id, headline, headline_key, best_tier, first_seen, last_seen)"
        " values ($1,'rev-growth','Example buys Target','example-buys-target',1,$2,$2)",
        TICKER, dt.datetime(2026, 9, 16, tzinfo=dt.UTC))  # fmt: skip
    cfg = await pool.fetchval("select id from config_version where domain='theses' and active")
    await pool.execute(
        "insert into verdict (ticker, kpi_id, metric, basis, kpi_period, status, observed_value, operator,"
        " threshold, threshold_unit, rationale, config_version_id) values ($1,'rev-growth','revenue_growth_yoy',"
        "'reported','quarter','pass',12,'>=',10,'%','ok',$2)", TICKER, cfg)  # fmt: skip
    links = await kpi_event_links(pool, TICKER)
    assert stored.id == cfg
    (link,) = links
    assert link["kpi_defined"] and link["kpi"]["metric"] == "revenue_growth_yoy"
    assert link["latest_verdict"]["status"] == "pass" and len(link["news_clusters"]) == 1
    assert link["can_support_breach"] is False  # a suggested event can prompt review only


async def test_wikidata_statements_keep_superseded_and_removed_claims(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    entity = json.loads(fixture("wikidata_entity.json"))
    parsed = parse_entity_statements(entity, "Q12345")
    t1 = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    assert await sync_statements(pool, "Q12345", parsed, checked_at=t1) == {
        "unchanged": 0,
        "added": 3,
        "changed": 0,
        "removed": 0,
    }
    t2 = dt.datetime(2026, 9, 20, tzinfo=dt.UTC)
    assert (await sync_statements(pool, "Q12345", parsed, checked_at=t2))["unchanged"] == 3
    assert {r["last_checked_at"] for r in await current_statements(pool, "Q12345")} == {t2}
    # The parent changes (same statement id, new value) and the website claim is removed.
    claims = entity["entities"]["Q12345"]["claims"]
    claims["P749"][0]["mainsnak"]["datavalue"]["value"]["id"] = "Q999"
    del claims["P856"]
    t3 = dt.datetime(2026, 10, 1, tzinfo=dt.UTC)
    counts = await sync_statements(
        pool, "Q12345", parse_entity_statements(entity, "Q12345"), checked_at=t3
    )
    assert counts == {"unchanged": 1, "added": 0, "changed": 1, "removed": 1}
    current = {r["statement_id"]: r for r in await current_statements(pool, "Q12345")}
    assert current["Q12345$AAAA-1111"]["value_qid"] == "Q999"
    old = await pool.fetchrow(
        "select * from wikidata_statement where statement_id='Q12345$AAAA-1111' and value_qid='Q777'"
    )
    assert (
        old["superseded_at"] == t3 and old["superseded_by_id"] == current["Q12345$AAAA-1111"]["id"]
    )
    gone = await pool.fetchrow("select * from wikidata_statement where property_id='P856'")
    assert gone["superseded_at"] == t3  # retained, not deleted
    assert await pool.fetchval("select count(*) from wikidata_statement") == 4
    assert [r["rank"] for r in await current_statements(pool, "Q12345", "P749")] == [
        "preferred",
        "deprecated",
    ]


async def test_parent_changes_are_reconciled_against_deal_events(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    key = deal_key(TICKER, "deal", "Megacorp Inc")
    await record_deal_event(
        pool, ticker=TICKER, event_type="deal", key=key, state="announced", source="sec",
        source_url="https://sec.example/a", effective_at=dt.date(2026, 6, 1), counterparty="Megacorp Inc",
        known_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
    )  # fmt: skip
    pending = await reconcile_parent_change(
        pool, ticker=TICKER, change_kind="parent_changed", from_key="name:oldco", to_key="Megacorp Inc",
        observed_source="gleif", observed_at=dt.datetime(2026, 7, 1, tzinfo=dt.UTC))  # fmt: skip
    assert pending["outcome"] == "deal_pending"
    await record_deal_event(
        pool, ticker=TICKER, event_type="deal", key=key, state="closed", source="sec",
        source_url="https://sec.example/c", effective_at=dt.date(2026, 8, 15),
        known_at=dt.datetime(2026, 8, 15, tzinfo=dt.UTC),
    )  # fmt: skip
    closed = await reconcile_parent_change(
        pool, ticker=TICKER, change_kind="parent_changed", from_key="name:oldco", to_key="Megacorp Inc",
        observed_source="wikidata", observed_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC))  # fmt: skip
    assert closed["outcome"] == "explained_by_closed_deal"
    unexplained = await reconcile_parent_change(
        pool, ticker=TICKER, change_kind="parent_changed", from_key="name:oldco", to_key="Other Holdings",
        observed_source="gleif")  # fmt: skip
    assert unexplained["outcome"] == "unexplained"
    open_items = await open_reconciliations(pool, TICKER)
    assert {i["to_key"] for i in open_items} == {
        "Megacorp Inc",
        "Other Holdings",
    }  # the pending one + the unexplained


# --------------------------------------------------------------------------------- routes


@pytest.fixture
async def api(backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    app.include_router(graph_router)

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[get_pool_or_error] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def test_decision_and_queue_routes(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    pool = backend_pool
    await seed_holding(pool)
    edge = await upsert_edge(
        pool, subject_key=TICKER, object_key="PRT", object_name="Partner SA", edge_type="partner",
        direction="symmetric", source="diffbot", source_url="https://d.example/p", passage="Partnership.",
    )  # fmt: skip
    short = await api.post(
        f"/company-graph/edges/{edge}/decision", json={"decision": "confirm", "reason": "short"}
    )
    assert short.status_code == 422
    ok = await api.post(
        f"/company-graph/edges/{edge}/decision",
        json={"decision": "confirm", "reason": "Press release read and matches"},
    )
    assert ok.status_code == 200 and ok.json()["status"] == "confirmed"
    bad = await api.post(
        f"/company-graph/edges/{edge}/decision",
        json={"decision": "reopen", "reason": "Reopening a confirmed edge is illegal"},
    )
    assert bad.status_code == 409
    assert (
        await api.post(
            "/company-graph/edges/999999/decision",
            json={"decision": "reject", "reason": "Does not exist at all"},
        )
    ).status_code == 404
    disclosure = await upsert_disclosure(
        pool, ticker=TICKER, relationship="customer", direction="issuer_sells_to",
        passage="Northwind Traders accounted for 11% of revenue.", source_url="https://s.example/1",
        source="sec", counterparty_name="Northwind Traders", reported_share=Decimal("0.11"),
        share_basis="issuer_revenue", fiscal_period="FY2025", valid_from=dt.date(2025, 1, 1),
    )  # fmt: skip
    queue = (await api.get("/relationship-review/queue", params={"ticker": TICKER})).json()
    assert [d["id"] for d in queue["suggested_disclosures"]] == [disclosure]
    wrong_ticker = await api.post(
        f"/holdings/OTHER/relationship-disclosures/{disclosure}/decision",
        json={"decision": "confirm", "reason": "Wrong holding on purpose"},
    )
    assert wrong_ticker.status_code == 404
    confirmed = await api.post(
        f"/holdings/{TICKER}/relationship-disclosures/{disclosure}/decision",
        json={"decision": "confirm", "reason": "Matches the 10-K note 12"},
    )
    assert confirmed.json()["status"] == "confirmed"
    conc = await concentration(pool, TICKER)
    assert conc and conc[0]["named_count"] == 1
    reset = await api.post("/provider-access/diffbot/organization/reset-block")
    assert reset.status_code == 200
    assert (await api.post("/provider-access/nope/nothing/reset-block")).status_code == 404
    deals = (await api.get(f"/holdings/{TICKER}/deals")).json()
    assert deals["deals"] == [] and deals["limitations"]
    assert (await api.get(f"/holdings/{TICKER}/patent-competitor-overlap")).json() == {
        "ticker": TICKER,
        "peers": [],
    }


async def test_scan_filing_stores_suggested_events_with_evidence(
    backend_pool: asyncpg.Pool,
) -> None:
    from steward.graph.deal_extract import scan_filing

    pool = backend_pool
    await seed_holding(pool)
    filing = Filing(
        cik="1234567", form="8-K", accession="0001234567-26-000099", filing_date=dt.date(2026, 9, 15),
        report_date=dt.date(2026, 9, 15), primary_document="ex8k.htm", items="1.01,9.01",
        accepted_at=dt.datetime(2026, 9, 15, 21, 30, tzinfo=dt.UTC),
    )  # fmt: skip
    base = "https://www.sec.gov/Archives/edgar/data/1234567/000123456726000099/"
    pages = {
        base + "ex8k.htm": filing_html(fixture("8k_merger_announced.txt")),
        base + "0001234567-26-000099-index.html": (
            "<table><tr><td>1</td><td>8-K</td><td>ex8k.htm</td><td>8-K</td><td>10</td></tr>"
            "<tr><td>2</td><td>Press release</td><td>ex99.htm</td><td>EX-99.1</td><td>10</td></tr></table>"
        ),
        base + "ex99.htm": filing_html(
            "Example Corp to acquire Target Software Inc for $38.50 per share in cash and stock."
        ),
    }
    edgar = edgar_client(pages)
    ids = await scan_filing(pool, edgar, ticker=TICKER, filing=filing)
    assert ids
    rows = await pool.fetch(
        "select event_type, event_state, status, evidence_id, known_at, deal_key, terms from company_event"
    )
    assert {r["status"] for r in rows} == {"suggested"} and all(r["evidence_id"] for r in rows)
    assert {r["known_at"] for r in rows} == {filing.accepted_at}  # the acceptance time, not now()
    announced = next(r for r in rows if r["event_state"] == "announced")
    assert json.loads(announced["terms"])["price_per_share"] == "38.50"
    # Nothing confirmed: no dilution figure appears until the owner confirms the terms.
    timeline = await deal_timeline(pool, TICKER)
    assert timeline and all(d["confirmed_terms"] == {} for d in timeline)
    # Scanning again is idempotent.
    await scan_filing(pool, edgar, ticker=TICKER, filing=filing)
    assert await pool.fetchval("select count(*) from company_event") == len(rows)


async def test_portfolio_knock_on_links_two_holdings_through_confirmed_dated_disclosures(
    backend_pool: asyncpg.Pool,
) -> None:
    from steward.graph.relationships import portfolio_knock_on

    pool = backend_pool
    await seed_holding(pool)
    await pool.execute(
        "insert into instrument (t212_ticker, isin, name, currency) values"
        " ('NWT_US_EQ', 'US30000A9999', 'Northwind Traders Inc', 'USD')"
    )
    job = await pool.fetchval(
        "insert into job (job_type, trigger, status) values ('portfolio_sync','manual','succeeded')"
        " returning id"
    )
    await pool.execute(
        "insert into account_snapshot (snapshot_date, job_id, captured_at, t212_account_id,"
        " account_currency, total_value, cash_available, cash_in_pies, cash_reserved, invested_value,"
        " invested_cost, unrealized_ppl, realized_ppl, base_currency, fx_source, fx_rate, cash_base,"
        " invested_base, total_base, weight_basis) values ('2026-10-02', $1, now(), 1, 'USD', 1000, 0,"
        " 0, 0, 1000, 1000, 0, 0, 'USD', 'ecb', 1, 0, 1000, 1000, 'total')",
        job,
    )
    for ticker in (TICKER, "NWT_US_EQ", "NOTHELD"):
        if ticker == "NOTHELD":
            continue
        await pool.execute(
            "insert into position_snapshot (snapshot_date, ticker, job_id, captured_at, quantity,"
            " avg_price, current_price, instrument_currency, value, account_currency, value_account,"
            " cost_account, ppl, base_currency, fx_source, fx_rate, value_base) values"
            " ('2026-10-02', $2, $1, now(), 1, 1, 1, 'USD', 1, 'USD', 1, 1, 0, 'USD', 'ecb', 1, 1)",
            job,
            ticker,
        )
    base = dict(
        ticker=TICKER, relationship="customer", direction="issuer_sells_to", source="sec",
        source_url="https://s.example/10k", fiscal_period="FY2025", valid_from=dt.date(2025, 1, 1),
        share_basis="issuer_revenue",
    )  # fmt: skip
    linked = await upsert_disclosure(
        pool, **base, passage="Northwind Traders, Inc. accounted for 11% of revenue.",
        counterparty_name="Northwind Traders, Inc.", reported_share=Decimal("0.11"),
    )  # fmt: skip
    outsider = await upsert_disclosure(
        pool, **{**base, "source_url": "https://s.example/other"},
        passage="Contoso Ltd accounted for 5% of revenue.", counterparty_name="Contoso Ltd",
        reported_share=Decimal("0.05"),
    )  # fmt: skip
    # Suggested links are not knock-on exposure.
    assert await portfolio_knock_on(pool) == []
    for d in (linked, outsider):
        await decide_disclosure(pool, d, "confirm", reason="Owner checked the 10-K note")
    links = await portfolio_knock_on(pool)
    assert [
        (link["ticker"], link["counterparty_ticker"], link["reported_share"]) for link in links
    ] == [(TICKER, "NWT_US_EQ", Decimal("0.11"))]
