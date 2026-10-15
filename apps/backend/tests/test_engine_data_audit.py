"""Second-look audits for prices, fund holdings and digests: pure rules, then the notices."""

import datetime as dt
import json

import asyncpg
import pytest

from steward.engine import data_audit as audit
from tests.conftest import MigratedDatabase

NOW = dt.datetime.now(dt.UTC)
DAY = NOW.date() - dt.timedelta(days=1)


def test_two_sources_that_agree_are_fine_and_ones_that_differ_are_flagged() -> None:
    assert audit.price_disagreement("AAA", DAY, {"stooq": 100.0, "eodhd": 100.5}) is None
    assert audit.price_disagreement("AAA", DAY, {"stooq": 100.0}) is None
    found = audit.price_disagreement("AAA", DAY, {"stooq": 100.0, "eodhd": 110.0})
    assert found is not None
    assert "differ" in found.detail


def test_only_a_very_large_one_day_move_is_flagged() -> None:
    assert audit.price_jump("AAA", DAY, 100.0, 110.0) is None
    assert audit.price_jump("AAA", DAY, 100.0, 45.0) is not None
    assert audit.price_jump("AAA", DAY, 100.0, 150.0) is not None


def test_holdings_must_add_up_to_the_fund() -> None:
    assert audit.weights_problem("F", 0.99, 0.08) is None
    assert audit.weights_problem("F", 0.5, 0.08) is not None
    assert audit.weights_problem("F", 1.3, 0.08) is not None
    assert audit.weights_problem("F", 1.0, 0.7) is not None


def test_a_digest_is_flagged_only_when_enough_sentences_are_unbacked() -> None:
    assert audit.digest_problem(1, "Daily", 9, 1, 0) is None
    assert audit.digest_problem(1, "Daily", 2, 1, 0) is None  # too few to judge
    assert audit.digest_problem(1, "Daily", 5, 3, 0) is not None


def test_portfolio_totals_must_add_up_and_the_sync_must_be_recent() -> None:
    today = dt.date(2026, 10, 14)
    fine = audit.portfolio_problems(today, 1000.0, 1004.0, 96.0, 1100.0, 0, today)
    assert fine == []
    wrong_sum = audit.portfolio_problems(today, 800.0, 1000.0, 100.0, 1100.0, 0, today)
    assert [f.subject.split(":")[1] for f in wrong_sum] == ["positions"]
    wrong_total = audit.portfolio_problems(today, 1000.0, 1000.0, 100.0, 1500.0, 0, today)
    assert [f.subject.split(":")[1] for f in wrong_total] == ["total"]
    bad = audit.portfolio_problems(today, 1000.0, 1000.0, 100.0, 1100.0, 2, today)
    assert [f.subject.split(":")[1] for f in bad] == ["rows"]
    old = audit.portfolio_problems(
        today, 1000.0, 1000.0, 100.0, 1100.0, 0, today + dt.timedelta(days=5)
    )
    assert [f.subject for f in old] == ["portfolio:stale"]


def test_alerts_that_fail_or_stay_unsent_are_reported() -> None:
    assert audit.undelivered_problem("telegram", 0, 0) is None
    one = audit.undelivered_problem("telegram", 2, 0)
    assert one is not None and "2 failed" in one.detail
    two = audit.undelivered_problem("email", 0, 3)
    assert two is not None and "3 are still waiting" in two.detail


def test_a_balance_sheet_must_balance_and_a_big_restatement_is_reported() -> None:
    day = dt.date(2026, 6, 30)
    assert audit.statement_problem("AAA", day, 1000.0, 1002.0) is None
    off = audit.statement_problem("AAA", day, 1000.0, 1100.0)
    assert off is not None and off.subject == "statement:balance:AAA:2026-06-30"
    assert audit.restatement_problem("AAA", "us-gaap:Revenues", day, 100.0, 102.0) is None
    moved = audit.restatement_problem("AAA", "us-gaap:Revenues", day, 100.0, 120.0)
    assert moved is not None and "Revenues" in moved.title


