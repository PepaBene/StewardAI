"""The fact and web-fact judges: the other-source comparison, the rules, prompts, outcome
mapping, and (with a database) apply, revert and outcome against the real tables."""

import datetime as dt
import hashlib
from decimal import Decimal
from typing import Any

import asyncpg
import pytest

from steward.autopilot import ledger
from steward.autopilot.judge import ApplyError, Candidate
from steward.autopilot.kinds import facts
from steward.autopilot.kinds.facts import (
    FACT,
    KINDS,
    WEB_FACT,
    build_opinions,
    fact_prompt,
    fact_rule,
    web_corroboration,
    web_prompt,
    web_rule,
)

TODAY = dt.date(2026, 11, 9)


# --------------------------------------------------------------------------- the registry


def test_kinds_are_well_formed() -> None:
    assert [k.name for k in KINDS] == ["fact", "web_fact"]
    for kind in KINDS:
        assert "defer" not in kind.actionable
        decisions = set(kind.output_model.model_fields["decision"].annotation.__args__)  # type: ignore[union-attr]
        assert "defer" in decisions
        assert kind.actionable < decisions
        assert kind.revert is not None and kind.outcome is not None and kind.rule is not None
    assert FACT.expire_decision is None
    assert WEB_FACT.expire_decision == "reject"


# ------------------------------------------------------------------------------- fact


def held(**kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": 7,
        "ticker": "AAPL_US_EQ",
        "metric": "revenue",
        "basis": "reported",
        "fiscal_year": 2026,
        "fiscal_period": "Q2",
        "period": "FY2026 Q2",
        "period_end": dt.date(2026, 3, 28),
        "value": Decimal("143.8"),
        "value_as_written": "143.8",
        "scale": "billions",
        "value_units": Decimal("143800000000"),
        "unit": "currency",
        "currency": "USD",
        "source_url": "https://www.apple.com/newsroom/q2.html",
        "snippet": "Revenue was 143.8 billion",
        "unit_snippet": None,
        "evidence_sha256": "a" * 64,
        "status": "quarantined",
        "status_reason": "reconciliation: mismatch",
        "checks": "[]",
        "restates_fact_id": None,
    }
    row.update(kw)
    return row


def recon(value: str, rid: int = 3) -> dict[str, Any]:
    return {
        "id": rid,
        "concept": "us-gaap:Revenues",
        "form": "10-Q",
        "accession": "0000320193-26-000010",
        "xbrl_value": Decimal(value),
        "outcome": "mismatch",
    }


def other(oid: int, units: str, url: str = "https://sec.gov/x") -> dict[str, Any]:
    return {"id": oid, "value_units": Decimal(units), "source_url": url}


def test_xbrl_within_tolerance_agrees_outside_is_an_option() -> None:
    # 143.8 billion printed: +-0.05 billion.
    agree = build_opinions(held(), recon("143830000000"), None, [])
    assert [(o.source, o.agrees, o.key) for o in agree] == [("xbrl", True, None)]
    differ = build_opinions(held(), recon("134800000000"), None, [])
    assert [(o.source, o.agrees, o.key) for o in differ] == [("xbrl", False, "xbrl")]


def test_options_are_deduplicated_keyed_and_capped() -> None:
    ops = build_opinions(
        held(),
        recon("134800000000"),
        {"id": 2, "value_units": Decimal("134800000000"), "source_url": "x"},
        [other(10, "1"), other(11, "2"), other(12, "3")],
    )
    assert [o.key for o in ops] == ["xbrl", None, "other_1", "other_2", None]
    assert all(not o.agrees for o in ops)


