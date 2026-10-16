"""P3-P5 gap closure against a real database: alerts raised once, split restatement, share-count
context, entity mapping, inbound claims and owner weights in the dependency view, structured TED
awards, the anonymous label, portfolio exposure and the adjudication store."""

import datetime as dt
from decimal import Decimal as D

import asyncpg
import pytest

from steward.disclosure import adjudication, alerts, dependency_read, insider_analysis
from steward.graph.relationships import upsert_disclosure
from steward.graph.ted import TedAward, store_award_detail

pytestmark = pytest.mark.db

UTC = dt.UTC
NOW = dt.datetime(2026, 7, 1, tzinfo=UTC)


async def filing(pool: asyncpg.Pool, acc: str, filed: dt.date, form: str = "4") -> None:
    await pool.execute(
        """insert into public.insider_filing
             (accession, ticker, issuer_cik, form, filed, source_url)
           values ($1,'AAA','0000011111',$2,$3,'https://sec.example/'||$1)""",
        acc,
        form,
        filed,
    )


async def buy(
    pool: asyncpg.Pool, acc: str, idx: int, shares: str, price: str, when: dt.date, owner: str = "7"
) -> None:
    await pool.execute(
        """insert into public.insider_transaction
             (accession, row_index, reporting_owner_cik, owner_name, is_officer, transaction_date,
              transaction_code, shares, price_per_share, shares_owned_after, direct_indirect,
              derivative, economic_class, classification_basis, rule_10b5_1_plan)
           values ($1,$2,$3,'Pat Officer',true,$4,'P',$5,$6,1000,'D',false,'open_market_buy',
                   'code',false)""",
        acc,
        idx,
        owner.zfill(10),
        when,
        D(shares),
        D(price),
    )


# ------------------------------------------------------------------------------------ insiders
async def test_a_material_trade_alerts_once_however_often_the_scan_runs(
    backend_pool: asyncpg.Pool,
) -> None:
    await filing(backend_pool, "0000099999-26-000001", dt.date(2026, 6, 3))
    await buy(backend_pool, "0000099999-26-000001", 0, "100", "2000", dt.date(2026, 6, 1))
    assert await alerts.scan_insiders(backend_pool, "AAA", NOW) == 1
    assert await alerts.scan_insiders(backend_pool, "AAA", NOW) == 0
    [a] = await alerts.list_alerts(backend_pool, ticker="AAA")
    assert a["kind"] == "insider_trade" and "open-market purchase" in a["headline"]
    assert a["detail"]["value"] == "200000"
    assert a["source_url"].endswith("0000099999-26-000001")


async def test_an_amendment_repeating_the_trade_does_not_alert_again(
    backend_pool: asyncpg.Pool,
) -> None:
    await filing(backend_pool, "0000099999-26-000001", dt.date(2026, 6, 3))
    await buy(backend_pool, "0000099999-26-000001", 0, "100", "2000", dt.date(2026, 6, 1))
    await alerts.scan_insiders(backend_pool, "AAA", NOW)
    await filing(backend_pool, "0000099999-26-000002", dt.date(2026, 6, 10), "4/A")
    await buy(backend_pool, "0000099999-26-000002", 0, "100", "2000", dt.date(2026, 6, 1))
    assert await alerts.scan_insiders(backend_pool, "AAA", NOW) == 0
    assert len(await alerts.list_alerts(backend_pool)) == 1


async def test_a_small_trade_does_not_alert(backend_pool: asyncpg.Pool) -> None:
    await filing(backend_pool, "0000099999-26-000001", dt.date(2026, 6, 3))
    await buy(backend_pool, "0000099999-26-000001", 0, "10", "20", dt.date(2026, 6, 1))
    assert await alerts.scan_insiders(backend_pool, "AAA", NOW) == 0


