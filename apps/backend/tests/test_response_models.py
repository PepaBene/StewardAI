"""Response models keep the wire format of the untyped dicts they replaced, and the routes that
use them have real OpenAPI response schemas."""

import datetime as dt
import json
from decimal import Decimal
from typing import Any

import pytest
from fastapi.encoders import jsonable_encoder

from api import response_models as m
from api.main import app

UTC = dt.UTC
NOW = dt.datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)
DAY = dt.date(2026, 3, 4)


def _norm(value: Any) -> Any:
    """Equality-friendly JSON: 'Z' and '+00:00' are the same instant."""
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_norm(v) for v in value]
    if isinstance(value, str) and value.endswith("Z"):
        return value[:-1] + "+00:00"
    return value


def _wire(model: type[m._Model], payload: dict[str, Any]) -> dict[str, Any]:
    """The JSON FastAPI emits for the typed route (validate, then serialise as JSON)."""
    out = model.model_validate(payload).model_dump(mode="json", exclude_unset=True)
    return json.loads(json.dumps(out))


def _old_wire(payload: dict[str, Any]) -> dict[str, Any]:
    """The JSON FastAPI emitted when the route returned the raw dict."""
    return json.loads(json.dumps(jsonable_encoder(payload)))


def _same(model: type[m._Model], payload: dict[str, Any]) -> None:
    assert _norm(_wire(model, payload)) == _norm(_old_wire(payload))


OBS = {
    "id": 1,
    "provider": "alpha_vantage",
    "dataset": "exposure_context",
    "metric": "fx_close",
    "period_end": DAY,
    "value": Decimal("1.0825"),
    "text_value": None,
    "unit": "ratio",
    "currency": "USD",
    "source_url": "https://example.test/x",
    "published_at": None,
    "known_at": NOW,
    "match_confidence": Decimal("0.9"),
    "raw": {"a": [1, 2]},
}


def test_company_research_and_exposure() -> None:
    _same(
        m.CompanyResearchObservationsResponse,
        {"ticker": "ABC", "known_as_of": None, "observations": [OBS]},
    )
    exposure_obs = {k: v for k, v in OBS.items() if k not in ("published_at", "match_confidence")}
    _same(
        m.ExposureContextResponse,
        {
            "ticker": "ABC",
            "exposure_assumptions": [{"kind": "revenue_currency", "name": "USD"}],
            "observations": [exposure_obs],
            "limitations": ["x"],
        },
    )


def test_refresh_responses_keep_absent_keys_absent() -> None:
    skipped = m.PdlRefreshResponse.model_validate(
        {"ticker": "A", "matched_records": 0, "skipped": "r"}
    )
    assert skipped.model_dump(mode="json", exclude_unset=True)["skipped"] == "r"
    plain = m.PdlRefreshResponse.model_validate({"ticker": "A", "matched_records": 2})
    assert plain.model_dump(mode="json", exclude_unset=True) == {
        "ticker": "A",
        "matched_records": 2,
    }
    _same(m.AlphaExposureRefreshResponse, {"ticker": "A", "series": "s", "observations_stored": 3})
    _same(m.DiffbotRefreshResponse, {"ticker": "A", "suggested_edges": 3})
    _same(m.EpoRefreshResponse, {"ticker": "A", "patent_candidates": 3})


def test_exchange_sessions() -> None:
    row = {
        "mic": "XPRA",
        "session_date": DAY,
        "open_at": NOW,
        "close_at": None,
        "state": "half_day",
        "source": "exchange",
        "confirmed": True,
        "evidence_url": None,
        "reason": None,
        "known_at": NOW,
    }
    assert _norm(m.ExchangeSessionRow.model_validate(row).model_dump(mode="json")) == _norm(
        jsonable_encoder(row)
    )
    _same(
        m.ExpectedSessionsResponse,
        {
            "mic": "XPRA",
            "start": DAY,
            "end": DAY,
            "count": 1,
            "basis": "confirmed_calendar",
            "confirmed_days": 1,
            "days": [DAY],
        },
    )
    _same(m.RecordedExchangeSessionResponse, {"session_date": DAY})


def test_peer_set() -> None:
    peer = {
        "id": 1,
        "peer_ticker": "XYZ",
        "basis": "sector",
        "status": "confirmed",
        "evidence_id": None,
        "source": "owner",
        "valid_from": None,
        "valid_to": DAY,
        "known_at": NOW,
        "detail": {"k": 1},
        "status_history": [{"status": "suggested"}],
    }
    _same(m.PeerSetResponse, {"ticker": "ABC", "known_as_of": NOW, "peers": [peer]})
    _same(m.RecordedPeerResponse, {"id": 4})


RESULT = {
    "symbol": "ABC.L",
    "source": "yahoo",
    "chosen": True,
    "currency": "GBP",
    "mic": None,
    "price_return": Decimal("0.0521"),
    "price_return_reason": None,
    "raw_price_return": Decimal("0.05"),
    "total_return": None,
    "total_return_reason": "corporate-action coverage is incomplete",
    "action_coverage": "incomplete",
    "coverage": Decimal("0.95"),
    "expected_sessions": 43,
    "observed_sessions": 41,
    "calendar_basis": "confirmed_calendar",
    "missing_actions": {"price_raw": [], "price_adjusted": ["x"], "total_return": ["y"]},
    "observations": 41,
    "start": DAY,
    "end": DAY,
}