def test_chat_gaps_are_reported_only_when_there_are_several() -> None:
    assert audit.chat_gap_problem(["a", "b"]) is None
    found = audit.chat_gap_problem(["What is X?", "Why Y?", "Z?"])
    assert found is not None and found.subject == "chat:gaps" and "3 chat questions" in found.title


async def test_graph_links_to_self_or_both_partner_and_competitor_are_flagged(
    migrated_db: MigratedDatabase, backend_pool: asyncpg.Pool
) -> None:
    admin = await asyncpg.connect(migrated_db.admin_url)

    async def edge(subject: str, obj: str, kind: str, url: str) -> None:
        await admin.execute(
            "insert into public.company_graph_edge (subject_key, object_key, object_name,"
            " edge_type, direction, status, evidence_tier, source, source_url)"
            " values ($1, $2, $2, $3, 'outgoing', 'confirmed', 'a', 's', $4)",
            subject,
            obj,
            kind,
            url,
        )

    try:
        await edge("AAA", "AAA", "partner", "http://x/1")
        await edge("BBB", "CCC", "partner", "http://x/2")
        await edge("BBB", "CCC", "competitor", "http://x/3")
        await edge("DDD", "EEE", "partner", "http://x/4")
        found = await audit.graph_edges(backend_pool, NOW)
    finally:
        await admin.close()
    assert sorted(f.subject.split(":")[1] for f in found) == ["opposed", "self"]


async def test_macro_values_known_before_release_or_out_of_order_or_disputed_are_flagged(
    migrated_db: MigratedDatabase, backend_pool: asyncpg.Pool
) -> None:
    admin = await asyncpg.connect(migrated_db.admin_url)
    period = dt.date(2026, 8, 1)
    day = NOW - dt.timedelta(days=3)

    async def add(
        series: str, source: str, value: float, revision: int, known: dt.datetime, release=None
    ) -> None:  # type: ignore[no-untyped-def]
        await admin.execute(
            "insert into public.macro_observation (series_id, source, geography, period, value,"
            " unit, frequency, release_at, known_at, vintage, revision, source_url)"
            " values ($1, $2, 'US', $3, $4, 'pct', 'monthly', $5, $6, 'v', $7, 'http://x')",
            series,
            source,
            period,
            value,
            release,
            known,
            revision,
        )

    try:
        # Known five days before it was released.
        await add("EARLY", "fred", 1.0, 0, day - dt.timedelta(days=5), release=day)
        # Revision 1 known before revision 0.
        await add("BACK", "alfred", 2.0, 0, day)
        await add("BACK", "alfred", 2.1, 1, day - dt.timedelta(days=1))
        # Two sources 10% apart; a third series that agrees is left alone.
        await add("DISPUTE", "fred", 3.0, 0, day)
        await add("DISPUTE", "ecb", 3.3, 0, day)
        await add("AGREE", "fred", 4.0, 0, day)
        await add("AGREE", "ecb", 4.01, 0, day)
        found = await audit.macro(backend_pool, NOW)
    finally:
        await admin.close()
    assert sorted(f.subject.split(":")[1:3] for f in found) == [
        ["backwards", "BACK"],
        ["early", "EARLY"],
        ["sources", "DISPUTE"],
    ]


async def open_notices(pool: asyncpg.Pool) -> int:
    return int(
        await pool.fetchval(
            "select count(*) from public.ops_task where kind = $1 and status <> 'resolved'",
            audit.KIND,
        )
    )


def test_a_zero_against_a_real_figure_is_a_full_disagreement() -> None:
    assert audit.relative_gap(0.0, 0.0) == 0.0
    assert audit.relative_gap(0.0, 5.0) == 1.0
    assert audit.relative_gap(5.0, 0.0) == 1.0
    assert audit.relative_gap(100.0, 90.0) == pytest.approx(0.1)
    assert audit.price_disagreement("AAA", DAY, {"stooq": 0.0, "eodhd": 10.0}) is not None
    day = dt.date(2026, 6, 30)
    assert audit.statement_problem("AAA", day, 0.0, 500.0) is not None


