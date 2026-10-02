"""Fund factsheet metadata beside each holdings snapshot (plan D6.1, D6.8).

Per fund and holdings as-of date this records what the issuer states about the fund (share
class, total assets, expense ratio, charges, benchmark, distribution policy, NAV basis) and
what the published rows add up to (equity / cash / derivatives / other, rows without a
sector or country, and the residual 1 - sum of rows). Everything the payload does not state
stays NULL: a missing expense ratio is not 0 %, a residual is not spread over the holdings.

The issuer payloads the look-through job already downloads carry the holdings and the share
class; total assets, charges and the like are read from data points whose names are listed in
`ISHARES_FACTSHEET_POINTS` / `VANGUARD_PROFILE_FIELDS`. Those names are candidates drawn from
the issuers' documented/public field vocabulary and NOT confirmed against a live response
(the recorded fixtures do not contain them). Extraction is therefore tolerant (names are
compared case- and punctuation-insensitively, a value may be wrapped in `{"value": ...}`, the
whole payload is searched) and every extracted field records where it came from in
`evidence.field_provenance` with `verified: false`; `evidence.names_unverified` is true on
every issuer-sourced row. A fund whose payload lacks a field lists it in `missing_fields` and
the owner can enter the figures from the KID/factsheet with `store_manual_factsheet`
(source 'manual').

Rows are insert-only versions per (fund, holdings as-of, source): a re-fetch whose figures
differ adds a row, identical figures add nothing. Point-in-time reads use `fetched_at`.
"""

import datetime as dt
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import asyncpg

from steward.config.jobs import LookThroughFund
from steward.sources.etf_issuers import FundSnapshot, HoldingRow

DEFAULT_TOLERANCE = Decimal("0.01")

# iShares: data point name -> factsheet field, searched in every component/container of the
# holdings payload. Percent values (0.07 = 0.07 %).
ISHARES_FACTSHEET_POINTS: dict[str, str] = {
    "totalExpenseRatio": "expense_ratio",
    "expenseRatio": "expense_ratio",
    "netExpenseRatio": "expense_ratio",
    "totalNetAssets": "total_assets",
    "netAssets": "total_assets",
    "fundSize": "total_assets",
    "totalFundAssets": "total_assets",
    "benchmarkIndex": "benchmark_name",
    "benchmarkName": "benchmark_name",
    "indexName": "benchmark_name",
    "distributionType": "distribution_policy",
    "incomeTreatment": "distribution_policy",
    "distributionPolicy": "distribution_policy",
    "navPrice": "nav_basis",
    "navType": "nav_basis",
    "shareClass": "share_class",
}
# Vanguard: GraphQL `funds[0].profile` keys.
VANGUARD_PROFILE_FIELDS: dict[str, str] = {
    "ongoingCharges": "expense_ratio",
    "ongoingCharge": "expense_ratio",
    "expenseRatio": "expense_ratio",
    "totalExpenseRatio": "expense_ratio",
    "netAssets": "total_assets",
    "totalNetAssets": "total_assets",
    "fundNetAssets": "total_assets",
    "benchmarkName": "benchmark_name",
    "benchmark": "benchmark_name",
    "indexName": "benchmark_name",
    "distributionStrategy": "distribution_policy",
    "distributionType": "distribution_policy",
    "incomeTreatment": "distribution_policy",
    "shareClassName": "share_class",
    "shareClass": "share_class",
}
# Expense ratios are quoted in percent (0.07 = 0.07 %) by both issuers' pages. The scale is
# still detected per value (see `normalise_expense_ratio`) because the field names are unverified.
MAX_PLAUSIBLE_EXPENSE_RATIO = Decimal("0.05")  # 5 % a year; above it a value is misread

FACTSHEET_FIELDS = (
    "expense_ratio",
    "total_assets",
    "benchmark_name",
    "distribution_policy",
    "nav_basis",
)

LookthroughBasis = Literal["issuer_holdings", "issuer_spreadsheet", "index_proxy", "manual"]


@dataclass(frozen=True)
class Reconciliation:
    total_weight: Decimal
    equity_weight: Decimal
    cash_weight: Decimal
    derivative_weight: Decimal
    other_weight: Decimal
    unclassified_sector_weight: Decimal
    unclassified_country_weight: Decimal
    residual_weight: Decimal
    tolerance: Decimal
    reconciles: bool
    holdings_count: int


