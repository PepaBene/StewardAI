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
    return None if v is None else str(v)


async def insider_activity(
    pool: Pool, ticker: str, as_of: dt.datetime | None = None
) -> dict[str, Any]:
    when = _aware(as_of)
    txs, urls = await _rows(pool, ticker)
    overrides = await _overrides(pool, txs)
    view = ins.build_view(txs, when, overrides=overrides)
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
        detail.append(
            {
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
                "position_change_percent": _s(ins.position_change(t)),
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
        "notes": list(view.notes),
        "transactions": detail,
    }


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
