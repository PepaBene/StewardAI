import datetime as dt
from decimal import Decimal

import httpx
import pytest

from api.free_sources_routes import RelationshipReview
from steward.market_analysis import (
    Action,
    PricePoint,
    calculate_return,
    compatible_peer_mean,
    financial_peer_metrics,
    patent_trend,
)
from steward.reference.price_series import SeriesRef
from steward.sources.company_research import parse_ops_patent_xml, parse_pdl_company
from steward.sources.free_market import (
    FreeSourceError,
    finnhub_earnings_surprises,
    finnhub_peers,
    parse_alpha_daily,
    parse_alpha_news,
    parse_alpha_series,
    parse_alpha_statement_facts,
)


@pytest.mark.asyncio
async def test_finnhub_research_parsers_keep_peers_as_symbols_and_eps_as_vintaged_events() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/stock/peers"):
            return httpx.Response(200, json=["AAA", "BBB", "AAA"])
        return httpx.Response(
            200,
            json=[
                {
                    "period": "2026-06-30",
                    "actual": 1.2,
                    "estimate": 1.0,
                    "surprise": 0.2,
                    "surprisePercent": 20,
                },
                {"period": "2026-03-31", "actual": None, "estimate": 0.9},
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        peers = await finnhub_peers(http, "secret", "AAA")
        earnings = await finnhub_earnings_surprises(http, "secret", "AAA")
    assert peers == ["BBB"]
    assert len(earnings) == 1
    assert earnings[0]["period"] == dt.date(2026, 6, 30)
    assert earnings[0]["surprise"] == Decimal("0.2")


def test_alpha_vantage_news_requires_ticker_relevance_and_bounded_sentiment() -> None:
    parsed = parse_alpha_news(
        {
            "feed": [
                {
                    "url": "https://publisher.example/article",
                    "title": "Company update",
                    "summary": "Summary",
                    "time_published": "20260930T140000",
                    "ticker_sentiment": [
                        {
                            "ticker": "AAA",
                            "relevance_score": "0.91",
                            "ticker_sentiment_score": "0.4",
                        }
                    ],
                },
                {
                    "url": "https://publisher.example/unrelated",
                    "title": "No ticker relevance",
                    "ticker_sentiment": [
                        {"ticker": "BBB", "relevance_score": "0.9", "ticker_sentiment_score": "0.2"}
                    ],
                },
            ]
        },
        "AAA",
    )
    assert len(parsed) == 1
    assert parsed[0].sentiment == Decimal("0.4")
    assert parsed[0].published_at == dt.date(2026, 9, 30)
    assert parse_alpha_series({"data": [{"date": "2026-09-30", "value": "72.5"}]}) == [
        (dt.date(2026, 9, 30), Decimal("72.5"))
    ]


def test_pdl_requires_domain_consistent_high_confidence_and_epo_parses_applicant_candidates() -> (
    None
):
    confidence, _ = parse_pdl_company(
        {"likelihood": 9, "website": "example.com", "employee_count": 120},
        "www.example.com",
    )
    assert confidence == Decimal("0.9")
    with pytest.raises(FreeSourceError):
        parse_pdl_company({"likelihood": 9, "website": "other.example"}, "example.com")
    xml = """<ops:world-patent-data xmlns:ops="urn:ops" xmlns:ex="urn:ex">
      <ex:exchange-document doc-number="123" country="EP">
        <ex:document-id document-id-type="docdb" doc-number="123"/>
        <ex:applicant><ex:applicant-name>Example Corporation</ex:applicant-name></ex:applicant>
        <ex:invention-title>Example invention</ex:invention-title>
        <ex:priority-claim date="20240102"/>
        <ex:classification-symbol>A01B</ex:classification-symbol>
      </ex:exchange-document>
    </ops:world-patent-data>"""
    patents = parse_ops_patent_xml(xml, ("Example Corporation",))
    assert patents[0]["family_key"] == "123"
    assert patents[0]["priority_date"] == dt.date(2024, 1, 2)
    assert patents[0]["cpc_codes"] == ["A01B"]
    assert parse_ops_patent_xml(xml, ("Unrelated Company",)) == []


def test_alpha_vantage_daily_parser_keeps_adjustments_and_actions_separate() -> None:
    rows = parse_alpha_daily(
        {
            "Time Series (Daily)": {
                "2026-09-30": {
                    "1. open": "10",
                    "2. high": "12",
                    "3. low": "9",
                    "4. close": "11",
                    "5. adjusted close": "10.5",
                    "6. volume": "100",
                    "7. dividend amount": "0.1",
                    "8. split coefficient": "1.0",
                }
            }
        }
    )
    assert rows[0].close == Decimal("11")
    assert rows[0].adjusted_close == Decimal("10.5")
    assert rows[0].dividend == Decimal("0.1")
    with pytest.raises(FreeSourceError):
        parse_alpha_daily({"Note": "API call frequency exceeded"})
    assert SeriesRef("IBM", "alpha_vantage").name == "Alpha Vantage"


def test_alpha_vantage_annual_statement_flattens_numeric_facts_only() -> None:
    facts = parse_alpha_statement_facts(
        {
            "annualReports": [
                {
                    "fiscalDateEnding": "2025-12-31",
                    "reportedDate": "2026-02-01",
                    "reportedCurrency": "USD",
                    "totalRevenue": "1000",
                    "netIncome": "200",
                    "note": "not a financial number",
                }
            ]
        },
        statement="income",
    )
    assert {r["label"] for r in facts} == {"totalRevenue", "netIncome"}
    assert facts[0]["period_end"] == dt.date(2025, 12, 31)
    assert facts[0]["currency"] == "USD"


def test_return_requires_ex_date_dividend_fx_and_adjusts_splits() -> None:
    points = [
        PricePoint(dt.date(2026, 1, 1), Decimal(100), "USD"),
        PricePoint(dt.date(2026, 1, 2), Decimal(50), "USD"),
    ]
    split = Action("split", dt.date(2026, 1, 2), ratio=Decimal(2))
    result = calculate_return(points, [split])
    assert result.value == Decimal(0)
    missing_fx = Action("cash_dividend", dt.date(2026, 1, 2), cash=Decimal(1), currency="EUR")
    result = calculate_return(points, [split, missing_fx], reinvest_dividends=True)
    assert result.value is None and "FX" in (result.reason or "")


def test_peer_metrics_explain_coverage_and_patents_deduplicate_families() -> None:
    result = compatible_peer_mean(
        [("A", Decimal(4), "ROIC"), ("B", None, "ROIC"), ("C", Decimal(9), "other")], metric="ROIC"
    )
    assert result["mean"] == Decimal(4) and result["covered_peers"] == 1
    rows = [
        {
            "family_key": "F1",
            "priority_date": dt.date(2025, 2, 1),
            "cpc_codes": ["A01", "A01"],
            "citations_forward": 3,
        }
    ]
    assert patent_trend(rows)[0]["families"] == 1


def test_filed_peer_metrics_require_complete_inputs_and_exclude_dimensions() -> None:
    rows = [
        {
            "ticker": "AAA",
            "period_end": dt.date(2025, 12, 31),
            "basis": "US_GAAP",
            "concept": "us-gaap:Revenue",
            "value": Decimal(100),
            "dimensions": {"members": []},
        },
        {
            "ticker": "AAA",
            "period_end": dt.date(2025, 12, 31),
            "basis": "US_GAAP",
            "concept": "us-gaap:GrossProfit",
            "value": Decimal(40),
            "dimensions": {"members": []},
        },
        {
            "ticker": "AAA",
            "period_end": dt.date(2025, 12, 31),
            "basis": "US_GAAP",
            "concept": "us-gaap:GrossProfit",
            "value": Decimal(90),
            "dimensions": {"members": ["SegmentA"]},
        },
    ]
    period = financial_peer_metrics(rows)["AAA"]["periods"][0]
    assert period["gross_margin"] == Decimal("0.4")
    assert period["operating_margin"] is None


def test_revenue_growth_compares_same_length_prior_year_periods() -> None:
    facts = []
    for start, end, revenue in (
        (dt.date(2024, 1, 1), dt.date(2024, 12, 31), Decimal(80)),
        (dt.date(2025, 1, 1), dt.date(2025, 12, 31), Decimal(100)),
    ):
        facts.append(
            {
                "ticker": "AAA",
                "period_start": start,
                "period_end": end,
                "basis": "US_GAAP",
                "concept": "us-gaap:Revenue",
                "value": revenue,
                "dimensions": {"members": []},
            }
        )
    periods = financial_peer_metrics(facts)["AAA"]["periods"]
    assert periods[0]["revenue_growth"] == Decimal("0.25")


def test_relationship_review_does_not_allow_unnamed_match_or_anonymous_alias() -> None:
    base = dict(
        relationship="customer",
        direction="issuer_sells_to",
        passage="The issuer sells widgets.",
        source_url="https://example.test/report",
        source="issuer_report",
    )
    with pytest.raises(ValueError):
        RelationshipReview(**base)
    with pytest.raises(ValueError):
        RelationshipReview(**base, anonymous_counterparty=True, counterparty_name="Made Up Co")
