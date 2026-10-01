"""Opt-in access probes for fund, index and institutional-ownership endpoints (plan D6.2).

Finnhub (ETF profile/holdings, index constituents, institutional ownership) and FMP (ETF info,
ETF holdings, institutional ownership) document these as free-key or premium depending on the
plan. Nothing here is a required dependency: with no key a probe is skipped as
``access_unverified`` ("credential not configured"), never as an empty success.

Outcome typing (see `classify_fund_probe`):

- HTTP 402/403, or a body naming a premium/paid plan -> ``paid_only`` (the key itself works for
  other endpoints; 401 is a bad key -> ``access_unverified``). The access ledger hard-blocks
  the endpoint, and `run_fund_probes` never retries a hard-blocked endpoint with the same
  credential, so a denied endpoint is not probed daily.
- HTTP 429 or a quota message -> ``quota_exhausted`` with the provider's Retry-After.
- 200 with rows -> ``available`` and the rows are normalized to `FundProbeRecord`s.
- 200 without rows -> ``not_covered``.

Wiring into the shared source audit (`sources/audit.py`, not edited here): in
`run_source_audit` iterate ``[*probes(settings), *fund_probe_list(settings)]`` and, after an
``available`` classification, call ``normalize_probe`` / ``store_probe_records``; or schedule
`fund_probes_handler` as its own job (`fund_probes`). Both use these endpoints' own
``provider_access`` rows (created by migration 20261003110000).

Endpoint paths follow Finnhub's and FMP's public API documentation; they have not been called
live from this code and every fixture under tests/fixtures/funds is hand-built to that format.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import asyncpg
import httpx

from steward import __version__
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.jobs.runner import JobContext
from steward.settings import Settings, get_settings
from steward.sources.audit import Probe, classify_probe
from steward.sources.base import AccessResult, AccessState, retry_after_at
from steward.sources.governor import Budget, PostgresRequestGovernor, QuotaExceeded
from steward.sources.nport import valid_isin
from steward.sources.store import record_access

FINNHUB = "https://finnhub.io/api/v1"
FMP = "https://financialmodelingprep.com/stable"
PREMIUM_WORDS = ("premium", "upgrade", "subscription", "not available under your", "plan")
QUOTA_WORDS = ("limit reached", "rate limit", "call frequency", "quota", "too many requests")


def fund_probe_list(settings: Settings) -> list[Probe]:
    """The D6.2 probes for the orchestrator to run beside `audit.probes(settings)`."""

    def secret(value: Any) -> str | None:
        return value.get_secret_value() if value else None

    finnhub, fmp = secret(settings.finnhub_api_key), secret(settings.fmp_api_key)
    return [
        Probe(
            "finnhub", "etf_profile", f"{FINNHUB}/etf/profile", {"symbol": "SPY"}, finnhub, "token"
        ),
        Probe(
            "finnhub",
            "etf_holdings",
            f"{FINNHUB}/etf/holdings",
            {"symbol": "SPY"},
            finnhub,
            "token",
            "SPY",
        ),
        Probe(
            "finnhub",
            "index_constituents",
            f"{FINNHUB}/index/constituents",
            {"symbol": "^GSPC"},
            finnhub,
            "token",
            "^GSPC",
        ),
        Probe(
            "finnhub",
            "institutional_ownership",
            f"{FINNHUB}/institutional/ownership",
            {"symbol": "AAPL", "from": "2025-01-01"},
            finnhub,
            "token",
            "AAPL",
        ),
        Probe("fmp", "etf_info", f"{FMP}/etf/info", {"symbol": "SPY"}, fmp, "apikey", "SPY"),
        Probe("fmp", "etf_holdings", f"{FMP}/etf/holdings", {"symbol": "SPY"}, fmp, "apikey"),
        Probe(
            "fmp",
            "institutional_ownership",
            f"{FMP}/institutional-ownership/extract-analytics/holder",
            {"symbol": "AAPL", "year": "2025", "quarter": "4"},
            fmp,
            "apikey",
        ),
    ]


def classify_fund_probe(status: int, body: Any, expected_symbol: str | None = None) -> AccessResult:
    """Map a response to a typed access state; a denial is never an empty success."""
    text = json.dumps(body, default=str).casefold() if body is not None else ""
    if status == 401:
        return AccessResult(AccessState.ACCESS_UNVERIFIED, "credential rejected")
    if status in (402, 403):
        return AccessResult(AccessState.PAID_ONLY, f"HTTP {status}: endpoint not in the free plan")
    if status == 429:
        return AccessResult(AccessState.QUOTA_EXHAUSTED, "provider rate or quota limit")
    if status == 200 and isinstance(body, dict):
        message = " ".join(
            str(body.get(key, "")) for key in ("error", "Error Message", "message", "Information")
        ).casefold()
        if message.strip():
            if any(word in message for word in PREMIUM_WORDS):
                return AccessResult(AccessState.PAID_ONLY, "provider reports a paid-tier endpoint")
            if any(word in message for word in QUOTA_WORDS):
                return AccessResult(AccessState.QUOTA_EXHAUSTED, "provider rate or quota limit")
    if status == 200 and isinstance(body, str) and any(w in text for w in PREMIUM_WORDS):
        return AccessResult(AccessState.PAID_ONLY, "provider reports a paid-tier endpoint")
    if status == 200 and _is_empty(body):
        return AccessResult(AccessState.NOT_COVERED, "provider returned no matching data")
    return classify_probe(status, body, expected_symbol)


def _is_empty(body: Any) -> bool:
    if body in (None, "", [], {}):
        return True
    if isinstance(body, dict):
        # Finnhub answers an uncovered symbol with the envelope and no rows.
        rows = [
            v
            for k, v in body.items()
            if k in ("holdings", "constituents", "constituentsBreakdown", "data", "profile")
        ]
        return bool(rows) and not any(rows)
    return False


# ----------------------------------------------------------------------------- normalization


@dataclass(frozen=True, slots=True)
class FundProbeRecord:
    provider: str
    endpoint: str
    record_kind: str  # etf_profile | etf_holding | index_constituent | institutional_holder
    subject_symbol: str
    item_key: str
    item_name: str | None = None
    item_symbol: str | None = None
    isin: str | None = None
    cusip: str | None = None
    as_of: dt.date | None = None
    weight_pct: Decimal | None = None
    shares: Decimal | None = None
    value: Decimal | None = None
    currency: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


def _dec(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _str(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text if text and text.upper() != "N/A" else None


def _isin(value: Any) -> str | None:
    text = (_str(value) or "").upper()
    return text if valid_isin(text) else None


def _cusip(value: Any) -> str | None:
    text = (_str(value) or "").upper()
    return text if len(text) == 9 and text.isalnum() and text != "0" * 9 else None


def _pct(value: Any) -> Decimal | None:
    number = _dec(value)
    # A weight beyond +/-100 % of a fund is a unit error, not data: keep NULL.
    return number if number is not None and abs(number) <= 100 else None


def _rows(body: Any, key: str | None = None) -> list[dict[str, Any]]:
    rows = body.get(key) if key and isinstance(body, dict) else body
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def normalize_probe(
    provider: str, endpoint: str, subject: str, body: Any
) -> tuple[list[FundProbeRecord], list[str]]:
    """Typed records plus validator warnings. An unexpected shape yields no records."""
    handler = _NORMALIZERS.get((provider, endpoint))
    if handler is None:
        return [], [f"no normalizer for {provider}:{endpoint}"]
    records, warnings = handler(provider, endpoint, subject, body)
    seen: set[str] = set()
    unique = []
    for record in records:
        if record.item_key in seen:
            warnings.append(f"duplicate {record.item_key} dropped")
            continue
        seen.add(record.item_key)
        unique.append(record)
    return unique, warnings


def _finnhub_profile(
    p: str, e: str, subject: str, body: Any
) -> tuple[list[FundProbeRecord], list[str]]:
    profile = body.get("profile") if isinstance(body, dict) else None
    if not isinstance(profile, dict) or not profile:
        return [], ["etf profile payload has no profile object"]
    attrs = {
        k: profile[k]
        for k in (
            "assetClass",
            "expenseRatio",
            "inceptionDate",
            "nav",
            "aum",
            "domicile",
            "etfCompany",
        )
        if profile.get(k) is not None
    }
    return [
        FundProbeRecord(
            p,
            e,
            "etf_profile",
            subject.upper(),
            subject.upper(),
            item_name=_str(profile.get("name")),
            item_symbol=subject.upper(),
            isin=_isin(profile.get("isin")),
            cusip=_cusip(profile.get("cusip")),
            currency=_str(profile.get("navCurrency")),
            attributes=attrs,
        )
    ], []


def _finnhub_holdings(
    p: str, e: str, subject: str, body: Any
) -> tuple[list[FundProbeRecord], list[str]]:
    if not isinstance(body, dict) or not isinstance(body.get("holdings"), list):
        return [], ["etf holdings payload has no holdings list"]
    as_of = _date(body.get("atDate"))
    warnings = [] if as_of else ["holdings carry no as-of date"]
    out = []
    for i, row in enumerate(_rows(body, "holdings")):
        isin, cusip, symbol = (
            _isin(row.get("isin")),
            _cusip(row.get("cusip")),
            _str(row.get("symbol")),
        )
        key = isin or cusip or symbol or f"row{i}"
        out.append(
            FundProbeRecord(
                p,
                e,
                "etf_holding",
                subject.upper(),
                key,
                item_name=_str(row.get("name")),
                item_symbol=symbol,
                isin=isin,
                cusip=cusip,
                as_of=as_of,
                weight_pct=_pct(row.get("percent")),
                shares=_dec(row.get("share")),
                value=_dec(row.get("value")),
            )
        )
    return out, warnings


def _finnhub_index(
    p: str, e: str, subject: str, body: Any
) -> tuple[list[FundProbeRecord], list[str]]:
    if not isinstance(body, dict) or not isinstance(body.get("constituents"), list):
        return [], ["index payload has no constituents list"]
    breakdown = _rows(body, "constituentsBreakdown")
    if breakdown:
        return [
            FundProbeRecord(
                p,
                e,
                "index_constituent",
                subject,
                _str(r.get("symbol")) or f"row{i}",
                item_name=_str(r.get("name")),
                item_symbol=_str(r.get("symbol")),
                isin=_isin(r.get("isin")),
                cusip=_cusip(r.get("cusip")),
                weight_pct=_pct(r.get("weight")),
                shares=_dec(r.get("shares")),
                attributes={k: r[k] for k in ("sector", "industry") if r.get(k)},
            )
            for i, r in enumerate(breakdown)
        ], ["index constituents have no historical as-of date; not a constituent history"]
    return [
        FundProbeRecord(p, e, "index_constituent", subject, str(s), item_symbol=str(s))
        for s in body["constituents"]
        if isinstance(s, str) and s
    ], ["index constituents have no historical as-of date; not a constituent history"]


def _finnhub_institutional(
    p: str, e: str, subject: str, body: Any
) -> tuple[list[FundProbeRecord], list[str]]:
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        return [], ["institutional ownership payload has no data list"]
    out = []
    for period in _rows(body, "data"):
        report_date = _date(period.get("reportDate"))
        for r in _rows(period, "ownership"):
            cik = _str(r.get("cik"))
            name = _str(r.get("name"))
            if not (cik or name) or report_date is None:
                continue
            out.append(
                FundProbeRecord(
                    p,
                    e,
                    "institutional_holder",
                    subject.upper(),
                    f"{cik or name}:{report_date.isoformat()}",
                    item_name=name,
                    as_of=report_date,
                    shares=_dec(r.get("share")),
                    value=_dec(r.get("value")),
                    weight_pct=_pct(r.get("portfolioPercent")),
                    attributes={
                        k: r[k] for k in ("cik", "change", "filingDate", "percentage") if k in r
                    },
                )
            )
    return out, ["13F-derived holder rows: never add to N-PORT or 13F tables"]


def _fmp_info(p: str, e: str, subject: str, body: Any) -> tuple[list[FundProbeRecord], list[str]]:
    rows = _rows(body)
    if not rows:
        return [], ["etf info payload is not a list of objects"]
    r = rows[0]
    attrs = {
        k: r[k]
        for k in (
            "assetClass",
            "expenseRatio",
            "assetsUnderManagement",
            "inceptionDate",
            "holdingsCount",
            "website",
        )
        if r.get(k) is not None
    }
    return [
        FundProbeRecord(
            p,
            e,
            "etf_profile",
            subject.upper(),
            subject.upper(),
            item_name=_str(r.get("name")),
            item_symbol=_str(r.get("symbol")),
            isin=_isin(r.get("isin")),
            as_of=_date(r.get("updatedAt")),
            currency=_str(r.get("navCurrency")),
            attributes=attrs,
        )
    ], []


def _fmp_holdings(
    p: str, e: str, subject: str, body: Any
) -> tuple[list[FundProbeRecord], list[str]]:
    if not isinstance(body, list):
        return [], ["etf holdings payload is not a list"]
    out = []
    for i, r in enumerate(_rows(body)):
        symbol = _str(r.get("asset")) or _str(r.get("symbol"))
        isin, cusip = _isin(r.get("isin")), _cusip(r.get("securityCusip") or r.get("cusip"))
        out.append(
            FundProbeRecord(
                p,
                e,
                "etf_holding",
                subject.upper(),
                isin or cusip or symbol or f"row{i}",
                item_name=_str(r.get("name")),
                item_symbol=symbol,
                isin=isin,
                cusip=cusip,
                as_of=_date(r.get("updatedAt") or r.get("updated")),
                weight_pct=_pct(r.get("weightPercentage")),
                shares=_dec(r.get("sharesNumber")),
                value=_dec(r.get("marketValue")),
            )
        )
    return out, []


def _fmp_institutional(
    p: str, e: str, subject: str, body: Any
) -> tuple[list[FundProbeRecord], list[str]]:
    if not isinstance(body, list):
        return [], ["institutional ownership payload is not a list"]
    out = []
    for r in _rows(body):
        cik, name, as_of = _str(r.get("cik")), _str(r.get("investorName")), _date(r.get("date"))
        if not (cik or name) or as_of is None:
            continue
        out.append(
            FundProbeRecord(
                p,
                e,
                "institutional_holder",
                subject.upper(),
                f"{cik or name}:{as_of.isoformat()}",
                item_name=name,
                as_of=as_of,
                shares=_dec(r.get("sharesNumber")),
                value=_dec(r.get("marketValue")),
                weight_pct=_pct(r.get("weightPercentage")),
                attributes={k: r[k] for k in ("cik", "changeInSharesNumber") if k in r},
            )
        )
    return out, ["13F-derived holder rows: never add to N-PORT or 13F tables"]


_NORMALIZERS = {
    ("finnhub", "etf_profile"): _finnhub_profile,
    ("finnhub", "etf_holdings"): _finnhub_holdings,
    ("finnhub", "index_constituents"): _finnhub_index,
    ("finnhub", "institutional_ownership"): _finnhub_institutional,
    ("fmp", "etf_info"): _fmp_info,
    ("fmp", "etf_holdings"): _fmp_holdings,
    ("fmp", "institutional_ownership"): _fmp_institutional,
}


async def store_probe_records(
    pool: asyncpg.Pool, records: list[FundProbeRecord], source_url: str, body: Any
) -> int:
    """Append normalized records; an unchanged payload (same hash) adds nothing."""
    payload_hash = hashlib.sha256(
        json.dumps(body, sort_keys=True, default=str).encode()
    ).hexdigest()
    await pool.executemany(
        """insert into public.fund_probe_record
           (provider,endpoint,record_kind,subject_symbol,item_key,item_name,item_symbol,isin,cusip,
            as_of,weight_pct,shares,value,currency,attributes,source_url,payload_hash)
           values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::jsonb,$16,$17)
           on conflict(provider,endpoint,subject_symbol,item_key,payload_hash) do nothing""",
        [
            (
                r.provider,
                r.endpoint,
                r.record_kind,
                r.subject_symbol,
                r.item_key,
                r.item_name,
                r.item_symbol,
                r.isin,
                r.cusip,
                r.as_of,
                r.weight_pct,
                r.shares,
                r.value,
                r.currency,
                json.dumps(r.attributes, default=str),
                source_url,
                payload_hash,
            )
            for r in records
        ],
    )
    return len(records)


# ----------------------------------------------------------------------------------- runner


def _dec_quota(value: int | None) -> Decimal | None:
    return None if value is None else Decimal(value)


async def run_fund_probes(ctx: JobContext, http: httpx.AsyncClient, settings: Settings) -> None:
    """Probe each fund endpoint once; denied endpoints stay disabled until the key changes."""
    stored = await load_active(ctx.pool, "jobs", JobsConfig)
    jobs = stored.body if stored else JobsConfig()
    if stored:
        await ctx.record_config_version(stored.id)
    governor = PostgresRequestGovernor(ctx.pool)
    for probe in fund_probe_list(settings):
        async with ctx.step(f"{probe.provider}:{probe.endpoint}") as step:
            config_hash = hashlib.sha256((probe.credential or "").encode()).hexdigest()
            gate = await ctx.pool.fetchrow(
                """select state,hard_blocked,retry_at,configuration_hash from public.provider_access
                   where provider=$1 and endpoint=$2""",
                probe.provider,
                probe.endpoint,
            )
            if gate is None:
                step.detail["skipped"] = "no provider_access row (apply migration 20261003110000)"
                continue
            now = dt.datetime.now(dt.UTC)
            if gate["retry_at"] and gate["retry_at"] > now:
                step.detail.update(state=gate["state"], skipped="provider Retry-After window")
                continue
            if gate["hard_blocked"] and gate["configuration_hash"] == config_hash:
                step.detail.update(state=gate["state"], skipped="endpoint disabled for this key")
                continue
            if not probe.credential:
                result = AccessResult(AccessState.ACCESS_UNVERIFIED, "credential not configured")
                await record_access(ctx.pool, probe.provider, probe.endpoint, result)
                step.detail["state"] = result.state
                continue
            await ctx.pool.execute(
                """update public.provider_access set configuration_hash=$3, hard_blocked=false
                   where provider=$1 and endpoint=$2 and configuration_hash is distinct from $3""",
                probe.provider,
                probe.endpoint,
                config_hash,
            )
            provider_settings = jobs.providers[probe.provider]
            try:
                await governor.reserve(
                    probe.provider,
                    probe.endpoint,
                    Decimal(1),
                    Budget(
                        daily=_dec_quota(provider_settings.daily_quota),
                        monthly=_dec_quota(provider_settings.monthly_quota),
                        reserve=Decimal(provider_settings.reserve_credits),
                    ),
                )
            except QuotaExceeded as exc:
                result = AccessResult(AccessState.QUOTA_EXHAUSTED, str(exc))
                await record_access(ctx.pool, probe.provider, probe.endpoint, result)
                step.detail.update(state=result.state, skipped="free request budget exhausted")
                continue
            params = dict(probe.params)
            headers: dict[str, str] = {}
            if probe.provider == "finnhub":
                headers["X-Finnhub-Token"] = probe.credential  # never in the URL
            elif probe.credential_param:
                params[probe.credential_param] = probe.credential
            started = time.monotonic()
            try:
                response = await http.get(probe.url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                result = AccessResult(AccessState.TEMPORARILY_FAILED, type(exc).__name__)
                await record_access(ctx.pool, probe.provider, probe.endpoint, result)
                step.detail.update(state=result.state, error=type(exc).__name__)
                continue
            try:
                body: Any = response.json()
            except ValueError:
                body = response.text[:2000]
            result = classify_fund_probe(response.status_code, body, probe.expected_symbol)
            if result.state == AccessState.QUOTA_EXHAUSTED:
                result = AccessResult(
                    result.state,
                    result.detail,
                    retry_after_at(response.headers.get("Retry-After")),
                    response.headers.get("X-Request-ID"),
                )
            await record_access(
                ctx.pool,
                probe.provider,
                probe.endpoint,
                result,
                status_code=response.status_code,
                elapsed_ms=round((time.monotonic() - started) * 1000),
                response_bytes=len(response.content),
                credits=1,
            )
            step.detail.update(state=result.state, status_code=response.status_code)
            if result.state == AccessState.AVAILABLE:
                records, warnings = normalize_probe(
                    probe.provider, probe.endpoint, probe.params["symbol"], body
                )
                stored_rows = await store_probe_records(ctx.pool, records, probe.url, body)
                step.detail.update(records=stored_rows, warnings=warnings)


async def fund_probes_handler(ctx: JobContext) -> None:
    headers = {"User-Agent": f"StewardAI/{__version__}"}
    async with httpx.AsyncClient(timeout=30, headers=headers) as http:
        await run_fund_probes(ctx, http, get_settings())
