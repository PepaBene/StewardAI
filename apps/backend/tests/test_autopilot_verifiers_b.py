"""The second look on the graph, inbox and thesis kinds: the pure rules (no database), then the
stored-row readers against the real tables (the `db` tests skip without a database)."""

import datetime as dt
import json
from typing import Any

import asyncpg
import pytest

from steward.autopilot import ledger
from steward.autopilot.judge import Candidate
from steward.autopilot.kinds import graph, inbox
from steward.autopilot.kinds import thesis as tk
from steward.config.store import save_config
from steward.config.thesis import ThesesConfig, ThesisConfig
from steward.graph.decisions import decide_edge
from steward.thesis.proposal import FieldChange, ProposalField, ProposedLine
from tests.conftest import MigratedDatabase
from tests.test_autopilot_graph import NOTE, TICKER, edge, suggestion
from tests.test_autopilot_thesis import OWNER, proposal, seed
from tests.test_phase5 import seed_holding


def auto(
    kind: str, ref: str, decision: str, payload: dict[str, Any] | None = None
) -> ledger.AutoDecision:
    return ledger.AutoDecision(
        id=1, kind=kind, subject_ref=ref, ticker=None, decision=decision, confidence=0.9,
        decided_by="model:test", summary="s", evidence={}, status="applied",
        revert_payload=payload, sampled=False, review=None, reviewed_by=None,
        reviewed_at=None, decided_at=dt.datetime.now(dt.UTC),
    )  # fmt: skip


# ======================================================================== graph: pure rules


def test_mentions_looks_for_a_distinctive_word_of_the_name() -> None:
    assert graph.mentions("Example and Alpha signed a pact.", "Alpha Corp")
    assert graph.mentions("Société Générale est partenaire", "Societe Generale S.A.")
    assert not graph.mentions("A pact was signed with a supplier.", "Alpha Corp")
    # Nothing to look for in an identifier or a name made of filler words.
    assert graph.mentions("anything", "5493001KJTIIGC8Y1R12")
    assert graph.mentions("anything", "The Group Holding Company")


def test_link_kinds_that_exclude_each_other_block_a_confirmation() -> None:
    parent = [{"edge_type": "parent"}]
    assert graph.link_problems("confirm", "subsidiary", parent)
    assert graph.link_problems("confirm", "competitor", parent)
    assert graph.link_problems("confirm", "acquired", [{"edge_type": "acquirer"}])
    assert graph.link_problems("confirm", "ultimate_parent", [{"edge_type": "subsidiary"}])
    # Compatible kinds, a twin of the same kind and an empty neighbourhood are all fine.
    assert graph.link_problems("confirm", "partner", parent) == []
    assert graph.link_problems("confirm", "parent", parent) == []
    assert graph.link_problems("confirm", "supplier", []) == []


def test_rejecting_must_not_take_a_confirmed_link_of_the_same_kind_with_it() -> None:
    confirmed = [{"edge_type": "supplier"}]
    found = graph.link_problems("reject", "supplier", confirmed)
    assert found and "also reject" in found[0]
    assert graph.link_problems("reject", "customer", confirmed) == []
    assert graph.link_problems("reject", "parent", [{"edge_type": "ultimate_parent"}])
    assert graph.link_problems("defer", "supplier", confirmed) == []


def edge_row(**kw: Any) -> dict[str, Any]:
    return {
        "holding": "Example Inc.", "object_name": "Alpha Corp", "object_key_norm": "name:alpha",
        "edge_type": "partner", "citation": "Example and Alpha signed a pact.", **kw,
    }  # fmt: skip


def test_edge_confirmation_needs_a_citation_that_names_the_counterparty() -> None:
    assert graph.edge_problems("confirm", edge_row(), []) == []
    assert graph.edge_problems("confirm", edge_row(citation=None), []) == [
        "the cited passage is missing"
    ]
    found = graph.edge_problems("confirm", edge_row(citation="A pact was signed."), [])
    assert found == ["the cited text does not mention Alpha Corp"]
    # A rejection does not need the counterparty to be named.
    assert graph.edge_problems("reject", edge_row(citation="A pact was signed."), []) == []


