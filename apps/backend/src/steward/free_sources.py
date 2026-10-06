"""Write gates and deterministic analytics for free-source phase 3-5 records."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import asyncpg
import httpx

from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.forward_consensus import observation_key
from steward.graph.edges import upsert_edge
from steward.graph.patents import store_patent_family  # noqa: F401 - re-exported
from steward.market_analysis import UNHANDLED_KINDS, Action, PricePoint, calculate_return
from steward.reference.calendar import expected_sessions
from steward.reference.price_series import series_mic
from steward.sources.base import AccessResult, AccessState, Priority
from steward.sources.free_market import (
    FreeSourceError,
    alpha_statement,
    finnhub_earnings_surprises,
    finnhub_peers,
    parse_alpha_statement_facts,
)
from steward.sources.governor import Budget, PostgresRequestGovernor, QuotaExceeded
from steward.sources.store import record_access

ForwardKind = Literal[
    "company_guidance", "owner_scenario", "published_analyst_consensus", "event_calendar_estimate"
]
EdgeStatus = Literal["suggested", "confirmed", "rejected", "disputed"]


async def collect_finnhub_research(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    *,
    ticker: str,
    symbol: str,
    api_key: str,
) -> dict[str, int]:
    """Collect only free-gated peer candidates and historical EPS event estimates."""
    gates = await pool.fetch(
        """select endpoint,state,hard_blocked from public.provider_access
           where provider='finnhub' and endpoint in ('peers','earnings_surprises')"""
    )
    by_endpoint = {row["endpoint"]: row for row in gates}
    if len(by_endpoint) != 2 or any(
        by_endpoint[name]["state"] != AccessState.AVAILABLE.value
        or by_endpoint[name]["hard_blocked"]
        for name in ("peers", "earnings_surprises")
    ):
        raise RuntimeError("Finnhub peer and earnings access gates must be available")
    governor = PostgresRequestGovernor(pool)
    empty_budget = Budget()
    results = {"peers": 0, "earnings": 0}
    for endpoint in ("peers", "earnings_surprises"):
        try:
            await governor.reserve("finnhub", endpoint, Decimal(1), empty_budget)
            if endpoint == "peers":
                peer_symbols = await finnhub_peers(http, api_key, symbol)
                async with pool.acquire() as conn, conn.transaction():
                    for peer in peer_symbols:
                        # Insert-once: a vendor re-sync never moves `known_at`, never changes
                        # a status the owner decided and never re-suggests a rejected peer.
                        await conn.execute(
                            """insert into public.peer_set
                               (ticker,peer_ticker,basis,status,source,detail,status_history)
                               values($1,$2,'Finnhub peer candidate','suggested','finnhub',
                                      '{"identity":"provider symbol; issuer mapping unverified"}'
                                      ::jsonb,
                                      jsonb_build_array(jsonb_build_object(
                                        'status','suggested','at',now())))
                               on conflict(ticker,peer_ticker,basis,source) do update set
                                 detail=peer_set.detail || excluded.detail""",
                            ticker,
                            peer,
                        )
                results["peers"] = len(peer_symbols)
            else:
                earnings = await finnhub_earnings_surprises(http, api_key, symbol)
                for row in earnings:
                    await store_forward_value(
                        pool,
                        ForwardValue(
                            ticker=ticker,
                            kind="event_calendar_estimate",
                            provider="finnhub",
                            metric="earnings_per_share",
                            basis="Finnhub estimate; actual and surprise retained in raw evidence",
                            fiscal_period=f"FY{row['period'].year}-{row['period'].month:02d}",
                            period_end=row["period"],
                            value=row["estimate"],
                            source_url=(
                                f"https://finnhub.io/api/v1/stock/earnings?symbol={symbol}"
                            ),
                            raw={
                                "actual": row["actual"],
                                "estimate": row["estimate"],
                                "surprise": row["surprise"],
                                "surprise_percent": row["surprise_percent"],
                                "period": row["period"],
                            },
                        ),
                    )
                results["earnings"] = len(earnings)
            await record_access(
                pool,
                "finnhub",
                endpoint,
                AccessResult(AccessState.AVAILABLE, "free endpoint returned data"),
                credits=1,
            )
        except QuotaExceeded:
            await record_access(
                pool,
                "finnhub",
                endpoint,
                AccessResult(AccessState.QUOTA_EXHAUSTED, "provider budget exhausted"),
            )
            raise
        except (FreeSourceError, httpx.HTTPError) as exc:
            status_code = exc.status_code if isinstance(exc, FreeSourceError) else None
            text = str(exc).casefold()
            state = (
                AccessState.PAID_ONLY
                if status_code in {401, 403} and "premium" in text
                else AccessState.ACCESS_UNVERIFIED
                if status_code in {401, 403}
                else AccessState.QUOTA_EXHAUSTED
                if status_code == 429
                else AccessState.TEMPORARILY_FAILED
            )
            await record_access(
                pool,
                "finnhub",
                endpoint,
                AccessResult(state, "Finnhub endpoint request failed"),
                status_code=status_code,
            )
            if state in {AccessState.PAID_ONLY, AccessState.ACCESS_UNVERIFIED}:
                await governor.hard_block("finnhub", endpoint)
            raise
    return results


async def collect_alpha_vantage_statement(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    *,
    ticker: str,
    symbol: str,
    function: Literal["INCOME_STATEMENT", "BALANCE_SHEET", "CASH_FLOW"],
    api_key: str,
    priority: Priority = Priority.NORMAL,
    reserve_credits: int = 0,
) -> dict[str, int | str]:
    """Fetch one gated Alpha Vantage statement, preserving revisions and provenance.

    Scheduled callers pass `Priority.BACKGROUND` and the provider's reserve so an
    owner-triggered request can still spend the credits they leave."""
    gates = await pool.fetch(
        """select endpoint,state,hard_blocked from public.provider_access
           where provider='alpha_vantage' and endpoint in ('account_free','financial_statements')"""
    )
    by_endpoint = {row["endpoint"]: row for row in gates}
    if len(by_endpoint) != 2 or any(
        by_endpoint[name]["state"] != AccessState.AVAILABLE.value
        or by_endpoint[name]["hard_blocked"]
        for name in ("account_free", "financial_statements")
    ):
        raise RuntimeError("Alpha Vantage account and statement access gates must be available")
    governor = PostgresRequestGovernor(pool)
    budget = Budget(daily=Decimal(25), reserve=Decimal(reserve_credits))
    try:
        await governor.reserve(
            "alpha_vantage", "account_free", Decimal(1), budget, priority=priority
        )
        await governor.reserve(
            "alpha_vantage", "financial_statements", Decimal(1), budget, priority=priority
        )
        payload = await alpha_statement(http, api_key, symbol, function)
        statement = {
            "INCOME_STATEMENT": "income",
            "BALANCE_SHEET": "balance_sheet",
            "CASH_FLOW": "cash_flow",
        }[function]
        facts = parse_alpha_statement_facts(payload, statement=statement)
    except QuotaExceeded:
        await record_access(
            pool,
            "alpha_vantage",
            "financial_statements",
            AccessResult(AccessState.QUOTA_EXHAUSTED, "shared free request budget exhausted"),
        )
        raise
    except (FreeSourceError, httpx.HTTPError) as exc:
        message = str(exc).casefold()
        status_code = exc.status_code if isinstance(exc, FreeSourceError) else None
        state = (
            AccessState.PAID_ONLY
            if status_code == 403
            and any(word in message for word in ("premium", "paid tier", "upgrade"))
            else AccessState.QUOTA_EXHAUSTED
            if status_code == 429
            or any(word in message for word in ("quota", "frequency", "rate limit"))
            else AccessState.ACCESS_UNVERIFIED
            if status_code in {401, 403}
            or any(word in message for word in ("api key", "unauthorized", "denied"))
            else AccessState.TEMPORARILY_FAILED
        )
        await record_access(
            pool,
            "alpha_vantage",
            "financial_statements",
            AccessResult(state, "Alpha Vantage statement request failed"),
            status_code=status_code,
        )
        if state in {AccessState.PAID_ONLY, AccessState.ACCESS_UNVERIFIED}:
            await governor.hard_block("alpha_vantage", "financial_statements")
        raise
    except Exception:
        raise

    statement_url = "https://www.alphavantage.co/query?function=" + function + "&symbol=" + symbol
    inserted = 0
    async with pool.acquire() as conn, conn.transaction():
        for fact in facts:
            accession = f"{function}:{fact['period_end'].isoformat()}"
            dims = json.dumps({"members": []})
            prior = await conn.fetchrow(
                """select id,value,revision from public.financial_statement_fact
                   where ticker=$1 and source='alpha_vantage' and accession=$2
                     and concept=$3 and unit=$4 and dimensions=$5::jsonb
                   order by revision desc,known_at desc limit 1""",
                ticker,
                accession,
                fact["concept"],
                fact["unit"],
                dims,
            )
            if prior is not None and Decimal(prior["value"]) == fact["value"]:
                continue
            published_at = None
            try:
                published_at = dt.datetime.fromisoformat(str(fact["published_at"])).replace(
                    tzinfo=dt.UTC
                )
            except (TypeError, ValueError):
                pass
            await conn.execute(
                """insert into public.financial_statement_fact
                   (ticker,accession,form,filing_url,concept,label,statement,period_end,
                    fiscal_period,basis,value,unit,currency,dimensions,source,source_url,
                    published_at,known_at,revision,amends_fact_id,validation)
                   values($1,$2,'Alpha Vantage Annual',$3,$4,$5,$6,$7,$8,'provider_reported',
                          $9,$10,$11,$12::jsonb,'alpha_vantage',$3,$13,now(),$14,$15,
                          '{"reviewed":false}'::jsonb)""",
                ticker,
                accession,
                statement_url,
                fact["concept"],
                fact["label"],
                fact["statement"],
                fact["period_end"],
                str(fact["period_end"].year),
                fact["value"],
                fact["unit"],
                fact["currency"],
                dims,
                published_at,
                (prior["revision"] + 1) if prior else 0,
                prior["id"] if prior else None,
            )
            inserted += 1
    await record_access(
        pool,
        "alpha_vantage",
        "financial_statements",
        AccessResult(AccessState.AVAILABLE, f"{len(facts)} annual statement fields parsed"),
        credits=1,
        response_bytes=len(json.dumps(payload)),
    )
    return {"facts": inserted, "fields": len(facts), "symbol": symbol}


@dataclass(frozen=True, slots=True)
class ForwardValue:
    ticker: str
    kind: ForwardKind
    metric: str
    fiscal_period: str
    source_url: str
    value: Decimal | None
    provider: str | None = None
    basis: str | None = None
    period_end: dt.date | None = None
    currency: str | None = None
    low: Decimal | None = None
    high: Decimal | None = None
    analyst_count: int | None = None
    vintage: str | None = None
    published_at: dt.datetime | None = None
    known_at: dt.datetime | None = None
    evidence_id: int | None = None
    raw: dict[str, object] | None = None


async def store_forward_value(pool: asyncpg.Pool, item: ForwardValue) -> int:
    """Append a point-in-time forward value; never substitute null consensus with a scenario.

    The same figures from the same provider, currency, basis and vintage are one observation:
    storing them again returns the existing row (and leaves its `known_at`); a changed figure
    is a new row."""
    if item.value is None and item.low is None and item.high is None:
        raise ValueError("a forward observation requires a value or range")
    if item.kind == "published_analyst_consensus" and item.analyst_count is None:
        raise ValueError("published consensus requires its analyst count (zero is valid)")
    if item.low is not None and item.high is not None and item.low > item.high:
        raise ValueError("estimate low exceeds estimate high")
    key = observation_key(
        kind=item.kind,
        provider=item.provider,
        metric=item.metric,
        fiscal_period=item.fiscal_period,
        basis=item.basis,
        currency=item.currency,
        vintage=item.vintage,
        period_end=item.period_end,
        value=item.value,
        low=item.low,
        high=item.high,
        analyst_count=item.analyst_count,
    )
    new_id: int | None = await pool.fetchval(
        """insert into public.forward_observation
           (ticker,kind,provider,metric,basis,fiscal_period,period_end,currency,value,low,high,
            analyst_count,vintage,published_at,known_at,source_url,evidence_id,raw,observation_key)
           values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18::jsonb,$19)
           on conflict (ticker,observation_key) do nothing
           returning id""",
        item.ticker,
        item.kind,
        item.provider,
        item.metric,
        item.basis,
        item.fiscal_period,
        item.period_end,
        item.currency,
        item.value,
        item.low,
        item.high,
        item.analyst_count,
        item.vintage,
        item.published_at,
        item.known_at or dt.datetime.now(dt.UTC),
        item.source_url,
        item.evidence_id,
        json.dumps(item.raw or {}, default=str),
        key,
    )
    if new_id is None:
        new_id = await pool.fetchval(
            "select id from public.forward_observation where ticker=$1 and observation_key=$2",
            item.ticker,
            key,
        )
    assert new_id is not None
    return new_id


PEER_STATUSES = ("suggested", "confirmed", "rejected")


async def upsert_peer(
    pool: asyncpg.Pool,
    *,
    ticker: str,
    peer_ticker: str,
    basis: str,
    status: str,
    source: str,
    evidence_id: int | None = None,
    valid_from: dt.date | None = None,
    valid_to: dt.date | None = None,
    detail: dict[str, object] | None = None,
) -> int:
    """Record a peer. A new row starts with `status` (its first history entry); an existing
    row keeps its `known_at` and its status: re-posting a peer never resets either. Changing a
    decided status is `set_peer_status`."""
    row_id: int = await pool.fetchval(
        """insert into public.peer_set
           (ticker,peer_ticker,basis,status,evidence_id,source,valid_from,valid_to,detail,
            status_history)
           values($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,
                  jsonb_build_array(jsonb_build_object('status',$4::text,'at',now())))
           on conflict(ticker,peer_ticker,basis,source) do update set
             evidence_id=coalesce(excluded.evidence_id,peer_set.evidence_id),
             valid_from=coalesce(excluded.valid_from,peer_set.valid_from),
             valid_to=coalesce(excluded.valid_to,peer_set.valid_to),
             detail=peer_set.detail || excluded.detail
           returning id""",
        ticker,
        peer_ticker,
        basis,
        status,
        evidence_id,
        source,
        valid_from,
        valid_to,
        json.dumps(detail or {}, default=str),
    )
    return row_id


async def set_peer_status(
    pool: asyncpg.Pool, peer_id: int, status: str, reason: str | None = None
) -> dict[str, object] | None:
    """The owner's decision on a peer, in either direction (confirmed -> rejected or back to
    suggested included). The change is appended to `status_history` with its time, so an
    as-of read still sees the status that was in force. Returns None for an unknown id."""
    if status not in PEER_STATUSES:
        raise ValueError(f"status must be one of {', '.join(PEER_STATUSES)}")
    row = await pool.fetchrow(
        """update public.peer_set set status=$2,
             status_history=status_history || jsonb_build_array(jsonb_build_object(
               'status',$2::text,'at',now(),'reason',$3::text)),
             detail=case when $3::text is null then detail
                         else detail || jsonb_build_object('decision_reason',$3::text) end
           where id=$1 and status is distinct from $2
           returning id,ticker,peer_ticker,status""",
        peer_id,
        status,
        reason,
    )
    if row is None:
        row = await pool.fetchrow(
            "select id,ticker,peer_ticker,status from public.peer_set where id=$1", peer_id
        )
    return dict(row) if row is not None else None


# The status of a peer_set row `alias` at the cutoff `param` (null: now), from its history.
def peer_status_as_of(alias: str, param: str) -> str:
    return (
        f"coalesce((select h->>'status' from jsonb_array_elements({alias}.status_history) h"  # noqa: S608
        f" where {param}::timestamptz is null or (h->>'at')::timestamptz <= {param}::timestamptz"
        f" order by (h->>'at')::timestamptz desc limit 1), {alias}.status)"
    )


async def store_company_edge(
    pool: asyncpg.Pool,
    *,
    subject_key: str,
    object_key: str,
    object_name: str,
    edge_type: str,
    direction: str,
    source: str,
    source_url: str,
    evidence_tier: str | None = None,
    status: EdgeStatus = "suggested",
    valid_from: dt.date | None = None,
    valid_to: dt.date | None = None,
    evidence_id: int | None = None,
    passage: str | None = None,
    detail: dict[str, object] | None = None,
) -> int:
    """Upsert a candidate edge (see `steward.graph.edges.upsert_edge`).

    `evidence_tier` is accepted for compatibility and ignored: the server derives the tier
    from the linked document's source class (or the writing source), never from the caller.
    A decided row (confirmed, rejected, disputed) is never changed by a re-sync."""
    del evidence_tier
    return await upsert_edge(
        pool,
        subject_key=subject_key,
        object_key=object_key,
        object_name=object_name,
        edge_type=edge_type,
        direction=direction,
        source=source,
        source_url=source_url,
        status=status,
        valid_from=valid_from,
        valid_to=valid_to,
        evidence_id=evidence_id,
        passage=passage,
        detail=detail,
    )


RETURN_CALC_VERSION = 2  # 2: session-calendar coverage, deduplicated and split-adjusted actions


@dataclass(frozen=True, slots=True)
class ReturnRow:
    basis: str  # price_raw | price_adjusted | total_return
    value: Decimal | None
    coverage: Decimal
    observed: int
    missing_actions: list[str]
    details: dict[str, object]


@dataclass(frozen=True, slots=True)
class ReturnComputation:
    output: dict[str, object]
    rows: list[ReturnRow]
    expected: int
    inputs_digest: str


@dataclass(frozen=True, slots=True)
class LoadedActions:
    actions: list[Action]
    flags: list[str]


async def load_confirmed_actions(
    pool: asyncpg.Pool,
    *,
    symbols: list[str],
    listing_id: int | None,
    start: dt.date,
    end: dt.date,
    ads_ratio: Decimal | None = None,
) -> LoadedActions:
    """Confirmed corporate actions of a listing in [start, end]: those recorded under any of its
    provider symbols or its listing id. Cash dividends carry their currency (incl. GBp), the ADS
    multiplier when the owner recorded them per ordinary share (`raw.per = "ordinary_share"`),
    and whether the amount is already split-adjusted (`raw.amount_basis`)."""
    rows = await pool.fetch(
        """select action_type,ex_date,ratio,cash_amount,currency,source,raw
           from public.corporate_action
           where status='confirmed' and ex_date between $2 and $3
             and (symbol = any($1::text[]) or ($4::bigint is not null and listing_id=$4))
           order by ex_date,id""",
        symbols,
        start,
        end,
        listing_id,
    )
    actions: list[Action] = []
    for r in rows:
        raw = r["raw"]
        if isinstance(raw, str):
            raw = json.loads(raw)
        raw = raw if isinstance(raw, dict) else {}
        per_ordinary = raw.get("per") == "ordinary_share" and ads_ratio is not None
        actions.append(
            Action(
                kind=r["action_type"],
                ex_date=r["ex_date"],
                ratio=Decimal(r["ratio"]) if r["ratio"] is not None else None,
                cash=Decimal(r["cash_amount"]) if r["cash_amount"] is not None else None,
                currency=r["currency"],
                source=r["source"],
                per_unit_multiplier=ads_ratio if per_ordinary and ads_ratio else Decimal(1),
                amount_basis="split_adjusted"
                if raw.get("amount_basis") == "split_adjusted"
                else "as_declared",
            )
        )
    flags = [
        f"{a.kind} on {a.ex_date.isoformat()} is not valued"
        for a in actions
        if a.kind in UNHANDLED_KINDS
    ]
    return LoadedActions(actions, flags)


def _digest(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, default=str, sort_keys=True).encode()).hexdigest()


async def compute_returns(
    pool: asyncpg.Pool,
    *,
    symbol: str,
    start: dt.date,
    end: dt.date,
    source: str,
    currency: str,
    actions_complete: bool,
    mic: str | None = None,
    listing_id: int | None = None,
    action_symbols: list[str] | None = None,
    ads_ratio: Decimal | None = None,
    withholding_rate: Decimal = Decimal(0),
    fx_to_quote: dict[dt.date, Decimal] | None = None,
) -> ReturnComputation:
    """The three return artifacts of one stored series, computed without writing anything.

    Coverage is observed sessions over the sessions the exchange actually held in the window
    (confirmed `exchange_session` rows, else weekdays less configured closures) and is never
    raised by the bars that happen to exist."""
    bars = await pool.fetch(
        """select coalesce(session_date,date) session,close,currency,bar_type,mic,listing_id
           from public.price_bar
           where symbol=$1 and source=$2 and coalesce(session_date,date) between $3 and $4
             and access_state='available'
           order by coalesce(session_date,date)""",
        symbol,
        source,
        start,
        end,
    )
    adjusted_points = [
        PricePoint(r["session"], Decimal(r["close"]), r["currency"] or currency)
        for r in bars
        if r["bar_type"] == "adjusted"
    ]
    raw_points = [
        PricePoint(r["session"], Decimal(r["close"]), r["currency"] or currency)
        for r in bars
        if r["bar_type"] == "raw"
    ]
    mic = mic or next((r["mic"] for r in bars if r["mic"]), None)
    mic = mic or series_mic(symbol, source)
    listing_id = listing_id or next((r["listing_id"] for r in bars if r["listing_id"]), None)
    jobs = await load_active(pool, "jobs", JobsConfig)
    calendar = (jobs.body if jobs else JobsConfig()).calendar
    expected = await expected_sessions(pool, calendar, mic, start, end)
    expected_days = set(expected.days)

    def coverage_of(points: list[PricePoint]) -> tuple[Decimal, int, int]:
        inside = sum(1 for p in points if p.day in expected_days)
        outside = len(points) - inside
        ratio = Decimal(inside) / Decimal(expected.count) if expected.count else Decimal(0)
        return min(ratio, Decimal(1)), inside, outside

    loaded = await load_confirmed_actions(
        pool,
        symbols=action_symbols or [symbol],
        listing_id=listing_id,
        start=start,
        end=end,
        ads_ratio=ads_ratio,
    )
    window_flags = loaded.flags
    base: dict[str, object] = {
        "calendar_basis": expected.basis,
        "calendar_mic": mic,
        "expected_session_days": expected.count,
        "method_version": RETURN_CALC_VERSION,
    }

    # price_raw: the closes as stored, no action applied. A split in the window shows as a fall.
    raw_cov, raw_in, raw_out = coverage_of(raw_points)
    raw_result = calculate_return(raw_points, [])
    splits_in_window = [a.ex_date.isoformat() for a in loaded.actions if a.kind == "split"]
    raw_row = ReturnRow(
        "price_raw",
        raw_result.value,
        raw_cov,
        raw_in,
        ([raw_result.reason] if raw_result.reason else []) + window_flags,
        {
            **base,
            "reason": raw_result.reason,
            "bar_type": "raw",
            "unexpected_session_bars": raw_out,
            "warnings": (
                [f"raw closes are not split-adjusted; splits in the window: {splits_in_window}"]
                if splits_in_window
                else []
            ),
        },
    )

    adj_cov, adj_in, adj_out = coverage_of(adjusted_points)
    price_result = calculate_return(adjusted_points, [])
    adj_row = ReturnRow(
        "price_adjusted",
        price_result.value,
        adj_cov,
        adj_in,
        ([price_result.reason] if price_result.reason else []) + window_flags,
        {
            **base,
            "reason": price_result.reason,
            "bar_type": "adjusted",
            "unexpected_session_bars": adj_out,
        },
    )

    total_result = calculate_return(
        raw_points,
        loaded.actions,
        reinvest_dividends=True,
        fx_to_quote=fx_to_quote,
        withholding_rate=withholding_rate,
    )
    total_reason = total_result.reason
    total_value = total_result.value
    if not raw_points:
        total_reason, total_value = "raw close history is unavailable", None
    elif not actions_complete:
        total_reason, total_value = "corporate-action coverage is incomplete", None
    total_row = ReturnRow(
        "total_return",
        total_value,
        raw_cov,
        raw_in,
        ([total_reason] if total_reason else []) + list(total_result.incomplete),
        {
            **base,
            **total_result.details,
            "reason": total_reason,
            "bar_type": "raw",
            "actions": len(loaded.actions),
        },
    )
    digest = _digest(
        [(p.day, p.close, p.currency) for p in raw_points],
        [(p.day, p.close, p.currency) for p in adjusted_points],
        [(a.kind, a.ex_date, a.ratio, a.cash, a.currency, a.source) for a in loaded.actions],
        withholding_rate,
        sorted((fx_to_quote or {}).items()),
        RETURN_CALC_VERSION,
    )
    output: dict[str, object] = {
        "symbol": symbol,
        "source": source,
        "start": start,
        "end": end,
        "currency": currency,
        "coverage": adj_cov,
        "expected_sessions": expected.count,
        "observed_sessions": adj_in,
        "raw_observations": raw_in,
        "calendar_basis": expected.basis,
        "price_raw": raw_row.value,
        "price_raw_reason": raw_result.reason,
        "price_adjusted": adj_row.value,
        "price_adjusted_reason": price_result.reason,
        "total_return": total_row.value,
        "total_return_reason": total_reason,
        "missing_actions": {
            "price_raw": raw_row.missing_actions,
            "price_adjusted": adj_row.missing_actions,
            "total_return": total_row.missing_actions,
        },
        "inputs_digest": digest,
    }
    return ReturnComputation(output, [raw_row, adj_row, total_row], expected.count, digest)


async def store_returns(
    pool: asyncpg.Pool,
    computation: ReturnComputation,
    *,
    symbol: str,
    start: dt.date,
    end: dt.date,
    source: str,
    currency: str,
) -> None:
    """Persist the artifacts of one computation under the current method version. A changed
    method (new RETURN_CALC_VERSION) adds rows beside the earlier answers; the same version
    refreshes its own row."""
    async with pool.acquire() as conn, conn.transaction():
        for row in computation.rows:
            await conn.execute(
                """insert into public.price_return_series
                   (symbol,start_date,end_date,currency,adjustment_basis,source,value,coverage,
                    expected_sessions,observed_sessions,missing_actions,known_at,details,
                    calc_version,inputs_digest)
                   values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,now(),$12::jsonb,$13,$14)
                   on conflict(symbol,start_date,end_date,adjustment_basis,source,calc_version)
                   do update set value=excluded.value,coverage=excluded.coverage,
                     currency=excluded.currency,
                     expected_sessions=excluded.expected_sessions,
                     observed_sessions=excluded.observed_sessions,
                     missing_actions=excluded.missing_actions,known_at=excluded.known_at,
                     details=excluded.details,inputs_digest=excluded.inputs_digest""",
                symbol,
                start,
                end,
                currency,
                row.basis,
                source,
                row.value,
                row.coverage,
                computation.expected,
                row.observed,
                row.missing_actions,
                json.dumps(row.details, default=str),
                RETURN_CALC_VERSION,
                computation.inputs_digest,
            )


async def calculate_and_store_returns(
    pool: asyncpg.Pool,
    *,
    symbol: str,
    start: dt.date,
    end: dt.date,
    source: str,
    currency: str,
    actions_complete: bool,
    mic: str | None = None,
    listing_id: int | None = None,
    action_symbols: list[str] | None = None,
    ads_ratio: Decimal | None = None,
    withholding_rate: Decimal = Decimal(0),
    fx_to_quote: dict[dt.date, Decimal] | None = None,
) -> dict[str, object]:
    """Compute and persist price (raw, adjusted) and total-return artifacts with
    exchange-session coverage. Only the scheduled `returns_refresh` job and explicit owner
    actions call this; the read routes use `compute_returns`."""
    computation = await compute_returns(
        pool,
        symbol=symbol,
        start=start,
        end=end,
        source=source,
        currency=currency,
        actions_complete=actions_complete,
        mic=mic,
        listing_id=listing_id,
        action_symbols=action_symbols,
        ads_ratio=ads_ratio,
        withholding_rate=withholding_rate,
        fx_to_quote=fx_to_quote,
    )
    await store_returns(
        pool, computation, symbol=symbol, start=start, end=end, source=source, currency=currency
    )
    return computation.output
