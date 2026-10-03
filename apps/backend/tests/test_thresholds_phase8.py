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
    ReconciliationReport,
    SampleRejected,
    SampleResult,
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
from tests.conftest import MigratedDatabase

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
DAY = dt.date(2026, 9, 30)


def primary(
    domain: str, key: str, value: str, *, metric: str = "close", **over: object
) -> PrimarySample:
    base: dict[str, object] = {
        "domain": domain,
        "subject_key": key,
        "metric": metric,
        "primary_value": D(value),
        "primary_source": "exchange official close",
        "primary_url": "https://primary.example/x",
        "period_end": DAY,
        "reviewer": "owner",
    }
    return PrimarySample(**(base | over))  # type: ignore[arg-type]


async def seed_prices(pool: asyncpg.Pool, count: int, close: str = "100") -> list[str]:
    keys = []
    for i in range(count):
        await pool.execute(
            "insert into public.price_bar(symbol,date,close,source,bar_type)"
            " values($1,$2,$3,'stooq','raw')",
            f"S{i}",
            DAY,
            D(close),
        )
        keys.append(f"S{i}:stooq:raw")
    return keys


async def seed_actions(pool: asyncpg.Pool, count: int, ratio: str = "2") -> list[str]:
    for i in range(count):
        await pool.execute(
            "insert into public.corporate_action(symbol,action_type,ex_date,ratio,source,"
            "source_url,status) values($1,'split',$2,$3,'issuer',$4,'confirmed')",
            f"A{i}",
            DAY,
            D(ratio),
            f"https://x.test/{i}",
        )
    return [f"A{i}:split" for i in range(count)]


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
    keys = await seed_prices(pool, t.min_samples)
    report = reconcile_samples([primary("prices", k, "100") for k in keys], t)
    await record_reconciliation(pool, report)
    real_now = dt.datetime.now(dt.UTC)
    decision = await domain_gate(pool, "prices", now=real_now)
    assert decision.allowed and decision.sample_count == t.min_samples
    assert decision.subject_count == t.min_samples
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
    keys = await seed_actions(pool, t.min_samples)
    samples = [primary("corporate_actions", k, "2", metric="ratio") for k in keys]
    samples[0] = primary("corporate_actions", keys[0], "3", metric="ratio")  # one miss
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
    keys = await seed_prices(pool, 5, close="101")  # stored 101 vs primary 100: 1 % error
    await record_reconciliation(
        pool, reconcile_samples([primary("prices", k, "100") for k in keys], loose)
    )
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