def test_edge_may_not_point_from_the_holding_to_itself() -> None:
    own = edge_row(object_name="Example", object_key_norm="name:example", citation="Example.")
    assert graph.edge_problems("confirm", own, []) == ["the link points from the holding to itself"]
    assert graph.edge_problems("confirm", edge_row(holding=None), []) == []


def event_row(**kw: Any) -> dict[str, Any]:
    return {"citation": "Example agreed to acquire Target.", "event_state": "announced", **kw}


def test_event_needs_its_quote_a_known_state_and_no_opposite_ending() -> None:
    assert graph.event_problems("confirm", event_row(), []) == []
    assert graph.event_problems("confirm", event_row(citation=" "), []) == [
        "the quoted evidence is missing"
    ]
    assert graph.event_problems("confirm", event_row(event_state="unknown"), []) == [
        "the event state is unknown"
    ]
    closed = event_row(event_state="closed")
    assert graph.event_problems("confirm", closed, ["terminated"])
    assert graph.event_problems("confirm", event_row(event_state="reversed"), ["closed"])
    assert graph.event_problems("confirm", closed, ["announced", "closed"]) == []
    assert graph.event_problems("reject", closed, ["terminated"]) == []


def test_a_reported_share_must_be_printed_in_the_passage() -> None:
    assert graph.share_in_passage(0.21, "Customer A accounted for 21% of revenue.")
    assert graph.share_in_passage(0.215, "about 21.5 percent of sales")
    assert graph.share_in_passage(0.215, "about 21,5 % of sales")
    assert graph.share_in_passage(None, "no number at all")
    assert not graph.share_in_passage(0.21, "Customer A accounted for 211% of revenue.")
    assert not graph.share_in_passage(0.21, "Customer A accounted for 21.7% of revenue.")
    assert not graph.share_in_passage(0.21, "Customer A accounted for 12% of revenue.")


def disclosure_row(**kw: Any) -> dict[str, Any]:
    return {
        "relationship": "customer", "direction": "issuer_sells_to",
        "counterparty_name": "Big Buyer Inc", "anonymous_counterparty": False,
        "reported_share": 0.21, "share_basis": "revenue",
        "passage": "Big Buyer accounted for 21% of revenue.", **kw,
    }  # fmt: skip


def test_disclosure_confirmation_rules() -> None:
    assert graph.disclosure_problems("confirm", disclosure_row(), []) == []
    assert graph.disclosure_problems("reject", disclosure_row(passage=None), []) == []
    assert graph.disclosure_problems("confirm", disclosure_row(passage=""), []) == [
        "the cited passage is missing"
    ]
    wrong_way = graph.disclosure_problems(
        "confirm", disclosure_row(direction="issuer_buys_from"), []
    )
    assert wrong_way and "cannot have the direction" in wrong_way[0]
    named = graph.disclosure_problems(
        "confirm", disclosure_row(passage="A retailer took 21% of revenue."), []
    )
    assert named == ["the passage does not mention Big Buyer Inc"]
    # An anonymous counterparty has no name to find.
    anonymous = disclosure_row(
        counterparty_name=None, anonymous_counterparty=True, passage="Customer A took 21%."
    )
    assert graph.disclosure_problems("confirm", anonymous, []) == []
    missing = graph.disclosure_problems(
        "confirm", disclosure_row(passage="Big Buyer is a large customer."), []
    )
    assert missing == ["the reported share is not in the passage"]


def test_disclosures_of_one_period_must_agree_and_stay_within_100_percent() -> None:
    row = disclosure_row()
    same_other_share = {**row, "reported_share": 0.30}
    assert graph.disclosure_problems("confirm", row, [same_other_share]) == [
        "a confirmed disclosure of the same company reports another share"
    ]
    other_direction = {**row, "direction": "issuer_buys_from"}
    assert "another direction" in graph.disclosure_problems("confirm", row, [other_direction])[0]
    twin = {**row, "counterparty_name": "BIG BUYER, Inc."}
    assert graph.disclosure_problems("confirm", row, [twin]) == []
    # Different companies: 21% + 60% fits, 21% + 85% does not (same basis only).
    fits = {**row, "counterparty_name": "Other", "reported_share": 0.60}
    too_big = {**row, "counterparty_name": "Other", "reported_share": 0.85}
    other_basis = {**too_big, "share_basis": "cost of sales"}
    assert graph.disclosure_problems("confirm", row, [fits]) == []
    assert graph.disclosure_problems("confirm", row, [too_big]) == [
        "the reported shares for the period would add up to more than 100%"
    ]
    assert graph.disclosure_problems("confirm", row, [other_basis]) == []