async def test_an_alert_can_be_acknowledged_once(backend_pool: asyncpg.Pool) -> None:
    await filing(backend_pool, "0000099999-26-000001", dt.date(2026, 6, 3))
    await buy(backend_pool, "0000099999-26-000001", 0, "100", "2000", dt.date(2026, 6, 1))
    await alerts.scan_insiders(backend_pool, "AAA", NOW)
    [a] = await alerts.list_alerts(backend_pool)
    assert await alerts.acknowledge(backend_pool, a["id"], "owner:1") is True
    assert await alerts.acknowledge(backend_pool, a["id"], "owner:1") is False
    assert await alerts.list_alerts(backend_pool) == []
    assert len(await alerts.list_alerts(backend_pool, include_acknowledged=True)) == 1


async def test_a_confirmed_split_restates_the_trade_but_only_once_it_was_known(
    backend_pool: asyncpg.Pool,
) -> None:
    await filing(backend_pool, "0000099999-26-000001", dt.date(2026, 1, 8))
    await buy(backend_pool, "0000099999-26-000001", 0, "100", "400", dt.date(2026, 1, 5))
    await backend_pool.execute(
        """insert into public.corporate_action
             (symbol, action_type, ex_date, ratio, source, source_url, known_at, status)
           values ('AAA','split',$1,4,'test','https://x.example',$2,'confirmed')""",
        dt.date(2026, 3, 1),
        dt.datetime(2026, 2, 20, tzinfo=UTC),
    )

    def bought(view: dict) -> D:
        return D(
            next(n for n in view["net"] if n["window_days"] == 365 and n["role"] == "all")[
                "buy_shares"
            ]
        )

    early = await insider_analysis.insider_activity(
        backend_pool, "AAA", dt.datetime(2026, 2, 1, tzinfo=UTC)
    )
    late = await insider_analysis.insider_activity(backend_pool, "AAA", NOW)
    assert bought(early) == 100 and early["splits_applied"] == 0
    assert bought(late) == 400 and late["splits_applied"] == 1
    assert late["transactions"][0]["price"] == "100"
    assert any("split" in n for n in late["notes"])


async def test_context_gives_share_count_percent_and_nearby_events_without_a_motive(
    backend_pool: asyncpg.Pool,
) -> None:
    await filing(backend_pool, "0000099999-26-000001", dt.date(2026, 6, 3))
    await buy(backend_pool, "0000099999-26-000001", 0, "1000", "10", dt.date(2026, 6, 1))
    for shares, as_of in ((1_000_000, dt.date(2026, 3, 31)), (1_100_000, dt.date(2026, 5, 15))):
        await backend_pool.execute(
            """insert into public.shares_outstanding (ticker, shares, as_of, method, filed, raw)
               values ('AAA',$1,$2,'manual',$2,'{}')""",
            D(shares),
            as_of,
        )
    doc = await backend_pool.fetchval(
        "insert into public.source_document (regulator, external_id, stable_url)"
        " values ('sec','0000111111-26-000010','https://example.test/8k') returning id"
    )
    version = await backend_pool.fetchval(
        "insert into public.source_document_version (document_id, content_hash, known_at)"
        " values ($1,'h1','2026-05-28') returning id",
        doc,
    )
    evidence = await backend_pool.fetchval(
        "insert into public.source_evidence (document_version_id, quote)"
        " values ($1,'The company announced a financing.') returning id",
        version,
    )
    await backend_pool.execute(
        """insert into public.company_event
             (ticker, event_type, event_state, announced_at, known_at, source, source_url,
              status, evidence_id)
           values ('AAA','financing','announced',$1,$2,'t','https://x.example','confirmed',$3)""",
        dt.date(2026, 5, 28),
        dt.datetime(2026, 5, 28, tzinfo=UTC),
        evidence,
    )
    view = await insider_analysis.insider_activity(backend_pool, "AAA", NOW)
    ctx = view["context"]
    assert ctx["shares_outstanding"] == "1100000"
    assert D(ctx["reported_share_count_change"]) == D("0.1")
    assert [e["event_type"] for e in ctx["events"]] == ["financing"]
    assert D(view["transactions"][0]["percent_of_outstanding"]) < D("0.1")