# --- B47: the gate cannot be passed with copies, forgeries or unattributed samples ----------
@pytest.mark.db
async def test_thirty_copies_of_one_sample_are_one_sample(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    t = DEFAULT_THRESHOLDS["prices"]
    (key,) = await seed_prices(pool, 1)
    for _ in range(t.min_samples):
        await record_reconciliation(pool, reconcile_samples([primary("prices", key, "100")], t))
    assert await pool.fetchval("select count(*) from public.reconciliation_sample") == 1
    decision = await domain_gate(pool, "prices", now=dt.datetime.now(dt.UTC))
    assert not decision.allowed and decision.sample_count == 1
    assert "distinct subjects" in decision.reasons[0]
    # the table itself refuses a second row for the same key (not only the Python path)
    with pytest.raises(asyncpg.UniqueViolationError):
        await pool.execute(
            "insert into public.reconciliation_sample(domain,subject_key,metric,period_end,"
            "primary_source,primary_url,matched,threshold_version,reviewer)"
            " values('prices',$1,'close',$2,'x','https://x',true,'v','owner')",
            key,
            DAY,
        )
    # same key without a period is unique too (NULL periods collide, not slip through)
    row = (
        "insert into public.reconciliation_sample(domain,subject_key,metric,primary_source,"
        "primary_url,matched,threshold_version,reviewer) values('patents','EP1','exists','x',"
        "'https://x',true,'v','owner')"
    )
    await pool.execute(row)
    with pytest.raises(asyncpg.UniqueViolationError):
        await pool.execute(row)


@pytest.mark.db
async def test_min_samples_counts_distinct_subjects_not_metrics(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    t = DEFAULT_THRESHOLDS["statements"]  # 30 required
    for i in range(t.min_samples):
        await pool.execute(
            "insert into public.financial_statement_fact(ticker,concept,statement,period_end,"
            "basis,value,unit,source,source_url,known_at) values('AAA',$1,'income',$2,"
            "'reported',10,'USD','sec','https://x',now())",
            f"concept-{i}",
            DAY,
        )
    samples = [
        primary("statements", "AAA", "10", metric=f"concept-{i}") for i in range(t.min_samples)
    ]
    await record_reconciliation(pool, reconcile_samples(samples, t))
    assert await pool.fetchval("select count(*) from public.reconciliation_sample") == 30
    decision = await domain_gate(pool, "statements", now=dt.datetime.now(dt.UTC))
    assert not decision.allowed
    assert decision.sample_count == 30 and decision.subject_count == 1
    assert "1 distinct subjects sampled, 30 required" in decision.reasons[0]


@pytest.mark.db
async def test_caller_supplied_stored_value_is_recomputed_from_the_tables(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    t = DEFAULT_THRESHOLDS["prices"]
    (key,) = await seed_prices(pool, 1, close="150")  # the app stores 150; the exchange says 100
    forged = primary("prices", key, "100", stored_value=D("100"))  # caller claims 100
    report = reconcile_samples([forged], t)
    assert report.failures == 0  # the pure comparison trusts what it is given ...
    await record_reconciliation(pool, report)  # ... the recorder does not
    row = await pool.fetchrow("select stored_value, matched, rel_error from reconciliation_sample")
    assert row is not None
    assert row["stored_value"] == D("150") and row["matched"] is False
    assert row["rel_error"] == D("0.5")


@pytest.mark.db
async def test_the_gate_rejudges_from_values_not_from_hand_edited_error_columns(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    t = DEFAULT_THRESHOLDS["prices"]
    keys = await seed_prices(pool, t.min_samples, close="150")
    await record_reconciliation(
        pool, reconcile_samples([primary("prices", k, "100") for k in keys], t)
    )
    await pool.execute(
        "update public.reconciliation_sample set abs_error=0, rel_error=0, matched=true"
    )
    decision = await domain_gate(pool, "prices", now=dt.datetime.now(dt.UTC))
    assert not decision.allowed and decision.failures == t.min_samples


@pytest.mark.db
async def test_future_dated_and_unattributed_samples_are_rejected(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    t = DEFAULT_THRESHOLDS["prices"]
    (key,) = await seed_prices(pool, 1)
    now = dt.datetime.now(dt.UTC)
    cases = {
        "future": primary("prices", key, "100", sampled_at=now + dt.timedelta(days=1)),
        "reviewer": primary("prices", key, "100", reviewer=" "),
        "source": primary("prices", key, "100", primary_source=""),
        "url": primary("prices", key, "100", primary_url=None),
        "nan": primary("prices", key, "NaN"),
    }
    for name, bad in cases.items():
        # reconcile_samples itself refuses NaN; the recorder refuses all of them
        report = ReconciliationReport("prices", t, (SampleResult(bad, False, None, None, None),))
        with pytest.raises(SampleRejected):
            await record_reconciliation(pool, report, now=now)
        assert name
    assert await pool.fetchval("select count(*) from public.reconciliation_sample") == 0
    # the table refuses them as well
    for column, value in (
        ("sampled_at", "now() + interval '1 day'"),
        ("reviewer", "null"),
    ):
        sql = (
            "insert into public.reconciliation_sample(domain,subject_key,metric,period_end,"
            "primary_source,primary_url,matched,threshold_version,reviewer,sampled_at)"
            " values('prices','k','close','2026-09-30','x','https://x',true,'v',"
            f"{'null' if column == 'reviewer' else chr(39) + 'owner' + chr(39)},"
            f"{value if column == 'sampled_at' else 'now()'})"
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(sql)


@pytest.mark.db
async def test_legacy_rows_without_provenance_or_in_the_future_never_count(
    backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> None:
    """Rows recorded before the constraints existed stay (NOT VALID) but are ignored."""
    t = DEFAULT_THRESHOLDS["prices"]
    admin = await asyncpg.connect(migrated_db.admin_url)
    try:
        for constraint in (
            "reconciliation_sample_not_future",
            "reconciliation_sample_reviewer_named",
            "reconciliation_sample_source_named",
        ):
            await admin.execute(
                f"alter table public.reconciliation_sample drop constraint {constraint}"
            )
        future = "now() + interval '2 days'"
        for i in range(t.min_samples):
            await admin.execute(
                "insert into public.reconciliation_sample(domain,subject_key,metric,period_end,"
                "stored_value,primary_value,primary_source,matched,threshold_version,reviewer,"
                f"sampled_at) values('prices','L{i}','close','2026-09-30',100,100,'x',true,'v',"
                f"{'null' if i % 2 else chr(39) + 'owner' + chr(39)},"
                f"{future if i % 3 == 0 else 'now()'})"
            )
    finally:
        await admin.close()
    decision = await domain_gate(backend_pool, "prices", now=dt.datetime.now(dt.UTC))
    assert not decision.allowed and decision.sample_count == 0
    assert any("ignored" in r for r in decision.reasons)


@pytest.mark.db
async def test_price_reconciliation_compares_the_named_source_and_bar_type(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    for source, bar_type, close in (
        ("stooq", "raw", "100"),
        ("stooq", "adjusted", "50"),
        ("yahoo", "raw", "100.04"),
    ):
        await pool.execute(
            "insert into public.price_bar(symbol,date,close,source,bar_type)"
            " values('AAA',$1,$2,$3,$4)",
            DAY,
            D(close),
            source,
            bar_type,
        )
    wanted = [
        primary("prices", "AAA:stooq:raw", "100"),
        primary("prices", "AAA:stooq:adjusted", "100"),
        primary("prices", "AAA:yahoo:raw", "100"),
        primary("prices", "AAA:yahoo:adjusted", "100"),
        primary("prices", "AAA", "100"),  # ambiguous: three stored bars that day
    ]
    stored = [r.stored_value for r in await resolve_stored_values(pool, wanted)]
    assert stored == [D("100"), D("50"), D("100.04"), None, None]
    only = await pool.fetchval("select count(*) from public.price_bar")
    assert only == 3


def test_non_finite_values_never_pass_a_comparison() -> None:
    t = DEFAULT_THRESHOLDS["prices"]
    nan = compare_sample(sample("prices", "NaN", "100"), t)
    assert not nan.matched and "finite" in (nan.reason or "")
    inf = compare_sample(sample("prices", "Infinity", "100"), t)
    assert not inf.matched
    for bad in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(ValueError, match="finite"):
            compare_sample(sample("prices", "100", bad), t)


def test_decide_counts_distinct_subjects() -> None:
    t = DEFAULT_THRESHOLDS["prices"]
    assert not decide("prices", t, 100, 0, subject_count=3).allowed
    assert decide("prices", t, 100, 0, subject_count=t.min_samples).allowed


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


@pytest.mark.db
async def test_statements_resolver_reads_the_consolidated_fact_not_a_segment_one(
    backend_pool: asyncpg.Pool,
) -> None:
    """Audit N5: a later segment fact of the same filing (120) resolved as the stored value, so
    a correct reviewer sample of 1000 failed. Two durations ending the same day stay
    unresolved rather than passing against one of them."""
    pool = backend_pool
    day = dt.date(2026, 6, 30)
    cols = (
        "insert into public.financial_statement_fact(ticker,concept,statement,period_start,"
        "period_end,basis,value,unit,dimensions,source,source_url,known_at,revision) values"
    )
    await pool.execute(
        cols
        + "('BBB','us-gaap:Revenues','income','2026-01-01',$1,'reported',1000,'USD','{}','sec',"
        "'https://x',now() - interval '1 day',0),"
        "('BBB','us-gaap:Revenues','segment','2026-01-01',$1,'reported',120,'USD',"
        '\'{"members":[{"axis":"Seg","member":"A"}]}\','
        "'sec','https://x',now(),0)",
        day,
    )
    sample = PrimarySample("statements", "BBB", "us-gaap:Revenues", D(1000), "filing", None, day)
    (resolved,) = await resolve_stored_values(pool, [sample])
    assert resolved.stored_value == D(1000)
    assert compare_sample(resolved, DEFAULT_THRESHOLDS["statements"]).matched
    await pool.execute(
        cols + "('BBB','us-gaap:Revenues','income','2026-04-01',$1,'reported',520,'USD','{}','sec',"
        "'https://x',now(),0)",
        day,
    )
    (ambiguous,) = await resolve_stored_values(pool, [sample])
    assert ambiguous.stored_value is None
