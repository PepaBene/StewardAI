"""P4.4: UK PDMR notifications read into the same discretionary-trade rules as Form 4.

A PDMR notification (EU/UK MAR Article 19) is its own regime, so it is kept apart from the SEC
rows: no merged total, no common identifier, and no currency conversion. What it shares with Form 4
is the rule for what may be counted (only open-market buys and sells with a volume and a price).

Known differences, stated on every read rather than hidden:
  * there is no 10b5-1 flag, so plan status is always unknown;
  * a notification is public the day the issuer announces it, so `known_at` is the announcement
    time (the time we first saw it when the announcement time is missing);
  * an amendment is not linked to its original, so an identical trade is counted once, at the
    earliest known notification;
  * the person is the name on the notice (there is no owner CIK), the role comes from the
    position text, and a role that cannot be read is `other`;
  * prices are in the notice's own currency and pence are not pounds: totals are per currency.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import asyncpg

from steward.disclosure import insiders as ins

type Pool = asyncpg.Pool | asyncpg.Connection

_OFFICER = re.compile(
    r"\b(chief|ceo|cfo|coo|cto|president|officer|managing director|general counsel)\b", re.I
)
_DIRECTOR = re.compile(r"\b(director|chair(man|woman|person)?)\b", re.I)


def role_from_position(position: str | None) -> ins.Role:
    """Officer when the position names an executive office, else director when it names a board
    seat, else other. 'Chief Financial Officer and Director' is an officer (officer comes first)."""
    text = position or ""
    if _OFFICER.search(text):
        return "officer"
    if _DIRECTOR.search(text):
        return "director"
    return "other"


def dedupe(txs: Sequence[ins.InsiderTx]) -> list[ins.InsiderTx]:
    """One row per identical trade (person, day, class, volume, price), keeping the earliest known:
    an amendment that repeats a notice is not a second trade. Two identical lines inside one
    notice are two trades (a split fill at one price) and both stay."""
    first_notice: dict[tuple[Any, ...], str] = {}
    kept: list[ins.InsiderTx] = []
    for t in sorted(txs, key=lambda r: (r.known_at, r.accession, r.row_index)):
        key = (t.insider, t.tx_date, t.economic_class, t.shares, t.price)
        notice = first_notice.setdefault(key, t.accession)
        if notice == t.accession:
            kept.append(t)
    return sorted(kept, key=lambda r: (r.accession, r.row_index))


def _s(v: Decimal | None) -> str | None:
    return None if v is None else format(v.normalize(), "f")


async def pdmr_activity(
    pool: Pool, ticker: str, as_of: dt.datetime | None = None
) -> dict[str, Any] | None:
    """UK PDMR trades for one holding, or None when no parsed notice exists for it."""
    when = as_of or dt.datetime.now(dt.UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.UTC)
    rows = await pool.fetch(
        """select n.id, n.source_url, n.person_name, n.position, n.is_amendment, n.announced_at,
                  n.first_attempt_at, t.row_index, t.instrument, t.nature, t.price, t.currency,
                  t.volume, t.transaction_date, t.venue, t.economic_class, t.classification_basis
             from public.pdmr_notification n
             join public.pdmr_transaction t on t.notification_id = n.id
            where n.ticker = $1 and n.status = 'parsed'
            order by n.id, t.row_index""",
        ticker,
    )
    if not rows:
        return None
    by_currency: dict[str, list[ins.InsiderTx]] = defaultdict(list)
    urls: dict[tuple[str, int], str] = {}
    meta: dict[tuple[str, int], Any] = {}
    for r in rows:
        known = r["announced_at"] or r["first_attempt_at"]
        person = (r["person_name"] or "unnamed").strip().lower()
        tx = ins.InsiderTx(
            accession=f"pdmr:{r['id']}",
            row_index=r["row_index"],
            insider=person,
            name=r["person_name"],
            role=role_from_position(r["position"]),
            tx_date=r["transaction_date"],
            filed=known.date(),
            known_at=known,
            economic_class=r["economic_class"],
            shares=r["volume"],
            price=r["price"],
            plan=None,
            class_confirmed=r["classification_basis"] is not None,
        )
        by_currency[r["currency"] or "unknown"].append(tx)
        urls[tx.key] = r["source_url"]
        meta[tx.key] = r
    visible_total = 0
    nets: list[dict[str, Any]] = []
    for currency, txs in sorted(by_currency.items()):
        unique = dedupe(ins.visible(txs, when))
        visible_total += len(unique)
        view = ins.build_view(unique, when)
        nets += [
            {
                "currency": currency,
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
            }
            for n in view.net
        ]
    shown = dedupe(ins.visible([t for txs in by_currency.values() for t in txs], when))
    transactions = [
        {
            "notice": t.accession,
            "person": t.name,
            "role": t.role,
            "tx_date": t.tx_date,
            "announced": t.known_at,
            "economic_class": t.economic_class or "unclassified",
            "countable": ins.countable(t, t.economic_class) and t.class_confirmed,
            "instrument": meta[t.key]["instrument"],
            "nature": meta[t.key]["nature"],
            "venue": meta[t.key]["venue"],
            "shares": _s(t.shares),
            "price": _s(t.price),
            "currency": meta[t.key]["currency"],
            "amendment": bool(meta[t.key]["is_amendment"]),
            "source_url": urls[t.key],
        }
        for t in sorted(shown, key=lambda r: (r.tx_date or r.filed, r.accession), reverse=True)
    ]
    return {
        "jurisdiction": "UK",
        "regime": "MAR Article 19 PDMR notifications",
        "notices": len({t["notice"] for t in transactions}),
        "currencies": sorted(by_currency),
        "net": [n for n in nets if n["role"] in {"all", *ins.ROLES}],
        "transactions": transactions,
        "notes": [
            "A separate regime from Form 4: never added to the SEC figures.",
            "No trading-plan flag exists on a PDMR notice, so plan status is unknown.",
            "An amendment is not linked to its original: identical trades are counted once.",
            "Totals are per currency; pence are not converted to pounds.",
        ],
    }