async def test_with_no_share_count_the_percent_is_unknown_and_says_so(
    backend_pool: asyncpg.Pool,
) -> None:
    await filing(backend_pool, "0000099999-26-000001", dt.date(2026, 6, 3))
    await buy(backend_pool, "0000099999-26-000001", 0, "1000", "10", dt.date(2026, 6, 1))
    view = await insider_analysis.insider_activity(backend_pool, "AAA", NOW)
    assert view["transactions"][0]["percent_of_outstanding"] is None
    assert any("share count" in n for n in view["notes"])


# ------------------------------------------------------------------------------ major holders
async def stake(
    pool: asyncpg.Pool,
    acc: str,
    pct: str,
    filed: dt.date,
    *,
    amendment: bool = False,
    prior: str | None = None,
) -> None:
    await pool.execute(
        """insert into public.control_stake_filing
             (accession, form, schedule, is_amendment, filed, issuer_cik, issuer_cusip, filer_cik,
              stake_shares, stake_percent, prior_accession, source_url)
           values ($1,$2,'13D',$3,$4,'0000011111','037833100','0000022222',1000,$5,$6,
                   'https://sec.example/'||$1)""",
        acc,
        "SC 13D/A" if amendment else "SC 13D",
        amendment,
        filed,
        D(pct),
        prior,
    )
    await pool.execute(
        """insert into public.control_stake_issuer_link
             (accession, ticker, match_basis, class_status, percent_check)
           values ($1,'AAA','cusip','cusip_match','not_checkable')""",
        acc,
    )
    await pool.execute(
        """insert into public.control_stake_holder
             (accession, row_index, person_key, person_name, aggregate_shares)
           values ($1,0,'k','Big Holder LP',1000)""",
        acc,
    )


async def test_a_new_13d_alerts_and_a_small_amendment_does_not(backend_pool: asyncpg.Pool) -> None:
    since = dt.datetime(2026, 5, 1, tzinfo=UTC)
    await stake(backend_pool, "0000022222-26-000001", "6.1", dt.date(2026, 5, 10))
    await stake(
        backend_pool,
        "0000022222-26-000002",
        "6.4",
        dt.date(2026, 6, 10),
        amendment=True,
        prior="0000022222-26-000001",
    )
    got = await alerts.scan_major_holders(backend_pool, "AAA", since, NOW)
    assert got == 1
    [a] = await alerts.list_alerts(backend_pool)
    assert (
        a["kind"] == "major_holder" and "Big Holder LP" in a["headline"] and "6.1%" in a["headline"]
    )


async def test_a_material_amendment_alerts_with_both_percentages(
    backend_pool: asyncpg.Pool,
) -> None:
    await stake(backend_pool, "0000022222-26-000001", "6.1", dt.date(2026, 1, 10))
    await stake(
        backend_pool,
        "0000022222-26-000002",
        "8.0",
        dt.date(2026, 6, 10),
        amendment=True,
        prior="0000022222-26-000001",
    )
    got = await alerts.scan_major_holders(
        backend_pool, "AAA", dt.datetime(2026, 5, 1, tzinfo=UTC), NOW
    )
    assert got == 1
    [a] = await alerts.list_alerts(backend_pool)
    assert "6.1% to 8.0%" in a["headline"]
    # run again: the same filing never alerts twice
    assert (
        await alerts.scan_major_holders(
            backend_pool, "AAA", dt.datetime(2026, 5, 1, tzinfo=UTC), NOW
        )
        == 0
    )


