"""P6: read deals for one holding, or across the portfolio, as of a date.

Everything is filtered by `known_at <= as_of`, so a view for an earlier date shows no later
knowledge: a transition, term version, document or closing link recorded after the date does not
exist in it. The state and the impacts come from the pure rules in `deals`; this module only reads
rows and shapes them.

What the database cannot yet supply (share counts, enterprise value, segment revenue) is passed to
the rules as unknown, so those impacts are null with a reason rather than guessed.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import asyncpg

from steward.disclosure import deals as dl
from steward.disclosure.deal_parties import effective_match_sql

type Pool = asyncpg.Pool | asyncpg.Connection

_TERM_COLUMNS = ", ".join(f"v.{n}, v.{n}_state" for n in dl.NUMERIC_TERMS)
_SOURCE = """left join public.source_evidence e on e.id = x.evidence_id
             left join public.source_document_version dv on dv.id = x.document_version_id
             left join public.source_document d on d.id = dv.document_id"""


def _aware(value: dt.datetime | None) -> dt.datetime:
    now = value or dt.datetime.now(dt.UTC)
    return now if now.tzinfo else now.replace(tzinfo=dt.UTC)


def _s(v: Decimal | None) -> str | None:
    return None if v is None else str(v)


def _term_version(r: Any) -> dl.TermVersion:
    return dl.TermVersion(
        id=int(r["id"]),
        version_no=int(r["version_no"]),
        known_at=r["known_at"],
        evidence_id=r["evidence_id"],
        origin=r["terms_origin"],
        supersedes_id=r["supersedes_id"],
        currency=r["currency"],
        values={
            n: r[n] for n in dl.NUMERIC_TERMS if r[f"{n}_state"] == "stated" and r[n] is not None
        },
        closing_conditions=r["closing_conditions"],
        unaffected_date=r["unaffected_date"],
        unaffected_source=r["unaffected_source"],
        document_version_id=r["document_version_id"],
        event_effective_at=r["event_effective_at"],
    )


def _transition(r: Any) -> dl.Transition:
    return dl.Transition(
        id=int(r["id"]),
        state=r["state"],
        effective_at=r["effective_at"],
        known_at=r["known_at"],
        evidence_id=r["evidence_id"],
        document_version_id=r["document_version_id"],
        rumor_source=r["rumor_source"],
        note=r["note"],
    )


def _bridge_json(b: dl.Bridge) -> dict[str, Any]:
    return {
        "currency": b.currency,
        "segments": [
            {"kind": s.kind, "state": s.state, "amount": _s(s.amount)} for s in b.segments
        ],
        "known_total": str(b.known_total),
        "unstated": list(b.unstated),
        "total_is_lower_bound": b.total_is_lower_bound,
        "cash_pct": _s(b.cash_pct),
        "stock_pct": _s(b.stock_pct),
        "term_version_id": b.term_version_id,
    }


def impact_json(i: dl.Impact) -> dict[str, Any]:
    return {
        "kind": i.kind,
        "label": i.label,
        "origin": i.origin,
        "value": _s(i.value),
        "unit": i.unit,
        "currency": i.currency,
        "reason": i.reason,
        "flags": list(i.flags),
        "inputs": dict(i.inputs),
        "detail": dict(i.detail),
        "formula": i.formula,
        "formula_version": i.formula_version,
        "term_version_ids": list(i.term_version_ids),
    }


async def _rows(pool: Pool, sql: str, ids: list[int], when: dt.datetime) -> dict[int, list[Any]]:
    out: dict[int, list[Any]] = {}
    for r in await pool.fetch(sql, ids, when):
        out.setdefault(int(r["deal_id"]), []).append(r)
    return out


def _group(rows: Sequence[Any]) -> dict[int, list[Any]]:
    out: dict[int, list[Any]] = {}
    for r in rows:
        out.setdefault(int(r["deal_id"]), []).append(r)
    return out


FACT_METRICS = ("shares_outstanding", "total_debt", "cash", "revenue")

FACTS_SQL = f"""
select x.deal_id, x.who, x.issuer_id, lower(o.metric) as metric, o.value, o.scale, o.known_at,
       o.id, o.segment, o.period_end, o.currency, o.dimensions
  from (
    select p.deal_id, p.role as who, m.issuer_id
      from public.deal_participant p
      join ({effective_match_sql("$2")}) m on m.participant_id = p.id and m.decision = 'confirmed'
     where p.deal_id = any($1::bigint[]) and p.known_at <= $2
    union all
    select e.id, 'held', i.id
      from public.deal_event e
      join public.instrument n on n.t212_ticker = e.ticker
      join public.issuer i on i.cik = n.cik
     where e.id = any($1::bigint[])
  ) x
  join public.disclosure_observation o on o.issuer_id = x.issuer_id
 where o.known_at <= $2 and o.value_state = 'reported' and o.value is not null
   and o.review_state <> 'rejected' and lower(o.metric) = any($3::text[])
 order by o.known_at, o.id
