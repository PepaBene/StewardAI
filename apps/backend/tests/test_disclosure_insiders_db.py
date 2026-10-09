"""P4/P5 against a real database: insider known_at, an amendment replacing its original at three
cutoffs, the exercise-and-sale bundle, the override ledger, and claim status as of a date."""

import datetime as dt
from decimal import Decimal as D

import asyncpg
import pytest

from steward.disclosure import dependency_read, insider_analysis

pytestmark = pytest.mark.db

UTC = dt.UTC
BEFORE = dt.datetime(2026, 5, 1, tzinfo=UTC)
BETWEEN = dt.datetime(2026, 6, 10, tzinfo=UTC)
AFTER = dt.datetime(2026, 6, 30, tzinfo=UTC)
ORIG = "0000099999-26-000001"
AMEND = "0000099999-26-000002"


async def filing(pool: asyncpg.Pool, acc: str, filed: dt.date, form: str = "4") -> None:
    await pool.execute(
        """insert into public.insider_filing
             (accession, ticker, issuer_cik, form, filed, source_url)
           values ($1,'AAA','0000011111',$2,$3,'https://sec.example/'||$1)""",
        acc,
        form,
        filed,
    )


async def trade(
    pool: asyncpg.Pool,
    acc: str,
    idx: int,
    cls: str,
    code: str,
    shares: str,
    price: str,
    when: dt.date,
    *,
    derivative: bool = False,
    plan: bool | None = False,
) -> None:
    await pool.execute(
        """insert into public.insider_transaction
             (accession, row_index, reporting_owner_cik, owner_name, is_officer, transaction_date,
              transaction_code, shares, price_per_share, shares_owned_after, direct_indirect,
              derivative, economic_class, classification_basis, rule_10b5_1_plan)
           values ($1,$2,'0000000007','Pat Officer',true,$3,$4,$5,$6,1000,'D',$7,$8,'code',$9)""",
        acc,
        idx,
        when,
        code,
        D(shares),
        D(price),
        derivative,
        cls,
        plan,
    )


async def test_known_at_defaults_to_the_end_of_the_filing_day(backend_pool: asyncpg.Pool) -> None:
    await filing(backend_pool, ORIG, dt.date(2026, 6, 3))
    known = await backend_pool.fetchval(
        "select known_at from public.insider_filing where accession=$1", ORIG
    )
    assert known == dt.datetime(2026, 6, 3, 23, 59, 59, tzinfo=UTC)


async def test_an_amendment_replaces_its_original_only_once_it_was_known(
    backend_pool: asyncpg.Pool,
) -> None:
    await filing(backend_pool, ORIG, dt.date(2026, 6, 3))
    await trade(backend_pool, ORIG, 0, "open_market_buy", "P", "100", "10", dt.date(2026, 6, 1))
    await filing(backend_pool, AMEND, dt.date(2026, 6, 20), "4/A")
    await trade(backend_pool, AMEND, 0, "open_market_buy", "P", "90", "10", dt.date(2026, 6, 1))
    await backend_pool.execute(
        "update public.insider_filing set superseded_by_accession=$1 where accession=$2",
        AMEND,
        ORIG,
    )

    def bought(view: dict) -> D:
        return D(
            next(n for n in view["net"] if n["window_days"] == 90 and n["role"] == "all")[
                "buy_shares"
            ]
        )

    assert bought(await insider_analysis.insider_activity(backend_pool, "AAA", BEFORE)) == 0
    assert bought(await insider_analysis.insider_activity(backend_pool, "AAA", BETWEEN)) == 100
    assert bought(await insider_analysis.insider_activity(backend_pool, "AAA", AFTER)) == 90


async def test_an_exercise_and_same_day_sale_is_a_bundle_in_the_database_read(
    backend_pool: asyncpg.Pool,
) -> None:
    await filing(backend_pool, ORIG, dt.date(2026, 6, 12))
    d = dt.date(2026, 6, 10)
    await trade(backend_pool, ORIG, 0, "option_exercise", "M", "500", "2", d, derivative=True)
    await trade(backend_pool, ORIG, 1, "open_market_sell", "S", "500", "30", d)
    view = await insider_analysis.insider_activity(backend_pool, "AAA", AFTER)
    assert len(view["bundles"]) == 1
    allr = next(n for n in view["net"] if n["window_days"] == 30 and n["role"] == "all")
    assert allr["sell_shares"] == "0" and allr["bundled_sales_excluded"] == 1


