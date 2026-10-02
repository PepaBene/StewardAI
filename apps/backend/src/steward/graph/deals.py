"""Deal and financing events: state machine, current-state derivation, terms and pro-forma.

A deal is a sequence of company_event rows sharing a `deal_key`, one row per state and source
document (`proposed`, `announced`, `amended`, `approved`, `closed`, `terminated`, `reversed`).

* Transitions are validated: a closed deal can only be reversed, a terminated or reversed deal
  is final, and a state cannot be recorded that the deal could not have reached.
* The first observation may start anywhere (a closing 8-K can be the first thing we see), and
  an earlier state discovered later is a *backfill*: it is stored, ordered by its date, and does
  not rewrite the state we already knew.
* `known_at` is preserved per state (a refresh never moves it), so the state *as known on any
  date* can be derived.
* Terms (price, consideration mix, debt raised, shares to issue) and the dilution / leverage
  they imply are computed from **confirmed** events only.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal, cast

import asyncpg

from steward.graph.edges import upsert_event
from steward.graph.keys import normalize_object_key

DealState = Literal[
    "proposed", "announced", "amended", "approved", "closed", "terminated", "reversed"
]
STATES: tuple[str, ...] = (
    "proposed",
    "announced",
    "amended",
    "approved",
    "closed",
    "terminated",
    "reversed",
)
RANK = {state: i for i, state in enumerate(STATES)}
ALLOWED: dict[str | None, frozenset[str]] = {
    None: frozenset(STATES),
    "proposed": frozenset({"announced", "amended", "approved", "closed", "terminated"}),
    "announced": frozenset({"amended", "approved", "closed", "terminated"}),
    "amended": frozenset({"amended", "approved", "closed", "terminated"}),
    "approved": frozenset({"amended", "closed", "terminated"}),
    "closed": frozenset({"reversed"}),
    "terminated": frozenset(),
    "reversed": frozenset(),
}
TERMINAL = frozenset({"terminated", "reversed"})


class DealTransitionError(ValueError):
    pass


def validate_transition(current: str | None, new: str) -> None:
    if new not in RANK:
        raise DealTransitionError(f"{new!r} is not a deal state")
    if current == new:
        return  # a second document corroborating the same state
    if new not in ALLOWED[current]:
        raise DealTransitionError(f"a deal cannot go from {current} to {new}")


@dataclass(frozen=True, slots=True)
class DealEvent:
    id: int
    state: str
    status: str
    date: dt.date
    known_at: dt.datetime
    terms: Mapping[str, object] = field(default_factory=dict)
    evidence_id: int | None = None
    source_url: str = ""


@dataclass(frozen=True, slots=True)
class DealState_:
    current: str | None
    history: tuple[DealEvent, ...]
    ignored: tuple[tuple[DealEvent, str], ...]


def _walk(events: Sequence[DealEvent]) -> DealState_:
    """Apply events in date order; an event that is not a valid next step is ignored (and
    reported), never silently reordered."""
    ordered = sorted(events, key=lambda e: (e.date, RANK.get(e.state, 99), e.known_at, e.id))
    current: str | None = None
    kept: list[DealEvent] = []
    ignored: list[tuple[DealEvent, str]] = []
    for event in ordered:
        try:
            validate_transition(current, event.state)
        except DealTransitionError as exc:
            ignored.append((event, str(exc)))
            continue
        kept.append(event)
        if event.state != current:
            current = event.state
    return DealState_(current, tuple(kept), tuple(ignored))


def derive_state(
    events: Iterable[DealEvent],
    *,
    as_of: dt.datetime | None = None,
    confirmed_only: bool = False,
) -> DealState_:
    """The state of a deal from the events known on `as_of`.

    Rejected and disputed events never drive a state. With `confirmed_only` only confirmed
    events count (the state analytics may rely on); without it suggested events do too (the
    state to show the owner, flagged as unconfirmed)."""
    wanted = {"confirmed"} if confirmed_only else {"confirmed", "suggested"}
    usable = [e for e in events if e.status in wanted and (as_of is None or e.known_at <= as_of)]
    return _walk(usable)


# ------------------------------------------------------------------------------------- terms

_DECIMAL_KEYS = (
    "price_per_share",
    "total_value",
    "cash_value",
    "stock_value",
    "debt_assumed",
    "debt_raised",
    "facility_size",
    "shares_to_issue",
    "exchange_ratio",
    "cash_pct",
    "stock_pct",
    "target_shares_outstanding",
    "target_ebitda",
    "ownership_pct",
    "interest_rate_pct",
)
_TEXT_KEYS = ("currency", "debt_instrument", "consideration_type", "target", "acquirer")


def normalize_terms(raw: Mapping[str, object]) -> dict[str, object]:
    """Validated, JSON-safe terms: decimals as strings, unknown keys rejected, percentages in
    range, and the consideration mix consistent."""
    out: dict[str, object] = {}
    unknown = set(raw) - set(_DECIMAL_KEYS) - set(_TEXT_KEYS) - {"extracted_from", "notes"}
    if unknown:
        raise ValueError(f"unknown deal term(s): {sorted(unknown)}")
    for key in _DECIMAL_KEYS:
        if raw.get(key) is None:
            continue
        try:
            value = Decimal(str(raw[key]))
        except InvalidOperation as exc:
            raise ValueError(f"deal term {key} is not a number") from exc
        if not value.is_finite() or value < 0:
            raise ValueError(f"deal term {key} must be a non-negative number")
        if key.endswith("_pct") and value > 100:
            raise ValueError(f"deal term {key} must be at most 100")
        out[key] = str(value)
    for key in _TEXT_KEYS:
        if isinstance(raw.get(key), str) and raw[key]:
            out[key] = str(raw[key])[:240]
    for key in ("extracted_from", "notes"):
        if raw.get(key) is not None:
            out[key] = raw[key]
    if "cash_pct" in out and "stock_pct" in out:
        total = Decimal(cast(str, out["cash_pct"])) + Decimal(cast(str, out["stock_pct"]))
        if abs(total - 100) > Decimal("0.5"):
            raise ValueError("cash_pct and stock_pct must add up to 100")
    return out


def confirmed_terms(events: Iterable[DealEvent]) -> tuple[dict[str, object], dict[str, int]]:
    """Terms in force: confirmed events folded in order, later amendments overriding earlier
    values key by key. Returns (terms, key -> id of the event that set it)."""
    state = derive_state(events, confirmed_only=True)
    merged: dict[str, object] = {}
    provenance: dict[str, int] = {}
    for event in state.history:
        for key, value in event.terms.items():
            merged[key] = value
            provenance[key] = event.id
    return merged, provenance


@dataclass(frozen=True, slots=True)
class BaseCapital:
    shares_outstanding: Decimal | None = None
    total_debt: Decimal | None = None
    cash: Decimal | None = None
    ebitda: Decimal | None = None
    share_price: Decimal | None = None
    currency: str | None = None


def _d(terms: Mapping[str, object], key: str) -> Decimal | None:
    value = terms.get(key)
    return Decimal(str(value)) if value is not None else None


def _ratio(num: Decimal | None, den: Decimal | None) -> Decimal | None:
    if num is None or den is None or den <= 0:
        return None
    return (num / den).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def pro_forma(terms: Mapping[str, object], base: BaseCapital) -> dict[str, Any]:
    """Dilution and leverage implied by *confirmed* terms and the latest reported capital.

    Nothing is guessed: each figure is None with its missing inputs listed. Net debt rises by
    the cash consideration plus assumed debt however it is financed; gross debt rises by debt
    raised plus assumed."""
    missing: list[str] = []
    shares_issued = _d(terms, "shares_to_issue")
    stock_value = _d(terms, "stock_value")
    if stock_value is None and (_d(terms, "total_value") is not None and _d(terms, "stock_pct")):
        stock_value = (
            cast(Decimal, _d(terms, "total_value")) * cast(Decimal, _d(terms, "stock_pct")) / 100
        )
    if shares_issued is None and stock_value is not None and base.share_price:
        shares_issued = stock_value / base.share_price
    if shares_issued is None and stock_value:
        missing.append("share price (to convert the stock consideration into shares)")
    if shares_issued is None and stock_value is None and _d(terms, "stock_pct") != 0:
        missing.append("stock consideration or shares to issue")
    cash_paid = _d(terms, "cash_value")
    if (
        cash_paid is None
        and _d(terms, "total_value") is not None
        and _d(terms, "cash_pct") is not None
    ):
        cash_paid = (
            cast(Decimal, _d(terms, "total_value")) * cast(Decimal, _d(terms, "cash_pct")) / 100
        )
    assumed = _d(terms, "debt_assumed") or Decimal(0)
    raised = _d(terms, "debt_raised") or Decimal(0)
    if base.shares_outstanding is None:
        missing.append("shares outstanding")
    if base.total_debt is None:
        missing.append("total debt")
    if base.ebitda is None:
        missing.append("EBITDA")
    out: dict[str, Any] = {
        "currency": terms.get("currency") or base.currency,
        "shares_issued": shares_issued,
        "share_count_increase": _ratio(shares_issued, base.shares_outstanding),
        "new_holders_ownership": _ratio(
            shares_issued,
            base.shares_outstanding + shares_issued
            if base.shares_outstanding is not None and shares_issued is not None
            else None,
        ),
        "added_gross_debt": raised + assumed,
        "net_debt_increase": (cash_paid + assumed) if cash_paid is not None else None,
        "gross_debt_after": base.total_debt + raised + assumed
        if base.total_debt is not None
        else None,
    }
    ebitda_after = (
        base.ebitda + (_d(terms, "target_ebitda") or Decimal(0))
        if base.ebitda is not None
        else None
    )
    out["gross_leverage_before"] = _ratio(base.total_debt, base.ebitda)
    out["gross_leverage_after"] = _ratio(out["gross_debt_after"], ebitda_after)
    if base.total_debt is not None and base.cash is not None:
        net_before = base.total_debt - base.cash
        out["net_leverage_before"] = _ratio(net_before, base.ebitda)
        out["net_leverage_after"] = (
            _ratio(net_before + out["net_debt_increase"], ebitda_after)
            if out["net_debt_increase"] is not None
            else None
        )
    else:
        out["net_leverage_before"] = out["net_leverage_after"] = None
        missing.append("cash")
    out["inputs_missing"] = sorted(set(missing))
    return out


# ------------------------------------------------------------------------------- persistence


def deal_key(ticker: str, kind: str, counterparty: str) -> str:
    return f"{ticker}:{kind}:{normalize_object_key(counterparty)}"


def _row_to_event(row: asyncpg.Record) -> DealEvent:
    terms = row["terms"]
    if isinstance(terms, str):
        import json

        terms = json.loads(terms)
    return DealEvent(
        id=row["id"],
        state=row["event_state"],
        status=row["status"],
        date=row["effective_at"] or row["announced_at"] or row["known_at"].date(),
        known_at=row["known_at"],
        terms=terms or {},
        evidence_id=row["evidence_id"],
        source_url=row["source_url"],
    )


async def load_deal_events(
    pool: asyncpg.Pool | asyncpg.Connection, ticker: str, key: str
) -> list[DealEvent]:
    rows = await pool.fetch(
        """select id,event_state,status,effective_at,announced_at,known_at,terms,evidence_id,
                  source_url
           from public.company_event
           where ticker=$1 and deal_key=$2 and event_type in ('deal','financing')
             and status <> 'rejected'""",
        ticker,
        key,
    )
    return [_row_to_event(r) for r in rows]


async def record_deal_event(
    pool: asyncpg.Pool,
    *,
    ticker: str,
    event_type: Literal["deal", "financing"],
    key: str,
    state: str,
    source: str,
    source_url: str,
    announced_at: dt.date | None = None,
    effective_at: dt.date | None = None,
    counterparty: str | None = None,
    terms: Mapping[str, object] | None = None,
    evidence_id: int | None = None,
    status: Literal["suggested", "confirmed"] = "suggested",
    thesis_kpi: str | None = None,
    detail: dict[str, object] | None = None,
    known_at: dt.datetime | None = None,
) -> int:
    """Record one deal state after validating it against the states already recorded."""
    if state not in RANK:
        raise DealTransitionError(f"{state!r} is not a deal state")
    clean = normalize_terms(terms or {})
    when = effective_at or announced_at or (known_at or dt.datetime.now(dt.UTC)).date()
    async with pool.acquire() as conn, conn.transaction():
        # Serialise writers for one deal so two filings cannot both pass validation.
        await conn.execute("select pg_advisory_xact_lock(hashtext($1))", key)
        existing = await load_deal_events(conn, ticker, key)
        candidate = DealEvent(
            id=0,
            state=state,
            status="suggested",
            date=when,
            known_at=known_at or dt.datetime.now(dt.UTC),
            terms=clean,
        )
        before = derive_state(existing)
        after = derive_state([*existing, candidate])
        if any(e.id == 0 for e, _ in after.ignored):
            reason = next(why for e, why in after.ignored if e.id == 0)
            raise DealTransitionError(
                f"{reason} (deal {key} is {before.current or 'new'} as of "
                f"{before.history[-1].date if before.history else 'n/a'})"
            )
        return await upsert_event(
            conn,
            ticker=ticker,
            event_type=event_type,
            event_state=state,
            source=source,
            source_url=source_url,
            status=status,
            announced_at=announced_at,
            effective_at=effective_at,
            evidence_id=evidence_id,
            counterparty_key=normalize_object_key(counterparty) if counterparty else None,
            terms=clean,
            thesis_kpi=thesis_kpi,
            detail=detail,
            deal_key=key,
            known_at=known_at,
        )


async def read_base_capital(
    pool: asyncpg.Pool, ticker: str, as_of: dt.datetime | None = None
) -> BaseCapital:
    """Latest reported capital from the statement fact store (whole-company facts only)."""

    async def latest(
        concepts: Sequence[str], statement_in: Sequence[str]
    ) -> tuple[Decimal | None, str | None]:
        row = await pool.fetchrow(
            """select value,currency from public.financial_statement_fact
               where ticker=$1 and concept=any($2::text[]) and value is not null
                 and (dimensions='{}'::jsonb or dimensions->'members'='[]'::jsonb)
                 and statement=any($3::text[])
                 and ($4::timestamptz is null or known_at<=$4)
               order by period_end desc, array_position($2::text[], concept), known_at desc
               limit 1""",
            ticker,
            list(concepts),
            list(statement_in),
            as_of,
        )
        return (Decimal(row["value"]), row["currency"]) if row else (None, None)

    shares, _ = await latest(
        ["dei:EntityCommonStockSharesOutstanding", "us-gaap:CommonStockSharesOutstanding"],
        ["shares", "balance_sheet"],
    )
    debt, currency = await latest(
        ["us-gaap:LongTermDebt", "us-gaap:DebtAndCapitalLeaseObligations", "ifrs-full:Borrowings"],
        ["balance_sheet"],
    )
    cash, _ = await latest(
        ["us-gaap:CashAndCashEquivalentsAtCarryingValue", "ifrs-full:CashAndCashEquivalents"],
        ["balance_sheet"],
    )
    operating, _ = await latest(
        ["us-gaap:OperatingIncomeLoss", "ifrs-full:ProfitLossFromOperatingActivities"], ["income"]
    )
    dna, _ = await latest(
        [
            "us-gaap:DepreciationDepletionAndAmortization",
            "us-gaap:DepreciationAndAmortization",
            "ifrs-full:DepreciationAndAmortisationExpense",
        ],
        ["cash_flow", "income"],
    )
    ebitda = operating + (dna or Decimal(0)) if operating is not None else None
    price_row = await pool.fetchrow(
        "select close from public.price_bar where symbol=$1 order by coalesce(session_date,date)"
        " desc limit 1",
        ticker,
    )
    price = Decimal(price_row["close"]) if price_row and price_row["close"] is not None else None
    return BaseCapital(shares, debt, cash, ebitda, price, currency)


async def deal_timeline(
    pool: asyncpg.Pool,
    ticker: str,
    as_of: dt.datetime | None = None,
    *,
    with_pro_forma: bool = True,
) -> list[dict[str, Any]]:
    """Evidence-linked timeline per deal with the displayed state, the confirmed state, the
    terms in force and, for confirmed terms, the dilution / leverage they imply."""
    rows = await pool.fetch(
        """select id,deal_key,event_type,event_state,status,effective_at,announced_at,known_at,
                  terms,evidence_id,source_url,source,counterparty_key,evidence_tier
           from public.company_event
           where ticker=$1 and deal_key is not null and event_type in ('deal','financing')
             and ($2::timestamptz is null or known_at<=$2)
           order by deal_key, coalesce(effective_at,announced_at,known_at::date), id""",
        ticker,
        as_of,
    )
    by_key: dict[str, list[asyncpg.Record]] = {}
    for row in rows:
        by_key.setdefault(row["deal_key"], []).append(row)
    base: BaseCapital | None = None
    out: list[dict[str, Any]] = []
    for key, group in by_key.items():
        events = [_row_to_event(r) for r in group if r["status"] != "rejected"]
        shown = derive_state(events, as_of=as_of)
        confirmed = derive_state(events, as_of=as_of, confirmed_only=True)
        terms, provenance = confirmed_terms(events)
        item: dict[str, Any] = {
            "deal_key": key,
            "event_type": group[0]["event_type"],
            "counterparty_key": next(
                (r["counterparty_key"] for r in group if r["counterparty_key"]), None
            ),
            "state": shown.current,
            "confirmed_state": confirmed.current,
            "confirmed_terms": terms,
            "terms_provenance": provenance,
            "timeline": [
                {
                    "event_id": r["id"],
                    "state": r["event_state"],
                    "status": r["status"],
                    "date": r["effective_at"] or r["announced_at"],
                    "known_at": r["known_at"],
                    "evidence_id": r["evidence_id"],
                    "source": r["source"],
                    "source_url": r["source_url"],
                    "evidence_tier": r["evidence_tier"],
                }
                for r in group
            ],
            "ignored": [{"event_id": e.id, "reason": why} for e, why in shown.ignored],
            "pro_forma": None,
        }
        if with_pro_forma and terms:
            if base is None:
                base = await read_base_capital(pool, ticker, as_of)
            item["pro_forma"] = pro_forma(terms, base)
            item["pro_forma_basis"] = "confirmed terms only"
        elif with_pro_forma:
            item["pro_forma_unavailable"] = "no confirmed terms"
        out.append(item)
    return out
