"""Fund factsheet metadata beside each holdings snapshot (plan D6.1, D6.8).

Per fund and holdings as-of date this records what the issuer states about the fund (share
class, total assets, expense ratio, charges, benchmark, distribution policy, NAV basis) and
what the published rows add up to (equity / cash / derivatives / other, rows without a
sector or country, and the residual 1 - sum of rows). Everything the payload does not state
stays NULL: a missing expense ratio is not 0 %, a residual is not spread over the holdings.

The issuer payloads the look-through job already downloads carry the holdings and the share
class; total assets, charges and the like are read from the data points named in
`ISHARES_FACTSHEET_POINTS` / `VANGUARD_PROFILE_FIELDS` when the payload has them. Those names
are candidates not yet confirmed against a live response (the recorded fixtures do not contain
them), so a fund whose payload lacks them lists them in `missing_fields` and the owner can
enter the figures from the KID/factsheet with `store_manual_factsheet` (source 'manual').
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
    "totalNetAssets": "total_assets",
    "fundSize": "total_assets",
    "benchmarkIndex": "benchmark_name",
    "benchmarkName": "benchmark_name",
    "distributionType": "distribution_policy",
    "incomeTreatment": "distribution_policy",
    "navPrice": "nav_basis",
}
# Vanguard: GraphQL `funds[0].profile` keys.
VANGUARD_PROFILE_FIELDS: dict[str, str] = {
    "ongoingCharges": "expense_ratio",
    "expenseRatio": "expense_ratio",
    "netAssets": "total_assets",
    "benchmarkName": "benchmark_name",
    "distributionStrategy": "distribution_policy",
    "shareClassName": "share_class",
}

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


def _policy(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if text.startswith(("acc", "capitalis", "reinvest")):
        return "accumulating"
    if text.startswith(("dist", "income", "pay")):
        return "distributing"
    return None


def _apply(facts: IssuerFacts, target: str, value: Any, *, percent: bool = False) -> None:
    if value in (None, "", "-"):
        return
    if target == "expense_ratio":
        number = _dec(value)
        if number is not None and facts.expense_ratio is None:
            facts.expense_ratio = number / 100 if percent else number
    elif target == "total_assets":
        number = _dec(value)
        if number is not None and facts.total_assets is None:
            facts.total_assets = number
    elif target == "distribution_policy":
        facts.distribution_policy = facts.distribution_policy or _policy(value)
    elif target in ("benchmark_name", "nav_basis", "share_class"):
        text = str(value).strip()
        if text and getattr(facts, target) is None:
            setattr(facts, target, text)


def extract_issuer_facts(snap: FundSnapshot) -> IssuerFacts:
    """Factsheet fields the snapshot's original payload states. Never guesses a value."""
    facts = IssuerFacts()
    raw = snap.raw or {}
    if snap.provider == "ishares":
        body = raw.get("holdings")
        if isinstance(body, dict):
            share_class = str(body.get("shareClass") or "").strip()
            facts.share_class = share_class or None
            facts.fund_ticker = str(body.get("aladdinFundTicker") or "").strip() or None
            for component in (body.get("componentsByNameMap") or {}).values():
                containers = (component or {}).get("containersByNameMap") or {}
                for container in containers.values():
                    points = (container or {}).get("dataPointsByNameMap") or {}
                    for name, target in ISHARES_FACTSHEET_POINTS.items():
                        point = points.get(name)
                        if isinstance(point, dict):
                            _apply(facts, target, point.get("value"), percent=True)
                            facts.charges.setdefault(name, point.get("value"))
    else:
        fund_body = raw.get("fund")
        try:
            profile = (fund_body or {})["data"]["funds"][0]["profile"]
        except (KeyError, IndexError, TypeError):
            profile = None
        if isinstance(profile, dict):
            for name, target in VANGUARD_PROFILE_FIELDS.items():
                if name in profile:
                    _apply(facts, target, profile[name], percent=True)
                    if target == "expense_ratio":
                        facts.charges.setdefault(name, profile[name])
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