async def test_a_real_split_in_raw_bars_is_not_called_a_missing_split(
    migrated_db: MigratedDatabase, backend_pool: asyncpg.Pool
) -> None:
    admin = await asyncpg.connect(migrated_db.admin_url)
    try:
        for days_ago, raw, adjusted in ((3, 400.0, 100.0), (2, 100.0, 100.0)):
            for bar_type, close in (("raw", raw), ("adjusted", adjusted)):
                await admin.execute(
                    "insert into public.price_bar (symbol, date, close, source, bar_type)"
                    " values ('AAA', $1, $2, 'stooq', $3)",
                    NOW.date() - dt.timedelta(days=days_ago),
                    close,
                    bar_type,
                )
    finally:
        await admin.close()
    assert [f for f in await audit.prices(backend_pool, NOW) if f.subject.startswith("jump:")] == []


async def test_cash_in_pies_and_reserved_count_toward_the_account_total(
    migrated_db: MigratedDatabase, backend_pool: asyncpg.Pool
) -> None:
    admin = await asyncpg.connect(migrated_db.admin_url)
    try:
        job_id = await admin.fetchval(
            "insert into public.job (job_type, trigger, status) values ('portfolio_sync',"
            " 'schedule', 'succeeded') returning id"
        )
        await admin.execute(
            "insert into public.account_snapshot (snapshot_date, job_id, captured_at,"
            " t212_account_id, account_currency, total_value, cash_available, cash_in_pies,"
            " cash_reserved, invested_value, invested_cost, unrealized_ppl, realized_ppl,"
            " base_currency, fx_source, fx_rate, cash_base, invested_base, total_base,"
            " weight_basis) values ($1, $2, $3, 1, 'EUR', 1100, 50, 30, 20, 1000, 1000, 0, 0,"
            " 'EUR', 'ecb', 1, 100, 1000, 1100, 'total')",
            NOW.date(),
            job_id,
            NOW,
        )
    finally:
        await admin.close()
    found = await audit.portfolio(backend_pool, NOW)
    assert "total" not in [f.subject.split(":")[1] for f in found]


async def test_a_failing_audit_does_not_stop_the_rest_or_resolve_anything(
    backend_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def finds(pool: asyncpg.Pool, now: dt.datetime) -> list[audit.Finding]:
        return [audit.Finding("x:1", "Something is off", "detail")]

    async def breaks(pool: asyncpg.Pool, now: dt.datetime) -> list[audit.Finding]:
        raise RuntimeError("boom")

    monkeypatch.setattr(audit, "AUDITS", {"breaks": breaks, "finds": finds})
    await backend_pool.execute(
        "insert into public.ops_task (kind, subject, title, detail, status) values ($1, 'old:1',"
        " 't', 'd', 'needs_owner')",
        audit.KIND,
    )
    counts = await audit.run(backend_pool, NOW)
    assert counts == {"breaks_failed": 1, "finds": 1}
    assert await open_notices(backend_pool) == 2  # the new one, and the old one left alone


async def test_the_run_opens_one_notice_per_finding_and_closes_it_when_fixed(
    migrated_db: MigratedDatabase, backend_pool: asyncpg.Pool
) -> None:
    admin = await asyncpg.connect(migrated_db.admin_url)
    try:
        for source, close in (("stooq", 100.0), ("eodhd", 120.0)):
            await admin.execute(
                "insert into public.price_bar (symbol, date, close, source) values"
                " ('AAA', $1, $2, $3)",
                DAY,
                close,
                source,
            )
        claims = {"mode": "mark", "supported": 2, "unsupported": 6, "unchecked": 0}
        await admin.execute(
            "insert into public.report (type, period, title, body, body_md) values ('digest',"
            " 'daily', 'Daily digest', $1::jsonb, 'x')",
            json.dumps({"claims": claims}),
        )
        counts = await audit.run(backend_pool, NOW)
        assert counts["prices"] == 1
        assert counts["digests"] == 1
        assert await open_notices(backend_pool) == 2

        await admin.execute("delete from public.price_bar where source = 'eodhd'")
        await admin.execute("delete from public.report")
    finally:
        await admin.close()
    await audit.run(backend_pool, NOW)
    assert await open_notices(backend_pool) == 0
