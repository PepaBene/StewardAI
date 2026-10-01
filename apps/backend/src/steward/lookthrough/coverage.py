"""Fund / index coverage ledger (plan D6.5).

For every fund the portfolio holds or looks through, and every benchmark index, the ledger
says where its constituents come from:

    official_holdings | issuer_downloadable | regulatory_filing | proxy_fund | unavailable

and whether a total-return series and historical constituents are known to exist. A
benchmark without both is flagged before performance or backtest use (`backtest_flags`), and
so is one whose constituents are only an ETF proxy: a fund's current holdings are not the
official index history. Unknown (NULL) is flagged like "no".

Derived rows are rebuilt from the Jobs config and the stored snapshots on every refresh;
rows entered by the owner (`origin = 'manual'`) are never overwritten by a refresh.
"""

import datetime as dt
import json
from dataclasses import dataclass, field
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.config.jobs import LookThroughSettings
from steward.lookthrough.read import PortfolioIn, is_fund_type

Tier = Literal[
    "official_holdings", "issuer_downloadable", "regulatory_filing", "proxy_fund", "unavailable"
]
SubjectKind = Literal["fund", "index"]

PROXY_CAVEAT = "constituents are the proxy fund's current holdings, not the official index history"


@dataclass(frozen=True)
class LedgerEntry:
    subject_kind: SubjectKind
    subject_key: str
    label: str
    tier: Tier
    reason: str
    origin: Literal["derived", "manual"] = "derived"
    proxy_fund_isin: str | None = None
    total_return_available: bool | None = None
    historical_constituents: bool | None = None
    latest_holdings_as_of: dt.date | None = None
    source_url: str | None = None
    evidence: dict[str, object] = field(default_factory=dict)


def derive_ledger(
    settings: LookThroughSettings,
    benchmark_symbols: list[str],
    portfolio: PortfolioIn | None,
    latest_as_of: dict[str, dt.date],
    snapshot_counts: dict[str, int] | None = None,
) -> list[LedgerEntry]:
    """Ledger rows implied by the configuration and the stored snapshots."""
    counts = snapshot_counts or {}
    entries: list[LedgerEntry] = []
    configured = {f.isin for f in settings.funds}
    for fund in settings.funds:
        as_of = latest_as_of.get(fund.isin)
        provider = "iShares" if fund.provider == "ishares" else "Vanguard"
        if as_of is None:
            entries.append(
                LedgerEntry(
                    "fund",
                    fund.isin,
                    fund.label,
                    "unavailable",
                    f"{provider} holdings are configured (id {fund.portfolio_id}) but no "
                    "snapshot is stored yet",
                )
            )
            continue
        n = counts.get(fund.isin, 1)
        entries.append(
            LedgerEntry(
                "fund",
                fund.isin,
                fund.label,
                "issuer_downloadable",
                f"{provider} publishes holdings for download; {n} dated snapshot(s) retained",
                latest_holdings_as_of=as_of,
                # Only retained snapshots are history we hold; the issuer's own history is
                # not claimed.
                historical_constituents=n > 1 or None,
            )
        )
    if portfolio is not None:
        for p in portfolio.positions:
            if p.isin and p.isin not in configured and is_fund_type(p.security_type):
                entries.append(
                    LedgerEntry(
                        "fund",
                        p.isin,
                        p.name or p.ticker,
                        "unavailable",
                        "held fund with no issuer mapping (add it to look_through.funds with "
                        "its iShares/Vanguard id, or its holdings stay a visible unknown)",
                    )
                )
    proxies = {p.symbol: p for p in settings.benchmark_proxies}
    for symbol in benchmark_symbols:
        proxy = proxies.get(symbol)
        if proxy is None:
            entries.append(
                LedgerEntry(
                    "index",
                    symbol,
                    symbol,
                    "unavailable",
                    "no constituent source configured for this benchmark",
                )
            )
            continue
        entries.append(
            LedgerEntry(
                "index",
                symbol,
                proxy.label,
                "proxy_fund",
                f"proxied by {proxy.fund_isin}: {PROXY_CAVEAT}",
                proxy_fund_isin=proxy.fund_isin,
                latest_holdings_as_of=latest_as_of.get(proxy.fund_isin),
            )
        )
    return entries