# ------------------------------------------------------------------------------ relationships
async def claim(
    pool: asyncpg.Pool,
    ticker: str,
    name: str | None,
    share: str | None,
    status: str,
    known: dt.datetime,
    *,
    relationship: str = "customer",
    key: str | None = None,
    valid_to: dt.date | None = None,
    anonymous: bool = False,
) -> int:
    cid = int(
        await pool.fetchval(
            """insert into public.relationship_disclosure
                 (ticker, relationship, direction, counterparty_key, counterparty_name,
                  anonymous_counterparty, reported_share, fiscal_period, valid_from, valid_to,
                  passage, source_url, source, status, known_at, claim_shape, denominator_kind)
               values ($1,$2,$3,$4,$5,$6,$7,'FY2025',$8,$9,
                       'It accounted for 20% of revenue in fiscal 2025.',
                       'https://sec.example/'||$1||$5,'sec_10k',$10,$11,$12,
                       case when $7::numeric is null then null else 'total_revenue' end)
               returning id""",
            ticker,
            relationship,
            "issuer_sells_to" if relationship == "customer" else "issuer_buys_from",
            key,
            name,
            anonymous,
            None if share is None else D(share),
            dt.date(2025, 1, 1),
            valid_to,
            status,
            known,
            "anonymous" if anonymous else "named",
        )
    )
    await pool.execute(
        "delete from public.graph_status_log"
        " where table_name='relationship_disclosure' and row_id=$1",
        cid,
    )
    await pool.execute(
        """insert into public.graph_status_log (table_name,row_id,from_status,to_status,changed_at)
           values ('relationship_disclosure',$1,null,$2,$3)""",
        cid,
        status,
        known,
    )
    return cid


async def test_a_claim_confirmed_inside_the_window_raises_a_new_relationship_alert(
    backend_pool: asyncpg.Pool,
) -> None:
    cid = await claim(
        backend_pool, "AAA", "Big Co", "0.2", "suggested", dt.datetime(2026, 1, 10, tzinfo=UTC)
    )
    await backend_pool.execute(
        "update public.relationship_disclosure set status='confirmed' where id=$1", cid
    )
    await backend_pool.execute(
        """insert into public.graph_status_log (table_name,row_id,from_status,to_status,changed_at)
           values ('relationship_disclosure',$1,'suggested','confirmed',$2)""",
        cid,
        dt.datetime(2026, 5, 1, tzinfo=UTC),
    )
    since = dt.datetime(2026, 4, 1, tzinfo=UTC)
    assert await alerts.scan_relationships(backend_pool, "AAA", since, NOW) == 1
    assert await alerts.scan_relationships(backend_pool, "AAA", since, NOW) == 0
    [a] = await alerts.list_alerts(backend_pool)
    assert a["kind"] == "relationship_change" and "now discloses Big Co" in a["headline"]
    assert "20%" in a["headline"]


async def test_a_relationship_that_closed_in_the_window_raises_an_ended_alert(
    backend_pool: asyncpg.Pool,
) -> None:
    await claim(
        backend_pool,
        "AAA",
        "Old Co",
        "0.2",
        "confirmed",
        dt.datetime(2025, 6, 1, tzinfo=UTC),
        valid_to=dt.date(2026, 5, 1),
    )
    since = dt.datetime(2026, 4, 1, tzinfo=UTC)
    assert await alerts.scan_relationships(backend_pool, "AAA", since, NOW) == 1
    [a] = await alerts.list_alerts(backend_pool)
    assert "no longer lists Old Co" in a["headline"]


async def test_a_suggestion_never_raises_a_relationship_alert(backend_pool: asyncpg.Pool) -> None:
    await claim(
        backend_pool, "AAA", "Big Co", "0.2", "suggested", dt.datetime(2026, 5, 1, tzinfo=UTC)
    )
    assert (
        await alerts.scan_relationships(
            backend_pool, "AAA", dt.datetime(2026, 4, 1, tzinfo=UTC), NOW
        )
        == 0
    )


