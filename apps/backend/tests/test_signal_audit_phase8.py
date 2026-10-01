"""D8.6: audit fixtures, review samples and the derived-signal publish gate."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from steward.ownership.insider_classify import classify
from steward.sources import xbrl
from steward.sources.schedule13 import parse_schedule13_xml, stake_of
from steward.verify_sources.signal_audit import (
    SIGNAL_REQUIREMENTS,
    AuditObservation,
    DealEvent,
    PublishBlocked,
    VersionedRecord,
    anonymous_share,
    consensus_is_stale,
    coverage_by_jurisdiction_and_metric,
    deal_state_at,
    evaluate_requirement,
    mention_is_relationship,
    passage_supports_number,
    point_in_time_pick,
    publish_gate,
    record_audit_result,
    require_publishable,
    summarize,
)

FIX = Path(__file__).parent / "fixtures"
SAMPLES: dict[str, Any] = json.loads((FIX / "audit" / "review_samples.json").read_text())
NOW = dt.datetime(2026, 10, 4, 12, 0, tzinfo=dt.UTC)


def ts(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------------------
# Review samples whose standard is pinned by a reference ORACLE.  The checks against the real
# extractors/readers live in tests/test_audit_scenarios_phase8.py; AUDIT_SCENARIOS says which
# scenario has which basis.  Tests below that call an oracle prove only that the oracle matches
# the reviewer's labels - they say nothing about the system.
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("sample", SAMPLES["filing_amendments"], ids=lambda s: s["id"])
def test_amendments_and_restatements_are_point_in_time(sample: dict[str, Any]) -> None:
    records = [
        VersionedRecord("rev", Decimal(r["value"]), ts(r["known_at"]), r["revision"])
        for r in sample["records"]
    ]
    picked = point_in_time_pick(records, ts(sample["as_of"]))
    expected = None if sample["expect"] is None else Decimal(sample["expect"])
    assert (picked.value if picked else None) == expected


def test_xbrl_dimension_collisions_never_merge_into_the_total() -> None:
    spec = SAMPLES["xbrl_dimension_collision"]
    instance = xbrl.parse_instance(spec["instance"].encode(), {spec["concept"]})
    plain = [f.value for f in xbrl.plain_facts(instance)]
    assert [str(v) for v in plain] == spec["expect_undimensioned"]
    dimensional = sorted(
        str(f.number) for f in instance.facts if f.context.dims and f.concept == spec["concept"]
    )
    assert dimensional == spec["expect_dimension_values"]
    # two distinct axes carry the same concept and period: the keys must stay distinct
    keys = {(f.concept, f.context.start, f.context.end, f.context.dims) for f in instance.facts}
    assert len(keys) == len(instance.facts) == 3


@pytest.mark.parametrize("sample", SAMPLES["sec_notes"], ids=lambda s: s["id"])
def test_note_claims_need_the_number_in_the_quoted_passage(sample: dict[str, Any]) -> None:
    assert (
        passage_supports_number(sample["passage"], Decimal(sample["claim"])) is sample["supported"]
    )


def test_13dg_group_members_are_never_summed() -> None:
    raw = (FIX / SAMPLES["group_13dg"]["fixture"]).read_bytes()
    filing = parse_schedule13_xml(raw)
    persons = [p for p in filing.persons if p.aggregate_shares is not None]
    assert len(persons) >= 2, "the fixture is a multi-person group"
    shares, percent = stake_of(filing.persons)
    total = sum((p.aggregate_shares for p in persons if p.aggregate_shares), Decimal(0))
    assert shares == max(p.aggregate_shares for p in persons if p.aggregate_shares)
    assert shares is not None and total > shares  # summing would overstate the stake
    assert percent is not None and percent <= Decimal(100)


@pytest.mark.parametrize("sample", SAMPLES["form4_codes"], ids=lambda s: s["id"])
def test_form4_non_discretionary_codes_are_excluded_from_open_market_activity(
    sample: dict[str, Any],
) -> None:
    result = classify(
        sample["code"], derivative=sample["derivative"], footnote_text=sample["footnote"]
    )
    assert result.economic_class == sample["expect_class"], result.basis
    assert result.open_market is sample["discretionary"]


@pytest.mark.parametrize("sample", SAMPLES["false_mentions"], ids=lambda s: s["id"])
def test_false_customer_supplier_mentions_are_rejected(sample: dict[str, Any]) -> None:
    assert (
        mention_is_relationship(sample["passage"], sample["counterparty"]) is sample["relationship"]
    )


@pytest.mark.parametrize("sample", SAMPLES["anonymous_customers"], ids=lambda s: s["id"])
def test_anonymous_customer_percentages_are_kept_unattributed(sample: dict[str, Any]) -> None:
    share = anonymous_share(sample["passage"])
    expected = None if sample["expect_share"] is None else Decimal(sample["expect_share"])
    assert share == expected
    if share is not None:
        assert sample["named"] is False  # an anonymous share can never name a counterparty


@pytest.mark.parametrize("sample", SAMPLES["deal_reversals"], ids=lambda s: s["id"])
def test_deal_state_follows_what_was_known_including_reversals(sample: dict[str, Any]) -> None:
    events = [
        DealEvent(e["state"], ts(e["known_at"]), e.get("reverses_completed", False))
        for e in sample["events"]
    ]
    assert deal_state_at(events, ts(sample["as_of"])) == sample["expect"]


@pytest.mark.parametrize("sample", SAMPLES["stale_consensus"], ids=lambda s: s["id"])
def test_stale_consensus_is_detected(sample: dict[str, Any]) -> None:
    published = None if sample["published_at"] is None else ts(sample["published_at"])
    assert (
        consensus_is_stale(published, ts(sample["as_of"]), max_age_days=sample["max_age_days"])
        is sample["stale"]
    )


def test_every_audit_scenario_in_the_plan_has_samples() -> None:
    required = {
        "filing_amendments",
        "xbrl_dimension_collision",
        "sec_notes",
        "group_13dg",
        "form4_codes",
        "false_mentions",
        "anonymous_customers",
        "deal_reversals",
        "stale_consensus",
    }
    assert required <= set(SAMPLES)


# ---------------------------------------------------------------------------------------
# Summaries and the requirement
# ---------------------------------------------------------------------------------------
def observations(
    n: int, *, wrong: int = 0, pit_bad: int = 0, pit_checked: int | None = None
) -> list[AuditObservation]:
    out = []
    for i in range(n):
        checked = i < (n if pit_checked is None else pit_checked)
        out.append(
            AuditObservation(
                f"s{i}",
                evidence_correct=i >= wrong,
                pit_ok=(i >= pit_bad) if checked else None,
                jurisdiction="US" if i % 2 else "CZ",
                metric="revenue" if i % 3 else "profit",
            )
        )
    return out


def test_summary_counts_precision_pit_and_breakdowns() -> None:
    summary = summarize(observations(40, wrong=2, pit_bad=1))
    assert (summary.sample_size, summary.evidence_correct) == (40, 38)
    assert summary.precision == Decimal("0.95")
    assert (summary.pit_checked, summary.pit_violations) == (40, 1)
    assert set(summary.by_jurisdiction) == {"US", "CZ"}
    assert sum(v["samples"] for v in summary.by_metric.values()) == 40
    with pytest.raises(ValueError):
        summarize([AuditObservation("a", True), AuditObservation("a", False)])
    assert summarize([]).precision is None


def decision(**over: Any) -> Any:
    args: dict[str, Any] = {
        "sample_size": 40,
        "evidence_correct": 40,
        "pit_checked": 40,
        "pit_violations": 0,
        "audited_at": NOW - dt.timedelta(days=10),
        "now": NOW,
        "reviewer": "owner",
    }
    return evaluate_requirement("deal_state", SIGNAL_REQUIREMENTS["deal_state"], **(args | over))


def test_requirement_passes_only_when_everything_holds() -> None:
    assert decision().allowed


@pytest.mark.parametrize(
    ("over", "fragment"),
    [
        ({"audited_at": None, "sample_size": 0, "evidence_correct": 0}, "never audited"),
        ({"sample_size": 10, "evidence_correct": 10}, "required"),
        ({"evidence_correct": 38}, "precision"),
        ({"pit_checked": 5}, "point-in-time checks"),
        ({"pit_violations": 1}, "point-in-time violations"),
        ({"audited_at": NOW - dt.timedelta(days=400)}, "re-sample"),
        ({"audited_at": NOW + dt.timedelta(days=3)}, "in the future"),
        ({"reviewer": None}, "no reviewer"),
        ({"reviewer": "  "}, "no reviewer"),
    ],
)
def test_requirement_blocks_with_the_specific_reason(over: dict[str, Any], fragment: str) -> None:
    d = decision(**over)
    assert not d.allowed
    assert any(fragment in r for r in d.reasons), d.reasons


# ---------------------------------------------------------------------------------------
# publish_gate reads recorded audit results
# ---------------------------------------------------------------------------------------
@pytest.mark.db
async def test_a_signal_never_audited_cannot_publish(backend_pool: asyncpg.Pool) -> None:
    d = await publish_gate(backend_pool, "customer_concentration", now=NOW)
    assert not d.allowed and any("never audited" in r for r in d.reasons)
    with pytest.raises(PublishBlocked, match="never audited"):
        await require_publishable(backend_pool, "customer_concentration")
    unknown = await publish_gate(backend_pool, "made_up_signal", now=NOW)
    assert not unknown.allowed and "unknown signal" in unknown.reasons[0]


@pytest.mark.db
async def test_recorded_audit_authorises_publication_until_it_goes_stale(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await record_audit_result(
        pool,
        "deal_state",
        observations(40),
        reviewer="owner",
        audited_at=NOW - dt.timedelta(days=5),
    )
    assert (await publish_gate(pool, "deal_state", now=NOW)).allowed
    later = NOW + dt.timedelta(days=400)
    old = await publish_gate(pool, "deal_state", now=later)
    assert not old.allowed and any("re-sample" in r for r in old.reasons)


@pytest.mark.db
async def test_latest_audit_wins_and_a_bad_resample_blocks_again(
    backend_pool: asyncpg.Pool,
) -> None:
    now = dt.datetime.now(dt.UTC)
    pool = backend_pool
    await record_audit_result(
        pool,
        "insider_net_discretionary",
        observations(40),
        reviewer="owner",
        audited_at=now - dt.timedelta(days=30),
    )
    assert (await publish_gate(pool, "insider_net_discretionary", now=now)).allowed
    await record_audit_result(
        pool,
        "insider_net_discretionary",
        observations(40, wrong=6, pit_bad=1),
        reviewer="owner",
        audited_at=now - dt.timedelta(days=1),
    )
    d = await publish_gate(pool, "insider_net_discretionary", now=now)
    assert not d.allowed
    assert d.pit_violations == 1 and d.precision == Decimal("0.85")
    details = await pool.fetchval(
        "select details from public.signal_audit_result"
        " where signal_name='insider_net_discretionary'"
        " order by audited_at desc limit 1"
    )
    parsed = json.loads(details) if isinstance(details, str) else details
    assert parsed["incorrect"] and parsed["pit_violations"] == ["s0"]


@pytest.mark.db
async def test_jurisdiction_only_rows_do_not_authorise_publication(
    backend_pool: asyncpg.Pool,
) -> None:
    now = dt.datetime.now(dt.UTC)
    await record_audit_result(
        backend_pool,
        "segment_revenue",
        observations(40),
        reviewer="owner",
        jurisdiction="US",
        audited_at=now - dt.timedelta(days=1),
    )
    assert not (await publish_gate(backend_pool, "segment_revenue", now=now)).allowed


@pytest.mark.db
async def test_unknown_signal_cannot_be_recorded(backend_pool: asyncpg.Pool) -> None:
    with pytest.raises(ValueError, match="unknown signal"):
        await record_audit_result(backend_pool, "made_up_signal", observations(3), reviewer="owner")


# ---------------------------------------------------------------------------------------
# Measured coverage by jurisdiction and metric
# ---------------------------------------------------------------------------------------
@pytest.mark.db
async def test_coverage_report_is_measured_and_empty_when_nothing_exists(
    backend_pool: asyncpg.Pool,
) -> None:
    assert await coverage_by_jurisdiction_and_metric(backend_pool) == []
    for geography, metric, value in (
        ("CZ", "revenue", "1"),
        ("CZ", "revenue", "2"),
        ("US", "revenue", None),
    ):
        await backend_pool.execute(
            """insert into public.source_observation
               (domain,metric,geography,period_start,period_end,value,text_value,unit,source,
                source_url,known_at,access_state)
               values('fundamentals',$1,$2,'2025-01-01',$3,$4,$5,'EUR','s','https://x.test',now(),
                      'available')""",
            metric,
            geography,
            dt.date(2025, 12, 31) - dt.timedelta(days=int(value or 0)),
            None if value is None else Decimal(value),
            "n/a" if value is None else None,
        )
    await backend_pool.execute(
        "insert into public.macro_observation(series_id,source,geography,period,value,unit,"
        "frequency,known_at,vintage,source_url) values('cpi','ecb','CZ','2025-01-01',null,'pct',"
        "'monthly',now(),'v1','https://x')"
    )
    rows = await coverage_by_jurisdiction_and_metric(backend_pool)
    by = {(r["jurisdiction"], r["domain"], r["metric"]): r for r in rows}
    assert by[("CZ", "fundamentals", "revenue")]["observations"] == 2
    assert by[("US", "fundamentals", "revenue")]["observations"] == 1
    assert by[("US", "fundamentals", "revenue")]["unknown_values"] == 0  # text value present
    assert by[("CZ", "macro", "cpi")]["unknown_values"] == 1  # unknown stays unknown, not zero
    assert ("DE", "fundamentals", "revenue") not in by


# ---------------------------------------------------------------------------------------
# B47: dates, reviewers and scoped audits
# ---------------------------------------------------------------------------------------
@pytest.mark.db
async def test_audit_needs_a_reviewer_and_cannot_be_dated_in_the_future(
    backend_pool: asyncpg.Pool,
) -> None:
    with pytest.raises(ValueError, match="reviewed"):
        await record_audit_result(backend_pool, "deal_state", observations(40), reviewer=None)
    with pytest.raises(ValueError, match="reviewed"):
        await record_audit_result(backend_pool, "deal_state", observations(40), reviewer=" ")
    with pytest.raises(ValueError, match="future"):
        await record_audit_result(
            backend_pool,
            "deal_state",
            observations(40),
            reviewer="owner",
            audited_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=2),
        )
    # the table refuses the same rows when written around the helper
    for reviewer, when in (("null", "now()"), ("'owner'", "now() + interval '2 days'")):
        with pytest.raises(asyncpg.CheckViolationError):
            await backend_pool.execute(
                "insert into public.signal_audit_result(signal_name,sample_size,evidence_correct,"
                f"pit_checked,pit_violations,reviewer,audited_at) values('deal_state',40,40,40,0,"
                f"{reviewer},{when})"
            )
    assert await backend_pool.fetchval("select count(*) from public.signal_audit_result") == 0


@pytest.mark.db
async def test_gate_ignores_an_audit_dated_after_now(backend_pool: asyncpg.Pool) -> None:
    real_now = dt.datetime.now(dt.UTC)
    await record_audit_result(
        backend_pool,
        "deal_state",
        observations(40),
        reviewer="owner",
        audited_at=real_now - dt.timedelta(days=1),
    )
    assert (await publish_gate(backend_pool, "deal_state", now=real_now)).allowed
    # evaluated "as of" a moment before the audit existed, it is a future audit: ignored
    earlier = await publish_gate(backend_pool, "deal_state", now=real_now - dt.timedelta(days=3))
    assert not earlier.allowed
    assert any("never audited" in r for r in earlier.reasons)
    assert any("dated in the future" in r for r in earlier.reasons)


@pytest.mark.db
async def test_legacy_audit_without_a_reviewer_cannot_authorise(
    backend_pool: asyncpg.Pool, migrated_db: Any
) -> None:
    admin = await asyncpg.connect(migrated_db.admin_url)
    try:
        await admin.execute(
            "alter table public.signal_audit_result"
            " drop constraint signal_audit_result_reviewer_named"
        )
        await admin.execute(
            "insert into public.signal_audit_result(signal_name,sample_size,evidence_correct,"
            "pit_checked,pit_violations,reviewer,audited_at) values('deal_state',40,40,40,0,"
            "null, now() - interval '1 day')"
        )
    finally:
        await admin.close()
    decision = await publish_gate(backend_pool, "deal_state")
    assert not decision.allowed and any("no reviewer" in r for r in decision.reasons)


@pytest.mark.db
async def test_a_newer_failing_scoped_audit_blocks_an_older_passing_overall_audit(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    now = dt.datetime.now(dt.UTC)
    await record_audit_result(
        pool,
        "deal_state",
        observations(40),
        reviewer="owner",
        audited_at=now - dt.timedelta(days=20),
    )
    assert (await publish_gate(pool, "deal_state")).allowed
    # an older scoped failure was already superseded by the overall audit: no effect
    await record_audit_result(
        pool,
        "deal_state",
        observations(10, wrong=4),
        reviewer="owner",
        jurisdiction="CZ",
        audited_at=now - dt.timedelta(days=30),
    )
    assert (await publish_gate(pool, "deal_state")).allowed
    # a newer scoped audit that fails blocks, even though its sample is small
    await record_audit_result(
        pool,
        "deal_state",
        observations(10, wrong=4),
        reviewer="owner",
        jurisdiction="CZ",
        audited_at=now - dt.timedelta(days=5),
    )
    blocked = await publish_gate(pool, "deal_state")
    assert not blocked.allowed
    assert any("jurisdiction=CZ" in r and "precision" in r for r in blocked.reasons)
    # a newer pit-violating metric audit blocks too
    await record_audit_result(
        pool,
        "deal_state",
        observations(10, pit_bad=1),
        reviewer="owner",
        metric="revenue",
        audited_at=now - dt.timedelta(days=4),
    )
    reasons = (await publish_gate(pool, "deal_state")).reasons
    assert any("metric=revenue" in r and "point-in-time" in r for r in reasons)
    # a newer passing re-sample of the failing slice clears that slice
    await record_audit_result(
        pool,
        "deal_state",
        observations(10),
        reviewer="owner",
        jurisdiction="CZ",
        audited_at=now - dt.timedelta(days=2),
    )
    reasons = (await publish_gate(pool, "deal_state")).reasons
    assert not any("jurisdiction=CZ" in r for r in reasons)
    assert any("metric=revenue" in r for r in reasons)
    # a fresh overall audit supersedes every older scoped result
    await record_audit_result(
        pool,
        "deal_state",
        observations(40),
        reviewer="owner",
        audited_at=now - dt.timedelta(hours=1),
    )
    assert (await publish_gate(pool, "deal_state")).allowed


def test_every_audit_scenario_states_whether_it_runs_against_a_real_extractor() -> None:
    from steward.verify_sources.signal_audit import AUDIT_SCENARIOS

    assert set(AUDIT_SCENARIOS) == set(SAMPLES)
    oracle_only = {k for k, v in AUDIT_SCENARIOS.items() if v.kind == "oracle"}
    assert oracle_only == {"false_mentions", "anonymous_customers"}
    for name, basis in AUDIT_SCENARIOS.items():
        assert basis.target and (basis.kind == "real" or basis.note), name


# ---------------------------------------------------------------------------------------
# P8-15: coverage query semantics
# ---------------------------------------------------------------------------------------
@pytest.mark.db
async def test_coverage_counts_entities_by_kind_and_leaves_macro_entities_unknown(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    issuer = await pool.fetchval("insert into public.issuer(legal_name) values('I') returning id")
    security = await pool.fetchval(
        "insert into public.security(kind,name) values('equity','S') returning id"
    )
    # one security and one issuer that happen to share an id value, plus rows tied to no entity
    assert issuer is not None and security is not None
    for i, entity_sql in enumerate(
        (f"{security}, null", f"null, {issuer}", "null, null", "null, null")
    ):
        await pool.execute(
            "insert into public.source_observation(domain,metric,geography,period_start,"
            "period_end,value,unit,source,source_url,known_at,access_state,security_id,issuer_id)"
            f" values('fundamentals','revenue','CZ','2025-01-01','2025-12-{20 + i}',1,'EUR','s',"
            f"'https://x.test', now(),'available',{entity_sql})"
        )
    await pool.execute(
        "insert into public.macro_observation(series_id,source,geography,period,value,unit,"
        "frequency,known_at,vintage,source_url) values('cpi','ecb','CZ','2025-01-01',1,'pct',"
        "'monthly',now(),'v1','https://x')"
    )
    by = {
        (r["jurisdiction"], r["domain"], r["metric"]): r
        for r in await coverage_by_jurisdiction_and_metric(pool)
    }
    revenue = by[("CZ", "fundamentals", "revenue")]
    assert revenue["observations"] == 4
    assert (
        revenue["entities"] == 2
    )  # a security and an issuer, never merged; no-entity rows add none
    assert by[("CZ", "macro", "cpi")]["entities"] is None  # not applicable, not a measured zero
    # the `since` bound is a lower bound on known_at
    future = dt.datetime.now(dt.UTC) + dt.timedelta(days=1)
    assert await coverage_by_jurisdiction_and_metric(pool, since=future) == []
