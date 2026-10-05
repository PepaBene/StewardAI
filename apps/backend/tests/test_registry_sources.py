"""Companies House, EDINET and Twelve Data corporate-action parsers, envelope classification,
job planning, the FMP symbol mapping and the wiring/zero-cost registration.

No database and no network. Every payload below is written from the providers' public
documentation: field names such as `company_name`, `items[].transaction_id` (Companies House),
`results[].docID`, `edinetCode`, `metadata.status` (EDINET v2) and `dividends[].ex_date`,
`splits[].from_factor/to_factor` (Twelve Data) could NOT be verified against a live response (no
keys were available).
"""

import datetime as dt
from decimal import Decimal
from typing import get_args

import httpx
import pytest

from steward.config.budget import BudgetJobType
from steward.config.holding_profiles import HoldingProfile, HoldingProfilesConfig
from steward.config.jobs import JobsConfig
from steward.jobs.registry import JOB_HANDLERS, JobType
from steward.sources import companies_house, edinet, twelvedata
from steward.sources.base import AccessState
from steward.sources.free_market import FreeSourceError
from steward.sources.provider_jobs import (
    companies_house_numbers,
    edinet_days_to_fetch,
    edinet_holdings,
    fmp_symbols,
)
from steward.verify_sources.budgets import NO_OVERAGE_PROVIDERS, literal_budgets

NEW_JOBS = ("companies_house_filings", "edinet_filings", "twelve_data_corporate_actions")


def _profiles(**fields: object) -> HoldingProfilesConfig:
    return HoldingProfilesConfig(profiles=[HoldingProfile(ticker="AAA_US_EQ", **fields)])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------- Companies House


def test_companies_house_profile_keeps_sent_fields_and_rejects_other_numbers() -> None:
    payload = {
        "company_number": "00102498",
        "company_name": "BP P.L.C.",
        "company_status": "active",
        "type": "plc",
        "sic_codes": ["06100", "19201"],
        "registered_office_address": {"locality": "London", "postal_code": "SW1Y 4PD"},
        "accounts": {"next_due": "2027-06-30", "last_accounts": {"made_up_to": "2025-12-31"}},
    }
    profile = companies_house.parse_profile(payload, "00102498")
    assert profile is not None
    assert profile.fields["company_name"] == "BP P.L.C."
    assert profile.fields["sic_codes"] == "06100,19201"
    assert profile.fields["registered_office"] == "London, SW1Y 4PD"
    assert profile.fields["accounts_last_made_up_to"] == "2025-12-31"
    assert "jurisdiction" not in profile.fields
    assert companies_house.parse_profile(payload, "SC123456") is None
    with pytest.raises(FreeSourceError):
        companies_house.parse_profile([], "00102498")


def test_companies_house_filing_history_skips_rows_without_id_or_date() -> None:
    payload = {
        "items": [
            {
                "transaction_id": "MzQ1",
                "category": "accounts",
                "date": "2026-03-01",
                "description": "accounts-with-accounts-type-full",
                "type": "AA",
            },
            {"category": "x", "date": "2026-03-01"},
            {"transaction_id": "bad", "date": "not-a-date"},
        ]
    }
    filings = companies_house.parse_filing_history(payload)
    assert [f.transaction_id for f in filings] == ["MzQ1"]
    assert filings[0].filed_on == dt.date(2026, 3, 1)
    assert filings[0].kind == "AA"
    assert companies_house.parse_filing_history({}) == []
    with pytest.raises(FreeSourceError):
        companies_house.parse_filing_history({"items": "x"})
    assert companies_house.filing_url("00102498", "MzQ1").endswith(
        "/company/00102498/filing-history/MzQ1"
    )


# ---------------------------------------------------------------------------------- EDINET

