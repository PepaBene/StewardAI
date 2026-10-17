"""Every fair value stored for one company, in the listing's price currency, beside its last price.

Sources: the daily `fair_value_point` rows (your saved DCF and multiple-path scenarios, the app's
default DCF, the thesis target, the peer-multiple value) and the latest analyst targets from
`consensus_snapshot` (mean, low, high; the median is kept in the note). Values in another currency
are converted at the latest stored fixing and the note says so; with no fixing the value is null
with the reason.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any

import asyncpg
from pydantic import BaseModel, Field

from steward.company.quote import Q4, Quote, load_member, load_quote
from steward.valuation.drafted import OWNED_POINT_SQL
from steward.valuation.fair_value_view import BASIS_LABELS, HEADLINE_ORDER, convert, latest_fx

# The order the app picks a default in (holdings, drawer and watchlist use the same): your
# scenario first.
DEFAULT_ORDER = HEADLINE_ORDER
SOURCE_OF = {
    "dcf_default": "dcf_default",
    "dcf_base": "dcf",
    "dcf_bear": "dcf_bear",
    "dcf_bull": "dcf_bull",
    "multiple_base": "multiple_path",
    "multiple_bear": "multiple_path_bear",
    "multiple_bull": "multiple_path_bull",
    "thesis_target": "thesis_target",
    "peer_multiple": "peer_multiple",
    "consensus_target": "analyst_mean",
}
LABELS = {
    **BASIS_LABELS,
    "dcf_bear": "Your DCF, bear case",
    "dcf_bull": "Your DCF, bull case",
    "multiple_bear": "Your multiple path, bear case",
    "multiple_bull": "Your multiple path, bull case",
    "analyst_mean": "Analyst mean target",
    "analyst_low": "Analyst low target",
    "analyst_high": "Analyst high target",
}


class FairValueItem(BaseModel):
    source: str = Field(
        description="dcf, dcf_default, dcf_bear, dcf_bull, multiple_path, multiple_path_bear, "
        "multiple_path_bull, thesis_target, peer_multiple, analyst_mean, analyst_low or analyst_high."
    )
    basis: str | None = Field(description="The stored basis, where one exists.")
    label: str
    value: Decimal | None
    currency: str | None
    upside: Decimal | None = Field(description="Value / last price - 1.")
    as_of: dt.date | None
    basis_note: str
    is_default: bool = False


class CompanyFairValues(BaseModel):
    ticker: str
    price: Decimal | None
    price_currency: str | None
    price_date: dt.date | None
    default_source: str | None
    values: list[FairValueItem]
    reasons: list[str]


Converter = Callable[[Decimal, str | None], Decimal | None]


def build_fair_values(
    ticker: str,
    quote: Quote,
    points: Sequence[dict[str, Any]],
    consensus: dict[str, Any] | None,
    to_price: Converter,
) -> CompanyFairValues:
    """Pure assembly. `points` are the latest row per basis; `consensus` the latest snapshot."""
    items: list[FairValueItem] = []
    reasons: list[str] = []
    price = quote.last
    if quote.reason:
        reasons.append(f"price: {quote.reason}")

    def add(source: str, basis: str | None, raw: Decimal, ccy: str | None, as_of: dt.date | None,
            note: str) -> None:  # fmt: skip
        v = to_price(raw, ccy)
        if v is None:
            reasons.append(f"{LABELS.get(source, source)}: no FX rate from {ccy} to {quote.currency}")
        elif ccy and quote.currency and ccy.upper() != quote.currency.upper():
            note = f"{note} Converted from {ccy} at the latest stored fixing."
        items.append(FairValueItem(
            source=source, basis=basis, label=LABELS.get(basis or source, LABELS.get(source, source)),
            value=v.quantize(Q4) if v is not None else None, currency=quote.currency if v is not None else ccy,
            upside=(v / price - 1).quantize(Q4) if v is not None and price else None, as_of=as_of, basis_note=note,
        ))  # fmt: skip

    has_snapshot = consensus is not None and consensus.get("target_mean") is not None
    for p in points:
        basis = p["basis"]
        if basis == "consensus_target" and has_snapshot:
            continue  # shown from the snapshot below, with its low and high
        src = SOURCE_OF.get(basis, basis)
        note = f"Recorded {p['as_of'].isoformat()} by the {p.get('source') or 'app'} ({p.get('method_version') or 'unversioned'})."
        add(src, basis, p["value"], p.get("currency"), p["as_of"], note)
    if consensus is not None:
        ccy = consensus.get("target_currency")
        n = consensus.get("analyst_count")
        who = f"{n} analysts" if n else "analysts"
        base = f"{who} via {consensus['source']}, snapshot {consensus['as_of'].isoformat()}."
        for src, key in (("analyst_mean", "target_mean"), ("analyst_low", "target_low"), ("analyst_high", "target_high")):
            if consensus.get(key) is not None:
                extra = f" Median {consensus['target_median']}." if src == "analyst_mean" and consensus.get("target_median") else ""
                add(src, "consensus_target" if src == "analyst_mean" else None, consensus[key], ccy, consensus["as_of"], base + extra)
    else:
        reasons.append("no analyst targets stored")
    if not points:
        reasons.append("no saved scenario, thesis target or default DCF recorded")
    default = next(
        (SOURCE_OF.get(b, b) for b in DEFAULT_ORDER if any(i.basis == b and i.value is not None for i in items)),
        None,
    )  # fmt: skip
    for i in items:
        i.is_default = i.source == default
    rank = {SOURCE_OF.get(b, b): n for n, b in enumerate(DEFAULT_ORDER)}
    items.sort(key=lambda i: rank.get(i.source, 50))
    return CompanyFairValues(
        ticker=ticker, price=price, price_currency=quote.currency, price_date=quote.as_of,
        default_source=default, values=items, reasons=reasons,
    )  # fmt: skip


async def fair_values(
    pool: asyncpg.Pool, owner_id: Any, ticker: str, quote: Quote | None = None
) -> CompanyFairValues | None:
    """None when the ticker is not in the universe."""
    member = await load_member(pool, ticker)
    if member is None:
        return None
    quote = quote or await load_quote(pool, member)
    points = [
        dict(r)
        for r in await pool.fetch(
            f"""select distinct on (basis) basis, value, currency, as_of, source, method_version
                 from public.fair_value_point where owner_id = $1 and ticker = $2
                  and {OWNED_POINT_SQL}
                order by basis, as_of desc""",  # noqa: S608
            owner_id, ticker,
        )
    ]  # fmt: skip
    snap = await pool.fetchrow(
        """select as_of, source, analyst_count, target_mean, target_median, target_high, target_low, target_currency
             from public.consensus_snapshot
            where ticker = $1 and access_state in ('complete', 'partial')
              and (target_mean is not null or target_low is not null or target_high is not null)
            order by as_of desc, source limit 1""",
        ticker,
    )  # fmt: skip
    account = await pool.fetchval(
        "select account_currency from public.account_snapshot order by snapshot_date desc limit 1"
    )
    home, rates = await latest_fx(pool, account)

    def to_price(value: Decimal, ccy: str | None) -> Decimal | None:
        if not quote or not quote.currency:
            return value
        return convert(value, ccy or quote.currency, quote.currency, home, rates)

    return build_fair_values(ticker, quote, points, dict(snap) if snap else None, to_price)