async def save_entry(pool: asyncpg.Pool, entry: LedgerEntry) -> bool:
    """Upsert one row; a derived row never replaces a manual one. True when written."""
    result: str = await pool.execute(
        "insert into public.fund_index_coverage (subject_kind, subject_key, label, tier, origin,"
        " proxy_fund_isin, total_return_available, historical_constituents,"
        " latest_holdings_as_of, reason, source_url, evidence, checked_at)"
        " values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb, now())"
        " on conflict (subject_kind, subject_key) do update set label = excluded.label,"
        " tier = excluded.tier, origin = excluded.origin,"
        " proxy_fund_isin = excluded.proxy_fund_isin,"
        " total_return_available = excluded.total_return_available,"
        " historical_constituents = excluded.historical_constituents,"
        " latest_holdings_as_of = excluded.latest_holdings_as_of, reason = excluded.reason,"
        " source_url = excluded.source_url, evidence = excluded.evidence, checked_at = now()"
        " where public.fund_index_coverage.origin = 'derived' or excluded.origin = 'manual'",
        entry.subject_kind,
        entry.subject_key,
        entry.label,
        entry.tier,
        entry.origin,
        entry.proxy_fund_isin,
        entry.total_return_available,
        entry.historical_constituents,
        entry.latest_holdings_as_of,
        entry.reason,
        entry.source_url,
        json.dumps(entry.evidence, default=str),
    )
    return result.endswith(" 1")


async def refresh_ledger(
    pool: asyncpg.Pool,
    settings: LookThroughSettings,
    benchmark_symbols: list[str],
    portfolio: PortfolioIn | None,
) -> list[LedgerEntry]:
    rows = await pool.fetch(
        "select fund_isin, max(as_of) as as_of, count(*) as n from public.etf_snapshot"
        " group by fund_isin"
    )
    entries = derive_ledger(
        settings,
        benchmark_symbols,
        portfolio,
        {r["fund_isin"]: r["as_of"] for r in rows},
        {r["fund_isin"]: r["n"] for r in rows},
    )
    for entry in entries:
        await save_entry(pool, entry)
    return entries


class LedgerRow(BaseModel):
    subject_kind: SubjectKind
    subject_key: str
    label: str
    tier: Tier
    origin: Literal["derived", "manual"]
    proxy_fund_isin: str | None
    total_return_available: bool | None
    historical_constituents: bool | None
    latest_holdings_as_of: dt.date | None
    reason: str
    source_url: str | None
    checked_at: dt.datetime
    backtest_flags: list[str] = Field(
        description="Why this subject should not feed a performance/backtest unreviewed."
    )
    usable_for_backtest: bool


class CoverageLedger(BaseModel):
    entries: list[LedgerRow]
    tiers: dict[str, int]
    flagged_benchmarks: list[str]


def backtest_flags(
    kind: str,
    tier: str,
    total_return: bool | None,
    historical: bool | None,
) -> list[str]:
    flags: list[str] = []
    if tier == "unavailable":
        flags.append("no constituent data available")
    if tier == "proxy_fund":
        flags.append(PROXY_CAVEAT)
    if total_return is not True and kind == "index":
        flags.append(
            "total-return series not confirmed"
            if total_return is None
            else "price index only (no total return)"
        )
    if historical is not True:
        flags.append(
            "historical constituents not confirmed"
            if historical is None
            else "no historical constituents"
        )
    return flags


async def read_ledger(pool: asyncpg.Pool) -> CoverageLedger:
    rows = await pool.fetch(
        "select * from public.fund_index_coverage order by subject_kind desc, subject_key"
    )
    entries: list[LedgerRow] = []
    for r in rows:
        flags = backtest_flags(
            r["subject_kind"], r["tier"], r["total_return_available"], r["historical_constituents"]
        )
        entries.append(
            LedgerRow(
                subject_kind=r["subject_kind"],
                subject_key=r["subject_key"],
                label=r["label"],
                tier=r["tier"],
                origin=r["origin"],
                proxy_fund_isin=r["proxy_fund_isin"],
                total_return_available=r["total_return_available"],
                historical_constituents=r["historical_constituents"],
                latest_holdings_as_of=r["latest_holdings_as_of"],
                reason=r["reason"],
                source_url=r["source_url"],
                checked_at=r["checked_at"],
                backtest_flags=flags,
                usable_for_backtest=not flags,
            )
        )
    tiers: dict[str, int] = {}
    for e in entries:
        tiers[e.tier] = tiers.get(e.tier, 0) + 1
    return CoverageLedger(
        entries=entries,
        tiers=tiers,
        flagged_benchmarks=[
            e.subject_key for e in entries if e.subject_kind == "index" and e.backtest_flags
        ],
    )


async def backtest_gate(pool: asyncpg.Pool, benchmark_symbol: str) -> LedgerRow | None:
    """The ledger row to consult before using a benchmark in a backtest; None = not in the
    ledger at all (treat as unknown/unavailable)."""
    ledger = await read_ledger(pool)
    return next(
        (
            e
            for e in ledger.entries
            if e.subject_kind == "index" and e.subject_key == benchmark_symbol
        ),
        None,
    )