# ----------------------------------------------------------------------------- dependency view
async def test_inbound_claims_give_a_second_hop_and_owner_weights_are_labelled(
    backend_pool: asyncpg.Pool,
) -> None:
    known = dt.datetime(2026, 1, 10, tzinfo=UTC)
    # AAA's customer is BIG (key BIG); SUP (another reporter) says AAA is its customer at 30%.
    await claim(backend_pool, "AAA", "Big Co", "0.5", "confirmed", known, key="BIG")
    await claim(backend_pool, "SUP", "AAA Corp", "0.3", "confirmed", known, key="AAA")
    view = await dependency_read.dependency_view(backend_pool, "AAA", NOW, shock=D("0.2"))
    assert view["inbound_claims"] == 1
    big = next(s for s in view["scenarios"] if s["origin"] == "BIG")
    by_entity = {i["entity"]: i for i in big["impacts"]}
    assert D(by_entity["AAA"]["lower"]) == D("0.1") and by_entity["AAA"]["hop"] == 1
    assert D(by_entity["SUP"]["lower"]) == D("0.03") and by_entity["SUP"]["hop"] == 2
    assert not any(i["uses_owner_assigned"] for i in big["impacts"])

    await backend_pool.execute(
        """insert into public.owner_dependency_weight
             (ticker, relationship, counterparty_key, share, denominator_kind, as_of, note,
              assigned_by, created_at)
           values ('AAA','customer','OWN',0.25,'total_revenue',$1,
                   'My estimate from channel checks','owner:1',$2)""",
        dt.date(2026, 2, 1),
        dt.datetime(2026, 2, 1, tzinfo=UTC),
    )
    view = await dependency_read.dependency_view(backend_pool, "AAA", NOW, shock=D("0.2"))
    own = next(s for s in view["scenarios"] if s["origin"] == "OWN")
    assert own["impacts"][0]["uses_owner_assigned"] is True
    assert view["owner_weights"][0]["note"].startswith("My estimate")
    # reported concentration never includes the owner's weight
    assert all(e["label"] != "OWN" for c in view["concentration"] for e in c["entries"])
    before = await dependency_read.dependency_view(
        backend_pool, "AAA", dt.datetime(2026, 1, 20, tzinfo=UTC)
    )
    assert before["owner_weights"] == []  # a replay before the weight existed does not see it


async def test_opposite_claims_from_the_two_reporters_are_listed_as_a_conflict(
    backend_pool: asyncpg.Pool,
) -> None:
    known = dt.datetime(2026, 1, 10, tzinfo=UTC)
    await claim(
        backend_pool, "AAA", "BBB Corp", None, "confirmed", known, key="BBB"
    )  # AAA sells to BBB
    await claim(
        backend_pool, "BBB", "AAA Corp", None, "confirmed", known, key="AAA"
    )  # BBB sells to AAA
    view = await dependency_read.dependency_view(backend_pool, "AAA", NOW)
    assert [c["kind"] for c in view["conflicts"]] == ["opposite_direction"]
    assert len(view["claims"]) == 1  # both claims are kept, nothing is resolved for the reader


async def test_a_confirmed_reviewed_mapping_gives_a_name_its_entity_key(
    backend_pool: asyncpg.Pool,
) -> None:
    known = dt.datetime(2026, 1, 10, tzinfo=UTC)
    cid = await claim(backend_pool, "AAA", "Acme Corp", "0.2", "confirmed", known)
    issuer_id = await backend_pool.fetchval(
        "insert into public.issuer (legal_name, cik)"
        " values ('Acme Corporation','0000012345') returning id"
    )
    before = await dependency_read.dependency_view(backend_pool, "AAA", NOW)
    c0 = next(c for c in before["claims"] if c["id"] == cid)
    assert c0["counterparty_key"] is None
    assert c0["entity"]["status"] == "candidate" and c0["entity"]["needs_review"] is True
    assert any("no entity key" in n for n in before["notes"])

    await backend_pool.execute(
        """insert into public.disclosure_entity_mapping
             (mention, mention_kind, issuer_id, status, match_basis, decided_by, decided_at)
           values ('Acme Corp','counterparty',$1,'confirmed','registered_name','owner:1',$2)""",
        issuer_id,
        dt.datetime(2026, 2, 1, tzinfo=UTC),
    )
    after = await dependency_read.dependency_view(backend_pool, "AAA", NOW)
    c1 = next(c for c in after["claims"] if c["id"] == cid)
    assert c1["counterparty_key"] == "cik:12345"
    assert any(s["origin"] == "cik:12345" for s in after["scenarios"])
    # a replay from before the reviewer decided does not use the mapping
    early = await dependency_read.dependency_view(
        backend_pool, "AAA", dt.datetime(2026, 1, 20, tzinfo=UTC)
    )
    assert next(c for c in early["claims"] if c["id"] == cid)["counterparty_key"] is None


