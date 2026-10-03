"""Total return for series whose closes are split-adjusted but not dividend-adjusted.

`compute_returns` computes total return from RAW closes and confirmed corporate actions. Stooq
and Yahoo do not provide raw closes: their closes are already split-adjusted and carry no
dividends, so that path used to answer "raw close history is unavailable" for every holding
priced by them. The honest alternative needs only what those series really are:

    total return = last close * (product over dividends of (1 + D / close on the ex-date))
                   / first close - 1

with D the cash dividend per share on the same (split-adjusted) share basis as the closes. It is
computed only when every input genuinely exists, and says which basis it used:

* **the series** is Stooq or Yahoo (the sources whose closes are documented split-adjusted,
  `docs/data/prices-and-returns.md`), and every confirmed split in the window is checked against
  the closes: a close ratio that still shows the split means these closes are not split-adjusted,
  and the answer is unavailable instead of being adjusted twice;
* **the dividends** are the owner-confirmed ones (amounts as declared are converted to the
  split-adjusted basis by every later confirmed split) and, for a Yahoo series only, the
  unconfirmed dividends Yahoo listed with the same answer as the closes (`yahoo_actions`:
  same source, same share basis, same unit). A confirmed dividend wins over Yahoo's on the same
  ex-date; a rejected one is never used;
* **completeness** is stated, never assumed: either the owner verified corporate-action coverage
  through the end of the window (the existing `owner_review` gate), or, for a Yahoo series, the
  Yahoo events were collected over the whole window (`yahoo_actions.events_cover`). The Yahoo
  basis is flagged: its list is unofficial and unverified against the issuer;
* anything else (spin-offs, mergers, a dividend without an ex-date close, conflicting sources,
  a foreign-currency dividend without an ECB fixing) is unavailable with its reason, exactly as
  in `calculate_return`.

When a condition is missing the value is `None` and the reason says which: no number is
substituted.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import asyncpg

from steward.market_analysis import Action, PricePoint, ReturnResult, calculate_return
from steward.reference.yahoo_actions import SOURCE as YAHOO_SOURCE
from steward.reference.yahoo_actions import events_cover

SPLIT_ADJUSTED_SOURCES = ("stooq", "yahoo")
BASIS = "split_adjusted_close_with_reinvested_dividends"


def _unavailable(reason: str, n: int, **details: object) -> ReturnResult:
    return ReturnResult(None, "total_return", reason, n, details={"basis": BASIS, **details})


def _later_split_factor(splits: list[Action], day: dt.date, end: dt.date) -> Decimal:
    factor = Decimal(1)
    for split in splits:
        if day < split.ex_date <= end and split.ratio is not None:
            factor *= split.ratio if split.kind == "split" else Decimal(1) + split.ratio
    return factor


def closes_show_a_split(points: list[PricePoint], splits: list[Action]) -> str | None:
    """Why these closes are NOT split-adjusted (a confirmed split is visible as a price jump
    of its own ratio), or None when none of the window's splits shows."""
    ordered = sorted(points, key=lambda p: p.day)
    for split in splits:
        if split.ratio is None or split.ratio <= 0:
            continue
        factor = split.ratio if split.kind == "split" else Decimal(1) + split.ratio
        before = [p for p in ordered if p.day < split.ex_date]
        after = [p for p in ordered if p.day >= split.ex_date]
        if not before or not after or abs(factor - 1) < Decimal("0.5"):
            continue
        observed = before[-1].close / after[0].close
        if abs(observed / factor - 1) <= Decimal("0.25"):
            return (
                f"the closes show the {factor} split of {split.ex_date.isoformat()} "
                f"(close ratio {observed:.4f}), so they are not split-adjusted"
            )
    return None


async def vendor_dividends(
    pool: asyncpg.Pool | asyncpg.Connection,
    *,
    symbols: list[str],
    listing_id: int | None,
    start: dt.date,
    end: dt.date,
) -> list[Action]:
    """Yahoo's unconfirmed cash dividends of the listing in the window (already split-adjusted).
    Decided rows (confirmed or rejected) are not here: confirmed ones are loaded with the other
    confirmed actions and rejected ones are the owner's veto."""
    rows = await pool.fetch(
        """select ex_date,cash_amount,currency,source from public.corporate_action
           where status='suggested' and source=$5 and action_type='cash_dividend'
             and cash_amount is not null and ex_date between $2 and $3
             and (symbol = any($1::text[]) or ($4::bigint is not null and listing_id=$4))
           order by ex_date""",
        symbols,
        start,
        end,
        listing_id,
        YAHOO_SOURCE,
    )
    return [
        Action(
            "cash_dividend",
            r["ex_date"],
            cash=Decimal(r["cash_amount"]),
            currency=r["currency"],
            source=r["source"],
            amount_basis="split_adjusted",
        )
        for r in rows
    ]