async def test_overrides_are_an_append_only_ledger_with_a_reason(
    backend_pool: asyncpg.Pool,
) -> None:
    await filing(backend_pool, ORIG, dt.date(2026, 6, 3))
    await trade(backend_pool, ORIG, 0, "open_market_sell", "S", "100", "10", dt.date(2026, 6, 1))
    with pytest.raises(insider_analysis.OverrideError):
        await insider_analysis.record_override(
            backend_pool, ORIG, 0, "tax_withholding", "short", "owner:1"
        )
    with pytest.raises(insider_analysis.OverrideError):
        await insider_analysis.record_override(
            backend_pool, ORIG, 9, "tax_withholding", "long enough reason", "owner:1"
        )
    oid = await insider_analysis.record_override(
        backend_pool, ORIG, 0, "tax_withholding", "Footnote says shares withheld", "owner:1"
    )
    row = await backend_pool.fetchrow(
        "select original_class, new_class from public.insider_classification_override where id=$1",
        oid,
    )
    assert (row["original_class"], row["new_class"]) == ("open_market_sell", "tax_withholding")
    with pytest.raises(asyncpg.PostgresError):
        await backend_pool.execute(
            "update public.insider_classification_override"
            " set reason='edited later on' where id=$1",
            oid,
        )
    before = await insider_analysis.insider_activity(backend_pool, "AAA", AFTER)
    assert before["transactions"][0]["economic_class"] == "open_market_sell"  # not yet decided
    view = await insider_analysis.insider_activity(
        backend_pool, "AAA", dt.datetime.now(UTC) + dt.timedelta(days=1)
    )
    assert view["transactions"][0]["economic_class"] == "tax_withholding"
    assert view["transactions"][0]["classifier_class"] == "open_market_sell"
    assert view["transactions"][0]["countable"] is False


async def claim(
    pool: asyncpg.Pool, name: str | None, share: str | None, status: str, **kw: object
) -> int:
    return int(
        await pool.fetchval(
            """insert into public.relationship_disclosure
                 (ticker, relationship, direction, counterparty_key, counterparty_name,
                  anonymous_counterparty, reported_share, passage, source_url, source, status,
                  known_at, claim_shape, counterparty_count, denominator_kind, period_end)
               values ('AAA','customer','issuer_sells_to',$1,$2,$3,$4,
                       'One customer accounted for 22% of revenue in 2025.',
                       'https://sec.example/10k','sec_10k',$5,now(),$6,$7,'total_revenue',$8)
               returning id""",
            kw.get("key"),
            name,
            kw.get("shape", "named") != "named",
            None if share is None else D(share),
            status,
            kw.get("shape", "named"),
            kw.get("count"),
            dt.date(2025, 12, 31),
        )
    )


async def test_claim_status_is_resolved_as_of_the_requested_date(
    backend_pool: asyncpg.Pool,
) -> None:
    cid = await claim(backend_pool, "Acme", "0.22", "suggested", key="ACME")
    anon = await claim(backend_pool, None, "0.11", "confirmed", shape="anonymous")
    await backend_pool.execute(
        "update public.relationship_disclosure set status='confirmed', decided_at=$2 where id=$1",
        cid,
        dt.datetime(2026, 6, 20, tzinfo=UTC),
    )
    await backend_pool.execute(
        """insert into public.graph_status_log (table_name,row_id,from_status,to_status,changed_at)
           values ('relationship_disclosure',$1,'suggested','confirmed',$2)""",
        cid,
        dt.datetime(2026, 6, 20, tzinfo=UTC),
    )
    when = dt.datetime.now(UTC)
    early = await dependency_read.dependency_view(
        backend_pool, "AAA", dt.datetime(2026, 6, 15, tzinfo=UTC)
    )
    # known_at defaults to now(), so nothing is visible in the past: that is the point-in-time rule
    assert early["claims"] == []
    late = await dependency_read.dependency_view(backend_pool, "AAA", when + dt.timedelta(days=1))
    by_id = {c["id"]: c for c in late["claims"]}
    assert by_id[cid]["status"] == "confirmed" and by_id[anon]["shape"] == "anonymous"
    assert by_id[anon]["counterparty_key"] is None  # an anonymous label is never given an identity
    (c,) = late["concentration"]
    assert c["lower_bound"] == "0.33" and c["unknown_remainder"] == "0.67"