def test_returns() -> None:
    _same(m.HoldingReturnsResponse, {"ticker": "A", "known_as_of": DAY, "results": [RESULT]})
    no_series = {"ticker": "A", "known_as_of": DAY, "results": [], "reason": "none"}
    _same(m.HoldingReturnsResponse, no_series)
    bare = m.HoldingReturnsResponse.model_validate(
        {"ticker": "A", "known_as_of": DAY, "results": []}
    )
    assert "reason" not in bare.model_dump(mode="json", exclude_unset=True)


def test_coverage_and_listing() -> None:
    entry = {
        "provider": "owner_review",
        "state": "available",
        "latest_period": DAY,
        "checked_at": NOW,
        "reason": None,
        "owner_verified": True,
    }
    _same(m.ActionCoverageResponse, {"symbol": "ABC", "entries": [entry]})
    _same(
        m.ActionCoverageConfirmedResponse,
        {"symbol": "ABC", "covered_through": DAY, "state": "available"},
    )
    listing = {
        "id": 7,
        "mic": "XLON",
        "ticker": "ABC",
        "currency": "GBP",
        "status": "active",
        "valid_from": DAY,
        "valid_to": None,
        "security_id": 3,
        "isin": "GB0000000001",
    }
    overview = {
        "ticker": "ABC",
        "status": "confirmed",
        "score": 1.0,
        "decided_at": NOW,
        "listing": listing,
        "evidence": {"sources": ["firds"]},
        "provider_symbols": [{"provider": "yahoo", "identifier": "ABC.L", "match_status": "x"}],
    }
    _same(m.ListingOverviewResponse, overview)
    _same(m.ListingOverviewResponse, {**overview, "listing": None, "decided_at": None})
    _same(
        m.DerivedListingResponse,
        {"ticker": "A", "status": "suggested", "listing_id": None, "reason": None, "score": None},
    )
    _same(m.ConfirmedListingResponse, {"ticker": "A", "listing_id": 2, "status": "confirmed"})


def test_funds() -> None:
    sheet = {
        "id": 1,
        "fund_isin": "IE00B4L5Y983",
        "as_of": DAY,
        "source": "issuer",
        "snapshot_id": None,
        "provider": "ishares",
        "share_class": None,
        "fund_name": "F",
        "fund_currency": "USD",
        "total_assets": Decimal("1234567890.50"),
        "total_assets_currency": "USD",
        "total_assets_as_of": DAY,
        "expense_ratio": Decimal("0.0020"),
        "charges": {"ter": 0.002},
        "benchmark_name": None,
        "distribution_policy": "accumulating",
        "nav_basis": None,
        "holdings_count": 1500,
        "equity_weight": Decimal("0.99"),
        "cash_weight": None,
        "derivative_weight": None,
        "other_weight": None,
        "total_weight": Decimal("1"),
        "residual_weight": Decimal("0"),
        "unclassified_sector_weight": None,
        "unclassified_country_weight": None,
        "reconcile_tolerance": Decimal("0.005"),
        "reconciles": True,
        "lookthrough_basis": "issuer",
        "coverage_note": None,
        "missing_fields": ["cash_weight"],
        "fetched_at": NOW,
        "field_names_verified": False,
        "field_provenance": {},
        "extraction_warnings": [],
    }
    _same(m.FactsheetRow, sheet)
    audit = {
        "fund_key": "k",
        "provider": "p",
        "as_of": DAY,
        "fingerprint": "f",
        "basis": "b",
        "rules": {"r": 1},
        "pe": Decimal("18.2"),
        "pb": None,
        "index_pe": None,
        "index_pb": Decimal("3"),
        "verdict": "real_value",
        "alert": False,
        "detail": {},
        "observed_at": NOW,
    }
    _same(m.CharacteristicsAuditRow, audit)


def test_look_through_as_of_wraps_the_existing_report_model() -> None:
    field = m.LookThroughAsOfResponse.model_fields["report"]
    assert field.annotation is m.LookThroughReport


TYPED = [
    ("get", "/holdings/{ticker}/company-research-observations"),
    ("get", "/holdings/{ticker}/exposure-context"),
    ("post", "/holdings/{ticker}/exposure-context/alpha-vantage"),
    ("post", "/holdings/{ticker}/company-research/diffbot"),
    ("post", "/holdings/{ticker}/company-research/pdl"),
    ("post", "/holdings/{ticker}/patent-families/epo-ops/refresh"),
    ("get", "/exchange-sessions/{mic}"),
    ("get", "/exchange-sessions/{mic}/expected"),
    ("post", "/exchange-sessions"),
    ("get", "/holdings/{ticker}/peer-set"),
    ("post", "/holdings/{ticker}/peer-set"),
    ("get", "/holdings/{ticker}/returns"),
    ("get", "/holdings/{ticker}/corporate-actions/coverage"),
    ("post", "/holdings/{ticker}/corporate-actions/coverage"),
    ("get", "/holdings/{ticker}/listing"),
    ("post", "/holdings/{ticker}/listing/derive"),
    ("post", "/holdings/{ticker}/listing/confirm"),
    ("get", "/funds/look-through"),
    ("get", "/funds/{isin}/factsheet"),
    ("get", "/funds/characteristics-audit"),
]


@pytest.mark.parametrize(("method", "path"), TYPED)
def test_route_has_a_response_schema(method: str, path: str) -> None:
    schema = app.openapi()["paths"][path][method]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert schema, f"{method} {path} has an empty response schema"
    assert schema != {} and (
        "$ref" in schema or (schema.get("type") == "array" and schema.get("items"))
    )