def reconcile_rows(
    rows: Iterable[HoldingRow], tolerance: Decimal = DEFAULT_TOLERANCE
) -> Reconciliation:
    """What the published rows add up to. The fund reconciles when equity + cash +
    derivatives + other is within `tolerance` of 100 % of net assets; the difference stays
    the residual (unknown) weight."""
    by_class: dict[str, Decimal] = {"equity": Decimal(0), "cash": Decimal(0)}
    derivative = other = no_sector = no_country = Decimal(0)
    count = 0
    for row in rows:
        count += 1
        if row.asset_class == "equity":
            by_class["equity"] += row.weight
            if not row.sector:
                no_sector += row.weight
            if not row.country:
                no_country += row.weight
        elif row.asset_class == "cash":
            by_class["cash"] += row.weight
        elif row.asset_class == "derivative":
            derivative += row.weight
        else:
            other += row.weight
    total = by_class["equity"] + by_class["cash"] + derivative + other
    residual = Decimal(1) - total
    return Reconciliation(
        total_weight=total,
        equity_weight=by_class["equity"],
        cash_weight=by_class["cash"],
        derivative_weight=derivative,
        other_weight=other,
        unclassified_sector_weight=no_sector,
        unclassified_country_weight=no_country,
        residual_weight=residual,
        tolerance=tolerance,
        reconciles=abs(residual) <= tolerance,
        holdings_count=count,
    )


@dataclass
class IssuerFacts:
    """What the issuer payload states about the fund itself (all optional)."""

    share_class: str | None = None
    fund_ticker: str | None = None
    expense_ratio: Decimal | None = None
    total_assets: Decimal | None = None
    benchmark_name: str | None = None
    distribution_policy: str | None = None
    nav_basis: str | None = None
    charges: dict[str, Any] = field(default_factory=dict)
    # Where each field came from, and what could not be read (all names are unverified).
    provenance: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def missing(self) -> list[str]:
        return [f for f in FACTSHEET_FIELDS if getattr(self, f) in (None, "")]


