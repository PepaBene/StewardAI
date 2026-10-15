import datetime as dt

import pytest

from steward.engine import watchers


def test_review_is_near_only_in_the_last_days_of_the_month() -> None:
    assert watchers.review_is_near(dt.date(2026, 10, 30))
    assert watchers.review_is_near(dt.date(2026, 10, 31))
    assert not watchers.review_is_near(dt.date(2026, 10, 14))
    assert not watchers.review_is_near(dt.date(2026, 11, 1))


def test_the_review_lead_time_is_the_owners() -> None:
    assert not watchers.review_is_near(dt.date(2026, 10, 28))
    assert watchers.review_is_near(dt.date(2026, 10, 28), lead_days=5)


def test_watcher_settings_cannot_be_set_to_switch_a_watcher_off() -> None:
    import pytest

    from steward.engine.gates import WatcherSettings

    assert WatcherSettings().large_move == 0.08
    for bad in ({"large_move": 0.0}, {"insider_min_people": 1}, {"review_lead_days": 0}):
        with pytest.raises(ValueError):
            WatcherSettings(**bad)


def test_only_the_owner_route_can_write_the_watcher_settings() -> None:
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src"
    callers = {
        p.relative_to(src).as_posix()
        for p in src.rglob("*.py")
        if re.search(r"\bgates\.save_watchers\(", p.read_text(encoding="utf-8"))
    }
    assert callers == {"api/action_routes.py"}


async def test_a_held_stock_that_moved_past_the_line_is_told_once(
    backend_pool, migrated_db, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    import asyncpg

    from steward.autopilot import ledger
    from steward.engine.gates import WatcherSettings

    async def held(_pool):  # type: ignore[no-untyped-def]
        return {"AAA_US_EQ": 0.1, "BBB_US_EQ": 0.1}

    monkeypatch.setattr(ledger, "holding_weights", held)
    now = dt.datetime.now(dt.UTC)
    admin = await asyncpg.connect(migrated_db.admin_url)
    try:
        for i, (ticker, after) in enumerate((("AAA_US_EQ", 90.0), ("BBB_US_EQ", 101.0))):
            await admin.execute(
                "insert into public.instrument (t212_ticker, price_symbol) values ($1, $2)",
                ticker,
                f"S{i}",
            )
            for day, close in ((now.date() - dt.timedelta(days=2), 100.0), (now.date(), after)):
                await admin.execute(
                    "insert into public.price_bar (symbol, date, close, bar_type)"
                    " values ($1, $2, $3, 'adjusted')",
                    f"S{i}",
                    day,
                    close,
                )
    finally:
        await admin.close()
    assert await watchers.large_moves(backend_pool, now, WatcherSettings()) == 1  # -10% vs +1%
    assert await watchers.large_moves(backend_pool, now, WatcherSettings()) == 0  # cooling down
    # A stricter line (a 30% move) leaves nothing to say.
    assert await watchers.large_moves(backend_pool, now, WatcherSettings(large_move=0.3)) == 0


def test_a_guided_figure_is_compared_with_the_one_before_it() -> None:
    assert watchers.guidance_move(100.0, 110.0) == pytest.approx(0.1)
    assert watchers.guidance_move(100.0, 90.0) == pytest.approx(-0.1)
    assert watchers.guidance_move(0.0, 5.0) is None


async def test_a_held_company_that_raised_its_guidance_is_told_once(
    backend_pool, migrated_db, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    import asyncpg

    from steward.autopilot import ledger
    from steward.engine.gates import WatcherSettings

    async def held(_pool):  # type: ignore[no-untyped-def]
        return {"AAA_US_EQ": 0.1, "BBB_US_EQ": 0.1}

    monkeypatch.setattr(ledger, "holding_weights", held)
    now = dt.datetime.now(dt.UTC)
    admin = await asyncpg.connect(migrated_db.admin_url)
    try:
        for i, (ticker, second) in enumerate((("AAA_US_EQ", 110.0), ("BBB_US_EQ", 100.5))):
            cik = f"000000000{i + 1}"
            await admin.execute(
                "insert into public.instrument (t212_ticker, cik) values ($1, $2)", ticker, cik
            )
            issuer = await admin.fetchval(
                "insert into public.issuer (legal_name, cik) values ($1, $2) returning id",
                ticker,
                cik,
            )
            source = await admin.fetchval(
                "insert into public.consensus_source (issuer_id, source_key, url, discovery_method,"
                " publisher, format) values ($1, $2, 'http://x', 'manual', 'ir', 'html')"
                " returning id",
                issuer,
                f"src-{i}",
            )
            for days, value in ((20, 100.0), (2, second)):
                snap = await admin.fetchval(
                    "insert into public.public_estimate_snapshot (issuer_id, source_id, series,"
                    " published_at, known_at) values ($1, $2, 'company_guidance', $3, $3)"
                    " returning id",
                    issuer,
                    source,
                    now - dt.timedelta(days=days),
                )
                await admin.execute(
                    "insert into public.estimate_value (snapshot_id, metric, fiscal_period,"
                    " period_end, unit, basis, low, high)"
                    " values ($1, 'revenue', 'FY2026', '2026-12-31', 'amount', 'GAAP', $2, $2)",
                    snap,
                    value,
                )
    finally:
        await admin.close()
    assert (
        await watchers.guidance_changes(backend_pool, now, WatcherSettings()) == 1
    )  # +10% vs +0.5%
    assert (
        await watchers.guidance_changes(backend_pool, now, WatcherSettings()) == 0
    )  # cooling down


def test_month_end_handles_february() -> None:
    assert watchers.month_end(dt.date(2028, 2, 3)) == dt.date(2028, 2, 29)