EDINET_LIST = {
    "metadata": {"status": "200", "resultset": {"count": 3}},
    "results": [
        {
            "docID": "S100AAAA",
            "edinetCode": "E02144",
            "filerName": "Toyota Motor Corporation",
            "docTypeCode": "120",
            "docDescription": "  Annual Securities Report  ",
            "submitDateTime": "2026-06-24 15:00",
            "periodEnd": "2026-03-31",
            "xbrlFlag": "1",
            "pdfFlag": "1",
            "withdrawalStatus": "0",
        },
        {"docID": "S100BBBB", "edinetCode": "E99999", "filerName": "Other"},
        {
            "docID": "S100CCCC",
            "edinetCode": "E00001",
            "issuerEdinetCode": "E02144",
            "withdrawalStatus": "1",
        },
    ],
}


def test_edinet_list_keeps_wanted_codes_and_drops_withdrawn_rows() -> None:
    docs = edinet.parse_document_list(EDINET_LIST, {"E02144"})
    assert [d.doc_id for d in docs] == ["S100AAAA"]
    doc = docs[0]
    assert doc.description == "Annual Securities Report"
    assert doc.submitted_at == dt.datetime(2026, 6, 24, 15, 0, tzinfo=edinet.JST)
    assert doc.period_end == dt.date(2026, 3, 31)
    assert doc.has_xbrl and doc.has_pdf
    assert "Subscription-Key" not in edinet.document_url("S100AAAA")


def test_edinet_empty_day_is_empty_and_wrong_shape_raises() -> None:
    assert edinet.parse_document_list({"metadata": {"status": "404"}}, {"E02144"}) == []
    with pytest.raises(FreeSourceError):
        edinet.parse_document_list({"results": []}, {"E02144"})


@pytest.mark.parametrize(
    ("body", "state"),
    [
        ({"metadata": {"status": "200"}}, None),
        ({"metadata": {"status": "404"}}, None),
        ({"metadata": {"status": "401"}}, AccessState.ACCESS_UNVERIFIED),
        ({"metadata": {"status": "500"}}, AccessState.TEMPORARILY_FAILED),
        ({"statusCode": 401, "message": "invalid subscription key"}, AccessState.ACCESS_UNVERIFIED),
        ({"unexpected": 1}, AccessState.TEMPORARILY_FAILED),
    ],
)
def test_edinet_envelope_classification(body: dict[str, object], state: AccessState | None) -> None:
    result = edinet.classify_envelope(httpx.Response(200, json=body))
    assert (result.state if result else None) == state


def test_edinet_days_skip_weekends_and_are_capped() -> None:
    today = dt.date(2026, 10, 7)  # Wednesday
    assert edinet_days_to_fetch(None, today) == [
        dt.date(2026, 10, 5),
        dt.date(2026, 10, 6),
        dt.date(2026, 10, 7),
    ]
    assert edinet_days_to_fetch(dt.date(2026, 9, 1), today, limit=2) == [
        dt.date(2026, 9, 1),
        dt.date(2026, 9, 2),
    ]
    assert edinet_days_to_fetch(dt.date(2026, 10, 7), today) == [today]


# ------------------------------------------------------------------------------ Twelve Data

DIVIDENDS = {
    "meta": {"symbol": "AAPL", "currency": "USD"},
    "dividends": [
        {"ex_date": "2026-08-11", "amount": 0.26},
        {"ex_date": "2020-01-01", "amount": 0.2},  # before `since`
        {"ex_date": "2026-05-12", "amount": 0},  # no positive amount
    ],
}
SPLITS = {
    "meta": {"symbol": "AAPL"},
    "splits": [
        {"date": "2026-07-01", "from_factor": 1, "to_factor": 4, "ratio": 0.25},
        {"date": "2026-06-01", "ratio": 0.5},  # factors missing: never guessed
    ],
}