def test_rule_accepts_only_when_an_independent_source_agrees_and_none_differs() -> None:
    def cand(opinions: list[Any]) -> Candidate:
        return Candidate("fact:7", "AAPL_US_EQ", "", {"opinions": [o.as_json() for o in opinions]})

    agree = build_opinions(held(status="unverified"), recon("143800000000"), None, [])
    result = fact_rule(cand(agree))
    assert result is not None and result.decision == "accept"
    # A source from the held fact's own host is not independent.
    same_host = build_opinions(
        held(), None, None, [other(9, "143800000000", "https://www.apple.com/other")]
    )
    assert fact_rule(cand(same_host)) is None
    # One disagreeing source (even an unnamed third one) blocks the rule.
    mixed = build_opinions(
        held(), recon("143800000000"), None, [other(i, str(i)) for i in (10, 11, 12)]
    )
    assert fact_rule(cand(mixed)) is None
    assert fact_rule(cand([])) is None
    # The filing's tagged value disagreeing settles it: correct to the XBRL value.
    differ = build_opinions(held(), recon("134800000000"), None, [])
    ruled = fact_rule(cand(differ))
    assert ruled is not None and ruled.decision == "correct_xbrl"


def test_prompt_lists_options_by_key_and_is_capped() -> None:
    ops = build_opinions(held(snippet="x" * 9000), recon("134800000000"), None, [])
    prompt = fact_prompt(held(snippet="x" * 9000), ops, [{"check": "sanity", "detail": "too big"}])
    assert "key xbrl" in prompt and "sanity: too big" in prompt
    assert len(prompt) <= 6000