def test_relationship_rules() -> None:
    rel = {
        "holding": "Example Inc.", "subject_name": "Example Holdings plc",
        "object_key_norm": "name:alpha", "type": "competitor",
    }  # fmt: skip
    assert graph.relationship_problems("confirm", rel, []) == []
    assert graph.relationship_problems("confirm", rel, [{"edge_type": "subsidiary"}])
    own = {**rel, "object_key_norm": "name:example holdings"}
    assert graph.relationship_problems("confirm", own, []) == [
        "the link points from the holding to itself"
    ]
    assert graph.relationship_problems("reject", own, []) == []
    assert graph.relationship_problems("reject", rel, [{"edge_type": "competitor"}])


def test_recorded_side_effects_of_a_confirmation_must_still_be_there() -> None:
    payload = {
        "profile": {"ticker": "X", "field": "competitors", "name": "Rival Corp"},
        "alias": {"ticker": "X", "alias": "Rival Corp"},
    }
    assert graph.profile_problems(payload, ["Rival Corp"], True) == []
    assert graph.profile_problems(payload, [], True) == [
        "Rival Corp is no longer in the holding's competitors"
    ]
    assert graph.profile_problems(payload, ["Rival Corp"], False) == [
        "the subsidiary alias is gone"
    ]
    assert graph.profile_problems({"profile": None, "alias": None}, [], False) == []


# ===================================================================== graph: stored rows


def cand(kind: str, row_id: int) -> Candidate:
    return Candidate(f"{kind}:{row_id}", TICKER, "", {})