async def test_an_anonymous_label_is_stored_and_never_becomes_a_name(
    backend_pool: asyncpg.Pool,
) -> None:
    cid = await upsert_disclosure(
        backend_pool,
        ticker="AAA",
        relationship="customer",
        direction="issuer_sells_to",
        passage="Customer A accounted for 23% of total revenue.",
        source_url="https://sec.example/10k",
        source="sec_10k",
        anonymous_counterparty=True,
        claim_label="Customer A",
        reported_share=D("0.23"),
        fiscal_period="FY2025",
        valid_from=dt.date(2025, 1, 1),
    )
    row = await backend_pool.fetchrow(
        "select counterparty_name, claim_label from public.relationship_disclosure where id=$1", cid
    )
    assert row["counterparty_name"] is None and row["claim_label"] == "Customer A"
    with pytest.raises(ValueError):
        await upsert_disclosure(
            backend_pool,
            ticker="AAA",
            relationship="customer",
            direction="issuer_sells_to",
            passage="x",
            source_url="u",
            source="s",
            counterparty_name="Acme",
            claim_label="Customer A",
        )


async def test_ted_award_lots_and_corrections_are_kept_without_duplicates(
    backend_pool: asyncpg.Pool,
) -> None:
    cid = await upsert_disclosure(
        backend_pool,
        ticker="AAA",
        relationship="procurement_award",
        direction="award_to_issuer",
        passage="TED notice 1-2026 awarded to AAA",
        source_url="https://ted.example/1",
        source="ted",
        counterparty_key="ted-buyer:ministry",
        counterparty_name="Ministry",
        fiscal_period="FY2026",
        valid_from=dt.date(2026, 3, 1),
    )
    award = TedAward(
        "1-2026",
        "can-standard",
        "Supplies",
        "Ministry",
        ("AAA",),
        D("1000"),
        "EUR",
        dt.date(2026, 3, 1),
        ("30213000",),
        ("LOT-1", "LOT-2"),
    )
    assert await store_award_detail(backend_pool, cid, award) == 2
    corrected = TedAward(
        "1-2026",
        "can-standard",
        "Supplies",
        "Ministry",
        ("AAA",),
        D("1200"),
        "EUR",
        dt.date(2026, 3, 1),
        ("30213000",),
        ("LOT-1", "LOT-2"),
    )
    await store_award_detail(backend_pool, cid, corrected)
    rows = await backend_pool.fetch(
        "select lot_id, value from public.procurement_award_detail order by lot_id"
    )
    assert [(r["lot_id"], r["value"]) for r in rows] == [("LOT-1", D("1200")), ("LOT-2", D("1200"))]
    view = await dependency_read.dependency_view(
        backend_pool, "AAA", dt.datetime.now(UTC) + dt.timedelta(days=1)
    )
    claim_out = next(c for c in view["claims"] if c["id"] == cid)
    assert claim_out["share"] is None and claim_out["denominator"] is None
    assert [a["lot_id"] for a in claim_out["awards"]] == ["LOT-1", "LOT-2"]
    assert claim_out["awards"][0]["cpv_codes"] == ["30213000"]
    with pytest.raises(asyncpg.PostgresError):  # currency must be a three-letter code
        await backend_pool.execute(
            "update public.procurement_award_detail set currency='euro' where lot_id='LOT-1'"
        )