async def split_adjusted_total_return(
    pool: asyncpg.Pool | asyncpg.Connection,
    *,
    source: str,
    symbol: str,
    points: list[PricePoint],
    confirmed: list[Action],
    owner_verified: bool,
    action_symbols: list[str],
    listing_id: int | None,
    start: dt.date,
    end: dt.date,
    fx_to_quote: dict[dt.date, Decimal] | None = None,
    withholding_rate: Decimal = Decimal(0),
) -> ReturnResult:
    n = len(points)
    if source not in SPLIT_ADJUSTED_SOURCES:
        return _unavailable("raw close history is unavailable", n)
    if n < 2:
        return _unavailable("at least two closes are required", n)
    first, last = min(p.day for p in points), max(p.day for p in points)

    vendor_covered = source == "yahoo" and await events_cover(pool, symbol, first, last)
    if not owner_verified and not vendor_covered:
        if source == "yahoo":
            reason = (
                "raw close history is unavailable and Yahoo's dividend list was not collected "
                "over this window (it is collected with the daily bars; or verify the holding's "
                "corporate-action coverage)"
            )
        else:
            reason = (
                "raw close history is unavailable and Stooq publishes no dividend history: "
                "record the holding's dividends and verify its corporate-action coverage"
            )
        return _unavailable(reason, n)

    splits = [a for a in confirmed if a.kind in ("split", "stock_dividend") and first < a.ex_date]
    if any(a.ratio is None or a.ratio <= 0 for a in splits):
        return _unavailable("a confirmed split has no valid ratio", n)
    shown = closes_show_a_split(points, splits)
    if shown is not None:
        return _unavailable(shown, n, split_check="failed")

    confirmed_dividends = [a for a in confirmed if a.kind == "cash_dividend"]
    vendor: list[Action] = []
    if source == "yahoo":
        decided_days = {a.ex_date for a in confirmed_dividends}
        vendor = [
            a
            for a in await vendor_dividends(
                pool, symbols=action_symbols, listing_id=listing_id, start=start, end=end
            )
            if a.ex_date not in decided_days
        ]
    # Confirmed amounts "as declared" are on the share basis of their ex-date: bring them onto
    # the closes' (today's) basis with every later confirmed split. Vendor amounts already are.
    dividends = [
        replace(
            d,
            cash=(d.cash / _later_split_factor(splits, d.ex_date, end))
            if d.cash is not None
            else None,
            amount_basis="split_adjusted",
        )
        if d.amount_basis == "as_declared"
        else d
        for d in confirmed_dividends
    ] + vendor
    # Spin-offs, mergers, delistings and symbol changes stay in so calculate_return refuses them;
    # splits are NOT passed on: the closes are already adjusted for them.
    others = [a for a in confirmed if a.kind not in ("split", "stock_dividend", "cash_dividend")]

    result = calculate_return(
        points,
        dividends + others,
        reinvest_dividends=True,
        fx_to_quote=fx_to_quote,
        withholding_rate=withholding_rate,
    )
    flags: list[str] = []
    if vendor:
        flags.append(
            f"{len(vendor)} dividend(s) are Yahoo's unofficial, unconfirmed list "
            "(completeness not verified against the issuer)"
        )
    elif vendor_covered and not owner_verified:
        flags.append(
            "Yahoo lists no dividend in this window (unofficial list, not verified against "
            "the issuer)"
        )
    details = {
        **result.details,
        "basis": BASIS,
        "close_basis": f"{source} closes, split-adjusted and not dividend-adjusted",
        "dividend_source": (
            "owner-confirmed" if not vendor else "owner-confirmed plus Yahoo (unconfirmed)"
        ),
        "completeness": "owner-verified corporate-action coverage"
        if owner_verified
        else "Yahoo events collected over the whole window (unverified)",
        "confirmed_dividends": len(confirmed_dividends),
        "vendor_dividends": len(vendor),
        "warnings": flags,
    }
    return replace(result, details=details)
