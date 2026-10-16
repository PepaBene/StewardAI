import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest

from steward.config.jobs import JobsConfig
from steward.macro.exposure import Exposure, exposure_summary, weighted_effect
from steward.macro.providers import (
    TradePoint,
    parse_comtrade,
    parse_sdmx_json,
    parse_world_bank,
    trade_metrics,
)
from steward.reference.matching import Candidate, match_identity
from steward.settings import Settings
from steward.sources.audit import classify_probe, probes
from steward.sources.base import AccessState, SourceObservation, access_from_http
from steward.sources.governor import Budget, QuotaExceeded, RequestGovernor
from steward.sources.reconcile import reconcile


def test_audit_corpus_has_required_cases() -> None:
    path = Path(__file__).parent / "fixtures/free_sources/audit_corpus.json"
    fixture = json.loads(path.read_text())
    assert len(fixture["securities"]) >= 30
    assert len(fixture["countries"]) >= 12
    assert len(fixture["hs_products"]) >= 10
    cases = {row["case"] for row in fixture["securities"]}
    assert {"adr", "dual_listing", "delisted_renamed", "ucits_etf", "us_etf"} <= cases


def test_denials_and_throttling_are_not_empty_successes() -> None:
    assert access_from_http(403).state == AccessState.ACCESS_UNVERIFIED
    assert access_from_http(403, paid_tier_denial=True).state == AccessState.PAID_ONLY
    assert access_from_http(429).state == AccessState.QUOTA_EXHAUSTED
    assert access_from_http(200, has_rows=False).state == AccessState.NOT_COVERED
    assert classify_probe(403, {"message": "upgrade to premium"}).state == AccessState.PAID_ONLY
    assert classify_probe(200, {"Symbol": "MSFT"}, "AAPL").state == AccessState.DISPUTED
    assert classify_probe(200, [{"symbol": "MSFT"}], "AAPL").state == AccessState.DISPUTED
    assert classify_probe(200, {"note": "sample response"}).state == AccessState.ACCESS_UNVERIFIED


def test_phase_zero_probe_manifest_covers_every_named_provider() -> None:
    registered = probes(Settings())
    # Companies House and EDINET are probed by their first governed call, not a phase-zero probe.
    assert {probe.provider for probe in registered} == set(JobsConfig().providers) - {
        "companies_house",
        "edinet",
    }
    assert {(p.provider, p.endpoint) for p in registered} >= {
        ("fmp", "analyst_estimates"),
        ("fmp", "sector_snapshot"),
        ("finnhub", "ownership"),
        ("finnhub", "etf_holdings"),
        ("finnhub", "index_constituents"),
        ("finnhub", "forward_estimates"),
        ("twelve_data", "corporate_actions"),
        ("twelve_data", "splits"),
    }


async def test_request_governor_reserves_quota_atomically() -> None:
    governor = RequestGovernor()
    budget = Budget(daily=Decimal(3), reserve=Decimal(1))
    await governor.reserve("demo", "data", Decimal(1), budget)
    await governor.reserve("demo", "data", Decimal(1), budget)
    with pytest.raises(QuotaExceeded):
        await governor.reserve("demo", "data", Decimal(1), budget)
    await governor.reserve("demo", "data", Decimal(1), budget, urgent=True)
    await governor.hard_block("demo", "paid")
    with pytest.raises(QuotaExceeded):
        await governor.reserve("demo", "paid", Decimal(1), budget)


async def test_request_governor_keeps_daily_and_monthly_windows_separate() -> None:
    governor = RequestGovernor()
    budget = Budget(per_minute=Decimal(2), daily=Decimal(3), monthly=Decimal(10))
    first = dt.datetime(2026, 10, 1, 0, 0, tzinfo=dt.UTC)
    await governor.reserve("demo", "data", Decimal(2), budget, now=first)
    with pytest.raises(QuotaExceeded, match="per-minute"):
        await governor.reserve("demo", "data", Decimal(1), budget, now=first)
    await governor.reserve("demo", "data", Decimal(1), budget, now=first + dt.timedelta(minutes=1))


def test_identity_keeps_adr_and_ordinary_distinct() -> None:
    query = Candidate(
        "query", {"lei": "ISSUER", "isin": "US-ADR"}, currency="USD", security_kind="adr"
    )
    ordinary = Candidate(
        "ordinary", {"lei": "ISSUER", "isin": "NL-ORD"}, currency="EUR", security_kind="ordinary"
    )
    adr = Candidate("adr", {"lei": "ISSUER", "isin": "US-ADR"}, currency="USD", security_kind="adr")
    result = match_identity(query, [ordinary, adr])
    assert result.status == "confirmed" and result.candidate == adr
    suggestion = match_identity(Candidate("q", name="Acme"), [Candidate("x", name="Acme")])
    assert suggestion.status == "suggested"


