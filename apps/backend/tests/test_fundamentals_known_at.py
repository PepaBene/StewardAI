"""Backend fixes for the financials / research audit findings F4-08, F4-10, F4-12, F5-18."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import asyncpg
import httpx
import pytest

from steward.forward_consensus import vintage_date
from steward.research.gather import financial_trend
from steward.research.run import ResearchBody
from steward.research.sources import Registry, Source, known_at_range
from tests.test_fund_stream_db import api, evidence, insert_fact  # noqa: F401

pytestmark = pytest.mark.db


async def _confirm(api: httpx.AsyncClient, peer: str, source: str, evidence_id: int) -> None:  # noqa: F811
    response = await api.post(
        "/holdings/AAA/peer-set",
        json={
            "peer_ticker": peer,
            "basis": "owner review",
            "source": source,
            "status": "confirmed",
            "evidence_id": evidence_id,
        },
    )
    assert response.status_code == 200


async def test_peer_metrics_lists_per_peer_values_and_counts_distinct_peers(
    backend_pool: asyncpg.Pool,
    api: httpx.AsyncClient,  # noqa: F811
) -> None:
    evidence_id = await evidence(backend_pool)
    await backend_pool.execute(
        "insert into instrument (t212_ticker, isin, name, short_name, currency, security_type)"
        " values ('BBB_US_EQ','US0000000BB1','Bravo Corp','BBB','USD','Common Stock')"
    )
    await insert_fact(backend_pool, "AAA", "us-gaap:Revenues", 200)
    await insert_fact(backend_pool, "AAA", "us-gaap:GrossProfit", 100)
    for peer, revenue, gross in (("BBB", 100, 30), ("CCC", 100, 60)):
        await insert_fact(backend_pool, peer, "us-gaap:Revenues", revenue)
        await insert_fact(backend_pool, peer, "us-gaap:GrossProfit", gross)
    # BBB is listed twice (a vendor suggestion the owner re-added): peer_set allows it.
    await _confirm(api, "BBB", "finnhub", evidence_id)
    await _confirm(api, "BBB", "owner", evidence_id)
    await _confirm(api, "CCC", "owner", evidence_id)
    body = (await api.get("/holdings/AAA/peer-metrics")).json()
    assert [p["peer_ticker"] for p in body["confirmed_peers"]] == ["BBB", "CCC"]
    assert sorted(body["confirmed_peers"][0]["sources"]) == ["finnhub", "owner"]
    gross = body["covered_peer_metrics"]["gross_margin"]
    assert gross["covered_peers"] == 2
    by_ticker = {p["ticker"]: p for p in gross["peers"]}
    assert set(by_ticker) == {"BBB", "CCC"}
    assert by_ticker["BBB"]["value"] == pytest.approx(0.3)
    assert by_ticker["CCC"]["value"] == pytest.approx(0.6)
    assert by_ticker["BBB"]["name"] == "Bravo Corp" and by_ticker["CCC"]["name"] is None
    assert by_ticker["BBB"]["basis"] == "US_GAAP" and by_ticker["BBB"]["period"] == "2025-12-31"
    assert by_ticker["BBB"]["definition_version"]
    # A metric a peer cannot supply is listed with a null value and the reason.
    roic = {p["ticker"]: p for p in body["covered_peer_metrics"]["roic"]["peers"]}
    assert roic["BBB"]["value"] is None and roic["BBB"]["reason"]


async def test_actual_vs_consensus_separates_unchecked_from_failed_and_dates_the_vintage(
    backend_pool: asyncpg.Pool,
    api: httpx.AsyncClient,  # noqa: F811
) -> None:
    reported = dt.datetime(2026, 7, 30, 20, 0, tzinfo=dt.UTC)

    async def actual(period: str, validation: str) -> None:
        await backend_pool.execute(
            "insert into public.financial_statement_fact (ticker, concept, statement, line_item,"
            " period_start, period_end, fiscal_period, basis, value, unit, currency, dimensions,"
            " source, source_url, known_at, published, validation) values ('AAA',"
            " 'us-gaap:Revenues','income','revenue','2026-04-01','2026-06-30',$2,'US_GAAP',1050,"
            " 'USD','USD','{\"members\": []}'::jsonb,'test','https://x',$1,true,$3::jsonb)",
            reported,
            period,
            validation,
        )
        await backend_pool.execute(
            "insert into public.forward_observation (ticker, kind, provider, metric,"
            " fiscal_period, currency, value, vintage, known_at, source_url, observation_key)"
            " values ('AAA','published_analyst_consensus','p','revenue',$1,'USD',1000,$2,$3,"
            " 'https://x', md5($1||$2))",
            period,
            "2026-07-10" if period == "FY2026Q2" else "Q1 2026 preview",
            reported - dt.timedelta(days=5),
        )

    await actual("FY2026Q2", "{}")
    await actual("FY2026Q3", '{"reconciled": false}')
    await actual("FY2026Q4", '{"reconciled": true}')
    states = {}
    for period in ("FY2026Q2", "FY2026Q3", "FY2026Q4"):
        body = (
            await api.get(
                "/holdings/AAA/actual-vs-consensus",
                params={"metric": "revenue", "fiscal_period": period},
            )
        ).json()
        states[period] = body
    # reconciled stays false for both (compatibility); the new field tells them apart.
    assert states["FY2026Q2"]["actual"]["reconciled"] is False
    assert states["FY2026Q3"]["actual"]["reconciled"] is False
    assert states["FY2026Q2"]["actual"]["reconciliation_state"] == "unchecked"
    assert states["FY2026Q3"]["actual"]["reconciliation_state"] == "failed"
    assert states["FY2026Q4"]["actual"]["reconciliation_state"] == "passed"
    assert states["FY2026Q2"]["consensus"]["vintage_date"] == "2026-07-10"
    assert states["FY2026Q3"]["consensus"]["vintage"] == "Q1 2026 preview"
    assert states["FY2026Q3"]["consensus"]["vintage_date"] is None
    rows = (await api.get("/holdings/AAA/forward-data")).json()
    assert {r["vintage"]: r["vintage_date"] for r in rows} == {
        "2026-07-10": "2026-07-10",
        "Q1 2026 preview": None,
    }
    series = (await api.get("/holdings/AAA/forward-data/consensus")).json()["consensus_series"]
    assert all("vintage_date" in v for s in series for v in s["vintages"])


def test_vintage_date_is_only_a_real_leading_iso_date() -> None:
    assert vintage_date("2026-03-01") == "2026-03-01"
    assert vintage_date("2026-03-01 post-earnings") == "2026-03-01"
    assert vintage_date("Q1 2026 preview") is None
    assert vintage_date("2026-13-45") is None
    assert vintage_date(None) is None and vintage_date("") is None


def _source(sid: str, known_at: str | None) -> dict[str, Any]:
    return {"id": sid, "kind": "fact", "ref": sid, "title": sid, "known_at": known_at}


def _body(sources: list[dict[str, Any]], cites: list[str]) -> dict[str, Any]:
    return {
        "ticker": "AAA", "name": "A", "owned": True, "headline": "h", "profile_id": "p",
        "profile_name": "p", "depth": "d", "model": "m", "language": "en",
        "sections": [{"id": "s", "title": "S", "written_by": "code",
                      "sentences": [{"text": "x", "source_ids": cites}]}],
        "valuation": [], "sources": sources,
    }  # fmt: skip


def test_sentence_known_at_bounds_derive_from_cited_sources_and_are_null_when_unknown() -> None:
    sources = [
        _source("S1", "2026-03-01T10:00:00+00:00"),
        _source("S2", "2026-05-02T09:00:00+00:00"),
        _source("S3", None),
    ]
    body = ResearchBody.model_validate(_body(sources, ["S1", "S2"]))
    sentence = body.sections[0].sentences[0]
    assert sentence.known_at_min == "2026-03-01T10:00:00+00:00"
    assert sentence.known_at_max == "2026-05-02T09:00:00+00:00"
    # A cited source without a known_at makes the bounds unknown: nothing is invented.
    mixed = ResearchBody.model_validate(_body(sources, ["S1", "S3"])).sections[0].sentences[0]
    assert mixed.known_at_min is None and mixed.known_at_max is None
    # A stored report from before the field: no source carries known_at.
    old = _body([{k: v for k, v in s.items() if k != "known_at"} for s in sources], ["S1"])
    assert ResearchBody.model_validate(old).sections[0].sentences[0].known_at_max is None
    assert known_at_range([Source(**sources[0])], []) == (None, None)


def test_fact_sources_carry_the_extraction_time_not_the_period() -> None:
    fact = {
        "id": 1, "metric": "revenue", "fiscal_year": 2026, "fiscal_period": "Q1",
        "period": "FY2026 Q1", "value_units": Decimal(100), "value_as_written": "100",
        "scale": "units", "unit": "currency", "currency": "USD", "snippet": "100",
        "source_url": "https://sec.test",
        "extracted_at": dt.datetime(2026, 5, 4, 8, 0, tzinfo=dt.UTC),
    }  # fmt: skip
    reg = Registry()
    financial_trend(reg, [fact])
    source = reg.sources[0]
    assert source.as_of == "FY2026 Q1"
    assert source.known_at == "2026-05-04T08:00:00+00:00"