"""  # noqa: S608 - fixed fragments, no input


# The held company's closing price on or before the announcement (never after the as-of date), in
# the instrument's currency: the market side of "enterprise value before the deal".
PRICES_SQL = """
select e.id as deal_id, b.close, n.currency, b.date as price_date
  from public.deal_event e
  join public.instrument n on n.t212_ticker = e.ticker
  join lateral (
    select close, date from public.price_bar
     where symbol = coalesce(n.price_symbol, n.t212_ticker)
       and date < least((coalesce(e.announced_at, $2::timestamptz) at time zone 'UTC')::date,
                        ($2::timestamptz at time zone 'UTC')::date)
     order by date desc limit 1
  ) b on true
 where e.id = any($1::bigint[])
"""

ACQUIRER = ("acquirer", "bidder", "buyer")


def _scaled(r: Any) -> Decimal:
    return Decimal(r["value"]) * (Decimal(10) ** int(r["scale"] or 0))


def _has_dimensions(r: Any) -> bool:
    d = r.get("dimensions")
    if isinstance(d, str):
        d = d.strip()
        return d not in ("", "{}", "null")
    return bool(d)


def _segments(
    rows: Sequence[Any], roles: Sequence[str], exclude_issuers: frozenset[int] = frozenset()
) -> dict[str, Decimal] | None:
    """Revenue by segment for the latest period that has any, for the first role that has rows.
    Rows of `exclude_issuers` are skipped, so an acquirer never borrows the target's segments."""
    for role in roles:
        mine = [
            r
            for r in rows
            if r["who"] == role
            and r["metric"] == "revenue"
            and r.get("segment")
            and r.get("period_end")
            and r.get("issuer_id") not in exclude_issuers
        ]
        if not mine:
            continue
        newest = max(r["period_end"] for r in mine)
        # rows arrive oldest-known first, so a later restatement of the same segment wins
        return {r["segment"]: _scaled(r) for r in mine if r["period_end"] == newest}
    return None


def context_from_facts(
    rows: Sequence[Any], announced_on: dt.date | None, price: Any = None
) -> dl.DealContext:
    """Share counts, net debt, enterprise value and segment revenue from stored observations known
    by the as-of date. The latest value per participant and metric wins. A value that is not stored
    stays None, so the impact that needs it says why it is missing instead of using zero.

    Enterprise value is the held company's market value (closing price x shares) plus net debt. It
    is built only when the price's currency equals the currency of both debt and cash; otherwise it
    stays unknown rather than mixing currencies. `price` is a row with `close` and `currency`."""
    latest: dict[tuple[str, str], Decimal] = {}
    currency: dict[tuple[str, str], str | None] = {}
    newest: dict[tuple[str, str], tuple[dt.date, dt.datetime, int]] = {}
    for r in rows:
        if r["metric"] == "revenue" or r.get("segment") or _has_dimensions(r):
            continue  # segment revenue is read below; a class or segment count is not the firm's
        key = (r["who"], r["metric"])
        # the latest period wins, then the latest-known restatement of it (not the order of rows)
        rank = (r.get("period_end") or dt.date.min, r["known_at"], int(r.get("id") or 0))
        if key in newest and rank < newest[key]:
            continue
        newest[key] = rank
        latest[key] = _scaled(r)
        cur = r.get("currency")
        currency[key] = cur.upper() if cur else None
    target_issuers = frozenset(
        int(r["issuer_id"]) for r in rows if r["who"] == "target" and r.get("issuer_id") is not None
    )

    def pick(roles: Sequence[str], metric: str) -> Decimal | None:
        for role in roles:
            if (role, metric) in latest:
                return latest[(role, metric)]
        return None

    debt = pick(("held",), "total_debt")
    cash = pick(("held",), "cash")
    net_debt = None if debt is None or cash is None else debt - cash
    held_shares = pick(("held",), "shares_outstanding")
    ev: Decimal | None = None
    if net_debt is not None and held_shares is not None and price is not None:
        px_cur = (price["currency"] or "").upper()
        same = {currency[("held", "total_debt")], currency[("held", "cash")]} == {px_cur}
        if price["close"] is not None and px_cur and same:
            ev = Decimal(price["close"]) * held_shares + net_debt
    return dl.DealContext(
        announced_on=announced_on,
        acquirer_shares=pick(ACQUIRER, "shares_outstanding"),
        target_shares=pick(("target",), "shares_outstanding"),
        parent_shares=pick(("issuer", "held"), "shares_outstanding"),
        net_debt_before=net_debt,
        enterprise_value_before=ev,
        acquirer_segments=_segments(rows, (*ACQUIRER, "held"), target_issuers),
        target_segments=_segments(rows, ("target",)),
    )