def _dec(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        out = Decimal(str(value).replace(",", "").replace("%", "").strip())
    except InvalidOperation:
        return None
    return out if out.is_finite() else None


def normalise_expense_ratio(raw: Any, *, percent_hint: bool) -> tuple[Decimal | None, str | None]:
    """An annual expense ratio as a fraction (0.0007 = 0.07 %), and a note when the scale had
    to be decided.

    A value with a percent sign is percent. Otherwise `percent_hint` says the issuer quotes
    percent: 0.07 is 0.07 %. Because the field names are unverified the hint is checked: a
    "percent" value below 0.005 would be a fee under 0.005 %, which is not a real fund, so it
    is read as an already-fractional figure (0.0007). Without the hint, a value above 0.2 is
    read as percent. Anything still above `MAX_PLAUSIBLE_EXPENSE_RATIO` (5 % a year) or
    negative is not stored: unknown beats a wrong fee (the table also rejects >= 0.2)."""
    text = str(raw).strip()
    number = _dec(text)
    if number is None:
        return None, f"expense ratio {text!r} is not a number"
    if number < 0:
        return None, f"expense ratio {text!r} is negative"
    if number == 0:
        return Decimal(0), None
    note: str | None = None
    if "%" in text:
        result = number / 100
    elif percent_hint:
        if number < Decimal("0.005"):
            result, note = (
                number,
                f"{text!r} read as a fraction (a fee under 0.005 % is implausible)",
            )
        else:
            result = number / 100
    elif number > Decimal("0.2"):
        result, note = number / 100, f"{text!r} read as percent (above 0.2 as a fraction)"
    else:
        result = number
    if result > MAX_PLAUSIBLE_EXPENSE_RATIO:
        return None, f"expense ratio {text!r} reads as {result:.4f} a year: not stored"
    return result, note


def _key(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _unwrap(value: Any) -> Any:
    """A data point may be a scalar or `{"value": ..., "formattedValue": ...}`."""
    if isinstance(value, dict):
        for k in ("value", "formattedValue", "displayValue", "raw", "text"):
            if value.get(k) not in (None, ""):
                return value[k]
        return None
    if isinstance(value, list):
        return None
    return value


def find_named(
    body: Any, aliases: dict[str, str], *, max_depth: int = 8
) -> list[tuple[str, str, str, Any]]:
    """Every (target field, matched name, path, raw value) in `body` whose key matches an alias
    (case/punctuation-insensitive). Lists of {"name": ..., "value": ...} entries match by name.
    Order is the payload's own, depth first."""
    wanted: dict[str, str] = {}
    for name, target in aliases.items():
        wanted.setdefault(_key(name), target)
    found: list[tuple[str, str, str, Any]] = []

    def walk(node: Any, path: str, depth: int) -> None:
        if depth > max_depth:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                target = wanted.get(_key(str(k)))
                if target is not None and not isinstance(v, list):
                    value = _unwrap(v)
                    if value not in (None, "", "-"):
                        found.append((target, str(k), f"{path}/{k}", value))
                if isinstance(v, dict | list):
                    walk(v, f"{path}/{k}", depth + 1)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                if isinstance(item, dict):
                    label = item.get("name") or item.get("label") or item.get("code")
                    target = wanted.get(_key(str(label))) if label else None
                    if target is not None:
                        value = _unwrap(item)
                        if value not in (None, "", "-"):
                            found.append((target, str(label), f"{path}[{i}]", value))
                if isinstance(item, dict | list):
                    walk(item, f"{path}[{i}]", depth + 1)

    walk(body, "", 0)
    return found


def _policy(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if text.startswith(("acc", "capitalis", "capitaliz", "reinvest")):
        return "accumulating"
    if text.startswith(("dist", "income", "pay")):
        return "distributing"
    return None


def _apply(
    facts: IssuerFacts, target: str, value: Any, *, percent: bool, name: str, path: str
) -> None:
    if value in (None, "", "-"):
        return
    if target in facts.provenance:  # first match wins; later ones are kept in the provenance
        facts.provenance[target].setdefault("also_seen", []).append(path)
        return
    applied = False
    if target == "expense_ratio":
        ratio, note = normalise_expense_ratio(value, percent_hint=percent)
        if note:
            facts.warnings.append(note)
        if ratio is not None:
            facts.expense_ratio = ratio
            applied = True
    elif target == "total_assets":
        number = _dec(value)
        if number is not None and number >= 0:
            facts.total_assets = number
            applied = True
    elif target == "distribution_policy":
        policy = _policy(value)
        if policy is not None:
            facts.distribution_policy = policy
            applied = True
    elif target in ("benchmark_name", "nav_basis", "share_class"):
        text = str(value).strip()
        if text and getattr(facts, target) is None:
            setattr(facts, target, text)
            applied = True
    if applied:
        # Names are candidates from the issuers' public vocabulary, not confirmed live.
        facts.provenance[target] = {
            "name": name,
            "path": path,
            "raw": str(value)[:80],
            "verified": False,
        }
        if target == "expense_ratio":
            facts.charges.setdefault(name, value)


def extract_issuer_facts(snap: FundSnapshot) -> IssuerFacts:
    """Factsheet fields the snapshot's original payload states. Never guesses a value; every
    extracted field carries its (unverified) provenance."""
    facts = IssuerFacts()
    raw = snap.raw or {}
    if snap.provider == "ishares":
        body = raw.get("holdings")
        if isinstance(body, dict):
            share_class = str(body.get("shareClass") or "").strip()
            facts.share_class = share_class or None
            facts.fund_ticker = str(body.get("aladdinFundTicker") or "").strip() or None
            if facts.share_class:
                facts.provenance["share_class"] = {
                    "name": "shareClass",
                    "path": "/shareClass",
                    "raw": facts.share_class[:80],
                    "verified": True,  # present in the recorded holdings fixtures
                }
            for target, name, path, value in find_named(body, ISHARES_FACTSHEET_POINTS):
                _apply(facts, target, value, percent=True, name=name, path=path)
    else:
        fund_body = raw.get("fund")
        try:
            fund = (fund_body or {})["data"]["funds"][0]
        except (KeyError, IndexError, TypeError):
            fund = None
        if isinstance(fund, dict):
            for target, name, path, value in find_named(fund, VANGUARD_PROFILE_FIELDS):
                _apply(facts, target, value, percent=True, name=name, path=path)
            profile = fund.get("profile")
            if isinstance(profile, dict) and profile.get("shareClassName"):
                facts.provenance["share_class"] = {
                    "name": "shareClassName",
                    "path": "/profile/shareClassName",
                    "raw": str(profile["shareClassName"])[:80],
                    "verified": True,  # present in the recorded GraphQL fixtures
                }
    return facts


def lookthrough_basis(snap: FundSnapshot) -> LookthroughBasis:
    return "issuer_spreadsheet" if snap.source == "ishares_spreadsheet" else "issuer_holdings"


def coverage_note(rec: Reconciliation, snap: FundSnapshot) -> str:
    parts = [
        f"{rec.holdings_count} published rows; equity {rec.equity_weight:.4f}, cash "
        f"{rec.cash_weight:.4f}, derivatives {rec.derivative_weight:.4f}, other "
        f"{rec.other_weight:.4f}; unclassified in equity: sector "
        f"{rec.unclassified_sector_weight:.4f}, country {rec.unclassified_country_weight:.4f}"
    ]
    if rec.reconciles:
        parts.append(f"rows reconcile to 100% within {rec.tolerance}")
    else:
        parts.append(
            f"rows do NOT reconcile: {rec.residual_weight:+.4f} of net assets is unaccounted for"
            " and stays unknown"
        )
    if snap.source == "ishares_spreadsheet":
        parts.append("spreadsheet fallback: no country or ISIN columns")
    return "; ".join(parts)


_VERSIONED = (
    "snapshot_id",
    "provider",
    "share_class",
    "fund_name",
    "fund_currency",
    "total_assets",
    "total_assets_currency",
    "total_assets_as_of",
    "expense_ratio",
    "charges",
    "benchmark_name",
    "distribution_policy",
    "nav_basis",
    "holdings_count",
    "equity_weight",
    "cash_weight",
    "derivative_weight",
    "other_weight",
    "total_weight",
    "residual_weight",
    "unclassified_sector_weight",
    "unclassified_country_weight",
    "reconcile_tolerance",
    "reconciles",
    "lookthrough_basis",
    "coverage_note",
    "missing_fields",
    "evidence",
)


def _comparable(value: Any) -> Any:
    if isinstance(value, str) and value[:1] in "{[":
        try:
            return json.loads(value)
        except ValueError:
            return value
    if isinstance(value, Decimal):
        return value.normalize()
    if isinstance(value, list | tuple):
        return list(value)
    return value


async def _insert_version(
    pool: asyncpg.Pool, fund_isin: str, as_of: dt.date, source: str, values: dict[str, Any]
) -> int:
    """Insert-only: add a row when the figures differ from the newest version of the same
    (fund, holdings as-of, source), else return that version's id. History is never rewritten
    (audit B39), so a point-in-time read keyed on `fetched_at` sees what was known then."""
    latest = await pool.fetchrow(
        "select * from public.fund_factsheet where fund_isin = $1 and as_of = $2 and source = $3"
        " order by fetched_at desc, id desc limit 1",
        fund_isin,
        as_of,
        source,
    )
    if latest is not None and all(
        _comparable(latest[c]) == _comparable(v) for c, v in values.items()
    ):
        return int(latest["id"])
    columns = ["fund_isin", "as_of", "source", *values]
    marks = ", ".join(
        f"${i + 1}::jsonb" if c in ("charges", "evidence") else f"${i + 1}"
        for i, c in enumerate(columns)
    )
    new_id: int = await pool.fetchval(
        f"insert into public.fund_factsheet ({', '.join(columns)}) values ({marks}) returning id",  # noqa: S608 - fixed column names
        fund_isin,
        as_of,
        source,
        *[
            json.dumps(v, default=str) if c in ("charges", "evidence") else v
            for c, v in values.items()
        ],
    )
    return new_id


async def store_factsheet(
    pool: asyncpg.Pool,
    fund: LookThroughFund,
    snap: FundSnapshot,
    snapshot_id: int | None,
    *,
    tolerance: Decimal = DEFAULT_TOLERANCE,
) -> int | None:
    """Record the factsheet row of a stored snapshot (insert-only versions per
    fund/as-of/source; an unchanged re-fetch adds nothing)."""
    rec = reconcile_rows(snap.rows, tolerance)
    facts = extract_issuer_facts(snap)
    values: dict[str, Any] = {
        "snapshot_id": snapshot_id,
        "provider": snap.provider,
        "share_class": facts.share_class,
        "fund_name": snap.fund_name,
        "fund_currency": snap.fund_currency,
        "total_assets": facts.total_assets,
        "total_assets_currency": snap.fund_currency if facts.total_assets is not None else None,
        "total_assets_as_of": snap.as_of if facts.total_assets is not None else None,
        "expense_ratio": facts.expense_ratio,
        "charges": facts.charges,
        "benchmark_name": facts.benchmark_name,
        "distribution_policy": facts.distribution_policy,
        "nav_basis": facts.nav_basis,
        "holdings_count": rec.holdings_count,
        "equity_weight": rec.equity_weight,
        "cash_weight": rec.cash_weight,
        "derivative_weight": rec.derivative_weight,
        "other_weight": rec.other_weight,
        "total_weight": rec.total_weight,
        "residual_weight": rec.residual_weight,
        "unclassified_sector_weight": rec.unclassified_sector_weight,
        "unclassified_country_weight": rec.unclassified_country_weight,
        "reconcile_tolerance": rec.tolerance,
        "reconciles": rec.reconciles,
        "lookthrough_basis": lookthrough_basis(snap),
        "coverage_note": coverage_note(rec, snap),
        "missing_fields": facts.missing(),
        "evidence": {
            "fund_ticker": facts.fund_ticker,
            "portfolio_id": snap.portfolio_id,
            # D6.1: the payload field names are candidates, not confirmed against a live
            # response; each extracted field says which name and path it came from.
            "names_unverified": True,
            "field_provenance": facts.provenance,
            "warnings": facts.warnings,
        },
    }
    return await _insert_version(pool, fund.isin, snap.as_of, snap.source, values)


@dataclass(frozen=True)
class ManualFactsheet:
    fund_isin: str
    as_of: dt.date
    share_class: str | None = None
    total_assets: Decimal | None = None
    total_assets_currency: str | None = None
    expense_ratio: Decimal | None = None
    benchmark_name: str | None = None
    distribution_policy: Literal["accumulating", "distributing"] | None = None
    nav_basis: str | None = None
    charges: dict[str, Any] = field(default_factory=dict)
    note: str | None = None


async def store_manual_factsheet(pool: asyncpg.Pool, sheet: ManualFactsheet) -> int:
    """Owner-entered factsheet figures (from the KID / issuer factsheet) for a gap the issuer
    payload leaves; kept apart from issuer rows by source 'manual'. Insert-only like the issuer
    rows: a correction adds a version."""
    values: dict[str, Any] = {
        "share_class": sheet.share_class,
        "total_assets": sheet.total_assets,
        "total_assets_currency": sheet.total_assets_currency,
        "total_assets_as_of": sheet.as_of if sheet.total_assets is not None else None,
        "expense_ratio": sheet.expense_ratio,
        "charges": sheet.charges,
        "benchmark_name": sheet.benchmark_name,
        "distribution_policy": sheet.distribution_policy,
        "nav_basis": sheet.nav_basis,
        "lookthrough_basis": "manual",
        "coverage_note": sheet.note,
    }
    return await _insert_version(pool, sheet.fund_isin, sheet.as_of, "manual", values)


async def factsheet_history(
    pool: asyncpg.Pool, isin: str, *, limit: int = 24
) -> list[asyncpg.Record]:
    rows: list[asyncpg.Record] = await pool.fetch(
        "select * from public.fund_factsheet where fund_isin = $1"
        " order by as_of desc, fetched_at desc limit $2",
        isin,
        limit,
    )
    return rows


async def latest_expense_ratios(
    pool: asyncpg.Pool, isins: list[str], as_of: dt.date | None = None
) -> dict[str, Decimal]:
    """Latest known expense ratio per fund (any source; NULL rows are skipped, never 0).

    With `as_of`, only versions fetched by the end of that day count: point-in-time means
    "what the system knew then", which is `fetched_at`, not the holdings date the factsheet
    describes (a factsheet for an old holdings date fetched today was not known back then)."""
    rows = await pool.fetch(
        "select distinct on (fund_isin) fund_isin, expense_ratio from public.fund_factsheet"
        " where fund_isin = any($1::text[]) and expense_ratio is not null"
        " and ($2::date is null or fetched_at < (($2::date + 1)::timestamp at time zone 'UTC'))"
        " order by fund_isin, as_of desc, fetched_at desc, id desc",
        isins,
        as_of,
    )
    return {r["fund_isin"]: Decimal(r["expense_ratio"]) for r in rows}