# ------------------------------------------------------------------------------------ portfolio
async def seed_portfolio(pool: asyncpg.Pool, holdings: dict[str, int]) -> None:
    today = dt.date(2026, 6, 30)
    job_id = await pool.fetchval(
        "insert into public.job (job_type, trigger, status) values"
        " ('portfolio_sync','schedule','succeeded') returning id"
    )
    await pool.execute(
        "insert into public.account_snapshot (snapshot_date, job_id, captured_at, t212_account_id,"
        " account_currency, total_value, cash_available, cash_in_pies, cash_reserved,"
        " invested_value, invested_cost, unrealized_ppl, realized_ppl, base_currency, fx_source,"
        " fx_rate, cash_base, invested_base, total_base, weight_basis)"
        " values ($1,$2,now(),1,'CZK',0,0,0,0,0,0,0,0,'CZK','cnb',1,0,0,0,'total')",
        today,
        job_id,
    )
    for ticker, value in holdings.items():
        await pool.execute(
            "insert into public.instrument (t212_ticker, isin, name, currency, security_type,"
            " home_country) values ($1, null, $1, 'USD', 'Common Stock', 'US')",
            ticker,
        )
        await pool.execute(
            "insert into public.position_snapshot (snapshot_date, ticker, job_id, captured_at,"
            " quantity, avg_price, current_price, instrument_currency, value, account_currency,"
            " value_account, cost_account, ppl, base_currency, fx_source, fx_rate, value_base)"
            " values ($1,$2,$3,now(),1,$4,$4,'USD',$4,'CZK',$4,$4,0,'CZK','cnb',1,$4)",
            today,
            ticker,
            job_id,
            D(value),
        )