async def _children(
    pool: Pool, ids: list[int], when: dt.datetime
) -> dict[str, dict[int, list[Any]]]:
    return {
        "facts": _group(await pool.fetch(FACTS_SQL, ids, when, list(FACT_METRICS))),
        "prices": _group(await pool.fetch(PRICES_SQL, ids, when)),
        "participants": await _rows(
            pool,
            """select p.deal_id, p.role, p.name, m.issuer_id, p.ticker, p.share_class from
            public.deal_participant p
                left join ("""
            + effective_match_sql("$2")
            + """) m on m.participant_id = p.id and m.decision = 'confirmed'
                where p.deal_id = any($1::bigint[]) and p.known_at <= $2 order by p.id""",
            ids,
            when,
        ),
        "transitions": await _rows(
            pool,
            f"""select x.*, e.quote, e.span_start, e.span_end, d.stable_url, dv.filed_at
                  from public.deal_state_transition x {_SOURCE}
                 where x.deal_id = any($1::bigint[]) and x.known_at <= $2 order by x.id""",  # noqa: S608
            ids,
            when,
        ),
        "versions": await _rows(
            pool,
            f"""select v.deal_id, v.id, v.version_no, v.supersedes_id, v.terms_origin, v.known_at,
                       v.event_effective_at, v.document_version_id, v.evidence_id, v.currency,
                       v.unaffected_date, v.unaffected_source, v.closing_conditions,
                       v.closing_conditions_state, {_TERM_COLUMNS}, e.quote, d.stable_url
                  from public.deal_term_version v
                  left join public.source_evidence e on e.id = v.evidence_id
                  left join public.source_document_version dv on dv.id = v.document_version_id
                  left join public.source_document d on d.id = dv.document_id
                 where v.deal_id = any($1::bigint[]) and v.known_at <= $2
                 order by v.version_no, v.id""",  # noqa: S608
            ids,
            when,
        ),
        "documents": await _rows(
            pool,
            """select x.deal_id, x.document_version_id, x.role, x.known_at, dv.filed_at,
                      d.stable_url
                 from public.deal_document x
                 join public.source_document_version dv on dv.id = x.document_version_id
                 join public.source_document d on d.id = dv.document_id
                where x.deal_id = any($1::bigint[]) and x.known_at <= $2 order by x.known_at,
                x.id""",
            ids,
            when,
        ),
        "links": await _rows(
            pool,
            """select * from public.deal_structure_link
                where deal_id = any($1::bigint[]) and known_at <= $2 order by known_at, id""",
            ids,
            when,
        ),
    }


