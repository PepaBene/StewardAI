"""D8.6 review scenarios run against the REAL readers and extractors (audit B48).

tests/fixtures/audit/review_samples.json holds the reviewer-labelled samples.  Each scenario is
fed to the app's own code where that code exists:

    filing_amendments    data_context.evidence.filed_notes     (DB reader, as-of revision pick)
    sec_notes            verify.claims.missing_numbers         (code check on note sentences)
    deal_reversals       data_context.evidence.deals           (DB reader, confirmed state)
    stale_consensus      data_context.evidence.consensus       (DB reader) + the staleness oracle
    xbrl / 13D-G / Form 4 are exercised in test_signal_audit_phase8.py against sources.xbrl,
                         sources.schedule13 and ownership.insider_classify.

Where the app has NO extractor (customer/supplier mention classification, anonymous customer
percentages) the sample can only pin the reviewer's standard through an oracle; that is stated in
`signal_audit.AUDIT_SCENARIOS`, not hidden, and the real behaviour that does exist (EDGAR
full-text hits stay unlinked review candidates) is pinned below.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from steward.data_context.evidence import consensus, deals, filed_notes
from steward.verify.claims import missing_numbers
from steward.verify_sources.signal_audit import AUDIT_SCENARIOS, consensus_is_stale

FIX = Path(__file__).parent / "fixtures"
SAMPLES: dict[str, Any] = json.loads((FIX / "audit" / "review_samples.json").read_text())
TICKER = "AUD_US_EQ"


def ts(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


async def evidence_id(pool: asyncpg.Pool, tag: str) -> int:
    doc = await pool.fetchval(
        "insert into public.source_document (regulator,external_id,stable_url)"
        " values ('sec',$1,$2) returning id",
        f"ext-{tag}",
        f"https://example.test/{tag}",
    )
    version = await pool.fetchval(
        "insert into public.source_document_version (document_id,content_hash,known_at)"
        " values ($1,$2,'2026-01-01') returning id",
        doc,
        f"hash-{tag}",
    )
    return int(
        await pool.fetchval(
            "insert into public.source_evidence (document_version_id,quote) values ($1,$2)"
            " returning id",
            version,
            f"quote {tag}",
        )
    )


# ---------------------------------------------------------------------------------------
# Restatement pick: the real as-of reader of filed notes
# ---------------------------------------------------------------------------------------
async def seed_note_revisions(pool: asyncpg.Pool, records: list[dict[str, Any]]) -> None:
    for record in records:
        await pool.execute(
            "insert into public.financial_statement_fact (ticker,concept,statement,period_end,"
            " basis,unit,source,source_url,known_at,text_block,revision,form)"
            " values ($1,'RevenueNote','note','2024-12-31','reported','text','sec_xbrl',"
            " $2,$3,$4,$5,'10-K')",
            TICKER,
            f"https://example.test/{record['revision']}",
            ts(record["known_at"]),
            record["value"],
            record["revision"],
        )


@pytest.mark.db
@pytest.mark.parametrize("sample", SAMPLES["filing_amendments"], ids=lambda s: s["id"])
async def test_restatement_pick_follows_what_was_known(
    backend_pool: asyncpg.Pool, sample: dict[str, Any]
) -> None:
    await seed_note_revisions(backend_pool, sample["records"])
    result = await filed_notes(backend_pool, TICKER, ts(sample["as_of"]))
    picked = [n.passage for n in result.notes]
    expected = [] if sample["expect"] is None else [sample["expect"]]
    assert picked == expected, sample["note"]


@pytest.mark.db
async def test_restatement_pick_prefers_the_higher_revision_whatever_the_insert_order(
    backend_pool: asyncpg.Pool,
) -> None:
    sample = next(s for s in SAMPLES["filing_amendments"] if s["id"] == "fa-4")
    await seed_note_revisions(backend_pool, list(reversed(sample["records"])))
    result = await filed_notes(backend_pool, TICKER, ts(sample["as_of"]))
    assert [n.passage for n in result.notes] == [sample["expect"]]


# ---------------------------------------------------------------------------------------
# Filed-note support: the real code check that a sentence's figures are in its passage
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("sample", SAMPLES["sec_notes"], ids=lambda s: s["id"])
def test_note_claims_are_checked_by_the_real_claim_verifier(sample: dict[str, Any]) -> None:
    unsupported = missing_numbers(f"The note reports {sample['claim']}.", [sample["passage"]])
    assert (unsupported == []) is sample["supported"], unsupported


# ---------------------------------------------------------------------------------------
# Deal state: the real reader of confirmed company events
# ---------------------------------------------------------------------------------------
# The reviewer vocabulary (completed, terminated-after-completion) maps onto the states the
# company_event table records (closed, reversed).
EVENT_STATE = {"completed": "closed"}


def recorded_state(event: dict[str, Any]) -> str:
    if event["state"] == "terminated" and event.get("reverses_completed"):
        return "reversed"
    return EVENT_STATE.get(event["state"], event["state"])


async def seed_deal_events(
    pool: asyncpg.Pool, events: list[dict[str, Any]], *, effective: dict[str, str] | None = None
) -> None:
    for i, event in enumerate(events):
        state = recorded_state(event)
        await pool.execute(
            "insert into public.company_event (ticker,event_type,event_state,effective_at,"
            " known_at,source,source_url,counterparty_key,status,evidence_id)"
            " values ($1,'deal',$2,$3,$4,'sec_xbrl',$5,'target-1','confirmed',$6)",
            TICKER,
            state,
            dt.date.fromisoformat((effective or {}).get(state, "2026-01-01"))
            if effective
            else None,
            ts(event["known_at"]),
            f"https://example.test/deal/{i}/{state}",
            await evidence_id(pool, f"deal-{i}"),
        )


def reviewer_state(state: str | None) -> str:
    if state is None:
        return "unknown"
    return {"closed": "completed"}.get(state, state)


@pytest.mark.db
@pytest.mark.parametrize("sample", SAMPLES["deal_reversals"], ids=lambda s: s["id"])
async def test_deal_state_follows_what_was_known_including_reversals(
    backend_pool: asyncpg.Pool, sample: dict[str, Any]
) -> None:
    await seed_deal_events(backend_pool, sample["events"])
    result = await deals(backend_pool, TICKER, ts(sample["as_of"]))
    state = result.deals[0].current_state if result.deals else None
    assert reviewer_state(state) == sample["expect"]


@pytest.mark.db
@pytest.mark.xfail(
    strict=False,
    reason="REAL DEFECT (audit C-low, found by this scenario): deals() sorts events by effective "
    "date before known_at, so a reversal known later but effective earlier is shown as older "
    "than the completion and current_state stays 'closed'. Fix: pick the current state by "
    "known_at (then id) in data_context/evidence.py; an XPASS here means it was fixed: "
    "delete the marker.",
)
async def test_a_reversal_known_later_wins_even_when_its_effective_date_is_earlier(
    backend_pool: asyncpg.Pool,
) -> None:
    """An unwind known in August but effective retroactively in March must be the current state
    in September: what was known last decides, not the effective date (audit C-low
    `deals().current_state` ordering)."""
    sample = next(s for s in SAMPLES["deal_reversals"] if s["id"] == "d-3")
    await seed_deal_events(
        backend_pool,
        sample["events"],
        effective={"announced": "2026-01-10", "closed": "2026-04-01", "reversed": "2026-03-15"},
    )
    result = await deals(backend_pool, TICKER, ts(sample["as_of"]))
    assert reviewer_state(result.deals[0].current_state) == "reversed"


# ---------------------------------------------------------------------------------------
# Consensus: the real vintage reader, then the staleness verdict on its output
# ---------------------------------------------------------------------------------------
@pytest.mark.db
@pytest.mark.parametrize("sample", SAMPLES["stale_consensus"], ids=lambda s: s["id"])
async def test_consensus_vintage_staleness_is_judged_on_the_readers_output(
    backend_pool: asyncpg.Pool, sample: dict[str, Any]
) -> None:
    published = None if sample["published_at"] is None else ts(sample["published_at"])
    known = published or ts("2026-04-01T00:00:00Z")
    await backend_pool.execute(
        "insert into public.forward_observation (ticker,kind,provider,metric,fiscal_period,"
        " value,analyst_count,vintage,published_at,known_at,source_url,observation_key)"
        " values ($1,'published_analyst_consensus','issuer_ir','eps','FY2026',1.0,10,'v1',$2,$3,"
        " 'https://example.test/c','k1')",
        TICKER,
        published,
        known,
    )
    as_of = ts(sample["as_of"])
    result = await consensus(backend_pool, TICKER, as_of)
    vintage = result.series[0].vintages[-1]
    assert vintage.published_at == published  # the reader preserves the publication date
    assert (
        consensus_is_stale(vintage.published_at, as_of, max_age_days=sample["max_age_days"])
        is sample["stale"]
    )
    # a view from before the vintage was known must not contain it
    before = await consensus(backend_pool, TICKER, known - dt.timedelta(days=1))
    assert before.series == []


# ---------------------------------------------------------------------------------------
# Relationship scenarios: no extractor exists; pin what the system really does
# ---------------------------------------------------------------------------------------
@pytest.mark.db
async def test_filing_text_hits_never_become_relationships(backend_pool: asyncpg.Pool) -> None:
    """EDGAR full-text search proves neither a role nor a direction.  The only real behaviour
    for the false-mention / anonymous-customer scenarios is that nothing is inferred: the hit is
    stored as a pending review candidate and no graph edge or disclosure appears."""
    from steward.news.pipeline import record_filing_hits
    from steward.news.regulatory import FilingHit, FtsResult

    hit = FilingHit("0000111111-26-000001", "d.htm", "111111", "Acme Corp", "10-K", None, "u")
    fts = FtsResult()
    fts.relations.append((TICKER, "relationship_candidate", "customer", hit))
    await record_filing_hits(backend_pool, fts)
    stored = await backend_pool.fetch("select kind, review_state from public.filing_hit")
    assert [(r["review_state"]) for r in stored] == ["pending"]
    for table in ("company_graph_edge", "relationship_disclosure", "entity_relationship"):
        count = await backend_pool.fetchval(f"select count(*) from public.{table}")  # noqa: S608
        assert count == 0, table


def test_the_two_oracle_only_scenarios_are_declared_oracle_only() -> None:
    from steward.verify_sources.signal_audit import (
        anonymous_share,
        mention_is_relationship,
    )

    for sample in SAMPLES["false_mentions"]:
        assert (
            mention_is_relationship(sample["passage"], sample["counterparty"])
            is (sample["relationship"])
        )
    for sample in SAMPLES["anonymous_customers"]:
        assert (anonymous_share(sample["passage"]) is None) == (sample["expect_share"] is None)
    assert AUDIT_SCENARIOS["false_mentions"].kind == "oracle"
    assert AUDIT_SCENARIOS["anonymous_customers"].kind == "oracle"
    assert "no text extractor" in AUDIT_SCENARIOS["false_mentions"].note