async def test_portfolio_exposure_weights_confirmed_claims_and_names_the_unknown(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_portfolio(backend_pool, {"AAA": 600, "BBB": 300, "CCC": 100})
    known = dt.datetime(2026, 1, 10, tzinfo=UTC)
    await claim(backend_pool, "AAA", "Big Co", "0.2", "confirmed", known, key="BIG")
    await claim(backend_pool, "BBB", "Big Co", "0.4", "suggested", known, key="BIG")
    out = await dependency_read.portfolio_view(backend_pool, NOW)
    assert out["holdings"] == 3
    assert D(out["covered_weight"]) == D("0.6")
    assert out["uncovered"] == ["BBB", "CCC"]
    assert {k: D(v) for k, v in out["by_counterparty"].items()} == {"BIG": D("0.12")}


async def test_scan_all_covers_every_held_ticker_and_counts_nothing_failed(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_portfolio(backend_pool, {"AAA": 100})
    await filing(backend_pool, "0000099999-26-000001", dt.date(2026, 6, 3))
    await buy(backend_pool, "0000099999-26-000001", 0, "100", "2000", dt.date(2026, 6, 1))
    totals = await alerts.scan_all(backend_pool, NOW)
    assert totals["insider_trade"] == 1 and totals["failed"] == 0


async def test_scan_all_leaves_a_backfilled_old_trade_alone(backend_pool: asyncpg.Pool) -> None:
    await seed_portfolio(backend_pool, {"AAA": 100})
    await filing(backend_pool, "0000099999-21-000001", dt.date(2021, 3, 3))
    await buy(backend_pool, "0000099999-21-000001", 0, "100", "2000", dt.date(2021, 3, 1))
    totals = await alerts.scan_all(backend_pool, NOW)
    assert totals["insider_trade"] == 0
    # asked without a window, the same trade still alerts
    assert await alerts.scan_insiders(backend_pool, "AAA", NOW) == 1


# ------------------------------------------------------------------------------- adjudication
async def test_labels_are_stored_per_person_and_the_gate_reads_them(
    backend_pool: asyncpg.Pool,
) -> None:
    key = "insider_class:t:50"
    for i in range(50):
        await adjudication.record(
            backend_pool,
            dataset="insider_class",
            sample_key=key,
            item_ref=f"a:{i}",
            machine_value="open_market_buy",
            human_value="open_market_buy" if i else "gift",
            by="owner:1",
        )
    gate = await adjudication.measure(backend_pool, "insider_class", key)
    assert gate.labelled == 50 and gate.correct == 49 and gate.met  # 98% exactly
    await adjudication.record(
        backend_pool,
        dataset="insider_class",
        sample_key=key,
        item_ref="a:1",
        machine_value="open_market_buy",
        human_value="gift",
        by="owner:1",
    )
    assert (await adjudication.measure(backend_pool, "insider_class", key)).met is False
    with pytest.raises(ValueError):
        await adjudication.record(
            backend_pool,
            dataset="insider_class",
            sample_key=key,
            item_ref="a:2",
            machine_value=None,
            human_value="true_relationship",
            by="owner:1",
        )


async def test_a_relationship_sample_hides_planted_items_among_the_real_ones(
    backend_pool: asyncpg.Pool,
) -> None:
    for i in range(12):
        await claim(
            backend_pool, "AAA", f"Co {i}", "0.1", "suggested", dt.datetime(2026, 1, 10, tzinfo=UTC)
        )
    items = await adjudication.relationship_sample(backend_pool, 10, "seed")
    # 10 real claims asked for, the 7 planted ones on top, so the sample can meet a gate of 10
    assert len(items) == 10 + 7
    assert sum(1 for i in items if i.planted) == 7
    again = await adjudication.relationship_sample(backend_pool, 10, "seed")
    assert [i.item_ref for i in items] == [i.item_ref for i in again]


async def test_an_ownership_sample_lists_each_cusip_ticker_pair_once_and_takes_its_labels(
    backend_pool: asyncpg.Pool,
) -> None:
    for acc in ("0000012345-26-000001", "0000012345-26-000002"):
        await backend_pool.execute(
            """insert into public.form13f_filing(accession, cik, manager_name, period, filed,
                 submission_type) values ($1,'0000012345','Alpha Capital',$2,$3,'13F-HR')""",
            acc,
            dt.date(2026, 3, 31),
            dt.date(2026, 5, 10),
        )
        await backend_pool.execute(
            """insert into public.institutional_holding
                 (accession, infotable_sk, cusip, ticker, issuer_name, shares, value_usd)
               values ($1,1,'037833100','AAPL','APPLE INC',10,10)""",
            acc,
        )
    items = await adjudication.ownership_sample(backend_pool, 50, "s")
    assert len(items) == 1 and items[0].machine_value == "same_company"
    assert "APPLE INC" in items[0].text and "AAPL" in items[0].text
    key = "ownership_match:s:50"
    await adjudication.record(
        backend_pool,
        dataset="ownership_match",
        sample_key=key,
        item_ref=items[0].item_ref,
        machine_value=items[0].machine_value,
        human_value="same_company",
        by="owner:1",
    )
    with pytest.raises(ValueError, match="not a label"):
        await adjudication.record(
            backend_pool,
            dataset="ownership_match",
            sample_key=key,
            item_ref="x",
            machine_value="same_company",
            human_value="true_relationship",
            by="owner:1",
        )


async def test_a_screen_row_with_no_gate_says_so_instead_of_passing(
    backend_pool: asyncpg.Pool,
) -> None:
    from api.screen_routes import _with_gates
    from steward.screen import ScreenResult, ScreenRow

    row = ScreenRow(
        ticker="NOGATE",
        basis="us-gaap",
        period_end="2025-12-31",
        metrics={},
        unknown=[],
    )
    result = ScreenResult(
        universe="u",
        universe_definition="",
        universe_size=1,
        evaluated=1,
        matched=1,
        shown=1,
        no_data=0,
        filtered_out=0,
        excluded=[],
        missing_treatment="exclude",
        filters=[],
        rows=[row],
        formula_definitions={},
        caveats=[],
    )
    gated = await _with_gates(backend_pool, result, None)
    gate = gated.rows[0].valuation_gate
    assert gate is not None and gate.usable_for_valuation is None
    assert gate.reasons == ["fact is not linked to an issuer"]
    only = await _with_gates(backend_pool, result.model_copy(deep=True), None, True)
    assert only.rows == [] and only.shown == 0
    assert "1 matching rows were left out" in only.caveats[-1]