def _deal_json(
    deal: Any,
    kids: dict[str, dict[int, list[Any]]],
    when: dt.datetime,
    owner_offer_price: Decimal | None,
) -> tuple[dict[str, Any], dl.DealSnapshot]:
    did = int(deal["id"])
    t_rows = kids["transitions"].get(did, [])
    v_rows = kids["versions"].get(did, [])
    versions = [_term_version(r) for r in v_rows]
    announced = deal["announced_at"]
    prices = kids.get("prices", {}).get(did, [])
    ctx = context_from_facts(
        kids.get("facts", {}).get(did, []),
        announced.astimezone(dt.UTC).date() if announced else None,
        prices[0] if prices else None,
    )
    snap = dl.snapshot(
        deal["deal_type"],
        [_transition(r) for r in t_rows],
        versions,
        when,
        ctx,
        owner_offer_price=owner_offer_price,
    )
    by_tid = {int(r["id"]): r for r in t_rows}
    quotes = {int(r["id"]): r for r in v_rows}
    last: dict[str, dl.TermVersion] = {}
    history: list[dict[str, Any]] = []
    for v in snap.history:
        changes = dl.term_changes(last.get(v.origin), v)
        last[v.origin] = v
        row = quotes[v.id]
        history.append(
            {
                "id": v.id,
                "version_no": v.version_no,
                "supersedes_id": v.supersedes_id,
                "origin": v.origin,
                "known_at": v.known_at,
                "event_effective_at": v.event_effective_at,
                "currency": v.currency,
                "document_version_id": v.document_version_id,
                "evidence_id": v.evidence_id,
                "source_url": row["stable_url"],
                "quote": row["quote"],
                "terms": [
                    {
                        "name": t.name,
                        "label": dl.TERM_LABEL[t.name],
                        "state": t.state,
                        "value": _s(t.value),
                    }
                    for t in dl.typed_terms(v)
                ],
                "closing_conditions": v.closing_conditions,
                "closing_conditions_state": row["closing_conditions_state"],
                "unaffected_date": v.unaffected_date,
                "unaffected_source": v.unaffected_source,
                "changes": [
                    {"field": c.field, "label": c.label, "old": c.old, "new": c.new}
                    for c in changes
                ],
                "problems": dl.version_problems(v),
            }
        )
    links = [
        dl.StructureLink(
            int(r["id"]),
            r["known_at"],
            r["evidence_id"],
            r["legal_closing_date"],
            r["first_reporting_period_end"],
            r["broker_posting_date"],
            r["successor_name"],
            r["successor_ticker"],
            r["successor_issuer_id"],
        )
        for r in kids["links"].get(did, [])
    ]
    closed = snap.state.state == "closed"
    closing = dl.closing_view(links, when, closed=closed) if (links or closed) else None
    spec = dl.TAXONOMY[deal["deal_type"]]
    return {
        "id": did,
        "transaction_key": deal["transaction_key"],
        "deal_type": deal["deal_type"],
        "type_label": spec.label,
        "title": deal["title"],
        "ticker": deal["ticker"],
        "announced_at": announced,
        "known_at": deal["known_at"],
        "state": snap.state.state,
        "confidence": snap.confidence,
        "currency": snap.latest.currency if snap.latest else None,
        "size": _s(snap.size),
        "participants": [
            {
                "role": r["role"],
                "name": r["name"],
                "issuer_id": r["issuer_id"],
                "ticker": r["ticker"],
                "share_class": r["share_class"],
            }
            for r in kids["participants"].get(did, [])
        ],
        "transitions": [
            {
                "id": t.id,
                "state": t.state,
                "effective_at": t.effective_at,
                "known_at": t.known_at,
                "evidence_id": t.evidence_id,
                "document_version_id": t.document_version_id,
                "source_url": by_tid[t.id]["stable_url"],
                "quote": by_tid[t.id]["quote"],
                "span_start": by_tid[t.id]["span_start"],
                "span_end": by_tid[t.id]["span_end"],
                "rumor_source": t.rumor_source,
                "note": t.note,
            }
            for t in snap.state.applied
        ],
        "rejected_transitions": [
            {"id": t.id, "state": t.state, "reason": why} for t, why in snap.state.rejected
        ],
        "term_versions": history,
        "latest_version_id": snap.latest.id if snap.latest else None,
        "bridge": _bridge_json(snap.bridge),
        "impacts": [impact_json(i) for i in snap.impacts],
        "documents": [
            {
                "document_version_id": r["document_version_id"],
                "role": r["role"],
                "source_url": r["stable_url"],
                "known_at": r["known_at"],
                "filed_at": r["filed_at"],
            }
            for r in kids["documents"].get(did, [])
        ],
        "missing_fields": [{"field": f, "label": dl.TERM_LABEL.get(f, f)} for f in snap.missing],
        "closing": None
        if closing is None
        else {
            "legal_closing_date": closing.legal_closing_date,
            "first_reporting_period_end": closing.first_reporting_period_end,
            "broker_posting_date": closing.broker_posting_date,
            "successor_name": closing.successor_name,
            "successor_ticker": closing.successor_ticker,
            "successor_issuer_id": closing.successor_issuer_id,
            "missing": list(closing.missing),
            "problems": list(closing.problems),
        },
    }, snap