def test_twelve_data_actions_are_ratio_after_per_before_and_drop_unusable_rows() -> None:
    actions = twelvedata.parse_actions(DIVIDENDS, SPLITS, since=dt.date(2026, 1, 1))
    by_type = {a.action_type: a for a in actions}
    assert len(actions) == 2
    assert by_type["cash_dividend"].cash_amount == Decimal("0.26")
    assert by_type["split"].ratio == Decimal(4)
    assert twelvedata.meta_currency(DIVIDENDS) == "USD"
    assert twelvedata.meta_currency({"meta": {"currency": "GBp"}}) is None
    with pytest.raises(twelvedata.TwelveDataError):
        twelvedata.parse_actions([], SPLITS, since=dt.date(2026, 1, 1))


@pytest.mark.parametrize(
    ("body", "state"),
    [
        ({"dividends": []}, None),
        ({"code": 403, "status": "error", "message": "Available on Grow plan"}, "paid_only"),
        ({"code": 400, "status": "error", "message": "requires a Pro plan"}, "paid_only"),
        ({"code": 429, "status": "error", "message": "run out of API credits"}, "quota_exhausted"),
        ({"code": 401, "status": "error", "message": "apikey is invalid"}, "access_unverified"),
        ({"code": 400, "status": "error", "message": "symbol not found"}, "not_covered"),
    ],
)
def test_twelve_data_plan_denial_is_paid_only_and_quota_is_not(
    body: dict[str, object], state: str | None
) -> None:
    result = twelvedata.classify_envelope(httpx.Response(200, json=body))
    assert (result.state.value if result else None) == state


def test_twelve_data_budget_is_a_small_share_of_the_basic_plan() -> None:
    budget = twelvedata.ACTIONS_BUDGET
    assert budget.per_minute is not None and budget.per_minute <= 8
    assert budget.daily is not None and budget.daily * 2 <= 800 * Decimal("0.15")


# ------------------------------------------------------------------------- mappings and wiring


def test_fmp_symbol_wins_over_sec_ticker_and_neither_means_no_call() -> None:
    assert fmp_symbols(_profiles(sec_ticker="AAA", fmp_symbol="AAA.L"), ["AAA_US_EQ"]) == {
        "AAA_US_EQ": "AAA.L"
    }
    assert fmp_symbols(_profiles(sec_ticker="AAA"), ["AAA_US_EQ"]) == {"AAA_US_EQ": "AAA"}
    assert fmp_symbols(_profiles(), ["AAA_US_EQ", "BBB_US_EQ"]) == {}


def test_profile_ids_are_validated_and_mapped() -> None:
    profiles = _profiles(companies_house_number="SC123456", edinet_code="E02144")
    assert companies_house_numbers(profiles, ["AAA_US_EQ"]) == {"AAA_US_EQ": "SC123456"}
    assert edinet_holdings(profiles, ["AAA_US_EQ"]) == {"E02144": "AAA_US_EQ"}
    with pytest.raises(ValueError):
        _profiles(companies_house_number="123")
    with pytest.raises(ValueError):
        _profiles(edinet_code="X02144")
    with pytest.raises(ValueError):
        _profiles(fmp_symbol="bad symbol")


def test_new_jobs_are_registered_and_off_by_default() -> None:
    jobs = JobsConfig()
    for name in NEW_JOBS:
        assert name in JOB_HANDLERS
        assert name in get_args(JobType)
        assert name in get_args(BudgetJobType)
        assert getattr(jobs, name).enabled is False
    assert set(get_args(BudgetJobType)) == set(get_args(JobType))
    for provider in ("edinet", "companies_house"):
        settings = jobs.providers[provider]
        assert settings.enabled is False and settings.daily_quota


def test_new_providers_have_hard_stops_in_the_zero_cost_registry() -> None:
    assert {"edinet", "companies_house"} <= set(NO_OVERAGE_PROVIDERS)
    covered = {(b.provider, b.endpoint) for b in literal_budgets()}
    assert ("twelve_data", "corporate_actions") in covered
    assert ("twelve_data", "splits") in covered