def test_macro_provider_parsers_and_trade_metrics() -> None:
    world_bank = parse_world_bank(
        [
            {},
            [
                {
                    "indicator": {"id": "GDP", "value": "GDP"},
                    "countryiso3code": "CAN",
                    "country": {"id": "CA"},
                    "date": "2025",
                    "value": 10,
                }
            ],
        ],
        "https://example.test",
    )
    assert world_bank[0].period == dt.date(2025, 1, 1)
    assert world_bank[0].geography == "CA"
    sdmx = parse_sdmx_json(
        {
            "data": {
                "structures": [
                    {
                        "dimensions": {
                            "observation": [{"id": "TIME_PERIOD", "values": [{"id": "2025-Q1"}]}]
                        }
                    }
                ],
                "dataSets": [{"observations": {"0": [2.5]}}],
            }
        },
        source="bis",
        series_id="credit",
        geography="CA",
        unit="%",
        frequency="quarterly",
        source_url="x",
        vintage="2026-01",
    )
    assert sdmx[0].value == Decimal("2.5")
    trade = parse_comtrade(
        {
            "data": [
                {
                    "reporterCode": 124,
                    "partnerCode": 840,
                    "flowDesc": "Export",
                    "classificationCode": "H6",
                    "cmdCode": "27",
                    "period": "2024",
                    "primaryValue": 40,
                },
                {
                    "reporterCode": 124,
                    "partnerCode": 840,
                    "flowDesc": "Export",
                    "classificationCode": "H6",
                    "cmdCode": "27",
                    "period": "2025",
                    "primaryValue": 60,
                },
                {
                    "reporterCode": 124,
                    "partnerCode": 156,
                    "flowDesc": "Export",
                    "classificationCode": "H6",
                    "cmdCode": "27",
                    "period": "2025",
                    "primaryValue": 40,
                },
            ]
        }
    )
    metrics = trade_metrics(trade)
    # Flows are never blended (audit C): growth and concentration are per flow, and a missing
    # import flow is "unknown", not zero - the export share used to come out as 1 here.
    assert metrics["export_growth"] == Decimal("1.5")
    assert metrics["import_growth"] is None
    assert metrics["export_partner_concentration"] == Decimal("0.52")
    assert metrics["import_partner_concentration"] is None
    assert metrics["revealed_trade_dependence"] is None
    assert metrics["export_share_of_two_way_trade"] is None
    assert metrics["product_mix_change"] == Decimal(0)


def test_trade_product_mix_uses_the_reporter_wide_product_set() -> None:
    rows = [
        TradePoint(
            "124", "840", "export", "H6", "27", dt.date(2024, 1, 1), Decimal(100), None, None
        ),
        TradePoint("124", "840", "export", "H6", "30", dt.date(2024, 1, 1), Decimal(0), None, None),
        TradePoint("124", "840", "export", "H6", "27", dt.date(2025, 1, 1), Decimal(0), None, None),
        TradePoint(
            "124", "840", "export", "H6", "30", dt.date(2025, 1, 1), Decimal(100), None, None
        ),
    ]
    assert trade_metrics(rows)["product_mix_change"] == Decimal(1)


def test_reconciliation_and_unknown_exposure() -> None:
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)

    def observation(source: str, value: str) -> SourceObservation:
        return SourceObservation("macro", "gdp", source, now, now, Decimal(value), "%", "x", now)

    result = reconcile(
        [observation("official", "2"), observation("vendor", "3")],
        {"official": 1, "vendor": 2},
        Decimal("0.1"),
    )
    assert result.preferred.source == "official" and len(result.conflicts) == 1
    rows = [Exposure("cloud", Decimal("0.4"), "reported", geography="US")]
    assert exposure_summary(rows)["unknown"] == Decimal("0.6")
    assert weighted_effect([], {"US": Decimal("1")}) is None
    assert weighted_effect(rows, {}) is None


def test_providers_have_no_switch_and_source_jobs_run_by_default() -> None:
    config = JobsConfig()
    assert len(config.providers) == 15
    assert all("enabled" not in p.model_dump() for p in config.providers.values())
    # A saved config from before the switch was removed still loads.
    legacy = JobsConfig.model_validate({"providers": {"fmp": {"enabled": False}}})
    assert "enabled" not in legacy.providers["fmp"].model_dump()
    assert len(config.public_data.world_bank) == 9
    assert all(item.consumer for item in config.public_data.world_bank)
    assert config.public_data_collect.enabled
    assert config.source_audit.enabled


def test_ecb_macro_defaults_cover_rates_credit_and_monetary_aggregates() -> None:
    configured = {item.key: item for item in JobsConfig().macro.ecb}
    assert {
        "ecb_deposit_rate",
        "euro_area_private_credit",
        "euro_area_10y_yield",
        "euro_area_m3_growth",
        "euro_area_household_loan_rate",
        "euro_area_corporate_loan_rate",
        "ea_household_overnight_deposit_rate",
        "ea_corporate_term_deposit_rate",
        "ea_bls_enterprise_credit_standards",
    } <= configured.keys()
    assert configured["euro_area_household_loan_rate"].flow_key.startswith("MIR/")
    assert configured["euro_area_corporate_loan_rate"].flow_key.startswith("MIR/")
    # The Data Portal answers 400 when the key repeats the dataflow ("BSI/BSI.M...").
    for item in configured.values():
        flow, key = item.flow_key.split("/", 1)
        assert not key.startswith(f"{flow}."), item.key


def test_fred_macro_defaults_cover_the_us_cycle_markets_and_japan() -> None:
    keys = {item.key for item in JobsConfig().macro.fred}
    assert {"us_hy_spread", "us_2y", "us_curve_10y2y", "us_gdp", "us_payrolls", "us_vix"} <= keys
    assert {"usd_jpy", "jp_10y", "jp_gdp"} <= keys
    assert len(keys) == len(JobsConfig().macro.fred)
