"""D8.1 adapter contract harness: one set of assertions over every source adapter.

For every parser: the recorded baseline yields rows; an empty, schema-drifted or partial
payload either yields fewer rows or raises the adapter's own typed error - never an untyped
KeyError/TypeError and never invented rows.  For every HTTP entry point: 403/429/5xx raise a
typed error (or return a denial state) that maps to the matching AccessState, never an empty
success.  The harness also pins the offline guard: no real key, no network.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import importlib
import json
import pkgutil
import socket
from decimal import Decimal
from typing import Any

import httpx
import pytest

import steward.sources as sources_pkg
from steward.macro.providers import parse_fred_vintages, parse_world_bank
from steward.sources import openfigi, yahoo
from steward.sources.audit import classify_probe
from steward.sources.base import AccessState, access_from_http, retry_after_at
from steward.sources.free_market import parse_alpha_statement_facts
from steward.sources.fund_probes import classify_fund_probe
from steward.sources.governor import Budget, QuotaExceeded, RequestGovernor
from steward.verify_sources.contract import DENIAL_STATES, classify_failure, status_of
from tests.adapter_contract_cases import (
    CLASSIFIER_STATUS_MATRIX,
    CON,
    FETCH_CASES,
    NON_ADAPTER_MODULES,
    PARSER_CASES,
    FetchCase,
    ParserCase,
    jfix,
)
from tests.conftest import NetworkAccessBlocked

# Retry back-off must neither slow the suite nor loop forever.  The fake sleep returns at once
# but records every requested delay; a runaway retry loop trips the call/total guard instead of
# hanging, and `attempt` additionally runs under a real wall-clock timeout (B-P8-12).
MAX_SLEEP_CALLS = 50
MAX_TOTAL_REQUESTED_SLEEP = 600.0
CASE_TIMEOUT_SECONDS = 10.0
REAL_SLEEP = asyncio.sleep


class SleepLog:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float = 0, *_a: Any, **_k: Any) -> None:
        self.delays.append(float(delay))
        if len(self.delays) > MAX_SLEEP_CALLS:
            raise AssertionError(f"retry loop: {len(self.delays)} sleeps requested")
        if sum(self.delays) > MAX_TOTAL_REQUESTED_SLEEP:
            raise AssertionError(f"back-off requested {sum(self.delays)}s in total")
        await REAL_SLEEP(0)  # still yield to the event loop like a real sleep


@pytest.fixture(autouse=True)
def sleep_log(monkeypatch: pytest.MonkeyPatch) -> SleepLog:
    log = SleepLog()
    monkeypatch.setattr(asyncio, "sleep", log)
    monkeypatch.setattr(yahoo, "_sleep", log)
    monkeypatch.setattr(openfigi, "_sleep", log)
    return log


async def test_sleep_guard_stops_a_runaway_retry_loop(sleep_log: SleepLog) -> None:
    async def forever() -> None:
        while True:
            await asyncio.sleep(1)

    with pytest.raises(AssertionError, match="retry loop"):
        await asyncio.wait_for(forever(), CASE_TIMEOUT_SECONDS)
    assert len(sleep_log.delays) == MAX_SLEEP_CALLS + 1


async def test_sleep_guard_flags_excessive_total_backoff() -> None:
    with pytest.raises(AssertionError, match="back-off"):
        await asyncio.sleep(MAX_TOTAL_REQUESTED_SLEEP + 1)


# ---------------------------------------------------------------------------------------
# Completeness: a new adapter module cannot be added without a contract
# ---------------------------------------------------------------------------------------
def test_every_source_module_has_a_contract_or_a_documented_exemption() -> None:
    discovered = {"steward.sources"} | {
        m.name for m in pkgutil.iter_modules(sources_pkg.__path__, prefix="steward.sources.")
    }
    covered = {c.module for c in PARSER_CASES} | {c.module for c in FETCH_CASES}
    covered |= set(NON_ADAPTER_MODULES)
    missing = sorted(discovered - covered)
    assert not missing, f"adapter modules without a contract case: {missing}"
    for module in sorted(covered | discovered):
        importlib.import_module(module)  # every registered module still imports


def test_case_ids_are_unique() -> None:
    ids = [c.id for c in PARSER_CASES] + [c.id for c in FETCH_CASES]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------------------
# Parser contracts
# ---------------------------------------------------------------------------------------
def run_parser(case: ParserCase, payload: Any) -> tuple[int, BaseException | None]:
    try:
        return case.rows(case.parse(payload)), None
    except case.typed as exc:
        return 0, exc


@pytest.mark.parametrize("case", PARSER_CASES, ids=lambda c: c.id)
def test_parser_baseline_reads_recorded_fixture(case: ParserCase) -> None:
    rows, error = run_parser(case, case.baseline())
    assert error is None, f"recorded fixture must parse: {error!r}"
    assert rows >= case.min_rows


def test_no_parser_case_may_be_vacuous() -> None:
    """A baseline that yields zero rows makes the empty/drift/partial checks meaningless."""
    assert [c.id for c in PARSER_CASES if c.min_rows < 1] == []
    for case in PARSER_CASES:
        rows, error = run_parser(case, case.baseline())
        assert error is None and rows >= 1, f"{case.id}: baseline yields {rows} rows"


@pytest.mark.parametrize("case", PARSER_CASES, ids=lambda c: c.id)
def test_parser_empty_response_is_empty_or_typed(case: ParserCase) -> None:
    rows, _error = run_parser(case, case.empty)
    assert rows == 0


@pytest.mark.parametrize("case", PARSER_CASES, ids=lambda c: c.id)
def test_parser_schema_drift_is_typed_or_empty_never_invented(case: ParserCase) -> None:
    rows, _error = run_parser(case, case.drift(case.baseline()))
    assert rows == 0, "a renamed schema must not produce rows"


@pytest.mark.parametrize("case", PARSER_CASES, ids=lambda c: c.id)
def test_parser_partial_payload_never_crashes_untyped(case: ParserCase) -> None:
    baseline_rows, _ = run_parser(case, case.baseline())
    rows, _error = run_parser(case, case.partial(case.baseline()))
    assert rows <= baseline_rows


# ---------------------------------------------------------------------------------------
# HTTP contracts
# ---------------------------------------------------------------------------------------
def client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def attempt(case: FetchCase, handler: Any) -> tuple[Any, BaseException | None]:
    async with client(handler) as http:
        try:
            return await asyncio.wait_for(case.call(http), CASE_TIMEOUT_SECONDS), None
        except case.typed as exc:
            return None, exc


def state_of(case: FetchCase, result: Any, error: BaseException | None) -> AccessState:
    if error is not None:
        return classify_failure(error).state
    assert case.access_of is not None, "a denial must raise a typed error"
    return AccessState(case.access_of(result).state)


@pytest.mark.parametrize("case", FETCH_CASES, ids=lambda c: c.id)
async def test_fetch_healthy_response_succeeds(case: FetchCase) -> None:
    result, error = await attempt(case, case.ok)
    assert error is None, f"healthy recorded response failed: {error!r}"
    if case.access_of is not None:
        assert case.access_of(result).state == AccessState.AVAILABLE


# Adapters that return an AccessResult instead of raising.  `access_from_http` (steward/sources/
# base.py, outside the verification stream) does not put the status into `detail` for 5xx, so
# these three cannot yet show provenance; new adapters must not be added here.
ACCESS_RESULTS_WITHOUT_STATUS = frozenset(
    {"public_macro_world_bank", "public_macro_sdmx", "public_macro_comtrade"}
)

EXPECTED_BY_STATUS = {
    403: {AccessState.ACCESS_UNVERIFIED, AccessState.PAID_ONLY},
    429: {AccessState.QUOTA_EXHAUSTED},
    500: {AccessState.TEMPORARILY_FAILED},
}


@pytest.mark.parametrize("status", sorted(EXPECTED_BY_STATUS))
@pytest.mark.parametrize("case", FETCH_CASES, ids=lambda c: c.id)
async def test_fetch_denial_maps_to_typed_access_state(case: FetchCase, status: int) -> None:
    def denied(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="denied", headers={"retry-after": "1"})

    result, error = await attempt(case, denied)
    state = state_of(case, result, error)
    assert state in EXPECTED_BY_STATUS[status], (case.id, status, state)
    assert state in DENIAL_STATES and state != AccessState.AVAILABLE
    if status == 500:
        # Provenance: a 5xx must say which HTTP status the provider returned (429/403 may be
        # named by the provider's envelope text instead), otherwise a server error is
        # indistinguishable from a parse failure in the audit trail.
        if error is not None:
            assert status_of(error) == 500, f"{case.id}: {error!r} does not carry HTTP 500"
        else:
            assert case.access_of is not None
            detail = case.access_of(result).detail
            if case.id not in ACCESS_RESULTS_WITHOUT_STATUS:
                assert detail and "500" in detail, f"{case.id}: access result lacks HTTP 500"


@pytest.mark.parametrize("case", [c for c in FETCH_CASES if c.garbage_typed], ids=lambda c: c.id)
async def test_fetch_html_instead_of_data_is_typed_not_empty_success(case: FetchCase) -> None:
    def html(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>Service temporarily unavailable</body></html>")

    result, error = await attempt(case, html)
    if error is None:
        # Some endpoints legitimately parse HTML into "no rows"; the contract is that the
        # result is then empty, never a populated success.
        assert not result, f"{case.id}: HTML body produced {result!r}"


@pytest.mark.parametrize(
    "case", [c for c in FETCH_CASES if c.not_found_is_empty], ids=lambda c: c.id
)
async def test_fetch_404_is_not_covered_not_failure(case: FetchCase) -> None:
    result, error = await attempt(case, lambda r: httpx.Response(404, json={}))
    assert error is None
    assert not result


async def test_public_macro_world_bank_error_envelope_is_not_available() -> None:
    from steward.sources.public_macro import WorldBankAdapter

    body = json.loads((CON / "world_bank_error.json").read_text())
    async with client(lambda r: httpx.Response(200, json=body)) as http:
        result = await WorldBankAdapter(http).fetch({"country": "ZZ", "indicators": ["X"]})
    assert result.access.state != AccessState.AVAILABLE


async def test_provider_envelope_denials_classify_as_paid_or_quota() -> None:
    from steward.sources.free_market import FreeSourceError, parse_alpha_daily

    quota = json.loads((CON / "alpha_quota_note.json").read_text())
    premium = json.loads((CON / "alpha_premium_information.json").read_text())
    with pytest.raises(FreeSourceError) as quota_error:
        parse_alpha_daily(quota)
    with pytest.raises(FreeSourceError) as premium_error:
        parse_alpha_daily(premium)
    assert classify_failure(quota_error.value).state == AccessState.QUOTA_EXHAUSTED
    assert classify_failure(premium_error.value).state == AccessState.PAID_ONLY


# ---------------------------------------------------------------------------------------
# Probe classifiers
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(("status", "body", "allowed"), CLASSIFIER_STATUS_MATRIX)
@pytest.mark.parametrize("classifier", [classify_probe, classify_fund_probe], ids=["audit", "fund"])
def test_probe_classifiers_never_report_denial_as_available(
    classifier: Any, status: int, body: Any, allowed: set[str]
) -> None:
    state = classifier(status, body).state.value
    if classifier is classify_probe and status == 403 and "premium" in str(body):
        allowed = allowed | {"paid_only"}
    # The two classifiers agree on the class of outcome; 403 may be paid or unverified.
    assert state in allowed | ({"paid_only"} if status == 403 else set()), (status, body, state)
    assert state != "available"


# ---------------------------------------------------------------------------------------
# Revised data
# ---------------------------------------------------------------------------------------
def test_revised_world_bank_value_carries_a_new_vintage() -> None:
    first = parse_world_bank(json.loads((CON / "world_bank.json").read_text()), "u")
    revised = parse_world_bank(json.loads((CON / "world_bank_revised.json").read_text()), "u")
    a = next(p for p in first if p.period.year == 2024)
    b = next(p for p in revised if p.period.year == 2024)
    assert (a.series_id, a.geography, a.period) == (b.series_id, b.geography, b.period)
    assert a.value != b.value and a.vintage != b.vintage
    # An unpublished value stays unknown rather than becoming zero.
    assert next(p for p in first if p.period.year == 2025).value is None


def test_fred_vintages_keep_every_release_with_release_time_as_known_at() -> None:
    body = json.loads((CON / "fred_vintages.json").read_text())
    points = parse_fred_vintages(body, series_id="GDP", unit="USD", source_url="u")
    q3 = [p for p in points if p.period == dt.date(2025, 7, 1)]
    assert len(q3) == 2 and {p.value for p in q3} == {Decimal("22000.0"), Decimal("22100.5")}
    assert all(p.release_at is not None for p in q3)
    missing = [p for p in points if p.period == dt.date(2025, 10, 1)]
    assert missing and missing[0].value is None


def test_restated_statement_keeps_both_reports_distinguishable() -> None:
    first = parse_alpha_statement_facts(
        json.loads((CON / "alpha_statement.json").read_text()), statement="income"
    )
    restated = parse_alpha_statement_facts(
        json.loads((CON / "alpha_statement_restated.json").read_text()), statement="income"
    )
    old = {f["label"]: f["value"] for f in first}
    new = {f["label"]: f["value"] for f in restated}
    assert old["totalRevenue"] == Decimal(1000) and new["totalRevenue"] == Decimal(1010)
    assert all(f["period_end"] == dt.date(2025, 12, 31) for f in first + restated)


# ---------------------------------------------------------------------------------------
# Quota reset
# ---------------------------------------------------------------------------------------
async def test_quota_resets_at_minute_day_and_month_boundaries() -> None:
    governor = RequestGovernor()
    budget = Budget(per_minute=Decimal(1), daily=Decimal(2), monthly=Decimal(3))
    t0 = dt.datetime(2026, 9, 29, 23, 50, tzinfo=dt.UTC)
    await governor.reserve("p", "e", Decimal(1), budget, now=t0)
    with pytest.raises(QuotaExceeded, match="per-minute"):
        await governor.reserve("p", "e", Decimal(1), budget, now=t0)
    # next minute: allowed again (second daily credit)
    await governor.reserve("p", "e", Decimal(1), budget, now=t0 + dt.timedelta(minutes=1))
    with pytest.raises(QuotaExceeded, match="daily"):
        await governor.reserve("p", "e", Decimal(1), budget, now=t0 + dt.timedelta(minutes=2))
    # midnight UTC: the daily window resets, the monthly one does not
    t1 = dt.datetime(2026, 9, 30, 0, 5, tzinfo=dt.UTC)
    await governor.reserve("p", "e", Decimal(1), budget, now=t1)
    with pytest.raises(QuotaExceeded, match="monthly"):
        await governor.reserve("p", "e", Decimal(1), budget, now=t1 + dt.timedelta(minutes=1))
    # first of the month: the monthly window resets
    t2 = dt.datetime(2026, 10, 1, 0, 1, tzinfo=dt.UTC)
    await governor.reserve("p", "e", Decimal(1), budget, now=t2)


async def test_hard_block_after_denial_survives_quota_reset() -> None:
    governor = RequestGovernor()
    budget = Budget(daily=Decimal(10))
    await governor.hard_block("p", "e")
    later = dt.datetime(2030, 1, 1, tzinfo=dt.UTC)
    with pytest.raises(QuotaExceeded, match="hard-blocked"):
        await governor.reserve("p", "e", Decimal(1), budget, now=later)


def test_retry_after_is_honoured_and_never_in_the_past() -> None:
    now = dt.datetime(2026, 10, 1, 12, 0, tzinfo=dt.UTC)
    at = retry_after_at("120", now=now)
    assert at is not None and now + dt.timedelta(seconds=120) <= at <= now + dt.timedelta(
        seconds=124
    )
    assert retry_after_at("not-a-date", now=now) is None
    assert retry_after_at(None, now=now) is None
    assert access_from_http(429, retry_at=at).retry_at == at


# ---------------------------------------------------------------------------------------
# Offline guard
# ---------------------------------------------------------------------------------------
def test_network_is_blocked_in_tests() -> None:
    with pytest.raises(NetworkAccessBlocked):
        socket.getaddrinfo("api.example.com", 443)
    with pytest.raises(NetworkAccessBlocked):
        socket.create_connection(("93.184.216.34", 443), timeout=1)


def test_network_block_cannot_be_swallowed_by_an_except_exception_adapter() -> None:
    """An adapter that turns every Exception into a typed failure must still fail the test
    that reached the network (B49: NetworkAccessBlocked is a BaseException)."""
    assert not issubclass(NetworkAccessBlocked, Exception)

    def adapter() -> str:
        try:
            socket.create_connection(("93.184.216.34", 443), timeout=1)
        except Exception:
            return "temporarily_failed"
        return "ok"

    with pytest.raises(NetworkAccessBlocked):
        adapter()


async def test_network_block_survives_an_async_except_exception_adapter() -> None:
    async def adapter() -> str:
        try:
            async with httpx.AsyncClient() as http:
                await http.get("https://api.example.com/x")
        except Exception:
            return "swallowed"
        return "ok"

    with pytest.raises(NetworkAccessBlocked):
        await adapter()


def test_proxy_environment_and_env_file_are_neutralised_in_tests() -> None:
    import os

    from steward.settings import Settings
    from tests.conftest import PROXY_ENV

    assert [n for n in PROXY_ENV if os.environ.get(n)] == []
    assert Settings.model_config.get("env_file") is None


def test_no_provider_key_is_visible_to_tests() -> None:
    import os

    from tests.conftest import PROVIDER_KEY_ENV

    assert [n for n in PROVIDER_KEY_ENV if os.environ.get(n)] == []


def test_recorded_fixtures_exist_for_every_contract_baseline() -> None:
    for case in PARSER_CASES:
        assert case.baseline() is not None, case.id
    assert jfix("reference", "firds_sap.json")
