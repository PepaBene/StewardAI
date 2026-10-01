"""D8.3: domain thresholds, sampled reconciliation and the enable gate."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import asyncpg
import pytest

from steward.verify_sources.thresholds import (
    CONCLUSION_DOMAINS,
    DEFAULT_THRESHOLDS,
    DOMAINS,
    DomainThreshold,
    GateBlocked,
    PrimarySample,
    activate_threshold,
    compare_sample,
    decide,
    domain_gate,
    load_threshold,
    reconcile_samples,
    record_reconciliation,
    require_domain_gate,
    resolve_stored_values,
)

D = Decimal
NOW = dt.datetime(2026, 10, 4, 12, 0, tzinfo=dt.UTC)


def sample(
    domain: str, stored: str | None, primary: str | None, key: str = "k", metric: str = "m"
) -> PrimarySample:
    return PrimarySample(
        domain,  # type: ignore[arg-type]
        key,
        metric,
        None if primary is None else D(primary),
        "exchange official close",
        "https://primary.example/x",
        dt.date(2026, 9, 30),
        None if stored is None else D(stored),
        "owner",
    )


# ---------------------------------------------------------------------------------------
# Defaults are domain specific
# ---------------------------------------------------------------------------------------
def test_every_domain_has_its_own_threshold_and_corporate_actions_are_exact() -> None:
    assert set(DEFAULT_THRESHOLDS) == set(DOMAINS)
    assert DEFAULT_THRESHOLDS["corporate_actions"].max_rel_error == 0
    assert DEFAULT_THRESHOLDS["corporate_actions"].max_failing_fraction == 0
    assert DEFAULT_THRESHOLDS["fund_weights"].max_abs_error is not None  # percentage points
    assert (
        DEFAULT_THRESHOLDS["prices"].max_rel_error != DEFAULT_THRESHOLDS["statements"].max_rel_error
    )
    for name, threshold in DEFAULT_THRESHOLDS.items():
        assert threshold.domain == name and threshold.min_samples >= 1
    for conclusion, domains in CONCLUSION_DOMAINS.items():
        assert domains and set(domains) <= set(DOMAINS), conclusion


def test_threshold_params_roundtrip_and_validate() -> None:
    t = DEFAULT_THRESHOLDS["prices"]
    again = DomainThreshold.from_params("prices", "v2", t.to_params())
    assert again.max_rel_error == t.max_rel_error and again.version == "v2"
    with pytest.raises(ValueError):
        DomainThreshold.from_params(
            "prices", "v", {"max_failing_fraction": "2", "max_rel_error": 1}
        )
    with pytest.raises(ValueError):
        DomainThreshold.from_params("prices", "v", {"max_failing_fraction": "0.1"})


# ---------------------------------------------------------------------------------------
# Comparing a stored value to a primary-source value
# ---------------------------------------------------------------------------------------
def test_price_within_and_beyond_relative_tolerance() -> None:
    t = DEFAULT_THRESHOLDS["prices"]
    ok = compare_sample(sample("prices", "100.04", "100"), t)
    assert ok.matched and ok.rel_error == D("0.0004")
    bad = compare_sample(sample("prices", "100.10", "100"), t)
    assert not bad.matched and "relative error" in (bad.reason or "")


def test_corporate_action_ratio_must_match_exactly() -> None:
    t = DEFAULT_THRESHOLDS["corporate_actions"]
    assert compare_sample(sample("corporate_actions", "2", "2"), t).matched
    assert not compare_sample(sample("corporate_actions", "2.0001", "2"), t).matched


def test_fund_weight_uses_absolute_percentage_points() -> None:
    t = DEFAULT_THRESHOLDS["fund_weights"]
    assert compare_sample(sample("fund_weights", "5.04", "5.00"), t).matched
    assert not compare_sample(sample("fund_weights", "5.20", "5.00"), t).matched


def test_missing_stored_value_fails_and_missing_primary_is_rejected() -> None:
    t = DEFAULT_THRESHOLDS["macro"]
    result = compare_sample(sample("macro", None, "3.1"), t)
    assert not result.matched and "no stored value" in (result.reason or "")
    with pytest.raises(ValueError):
        compare_sample(sample("macro", "3.1", None), t)


def test_zero_primary_value_uses_absolute_comparison_not_division() -> None:
    t = DEFAULT_THRESHOLDS["prices"]
    assert compare_sample(sample("prices", "0", "0"), t).matched
    assert not compare_sample(sample("prices", "0.01", "0"), t).matched


def test_reconcile_rejects_mixed_domains() -> None:
    with pytest.raises(ValueError):
        reconcile_samples(
            [sample("prices", "1", "1"), sample("macro", "1", "1")], DEFAULT_THRESHOLDS["prices"]
        )


def test_report_counts_failures_and_decides() -> None:
    t = DomainThreshold.from_params(
        "prices", "t", {"max_rel_error": "0.001", "max_failing_fraction": "0.1", "min_samples": 10}
    )
    good = [sample("prices", "100", "100", key=f"s{i}") for i in range(9)]
    report = reconcile_samples([*good, sample("prices", "110", "100")], t)
    assert (report.sample_count, report.failures) == (10, 1)
    assert report.failing_fraction == D("0.1") and report.max_rel_error == D("0.1")
    assert report.decision().allowed  # 10 % allowed, exactly met
    worse = reconcile_samples(
        [*good[:8], sample("prices", "110", "100"), sample("prices", "9", "1")], t
    )
    assert not worse.decision().allowed


def test_decide_blocks_without_samples_or_with_too_few() -> None:
    t = DEFAULT_THRESHOLDS["prices"]
    none = decide("prices", t, 0, 0)
    assert not none.allowed and "no reconciliation samples" in none.reasons[0]
    few = decide("prices", t, 5, 0)
    assert not few.allowed and "required" in few.reasons[0]
    assert decide("prices", t, t.min_samples, 0).allowed


# ---------------------------------------------------------------------------------------
# Persistence and the gate
# ---------------------------------------------------------------------------------------
def _passing(domain: str, count: int, *, key: str = "s") -> list[PrimarySample]:
    return [sample(domain, "100", "100", key=f"{key}{i}") for i in range(count)]


@pytest.mark.db
async def test_gate_blocks_until_enough_passing_samples_are_recorded(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    first = await domain_gate(pool, "prices", now=NOW)
    assert not first.allowed and first.sample_count == 0
    with pytest.raises(GateBlocked, match="price_alert is blocked"):
        await require_domain_gate(pool, "price_alert", now=NOW)

    t = DEFAULT_THRESHOLDS["prices"]
    report = reconcile_samples(_passing("prices", t.min_samples), t)
    await record_reconciliation(pool, report)
    # sampled_at defaults to now() - evaluate "now" relative to that
    real_now = dt.datetime.now(dt.UTC)
    decision = await domain_gate(pool, "prices", now=real_now)
    assert decision.allowed and decision.sample_count == t.min_samples
    assert (await require_domain_gate(pool, "price_alert", now=real_now))[0].allowed
    # a conclusion that also needs corporate actions stays blocked
    with pytest.raises(GateBlocked) as blocked:
        await require_domain_gate(pool, "return_alert", now=real_now)
    assert {d.domain for d in blocked.value.decisions if not d.allowed} == {"corporate_actions"}


@pytest.mark.db
async def test_failing_samples_block_and_stale_samples_are_ignored(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    t = DEFAULT_THRESHOLDS["corporate_actions"]
    samples = _passing("corporate_actions", t.min_samples)
    samples[0] = sample("corporate_actions", "3", "2", key="bad")  # one miss; none tolerated
    await record_reconciliation(pool, reconcile_samples(samples, t))
    real_now = dt.datetime.now(dt.UTC)
    blocked = await domain_gate(pool, "corporate_actions", now=real_now)
    assert not blocked.allowed and blocked.failures == 1
    # the same samples, judged long after their validity window, no longer count
    later = real_now + dt.timedelta(days=t.max_sample_age_days + 1)
    stale = await domain_gate(pool, "corporate_actions", now=later)
    assert stale.sample_count == 0 and not stale.allowed


@pytest.mark.db
async def test_versioned_threshold_activation_reevaluates_recorded_errors(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    assert (await load_threshold(pool, "prices")).version == DEFAULT_THRESHOLDS["prices"].version
    loose = DomainThreshold.from_params(
        "prices", "owner-v1", {"max_rel_error": "0.05", "max_failing_fraction": 0, "min_samples": 5}
    )
    await activate_threshold(pool, loose)
    five = [sample("prices", "101", "100", key=f"p{i}") for i in range(5)]  # 1 % error
    await record_reconciliation(pool, reconcile_samples(five, loose))
    now = dt.datetime.now(dt.UTC)
    assert (await domain_gate(pool, "prices", now=now)).allowed

    strict = DomainThreshold.from_params(
        "prices",
        "owner-v2",
        {"max_rel_error": "0.001", "max_failing_fraction": 0, "min_samples": 5},
    )
    await activate_threshold(pool, strict)
    assert (await load_threshold(pool, "prices")).version == "owner-v2"
    assert (
        await pool.fetchval("select count(*) from public.domain_error_threshold where active") == 1
    )
    tightened = await domain_gate(pool, "prices", now=now)
    assert not tightened.allowed and tightened.failures == 5 and tightened.version == "owner-v2"


@pytest.mark.db
async def test_unknown_conclusion_kind_is_blocked(backend_pool: asyncpg.Pool) -> None:
    with pytest.raises(GateBlocked, match="unknown conclusion"):
        await require_domain_gate(backend_pool, "teleport_alert")


# ---------------------------------------------------------------------------------------
# Stored-value resolvers read the app's own tables
# ---------------------------------------------------------------------------------------
@pytest.mark.db
async def test_resolvers_read_prices_actions_statements_macro_patents_and_fund_weights(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    day = dt.date(2026, 9, 30)
    await pool.execute(
        "insert into public.price_bar(symbol,date,close,source,bar_type)"
        " values('AAA',$1,101.5,'stooq','raw')",
        day,
    )
    await pool.execute(
        "insert into public.corporate_action(symbol,action_type,ex_date,ratio,source,source_url,"
        "status) values('AAA','split',$1,2,'issuer','https://x.test','confirmed')",
        day,
    )
    await pool.execute(
        "insert into public.financial_statement_fact(ticker,concept,statement,period_end,basis,"
        "value,unit,source,source_url,known_at,revision) values"
        "('AAA','us-gaap:Revenues','income',$1,'reported',1000,'USD','sec','https://x',now(),0),"
        "('AAA','us-gaap:Revenues','income',$1,'reported',1010,'USD','sec','https://x',now(),1)",
        day,
    )
    await pool.execute(
        "insert into public.macro_observation(series_id,source,geography,period,value,unit,"
        "frequency,known_at,vintage,source_url) values('cpi','ecb','EA',$1,2.4,'pct','monthly',"
        "now(),'v1','https://x')",
        day,
    )
    await pool.execute(
        "insert into public.patent_family(family_key,legal_status,source,source_url,known_at,"
        "citations_forward) values('EP1','granted','epo','https://x',now(),7)"
    )
    snapshot = await pool.fetchval(
        "insert into public.etf_snapshot(fund_isin,provider,portfolio_id,source,as_of,raw)"
        " values('IE00B5BMR087','ishares','1','ishares_json',$1,'{}'::jsonb) returning id",
        day,
    )
    await pool.execute(
        "insert into public.etf_holding(snapshot_id,row_index,isin,asset_class,weight)"
        " values($1,0,'US0378331005','equity',0.0621)",
        snapshot,
    )
    wanted = [
        PrimarySample("prices", "AAA", "close", D("101.5"), "exchange", None, day),
        PrimarySample("corporate_actions", "AAA:split", "ratio", D(2), "issuer", None, day),
        PrimarySample("statements", "AAA", "us-gaap:Revenues", D(1010), "filing", None, day),
        PrimarySample("macro", "ecb:cpi:EA", "value", D("2.4"), "ECB", None, day),
        PrimarySample("patents", "EP1", "citations_forward", D(7), "EPO", None),
        PrimarySample("patents", "EP-missing", "exists", D(1), "EPO", None),
        PrimarySample(
            "fund_weights", f"{snapshot}:US0378331005", "weight_pct", D("6.21"), "iShares", None
        ),
        PrimarySample("prices", "ZZZ", "close", D("1"), "exchange", None, day),
    ]
    resolved = await resolve_stored_values(pool, wanted)
    stored = [r.stored_value for r in resolved]
    assert stored[:5] == [D("101.5"), D(2), D(1010), D("2.4"), D(7)]  # latest revision wins
    assert stored[5] == D(0)  # a missing patent family reads as "does not exist"
    assert stored[6] == D("6.21")
    assert stored[7] is None  # unresolvable: fails the comparison, is never skipped
    for r in resolved[:7]:
        assert compare_sample(r, DEFAULT_THRESHOLDS[r.domain]).matched or r.metric == "exists"
    assert not compare_sample(resolved[7], DEFAULT_THRESHOLDS["prices"]).matched