class FakePool:
    """Answers `fetchrow` / `fetchval` / `fetch` from scripted rows, in call order."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)

    async def fetchrow(self, *_: Any) -> Any:
        return self.answers.pop(0)

    fetchval = fetchrow
    fetch = fetchrow


async def test_fact_outcome_ignores_autopilot_and_maps_human_calls() -> None:
    mine = {"action": "accept", "reason": "Autopilot (model:x, 0.95): ok", "reviewed_by": None}
    assert await facts.fact_outcome(FakePool(mine), "fact:7") is None
    accepted = {
        "action": "accept",
        "reason": "Owner accepted the autopilot: ok",
        "reviewed_by": "u",
    }
    assert await facts.fact_outcome(FakePool(accepted), "fact:7") is None
    human = {"action": "reject", "reason": "wrong period", "reviewed_by": "u"}
    assert await facts.fact_outcome(FakePool(human), "fact:7") == "reject"
    assert await facts.fact_outcome(FakePool(None), "fact:7") is None


async def test_fact_outcome_maps_a_human_correction_to_the_matching_option() -> None:
    review = {"action": "correct", "reason": "xbrl", "reviewed_by": "u", "corrected_fact_id": 8}
    # review row, held fact, corrected value, recon, (no prior), others
    pool = FakePool(review, held(), Decimal("134800000000"), recon("134800000000"), [])
    assert await facts.fact_outcome(pool, "fact:7") == "correct_xbrl"
    pool = FakePool(review, held(), Decimal("1"), recon("134800000000"), [])
    assert await facts.fact_outcome(pool, "fact:7") == "correct"


# ----------------------------------------------------------------------------- web_fact


def web_row(**kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": 5,
        "ticker": "AAPL_US_EQ",
        "slot": "shares_outstanding",
        "value_text": "1234567",
        "unit": "shares",
        "period": "31 Dec 2025",
        "source_url": "https://www.reuters.com/a",
        "host": "www.reuters.com",
        "tier": "T2",
        "quote": "The company had 1,234,567 shares outstanding on 31 December 2025.",
        "reasons": "tier 2 figure without a second agreeing source",
        "confidence": 0.7,
    }
    row.update(kw)
    return row


def peer(pid: int, host: str, tier: str = "T2", value: str = "1234567") -> dict[str, Any]:
    return {"id": pid, "host": host, "tier": tier, "value_text": value}


def test_corroboration_counts_distinct_registrable_domains_of_tier_1_and_2() -> None:
    agreeing, conflicting = web_corroboration(
        "www.reuters.com",
        "1234567",
        [
            peer(1, "uk.reuters.com"),  # same registrable domain: not independent
            peer(2, "www.ft.com"),
            peer(3, "markets.ft.com"),  # same domain as the previous one
            peer(4, "wikipedia.org", "T3"),  # reference site: not corroboration
            peer(5, "www.bloomberg.com", value="999"),
        ],
    )
    assert [a["domain"] for a in agreeing] == ["ft.com"]
    assert [c["domain"] for c in conflicting] == ["bloomberg.com"]


def web_cand(**kw: Any) -> Candidate:
    row = web_row()
    agreeing, conflicting = web_corroboration(
        row["host"], row["value_text"], [peer(2, "www.ft.com")]
    )
    ev: dict[str, Any] = {
        "web_fact_id": 5,
        "slot": row["slot"],
        "value_text": row["value_text"],
        "unit": row["unit"],
        "period": row["period"],
        "source_url": row["source_url"],
        "quote": row["quote"],
        "tier": "T2",
        "domain": "reuters.com",
        "agreeing": agreeing,
        "conflicting": conflicting,
    }
    ev.update(kw)
    return Candidate("web_fact:5", "AAPL_US_EQ", "", ev)


def test_web_rule_needs_two_domains_and_a_quote_that_prints_the_value() -> None:
    result = web_rule(web_cand(), TODAY)
    assert result is not None and result.decision == "accept"
    assert web_rule(web_cand(agreeing=[]), TODAY) is None
    assert web_rule(web_cand(conflicting=[{"domain": "x.com"}]), TODAY) is None
    assert web_rule(web_cand(tier="T3"), TODAY) is None
    # The quote does not print the number: never accepted, whatever the sources say.
    assert web_rule(web_cand(quote="The company had 7,654,321 shares outstanding."), TODAY) is None
    assert web_rule(web_cand(slot="no_such_slot"), TODAY) is None
    assert web_rule(web_cand(slot="analyst_estimates"), TODAY) is None


def test_web_prompt_has_the_passage_tier_and_corroboration() -> None:
    cand = web_cand()
    prompt = web_prompt(web_row(), "Apple Inc.", cand.evidence["agreeing"], [])
    assert 'domain="www.reuters.com" tier="T2"' in prompt
    assert "agrees: ft.com" in prompt
    assert "tier 2 alone is NOT enough" in prompt
    assert len(prompt) <= 6000


async def test_web_outcome_only_reports_the_owners_call() -> None:
    owner = {"status": "approved", "decided_by": "owner:abc", "reasons": "ok"}
    assert await facts.web_outcome(FakePool(owner), "web_fact:5") == "accept"
    rejected = {"status": "rejected", "decided_by": "owner:abc", "reasons": "no"}
    assert await facts.web_outcome(FakePool(rejected), "web_fact:5") == "reject"
    mine = {"status": "approved", "decided_by": "ai:autopilot", "reasons": "x"}
    assert await facts.web_outcome(FakePool(mine), "web_fact:5") is None
    waiting = {"status": "needs_review", "decided_by": "ai:m", "reasons": "x"}
    assert await facts.web_outcome(FakePool(waiting), "web_fact:5") is None


# --------------------------------------------------------------------- against the database

OWNER = "00000000-0000-0000-0000-00000000000f"


async def _fact(
    pool: asyncpg.Pool, ticker: str, value: str, status: str = "quarantined", sha_tag: str = "a"
) -> int:
    sha = hashlib.sha256(f"{ticker}:{sha_tag}".encode()).hexdigest()
    path = f"evidence/{sha}.html"
    await pool.execute(
        "insert into public.evidence (sha256, storage_path, source_url, content_type, byte_size)"
        " values ($1, $2, 'https://ir.test/x', 'text/html', 10) on conflict do nothing",
        sha,
        path,
    )
    fact_id: int = await pool.fetchval(
        "insert into public.fact (ticker, metric, basis, fiscal_year, fiscal_period, period,"
        " period_end, value, value_as_written, scale, unit, currency, source_url, snippet,"
        " evidence_sha256, evidence_path, status, status_reason, checks)"
        " values ($1, 'revenue', 'reported', 2026, 'Q2', 'FY2026 Q2', '2026-03-28', $2::numeric,"
        " $3, 'millions', 'currency', 'USD', 'https://ir.test/x', 'Revenue was ' || $3, $4, $5,"
        " $6, 'held', '[]'::jsonb) returning id",
        ticker,
        Decimal(value),
        value,
        sha,
        path,
        status,
    )
    return fact_id


@pytest.mark.db
async def test_fact_accept_revert_and_outcome(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    fact_id = await _fact(pool, "DBT_US_EQ", "143800")
    loaded = [c for c in await facts.fact_load(pool, 50) if c.ref == f"fact:{fact_id}"]
    assert len(loaded) == 1
    assert await facts.fact_is_open(pool, [f"fact:{fact_id}"]) == {f"fact:{fact_id}"}

    payload = await facts.fact_apply(pool, loaded[0], "accept", "Autopilot (model:x, 0.95): ok")
    assert payload is not None and payload["new_status"] == "verified"
    assert await facts.fact_is_open(pool, [f"fact:{fact_id}"]) == set()
    assert await facts.fact_outcome(pool, f"fact:{fact_id}") is None  # our own decision
    with pytest.raises(ApplyError):
        await facts.fact_apply(pool, loaded[0], "reject", "Autopilot (model:x, 0.95): again")

    await facts.fact_revert(pool, payload)
    assert await facts.fact_is_open(pool, [f"fact:{fact_id}"]) == {f"fact:{fact_id}"}
    with pytest.raises(ApplyError):  # nothing left to revert
        await facts.fact_revert(pool, payload)


@pytest.mark.db
async def test_fact_correct_uses_a_stored_value_and_a_human_call_is_final(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    held_id = await _fact(pool, "DBC_US_EQ", "143800")
    await _fact(pool, "DBC_US_EQ", "134800", status="verified", sha_tag="b")
    cand = next(c for c in await facts.fact_load(pool, 50) if c.ref == f"fact:{held_id}")
    keys = [o["key"] for o in cand.evidence["opinions"]]
    assert keys == ["other_1"]
    with pytest.raises(ApplyError):  # a key that names no stored value
        await facts.fact_apply(pool, cand, "correct_xbrl", "Autopilot (model:x, 0.95): x")

    payload = await facts.fact_apply(pool, cand, "correct_other_1", "Autopilot (model:x, 0.95): x")
    assert payload is not None and payload["corrected_fact_id"] is not None
    corrected = await pool.fetchval(
        "select value_units from public.fact where id = $1", payload["corrected_fact_id"]
    )
    assert corrected == Decimal("134800000000")

    await facts.fact_revert(pool, payload)
    assert (
        await pool.fetchval("select status from public.fact where id = $1", held_id)
        == "quarantined"
    )

    # The owner reviews it by hand afterwards: the autopilot must not touch it again.
    from steward.earnings.review import review_fact

    await review_fact(pool, held_id, "reject", "wrong period", owner_id=OWNER)
    assert await facts.fact_outcome(pool, f"fact:{held_id}") == "reject"
    with pytest.raises(ApplyError):
        await facts.fact_apply(pool, cand, "accept", "Autopilot (model:x, 0.95): x")


@pytest.mark.db
async def test_a_correction_is_not_the_owners_and_is_not_flagged_as_restated(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    held_id = await _fact(pool, "DBR_US_EQ", "143800")
    source_id = await _fact(pool, "DBR_US_EQ", "134800", status="verified", sha_tag="b")
    cand = next(c for c in await facts.fact_load(pool, 50) if c.ref == f"fact:{held_id}")
    assert await facts.fact_verify(pool, cand, "correct_other_1") == []

    payload = await facts.fact_apply(pool, cand, "correct_other_1", "Autopilot (model:x, 0.95): x")
    assert payload is not None
    made = await pool.fetchrow(
        "select origin, status_reason from public.fact where id = $1", payload["corrected_fact_id"]
    )
    # Reconciliation skips owner-origin facts; this one is the model's and must still be checked.
    assert made["origin"] == "extraction" and made["status_reason"].startswith("autopilot")

    row = ledger.AutoDecision(
        id=1,
        kind="fact",
        subject_ref=cand.ref,
        ticker=cand.ticker,
        decision="correct_other_1",
        confidence=0.95,
        decided_by="model:x",
        summary="s",
        evidence=cand.evidence,
        status="applied",
        revert_payload=payload,
        sampled=False,
        review=None,
        reviewed_by=None,
        reviewed_at=None,
        decided_at=dt.datetime.now(dt.UTC),
    )
    assert await facts.fact_recheck(pool, row) == []  # its own correction is not a restatement

    # Another fact restating it later still counts.
    await pool.execute(
        "insert into public.fact (ticker, metric, basis, fiscal_year, fiscal_period, period,"
        " period_end, value, value_as_written, scale, unit, currency, source_url, snippet,"
        " evidence_sha256, evidence_path, status, status_reason, checks, restates_fact_id)"
        " select ticker, metric, basis, fiscal_year, fiscal_period, period, period_end, 150000,"
        " '150000', scale, unit, currency, source_url, snippet, evidence_sha256, evidence_path,"
        " 'unverified', 'restated', '[]'::jsonb, id from public.fact where id = $1",
        held_id,
    )
    assert await facts.fact_recheck(pool, row) == ["a later filing restated it"]

    # A correction needs its source fact to be verified when it is applied, not as judged.
    await pool.execute("update public.fact set status = 'rejected' where id = $1", source_id)
    assert await facts.fact_verify(pool, cand, "correct_other_1") == [
        "the fact holding the competing value is rejected"
    ]


async def _web(pool: asyncpg.Pool, ticker: str, host: str, decided_by: str = "ai:m") -> int:
    web_id: int = await pool.fetchval(
        "insert into public.web_fact (ticker, slot, value_text, unit, period, source_url, host,"
        " tier, quote, status, decided_by, confidence, reasons)"
        " values ($1, 'shares_outstanding', '1234567', 'shares', '2025', $2, $3, 'T2', $4,"
        " 'needs_review', $5, 0.7, 'tier 2 figure without a second agreeing source')"
        " returning id",
        ticker,
        f"https://{host}/a",
        host,
        "The company had 1,234,567 shares outstanding on 31 December 2025.",
        decided_by,
    )
    return web_id


@pytest.mark.db
async def test_web_fact_accept_reject_revert_and_owner_is_final(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    first = await _web(pool, "DBW_US_EQ", "www.reuters.com")
    await _web(pool, "DBW_US_EQ", "www.ft.com")
    cands = {c.ref: c for c in await facts.web_load(pool, 50)}
    cand = cands[f"web_fact:{first}"]
    assert [a["domain"] for a in cand.evidence["agreeing"]] == ["ft.com"]
    assert web_rule(cand) is not None

    payload = await facts.web_apply(pool, cand, "accept", "Autopilot (rule:two_domains_agree): ok")
    assert payload is not None
    row = await pool.fetchrow("select status, decided_by from public.web_fact where id = $1", first)
    assert (row["status"], row["decided_by"]) == ("approved", "ai:autopilot")
    assert await facts.web_outcome(pool, f"web_fact:{first}") is None
    await facts.web_revert(pool, payload)
    assert await facts.web_is_open(pool, [f"web_fact:{first}"]) == {f"web_fact:{first}"}

    # The owner decides: the autopilot may no longer apply anything to it.
    await pool.execute(
        "update public.web_fact set status = 'rejected', decided_by = 'owner:u' where id = $1",
        first,
    )
    assert await facts.web_outcome(pool, f"web_fact:{first}") == "reject"
    with pytest.raises(ApplyError):
        await facts.web_apply(pool, cand, "accept", "Autopilot (model:x, 0.95): x")
    assert await facts.web_is_open(pool, [f"web_fact:{first}"]) == set()


@pytest.mark.db
async def test_web_fact_accept_refuses_a_quote_that_does_not_print_the_value(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    web_id = await _web(pool, "DBQ_US_EQ", "www.reuters.com")
    cand = next(c for c in await facts.web_load(pool, 50) if c.ref == f"web_fact:{web_id}")
    cand.evidence["value_text"] = "7654321"
    with pytest.raises(ApplyError):
        await facts.web_apply(pool, cand, "accept", "Autopilot (model:x, 0.95): x")
    await facts.web_apply(pool, cand, "reject", "Autopilot (model:x, 0.95): not stated")
    assert await facts.web_is_open(pool, [f"web_fact:{web_id}"]) == set()