async def holding_deals(
    pool: Pool,
    ticker: str,
    as_of: dt.datetime | None = None,
    *,
    deal_type: str | None = None,
    state: str | None = None,
    currency: str | None = None,
    min_size: Decimal | None = None,
    max_size: Decimal | None = None,
    owner_offer_price: Decimal | None = None,
) -> dict[str, Any]:
    """Deals linked to a holding, as known at `as_of`, optionally filtered. A size filter excludes a
    deal whose size is not stated and counts it, so an unknown size is never read as small."""
    when = _aware(as_of)
    rows = await pool.fetch(
        """select id, transaction_key, deal_type, title, ticker, announced_at, known_at
             from public.deal_event where ticker = $1 and known_at <= $2
            order by announced_at nulls last, id""",
        ticker,
        when,
    )
    kids = (
        await _children(pool, [int(r["id"]) for r in rows], when)
        if rows
        else {k: {} for k in ("participants", "transitions", "versions", "documents", "links")}
    )
    deals: list[dict[str, Any]] = []
    unknown_size = 0
    for r in rows:
        d, snap = _deal_json(r, kids, when, owner_offer_price)
        if deal_type and d["deal_type"] != deal_type:
            continue
        if state and d["state"] != state:
            continue
        if currency and d["currency"] != currency:
            continue
        if min_size is not None or max_size is not None:
            if snap.size is None:
                unknown_size += 1
                continue
            if (min_size is not None and snap.size < min_size) or (
                max_size is not None and snap.size > max_size
            ):
                continue
        deals.append(d)
    notes: list[str] = []
    if not rows:
        notes.append(
            "No public deal is recorded for this holding by that date. That is not proof that none "
            "exists."
        )
    if unknown_size:
        notes.append(f"{unknown_size} deal(s) were left out because their size is not stated.")
    return {
        "ticker": ticker,
        "as_of": when,
        "deals": deals,
        "total_known": len(rows),
        "excluded_unknown_size": unknown_size,
        "notes": notes,
    }


async def portfolio_view(pool: Pool, as_of: dt.datetime | None = None) -> dict[str, Any]:
    """Portfolio weight sitting in holdings with an active deal known at the date."""
    when = _aware(as_of)
    held = await pool.fetch(
        """select ticker, value_account from public.position_snapshot
            where snapshot_date = (
                  select max(snapshot_date) from public.account_snapshot
                   where snapshot_date <= ($1::timestamptz at time zone 'UTC')::date)""",
        when,
    )
    total = sum((Decimal(r["value_account"]) for r in held), Decimal(0))
    weights = {r["ticker"]: Decimal(r["value_account"]) / total for r in held if total > 0}
    refs: list[dl.DealRef] = []
    titles: dict[int, str] = {}
    if weights:
        deals = await pool.fetch(
            """select id, deal_type, title, ticker from public.deal_event
                where ticker = any($1::text[]) and known_at <= $2 order by id""",
            sorted(weights),
            when,
        )
        ids = [int(r["id"]) for r in deals]
        trans = (
            await _rows(
                pool,
                """select * from public.deal_state_transition
                where deal_id = any($1::bigint[]) and known_at <= $2 order by id""",
                ids,
                when,
            )
            if ids
            else {}
        )
        for r in deals:
            st = dl.resolve_state(
                r["deal_type"], [_transition(t) for t in trans.get(int(r["id"]), [])], when
            )
            refs.append(dl.DealRef(r["ticker"], int(r["id"]), r["deal_type"], st.state))
            titles[int(r["id"])] = r["title"]
    ex = dl.portfolio_exposure(weights, refs)
    return {
        "as_of": when,
        "holdings": len(weights),
        "denominator": str(ex.denominator),
        "covered_weight": str(ex.covered_weight),
        "none_known": list(ex.none_known),
        "unknown_state": list(ex.unknown_state),
        "by_state": {k: str(v) for k, v in ex.by_state.items()},
        "by_type": {k: str(v) for k, v in ex.by_type.items()},
        "rows": [
            {
                "ticker": d.ticker,
                "deal_id": d.deal_id,
                "title": titles.get(d.deal_id, ""),
                "deal_type": d.deal_type,
                "state": d.state,
                "weight": str(w),
            }
            for d, w in ex.rows
        ],
        "notes": list(ex.notes),
    }