@pytest.mark.db
async def test_edge_verify_reads_the_stored_citation_and_neighbours(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    verify = graph.graph_edge.verify
    assert verify is not None
    good = await edge(pool, "Alpha Corp", "https://x.example/a", "Example and Alpha signed a pact.")
    assert await verify(pool, cand("graph_edge", good), "confirm") == []
    # The passage never names the counterparty.
    vague = await edge(pool, "Beta Corp", "https://x.example/b", "A pact was signed in May.")
    assert await verify(pool, cand("graph_edge", vague), "confirm") == [
        "the cited text does not mention Beta Corp"
    ]
    assert await verify(pool, cand("graph_edge", vague), "defer") == []
    # A confirmed parent link contradicts a suggested subsidiary one to the same company.
    parent = await edge(
        pool, "Delta Corp", "https://x.example/d1", "Delta is the parent.", edge_type="parent"
    )
    await decide_edge(pool, parent, "confirm", reason="Read the filing")
    child = await edge(
        pool, "Delta Corp", "https://x.example/d2", "Delta is a subsidiary.", edge_type="subsidiary"
    )
    found = await verify(pool, cand("graph_edge", child), "confirm")
    assert found and "contradicts" in found[0]
    # Rejecting a twin of a confirmed link would reject the confirmed one too.
    twin = await edge(
        pool, "Delta Corp", "https://x.example/d3", "Delta again.", edge_type="parent"
    )
    found = await verify(pool, cand("graph_edge", twin), "reject")
    assert found and "also reject" in found[0]
    assert await verify(pool, cand("graph_edge", 10**9), "confirm") == ["the edge no longer exists"]


@pytest.mark.db
async def test_edge_recheck_follows_the_status_the_autopilot_gave(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    recheck = graph.graph_edge.recheck
    assert recheck is not None
    row_id = await edge(pool, "Gamma Corp", "https://x.example/g", "Gamma is a partner.")
    ref = f"graph_edge:{row_id}"
    assert await recheck(pool, auto("graph_edge", ref, "confirm")) == ["the item is now suggested"]
    await decide_edge(pool, row_id, "confirm", reason=NOTE)
    assert await recheck(pool, auto("graph_edge", ref, "confirm")) == []
    assert await recheck(pool, auto("graph_edge", ref, "reject")) == ["the item is now confirmed"]
    assert await recheck(pool, auto("graph_edge", ref, "defer")) == []
    # The cited text disappears afterwards: the confirmation no longer rests on anything.
    await pool.execute("update company_graph_edge set passage = null where id = $1", row_id)
    assert await recheck(pool, auto("graph_edge", ref, "confirm")) == [
        "the cited passage is missing"
    ]
    gone = auto("graph_edge", "graph_edge:999999999", "confirm")
    assert await recheck(pool, gone) == ["the item no longer exists"]


@pytest.mark.db
async def test_relationship_verify_and_recheck(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    assert graph.relationship.verify is not None and graph.relationship.recheck is not None
    rel_id = await suggestion(pool, "competitor", "Rival Corp", "https://wikidata.example/Q1")
    rel = cand("relationship", rel_id)
    assert await graph.relationship.verify(pool, rel, "confirm") == []
    # A confirmed subsidiary edge to the same company excludes a competitor.
    sub = await edge(
        pool, "Rival Corp", "https://x.example/r", "Rival is ours.", edge_type="subsidiary"
    )
    await decide_edge(pool, sub, "confirm", reason="Read the filing")
    found = await graph.relationship.verify(pool, rel, "confirm")
    assert found and "contradicts" in found[0]
    ref = f"relationship:{rel_id}"
    assert await graph.relationship.recheck(pool, auto("relationship", ref, "confirm")) == [
        "the relationship is now suggested"
    ]


# ======================================================================== inbox: pure rules


def item_row(**kw: Any) -> dict[str, Any]:
    return {
        "ticker": "AAPL_US_EQ", "status": "new",
        "body": json.dumps({"statements": [{"text": "Apple cut its guidance."}]}), **kw,
    }  # fmt: skip


def job_action(kind: str = "earnings_check", **params: Any) -> dict[str, Any]:
    return {"kind": kind, "params": {"ticker": "AAPL_US_EQ", **params}, "reason": "r"}


def test_a_proposed_job_must_fit_the_item_it_came_from() -> None:
    ok = inbox.action_problems(item_row(), job_action(), known_company=True)
    assert ok == []
    assert inbox.action_problems(item_row(status="dismissed"), job_action(), known_company=True)
    assert inbox.action_problems(item_row(), None, known_company=True) == [
        "the item proposes no action"
    ]
    assert inbox.action_problems(item_row(), job_action("wire_money"), known_company=True)
    other = job_action(ticker="MSFT_US_EQ")
    assert inbox.action_problems(item_row(), other, known_company=True) == [
        "the job names another company than the item is about"
    ]
    assert inbox.action_problems(item_row(), job_action(), known_company=False) == [
        "the company is neither held nor in a thesis"
    ]
    research = job_action("research", profile="deep")
    assert inbox.action_problems(item_row(), research, known_company=True) == []
    missing = inbox.action_problems(item_row(), research, known_company=True, profile_known=False)
    assert missing == ["there is no research profile 'deep'"]
    bare = item_row(body=json.dumps({"statements": [{"text": "  "}]}))
    assert inbox.action_problems(bare, job_action(), known_company=True) == [
        "no stated fact supports the proposal"
    ]


def test_a_sync_needs_no_company() -> None:
    sync = {"kind": "portfolio_sync", "params": {}, "reason": "r"}
    assert inbox.action_problems(item_row(ticker=None), sync, known_company=False) == []


def test_an_applied_action_still_holds_while_its_state_and_job_do() -> None:
    fine = {"status": "succeeded", "error": None}
    assert inbox.action_recheck_problems("confirm", "confirmed", fine) == []
    assert inbox.action_recheck_problems("confirm", "confirmed", fine | {"status": "queued"}) == []
    failed = {"status": "failed", "error": "boom"}
    assert inbox.action_recheck_problems("confirm", "confirmed", failed) == [
        "the job it started failed: boom"
    ]
    assert inbox.action_recheck_problems("confirm", "confirmed", None) == [
        "the job it started is gone"
    ]
    assert inbox.action_recheck_problems("confirm", "declined", fine) == [
        "the action is now declined"
    ]
    assert inbox.action_recheck_problems("decline", "declined", None) == []
    assert inbox.action_recheck_problems("decline", "proposed", None) == [
        "the action is now proposed"
    ]


def test_a_suggested_note_must_be_new_and_for_a_known_company() -> None:
    mind_fx = "Mind FX: more than half of revenue is billed in euros."
    assert inbox.note_problems("accept", mind_fx, ["Services margin is the thesis."],
                               known_company=True) == []  # fmt: skip
    assert inbox.note_problems("decline", "", [], known_company=False) == []
    assert inbox.note_problems("accept", "  ", [], known_company=True) == [
        "the suggested note is empty"
    ]
    assert inbox.note_problems("accept", "x" * 2001, [], known_company=True)
    assert inbox.note_problems("accept", mind_fx, [], known_company=False) == [
        "the note is for a company that is neither held nor in a thesis"
    ]
    same_words = "mind FX -- more than half of revenue is billed in euros"
    assert inbox.note_problems("accept", mind_fx, [same_words], known_company=True) == [
        "an existing note already says this"
    ]
    longer = f"Standing fact. {mind_fx} Review each quarter."
    assert inbox.note_problems("accept", mind_fx, [longer], known_company=True)
    # A short existing note inside a long suggestion says too little to count as a repeat.
    assert inbox.note_problems("accept", mind_fx, ["Mind FX."], known_company=True) == []


def test_an_applied_note_decision_still_holds_while_the_suggestion_and_note_do() -> None:
    live = {"active": True}
    assert inbox.note_recheck_problems("accept", "accepted", live, expects_note=True) == []
    assert inbox.note_recheck_problems("decline", "declined", None, expects_note=False) == []
    assert inbox.note_recheck_problems("accept", "suggested", live, expects_note=True) == [
        "the suggestion is now suggested"
    ]
    assert inbox.note_recheck_problems("accept", "accepted", None, expects_note=True) == [
        "the note it created was deleted"
    ]
    assert inbox.note_recheck_problems(
        "accept", "accepted", {"active": False}, expects_note=True
    ) == ["the note it created was switched off"]


# ===================================================================== inbox: stored rows


async def add_item(
    pool: asyncpg.Pool,
    key: str,
    ticker: str | None,
    *,
    action: dict[str, Any] | None = None,
    note: str | None = None,
) -> int:
    body = {"statements": [{"text": "Apple cut its guidance.", "source_ids": ["S1"]}]}
    if note:
        body |= {"suggested_note": note, "note_status": "suggested"}
    return int(
        await pool.fetchval(
            "insert into public.inbox_item (trigger_key, trigger_kind, trigger_ref, ticker,"
            " severity, headline, body, proposed_action, action_status) values ($1, 'alert', 1,"
            " $2, 3, 'Guidance cut', $3::jsonb, $4::jsonb, $5) returning id",
            key,
            ticker,
            json.dumps(body),
            json.dumps(action) if action else None,
            "proposed" if action else None,
        )
    )


@pytest.mark.db
async def test_action_verify_reads_the_stored_item(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    assert inbox.INBOX_ACTION.verify is not None
    verify = inbox.INBOX_ACTION.verify
    await seed_holding(pool)
    good = await add_item(pool, "alert:1", "AAPL_US_EQ", action=job_action())
    c = Candidate(f"inbox_action:{good}", "AAPL_US_EQ", "", {"item_id": good})
    assert await verify(pool, c, "confirm") == []
    assert await verify(pool, c, "decline") == []
    mismatch = await add_item(pool, "alert:2", "AAPL_US_EQ", action=job_action(ticker="MSFT_US_EQ"))
    c2 = Candidate(f"inbox_action:{mismatch}", "AAPL_US_EQ", "", {"item_id": mismatch})
    assert await verify(pool, c2, "confirm") == [
        "the job names another company than the item is about"
    ]
    stranger = await add_item(pool, "alert:3", "ZZZ_US_EQ", action=job_action(ticker="ZZZ_US_EQ"))
    c3 = Candidate(f"inbox_action:{stranger}", "ZZZ_US_EQ", "", {"item_id": stranger})
    assert await verify(pool, c3, "confirm") == ["the company is neither held nor in a thesis"]
    c4 = Candidate("inbox_action:999999999", None, "", {"item_id": 999999999})
    assert await verify(pool, c4, "confirm") == ["the inbox item no longer exists"]


@pytest.mark.db
async def test_action_recheck_sees_a_failed_job(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    assert inbox.INBOX_ACTION.recheck is not None
    recheck = inbox.INBOX_ACTION.recheck
    await seed_holding(pool)
    item = await add_item(pool, "alert:10", "AAPL_US_EQ", action=job_action())
    # The queue extension is not in every test database, so the job is recorded by hand.
    job = await pool.fetchval(
        "insert into public.job (job_type, trigger, status) values ('earnings_check', 'event',"
        " 'queued') returning id"
    )
    await pool.execute(
        "update public.inbox_item set action_status = 'confirmed', status = 'actioned',"
        " action_job_id = $2 where id = $1",
        item,
        job,
    )
    decided = auto("inbox_action", f"inbox_action:{item}", "confirm")
    assert await recheck(pool, decided) == []
    await pool.execute("update public.job set status = 'failed', error = 'boom' where id = $1", job)
    assert await recheck(pool, decided) == ["the job it started failed: boom"]
    assert await recheck(pool, auto("inbox_action", "inbox_action:999999999", "confirm")) == [
        "the inbox item no longer exists"
    ]


@pytest.mark.db
async def test_note_verify_and_recheck_read_the_stored_notes(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    assert inbox.AGENT_NOTE.verify is not None and inbox.AGENT_NOTE.recheck is not None
    await seed_holding(pool)
    text = "Services margin above 70 percent is the heart of the thesis."
    await pool.execute(
        "insert into public.agent_note (ticker, text) values ('AAPL_US_EQ', $1)", text
    )
    repeat = await add_item(pool, "alert:20", "AAPL_US_EQ", note=text.upper())
    fresh = await add_item(pool, "alert:21", "AAPL_US_EQ", note="Watch China export share.")
    c_repeat = Candidate(f"agent_note:{repeat}", "AAPL_US_EQ", "", {"item_id": repeat})
    c_fresh = Candidate(f"agent_note:{fresh}", "AAPL_US_EQ", "", {"item_id": fresh})
    assert await inbox.AGENT_NOTE.verify(pool, c_repeat, "accept") == [
        "an existing note already says this"
    ]
    assert await inbox.AGENT_NOTE.verify(pool, c_repeat, "decline") == []
    assert await inbox.AGENT_NOTE.verify(pool, c_fresh, "accept") == []

    payload = await inbox.AGENT_NOTE.apply(pool, c_fresh, "accept", "n")
    assert payload is not None
    decided = auto("agent_note", c_fresh.ref, "accept", payload)
    assert await inbox.AGENT_NOTE.recheck(pool, decided) == []
    await pool.execute("delete from public.agent_note where id = $1", payload["note_id"])
    assert await inbox.AGENT_NOTE.recheck(pool, decided) == ["the note it created was deleted"]


# ====================================================================== thesis: pure rules


def change(field: ProposalField, texts: list[str], ids: list[str] | None = None) -> FieldChange:
    return FieldChange(
        field=field,
        kind="add",
        before=[],
        after=[ProposedLine(text=t, evidence_ids=["s1"] if ids is None else ids) for t in texts],
    )


def test_a_proposal_must_fit_the_report_and_the_schema() -> None:
    base = proposal(1, ("exit_conditions", "pre_mortem"))
    fields: list[ProposalField] = ["exit_conditions", "pre_mortem"]
    assert tk.proposal_problems("AAPL", base, fields, {"s1"}) == []

    def check(changes: list[FieldChange], ticker: str = "AAPL", **kw: Any) -> list[str]:
        edited = base.model_copy(update={"changes": changes, **kw})
        return tk.proposal_problems(ticker, edited, fields, {"s1"})

    assert check([change("exit_conditions", ["a"])]) == []
    assert check([change("exit_conditions", ["a"])], ticker="MSFT") == [
        "the report is about MSFT but the proposal edits AAPL"
    ]
    assert check([change("exit_conditions", ["a"])], mode="research_only")
    assert check([change("exit_conditions", [" "])]) == [
        "exit_conditions: a proposed line is blank"
    ]
    assert check([change("exit_conditions", ["a", "a"])]) == [
        "exit_conditions: a line is proposed twice"
    ]
    assert check([change("pre_mortem", ["a", "b", "c", "d"])]) == [
        "pre_mortem: 4 reasons, the thesis holds 3"
    ]
    assert check([change("exit_conditions", ["a"], ids=[])]) == [
        "exit_conditions: a line cites no source"
    ]
    assert check([change("exit_conditions", ["a"], ids=["s1", "s9"])]) == [
        "exit_conditions: cites s9, not in the report"
    ]
    # Fields the autopilot is not applying are not judged.
    assert check([change("why_owned", [" "])]) == []


def test_the_saved_theses_version_must_stay_active_unless_someone_explains_it() -> None:
    assert tk.thesis_drift(5, 5, 0) == []
    assert tk.thesis_drift(None, 9, 0) == []
    assert tk.thesis_drift(5, 7, 1) == []  # the owner saved after it
    found = tk.thesis_drift(5, 7, 0)
    assert len(found) == 1 and "#5" in found[0] and "#7" in found[0]


# ==================================================================== thesis: stored rows


@pytest.mark.db
async def test_thesis_verify_reads_the_stored_report(
    backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> None:
    pool = backend_pool
    report, _ = await seed(pool, ("exit_conditions", "pre_mortem"))
    cand_ = (await tk.KIND.load(pool, 10))[0]
    assert tk.KIND.verify is not None
    verify = tk.KIND.verify
    # The seeded report cites "s1" but stores no sources.
    assert await verify(pool, cand_, "accept") == [
        "exit_conditions: cites s1, not in the report",
        "pre_mortem: cites s1, not in the report",
    ]
    # The backend role may not edit a report, so the test edits it as the database owner.
    admin = await asyncpg.connect(migrated_db.admin_url)
    try:
        await admin.execute(
            'update public.report set body = body || \'{"sources": [{"id": "s1"}]}\'::jsonb'
            " where id = $1",
            report,
        )
        assert await verify(pool, cand_, "accept") == []
        assert await verify(pool, cand_, "reject") == []
        await admin.execute("update public.report set ticker = 'MSFT' where id = $1", report)
        assert await verify(pool, cand_, "accept") == [
            "the report is about MSFT but the proposal edits AAPL"
        ]
        await admin.execute("delete from public.report where id = $1", report)
    finally:
        await admin.close()
    assert await verify(pool, cand_, "accept") == ["the research report no longer exists"]


@pytest.mark.db
async def test_thesis_recheck_wants_the_saved_version_or_an_owner_edit(
    backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> None:
    pool = backend_pool
    assert tk.KIND.recheck is not None
    recheck = tk.KIND.recheck
    report, _ = await seed(pool, ("exit_conditions",))
    cand_ = (await tk.KIND.load(pool, 10))[0]
    payload = await tk.KIND.apply(pool, cand_, "accept", "ok")
    assert payload is not None
    decided = auto("thesis_proposal", cand_.ref, "accept", payload)
    assert await recheck(pool, decided) == []
    assert await recheck(pool, auto("thesis_proposal", cand_.ref, "reject")) == []

    async def save(why: str, by: str | None) -> None:
        async with pool.acquire() as conn:
            await save_config(
                conn,
                "theses",
                ThesesConfig(theses=[ThesisConfig(ticker="AAPL", why_owned=why)]),
                created_by=by,
            )

    await save("rewritten", None)  # a save nobody owns
    found = await recheck(pool, decided)
    assert len(found) == 1 and "no owner edit explains it" in found[0]
    await save("rewritten by the owner", OWNER)  # the owner's later edit explains it
    assert await recheck(pool, decided) == []
    admin = await asyncpg.connect(migrated_db.admin_url)
    try:
        await admin.execute("delete from public.report where id = $1", report)
    finally:
        await admin.close()
    assert await recheck(pool, decided) == ["the research report it came from no longer exists"]
