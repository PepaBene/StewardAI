"""P4.5: read insider activity for one holding as of a date.

Reads the existing Form 3/4/5 tables (nothing is copied) and the override ledger, and hands the rows
to the pure logic in `insiders.py`. Only filings known by `as_of` are visible; an amendment replaces
its original only once the amendment was known.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import asyncpg

from steward.disclosure import insiders as ins

type Pool = asyncpg.Pool | asyncpg.Connection


class OverrideError(Exception):
    """The override was refused: the row does not exist or the reason is too short."""


def _aware(value: dt.datetime | None) -> dt.datetime:
    now = value or dt.datetime.now(dt.UTC)
    return now if now.tzinfo else now.replace(tzinfo=dt.UTC)


async def _rows(pool: Pool, ticker: str) -> tuple[list[ins.InsiderTx], dict[tuple[str, int], str]]:
    rows = await pool.fetch(
        """select t.accession, t.row_index, t.reporting_owner_cik, t.owner_name, t.is_officer,
                  t.is_director, t.is_ten_percent_owner, t.transaction_date, t.transaction_code,
                  t.shares, t.price_per_share, t.shares_owned_after, t.direct_indirect,
                  t.derivative, t.economic_class, t.classification_basis, t.rule_10b5_1_plan,
                  t.equity_swap_involved, f.filed, f.known_at, f.source_url,
                  s.known_at as superseded_known_at
             from public.insider_transaction t
             join public.insider_filing f on f.accession = t.accession
             left join public.insider_filing s on s.accession = f.superseded_by_accession
            where f.ticker = $1""",
        ticker,
    )
    out: list[ins.InsiderTx] = []
    urls: dict[tuple[str, int], str] = {}
    for r in rows:
        tx = ins.InsiderTx(
            accession=r["accession"],
            row_index=r["row_index"],
            insider=r["reporting_owner_cik"],
            name=r["owner_name"],
            role=ins.role_of(r["is_officer"], r["is_director"], r["is_ten_percent_owner"]),
            tx_date=r["transaction_date"],
            filed=r["filed"],
            known_at=r["known_at"],
            economic_class=r["economic_class"],
            derivative=r["derivative"],
            shares=r["shares"],
            price=r["price_per_share"],
            shares_after=r["shares_owned_after"],
            direct_indirect=r["direct_indirect"],
            plan=r["rule_10b5_1_plan"],
            equity_swap=r["equity_swap_involved"],
            superseded_by_known_at=r["superseded_known_at"],
            class_confirmed=r["classification_basis"] is not None
            and r["economic_class"] is not None,
        )
        out.append(tx)
        urls[tx.key] = r["source_url"]
    return out, urls


async def _splits(pool: Pool, ticker: str, when: dt.datetime) -> list[tuple[dt.date, Decimal]]:
    """Confirmed splits already known at `when`, so a replay cannot know a later split."""
    rows = await pool.fetch(
        """select ex_date, ratio from public.corporate_action
            where symbol = $1 and action_type = 'split' and status = 'confirmed'
              and ratio is not null and ratio > 0 and known_at <= $2
            order by ex_date""",
        ticker,
        when,
    )
    return [(r["ex_date"], Decimal(r["ratio"])) for r in rows]


async def _detail_extras(pool: Pool, txs: list[ins.InsiderTx]) -> dict[tuple[str, int], Any]:
    """Footnote text and derivative terms exactly as parsed from the form (P4.2)."""
    if not txs:
        return {}
    rows = await pool.fetch(
        """select accession, row_index, footnote_text, exercise_price, exercisable_date,
                  expiration_date, underlying_security_title, underlying_shares,
                  rule_10b5_1_basis, rule_10b5_1_adopted
             from public.insider_transaction where accession = any($1::text[])""",
        sorted({t.accession for t in txs}),
    )
    return {(r["accession"], r["row_index"]): r for r in rows}


async def context_for(pool: Pool, ticker: str, when: dt.datetime) -> dict[str, Any]:
    """Share count and nearby company events as labelled context. No motive is implied: these are
    facts that sit beside the trades, and a missing share count is said to be missing."""
    shares = await pool.fetch(
        """select shares, as_of, method from public.shares_outstanding
            where ticker = $1 and as_of <= ($2::timestamptz at time zone 'UTC')::date
              and filed is not null
              -- known at the end of the filing day (UTC); an undated count cannot be placed in
              -- time, so it is never shown in a replay
              and ((filed + 1)::timestamp at time zone 'UTC') <= $2
            order by as_of desc limit 2""",
        ticker,
        when,
    )
    events = await pool.fetch(
        """select event_type, event_state, announced_at, source_url from public.company_event
            where ticker = $1 and status = 'confirmed' and known_at <= $2
              and announced_at is not null and announced_at >= $3
            order by announced_at desc limit 20""",
        ticker,
        when,
        when.date() - dt.timedelta(days=365),
    )
    latest = shares[0] if shares else None
    prior = shares[1] if len(shares) > 1 else None
    change = None
    if latest and prior and prior["shares"] > 0:
        change = (Decimal(latest["shares"]) - Decimal(prior["shares"])) / Decimal(prior["shares"])
    return {
        "shares_outstanding": _s(Decimal(latest["shares"])) if latest else None,
        "shares_outstanding_as_of": latest["as_of"] if latest else None,
        "shares_outstanding_method": latest["method"] if latest else None,
        "reported_share_count_change": _s(change),
        "events": [
            {
                "event_type": e["event_type"],
                "event_state": e["event_state"],
                "announced_at": e["announced_at"],
                "source_url": e["source_url"],
            }
            for e in events
        ],
    }


async def _overrides(pool: Pool, txs: list[ins.InsiderTx]) -> list[ins.Override]:
    if not txs:
        return []
    rows = await pool.fetch(
        """select accession, row_index, original_class, new_class, reason, decided_by, decided_at
             from public.insider_classification_override
            where accession = any($1::text[])""",
        sorted({t.accession for t in txs}),
    )
    return [ins.Override(**dict(r)) for r in rows]


def _s(v: Decimal | None) -> str | None:
    """Plain decimal text: Postgres numerics such as 1.10E+6 are shown as 1100000."""
    return None if v is None else format(v.normalize(), "f")


async def insider_activity(
    pool: Pool, ticker: str, as_of: dt.datetime | None = None
) -> dict[str, Any]:
    when = _aware(as_of)
    raw_txs, urls = await _rows(pool, ticker)
    splits = await _splits(pool, ticker, when)
    txs = ins.restate_for_splits(raw_txs, splits, when.date()) if splits else raw_txs
    extras = await _detail_extras(pool, txs)
    context = await context_for(pool, ticker, when)
    overrides = await _overrides(pool, txs)
    view = ins.build_view(txs, when, overrides=overrides)
    outstanding = Decimal(context["shares_outstanding"]) if context["shares_outstanding"] else None
    if outstanding is not None and splits:
        # The trades are in today's share count, so the count they are divided by must be too.
        counted_on = context["shares_outstanding_as_of"]
        outstanding, _ = ins.adjust_for_splits(outstanding, None, counted_on, splits, when.date())
    shown = sorted(
        ins.visible(txs, when),
        key=lambda t: (t.tx_date or t.filed, t.accession, t.row_index),
        reverse=True,
    )
    bundled = {r for b in view.bundles for r in b.rows}
    made = {(o.accession, o.row_index): o for o in overrides if o.decided_at <= when}
    detail = []
    for t in shown:
        cls = ins.effective_class(t, overrides, when)
        o = made.get(t.key)
        extra = extras.get(t.key)
        detail.append(
            {
                "footnote": extra["footnote_text"] if extra else None,
                "exercise_price": _s(extra["exercise_price"]) if extra else None,
                "exercisable_date": extra["exercisable_date"] if extra else None,
                "expiration_date": extra["expiration_date"] if extra else None,
                "underlying_security": extra["underlying_security_title"] if extra else None,
                "underlying_shares": _s(extra["underlying_shares"]) if extra else None,
                "plan_basis": extra["rule_10b5_1_basis"] if extra else None,
                "plan_adopted": extra["rule_10b5_1_adopted"] if extra else None,
                "percent_of_outstanding": _s(
                    t.shares / outstanding * 100 if t.shares is not None and outstanding else None
                ),
                "accession": t.accession,
                "row_index": t.row_index,
                "insider": t.name or t.insider,
                "role": t.role,
                "tx_date": t.tx_date,
                "filed": t.filed,
                "known_at": t.known_at,
                "economic_class": cls or "unclassified",
                "classifier_class": t.economic_class,
                "override_reason": o.reason if o else None,
                "countable": ins.countable(t, cls) and t.key not in bundled and t.plan is not True,
                "bundled": t.key in bundled,
                "plan": ins.plan_status(t),
                "derivative": t.derivative,
                "direct_indirect": t.direct_indirect,
                "shares": _s(t.shares),
                "price": _s(t.price),
                "value": _s(None if t.shares is None or t.price is None else t.shares * t.price),
                "shares_after": _s(t.shares_after),
                "position_change_percent": _s(ins.position_change(t, cls)),
                "caveats": ins.caveats(t),
                "source_url": urls[t.key],
            }
        )
    return {
        "ticker": ticker,
        "as_of": when,
        "net": [
            {
                "window_days": n.window_days,
                "role": n.role,
                "buy_shares": str(n.buy_shares),
                "sell_shares": str(n.sell_shares),
                "net_shares": str(n.net_shares),
                "buy_value": str(n.buy_value),
                "sell_value": str(n.sell_value),
                "net_value": str(n.net_value),
                "trades": n.trades,
                "insiders": n.insiders,
                "plan_trades_excluded": n.plan_trades_excluded,
                "plan_unknown_trades": n.plan_unknown_trades,
                "bundled_sales_excluded": n.bundled_sales_excluded,
            }
            for n in view.net
        ],
        "clusters": [
            {
                "start": c.start,
                "end": c.end,
                "insiders": len(c.insiders),
                "shares": str(c.shares),
                "value": str(c.value),
            }
            for c in view.clusters
        ],
        "bundles": [
            {
                "day": b.day,
                "exercised": str(b.exercised),
                "sold": str(b.sold),
                "proceeds": str(b.proceeds),
                "retained": str(b.retained),
            }
            for b in view.bundles
        ],
        "class_counts": dict(ins.by_class(txs, overrides, when)),
        "notes": [
            *view.notes,
            *(
                [f"{len(splits)} confirmed split(s) applied: shares are in today's count."]
                if splits
                else []
            ),
            *(
                []
                if outstanding
                else ["No share count was known at this date, so no trade is shown as a percent."]
            ),
        ],
        "context": context,
        "splits_applied": len(splits),
        "transactions": detail,
    }


async def discretionary_buys(
    pool: Pool, ticker: str, as_of: dt.datetime, window_days: int
) -> tuple[int, dt.datetime | None]:
    """Open-market buys in the trailing window, counted exactly as the net view counts them: the
    filings known and not yet superseded at `as_of`, the owner's class overrides applied, bundled
    exercise-and-sell lines left out, and a buy with no plan indicator counted (only a stated
    10b5-1 plan excludes it). Returns the count and the latest filing time seen."""
    when = _aware(as_of)
    txs, _ = await _rows(pool, ticker)
    if not txs:
        return 0, None
    overrides = await _overrides(pool, txs)
    rows = ins.visible(txs, when)
    bundled = {r for b in ins.find_bundles(rows, overrides, when) for r in b.rows}
    start = when.date() - dt.timedelta(days=window_days)
    count = sum(
        1
        for t in rows
        if t.tx_date is not None
        and start < t.tx_date <= when.date()
        and (cls := ins.effective_class(t, overrides, when)) == "open_market_buy"
        and ins.countable(t, cls)
        and t.key not in bundled
        and t.plan is not True
    )
    return count, max((t.known_at for t in rows), default=None)


async def pending_alerts(
    pool: Pool, ticker: str, min_value: Decimal, as_of: dt.datetime | None = None
) -> list[ins.AlertCandidate]:
    """Material verified open-market trades visible at `as_of`, split-adjusted. Raising each one
    once is the alert store's job (`alerts.record` deduplicates by trade), so running this twice,
    or reading an amendment that repeats a trade, cannot alert twice."""
    when = _aware(as_of)
    raw_txs, _ = await _rows(pool, ticker)
    splits = await _splits(pool, ticker, when)
    txs = ins.restate_for_splits(raw_txs, splits, when.date()) if splits else raw_txs
    return ins.alerts(txs, when, min_value=min_value, overrides=await _overrides(pool, txs))


async def record_override(
    pool: Pool,
    accession: str,
    row_index: int,
    new_class: str,
    reason: str,
    decided_by: str,
) -> int:
    """Append a reviewer override. The original class comes from the row, not the caller."""
    row = await pool.fetchrow(
        "select economic_class from public.insider_transaction where accession=$1 and row_index=$2",
        accession,
        row_index,
    )
    if row is None:
        raise OverrideError("that transaction does not exist")
    try:
        ins.Override(accession, row_index, new_class, reason, decided_by, dt.datetime.now(dt.UTC))
    except ValueError as exc:
        raise OverrideError(str(exc)) from exc
    # The same decision again (a resubmitted form) is the one already on file: the table is
    # append-only, so a repeat would sit in the line's history for good.
    latest = await pool.fetchrow(
        "select id, new_class, reason, decided_by from public.insider_classification_override"
        " where accession = $1 and row_index = $2 order by id desc limit 1",
        accession,
        row_index,
    )
    if latest is not None and (latest["new_class"], latest["reason"], latest["decided_by"]) == (
        new_class,
        reason.strip(),
        decided_by,
    ):
        return int(latest["id"])
    new_id = await pool.fetchval(
        """insert into public.insider_classification_override
             (accession, row_index, original_class, new_class, reason, decided_by)
           values ($1,$2,$3,$4,$5,$6) returning id""",
        accession,
        row_index,
        row["economic_class"],
        new_class,
        reason.strip(),
        decided_by,
    )
    return int(new_id)
