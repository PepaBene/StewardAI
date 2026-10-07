"""P6.7 / P8.5 alert scans against a real database: a documented deal transition raises one alert
and a rumour none, a rerun raises none, a debt-maturity shift raises one alert with the numbers
that fired it, and the per-ticker throttle holds. Skipped without STEWARD_TEST_DATABASE_URL."""

import datetime as dt
import json

import asyncpg
import pytest

from steward.disclosure import alerts
from steward.disclosure.note_store import CONCEPT_PREFIX, NOTE_SOURCE
from tests.test_disclosure_deals_db import at, deal, evidence, move

pytestmark = pytest.mark.db

UTC = dt.UTC
NOW = dt.datetime(2026, 9, 1, 12, tzinfo=UTC)
SINCE = NOW - dt.timedelta(days=90)


async def rows(pool: asyncpg.Pool, kind: str) -> list[asyncpg.Record]:
    return await pool.fetch(
        "select * from public.disclosure_alert where kind = $1 order by id", kind
    )


async def test_a_documented_deal_transition_alerts_once_and_a_rumour_never(
    backend_pool: asyncpg.Pool,
) -> None:
    deal_id = await deal(backend_pool, "k1", "merger", "Acme to buy Beta", at(7, 1))
    ver, ev = await evidence(backend_pool, "e1", at(7, 1))
    await backend_pool.execute(
        """insert into public.deal_state_transition
             (deal_id, state, effective_at, known_at, document_version_id, evidence_id,
              rumor_source)
           values ($1, 'rumored', $2, $2, $3, $4, 'press report')""",
        deal_id,
        at(7, 1),
        ver,
        ev,
    )
    assert await alerts.scan_deals(backend_pool, "AAA", SINCE, NOW) == 0
    await move(backend_pool, deal_id, "announced", at(7, 10), at(7, 10), "e2")
    assert await alerts.scan_deals(backend_pool, "AAA", SINCE, NOW) == 1
    assert await alerts.scan_deals(backend_pool, "AAA", SINCE, NOW) == 0  # a rerun adds nothing
    (alert,) = await rows(backend_pool, "deal_state")
    assert alert["ticker"] == "AAA" and "announced" in alert["headline"]
    why = json.loads(alert["why_fired"])
    assert why["rule"] == "deal_state_transition" and why["inputs"]["state"] == "announced"


async def test_a_transition_known_after_the_window_does_not_alert_yet(
    backend_pool: asyncpg.Pool,
) -> None:
    deal_id = await deal(backend_pool, "k2", "merger", "Acme to buy Gamma", at(7, 1))
    await move(backend_pool, deal_id, "announced", at(10, 1), at(10, 1), "e3")
    assert await alerts.scan_deals(backend_pool, "AAA", SINCE, NOW) == 0


async def test_the_throttle_stops_a_sixth_alert_for_one_ticker_and_kind(
    backend_pool: asyncpg.Pool,
) -> None:
    results = [
        await alerts.record(
            backend_pool,
            kind="deal_state",
            ticker="AAA",
            dedupe_key=f"t:{i}",
            headline=f"alert {i}",
            detail={},
            known_at=NOW,
        )
        for i in range(7)
    ]
    assert results == [True] * alerts.THROTTLE_PER_DAY + [False] * 2
    # a throttled alert stored nothing, so it can still be raised later
    assert len(await rows(backend_pool, "deal_state")) == alerts.THROTTLE_PER_DAY
    # another kind for the same ticker is not throttled by this one
    assert await alerts.record(
        backend_pool,
        kind="insider_trade",
        ticker="AAA",
        dedupe_key="other",
        headline="x",
        detail={},
        known_at=NOW,
    )


async def test_the_same_dedupe_key_is_stored_once(backend_pool: asyncpg.Pool) -> None:
    args = {
        "kind": "deal_state",
        "ticker": "AAA",
        "dedupe_key": "same",
        "headline": "h",
        "detail": {},
        "known_at": NOW,
    }
    assert await alerts.record(backend_pool, **args) is True
    assert await alerts.record(backend_pool, **args) is False


async def seed_debt(
    pool: asyncpg.Pool, accession: str, known: dt.datetime, year_1: int, later: int
) -> None:
    issuer = await pool.fetchval("select id from public.issuer where cik = '0000011111'")
    if issuer is None:
        issuer = await pool.fetchval(
            "insert into public.issuer (legal_name, cik, country)"
            " values ('Acme', '0000011111', 'US') returning id"
        )
    for bucket, value in (("year_1", year_1), ("thereafter", later)):
        await pool.execute(
            """insert into public.financial_statement_fact
                 (issuer_id, ticker, accession, form, concept, line_item, statement, period_end,
                  basis, value, unit, currency, dimensions, source, source_url, known_at)
               values ($1,'AAA',$2,'10-K',$3,'Debt maturities','note','2025-12-31','as_filed',$4,
                       'USD','USD',$5::jsonb,$6,'https://sec.example/x',$7)""",
            issuer,
            accession,
            CONCEPT_PREFIX + "debt_maturity",
            value,
            json.dumps({"note": {"bucket": bucket, "status": "reviewed"}}),
            NOTE_SOURCE,
            known,
        )


async def test_a_debt_maturity_shift_alerts_once_with_the_numbers_that_fired_it(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_debt(backend_pool, "0000011111-25-000001", at(2, 1), year_1=100, later=900)
    assert (
        await alerts.scan_debt_maturity(backend_pool, "AAA", NOW) == 0
    )  # one filing, nothing to compare
    await seed_debt(backend_pool, "0000011111-26-000001", at(8, 1), year_1=400, later=600)
    assert await alerts.scan_debt_maturity(backend_pool, "AAA", NOW) == 1
    assert await alerts.scan_debt_maturity(backend_pool, "AAA", NOW) == 0
    (alert,) = await rows(backend_pool, "debt_maturity_change")
    inputs = json.loads(alert["why_fired"])["inputs"]
    assert inputs == {"prior_share": "10.0", "current_share": "40.0", "move_points": "+30.0"}


async def test_a_small_debt_maturity_move_does_not_alert(backend_pool: asyncpg.Pool) -> None:
    await seed_debt(backend_pool, "0000011111-25-000001", at(2, 1), year_1=100, later=900)
    await seed_debt(backend_pool, "0000011111-26-000001", at(8, 1), year_1=120, later=880)
    assert await alerts.scan_debt_maturity(backend_pool, "AAA", NOW) == 0