async def store_factsheet(
    pool: asyncpg.Pool,
    fund: LookThroughFund,
    snap: FundSnapshot,
    snapshot_id: int | None,
    *,
    tolerance: Decimal = DEFAULT_TOLERANCE,
) -> int | None:
    """Record the factsheet row of a stored snapshot (idempotent per fund/as-of/source)."""
    rec = reconcile_rows(snap.rows, tolerance)
    facts = extract_issuer_facts(snap)
    value: int | None = await pool.fetchval(
        "insert into public.fund_factsheet (fund_isin, as_of, source, snapshot_id, provider,"
        " share_class, fund_name, fund_currency, total_assets, total_assets_currency,"
        " total_assets_as_of, expense_ratio, charges, benchmark_name, distribution_policy,"
        " nav_basis, holdings_count, equity_weight, cash_weight, derivative_weight,"
        " other_weight, total_weight, residual_weight, unclassified_sector_weight,"
        " unclassified_country_weight, reconcile_tolerance, reconciles, lookthrough_basis,"
        " coverage_note, missing_fields, evidence) values ($1, $2, $3, $4, $5, $6, $7, $8, $9,"
        " $10, $11, $12, $13::jsonb, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24,"
        " $25, $26, $27, $28, $29, $30, $31::jsonb)"
        " on conflict (fund_isin, as_of, source) do update set snapshot_id = excluded.snapshot_id,"
        " share_class = excluded.share_class, total_assets = excluded.total_assets,"
        " expense_ratio = excluded.expense_ratio, charges = excluded.charges,"
        " benchmark_name = excluded.benchmark_name,"
        " distribution_policy = excluded.distribution_policy, nav_basis = excluded.nav_basis,"
        " holdings_count = excluded.holdings_count, equity_weight = excluded.equity_weight,"
        " cash_weight = excluded.cash_weight, derivative_weight = excluded.derivative_weight,"
        " other_weight = excluded.other_weight, total_weight = excluded.total_weight,"
        " residual_weight = excluded.residual_weight,"
        " unclassified_sector_weight = excluded.unclassified_sector_weight,"
        " unclassified_country_weight = excluded.unclassified_country_weight,"
        " reconciles = excluded.reconciles, coverage_note = excluded.coverage_note,"
        " missing_fields = excluded.missing_fields, fetched_at = now() returning id",
        fund.isin,
        snap.as_of,
        snap.source,
        snapshot_id,
        snap.provider,
        facts.share_class,
        snap.fund_name,
        snap.fund_currency,
        facts.total_assets,
        snap.fund_currency if facts.total_assets is not None else None,
        snap.as_of if facts.total_assets is not None else None,
        facts.expense_ratio,
        json.dumps(facts.charges, default=str),
        facts.benchmark_name,
        facts.distribution_policy,
        facts.nav_basis,
        rec.holdings_count,
        rec.equity_weight,
        rec.cash_weight,
        rec.derivative_weight,
        rec.other_weight,
        rec.total_weight,
        rec.residual_weight,
        rec.unclassified_sector_weight,
        rec.unclassified_country_weight,
        rec.tolerance,
        rec.reconciles,
        lookthrough_basis(snap),
        coverage_note(rec, snap),
        facts.missing(),
        json.dumps({"fund_ticker": facts.fund_ticker, "portfolio_id": snap.portfolio_id}),
    )
    return value


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
    payload leaves; kept apart from issuer rows by source 'manual'."""
    value: int = await pool.fetchval(
        "insert into public.fund_factsheet (fund_isin, as_of, source, share_class, total_assets,"
        " total_assets_currency, total_assets_as_of, expense_ratio, charges, benchmark_name,"
        " distribution_policy, nav_basis, lookthrough_basis, coverage_note)"
        " values ($1, $2, 'manual', $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11, 'manual', $12)"
        " on conflict (fund_isin, as_of, source) do update set share_class = excluded.share_class,"
        " total_assets = excluded.total_assets,"
        " total_assets_currency = excluded.total_assets_currency,"
        " expense_ratio = excluded.expense_ratio, charges = excluded.charges,"
        " benchmark_name = excluded.benchmark_name,"
        " distribution_policy = excluded.distribution_policy, nav_basis = excluded.nav_basis,"
        " coverage_note = excluded.coverage_note, fetched_at = now() returning id",
        sheet.fund_isin,
        sheet.as_of,
        sheet.share_class,
        sheet.total_assets,
        sheet.total_assets_currency,
        sheet.as_of if sheet.total_assets is not None else None,
        sheet.expense_ratio,
        json.dumps(sheet.charges, default=str),
        sheet.benchmark_name,
        sheet.distribution_policy,
        sheet.nav_basis,
        sheet.note,
    )
    return value


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
    """Latest known expense ratio per fund (any source; NULL rows are skipped, never 0),
    using only factsheets for dates up to `as_of` when given."""
    rows = await pool.fetch(
        "select distinct on (fund_isin) fund_isin, expense_ratio from public.fund_factsheet"
        " where fund_isin = any($1::text[]) and expense_ratio is not null"
        " and ($2::date is null or as_of <= $2)"
        " order by fund_isin, as_of desc, fetched_at desc",
        isins,
        as_of,
    )
    return {r["fund_isin"]: Decimal(r["expense_ratio"]) for r in rows}
