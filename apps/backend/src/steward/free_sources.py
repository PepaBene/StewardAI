"""Write gates and deterministic analytics for free-source phase 3-5 records."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import asyncpg
import httpx

from steward.market_analysis import Action, PricePoint, calculate_return
from steward.sources.base import AccessResult, AccessState
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
                        await conn.execute(
                            """insert into public.peer_set
                               (ticker,peer_ticker,basis,status,source,detail)
                               values($1,$2,'Finnhub peer candidate','suggested','finnhub',
                                      '{"identity":"provider symbol; issuer mapping "'
                                      '"unverified"}'::jsonb)
                               on conflict(ticker,peer_ticker,basis,source) do update set
                                 known_at=now(),detail=peer_set.detail || excluded.detail,
                                 status=case when peer_set.status in ('confirmed','rejected')
                                             then peer_set.status else 'suggested' end""",
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
) -> dict[str, int | str]:
    """Fetch one gated Alpha Vantage statement, preserving revisions and provenance."""
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
    budget = Budget(daily=Decimal(25))
    try:
        await governor.reserve("alpha_vantage", "account_free", Decimal(1), budget)
        await governor.reserve("alpha_vantage", "financial_statements", Decimal(1), budget)
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
    """Append a point-in-time forward value; never substitute null consensus with a scenario."""
    if item.value is None and item.low is None and item.high is None:
        raise ValueError("a forward observation requires a value or range")
    if item.kind == "published_analyst_consensus" and item.analyst_count is None:
        raise ValueError("published consensus requires its analyst count (zero is valid)")
    if item.low is not None and item.high is not None and item.low > item.high:
        raise ValueError("estimate low exceeds estimate high")
    new_id: int = await pool.fetchval(
        """insert into public.forward_observation
           (ticker,kind,provider,metric,basis,fiscal_period,period_end,currency,value,low,high,
            analyst_count,vintage,published_at,known_at,source_url,evidence_id,raw)
           values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18::jsonb)
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
    )
    return new_id


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
    evidence_tier: str,
    status: EdgeStatus = "suggested",
    valid_from: dt.date | None = None,
    valid_to: dt.date | None = None,
    evidence_id: int | None = None,
    passage: str | None = None,
    detail: dict[str, object] | None = None,
) -> int:
    """Upsert a candidate while preserving owner rejection and requiring evidence to confirm."""
    if valid_from and valid_to and valid_to < valid_from:
        raise ValueError("relationship valid_to precedes valid_from")
    if status == "confirmed" and (
        evidence_tier not in {"primary", "regulatory", "owner_review"}
        or (not evidence_id and not passage)
    ):
        raise ValueError("confirmed edges require primary evidence and a cited passage")
    new_id: int = await pool.fetchval(
        """insert into public.company_graph_edge
           (subject_key,object_key,object_name,edge_type,direction,status,valid_from,valid_to,
            evidence_tier,source,source_url,evidence_id,passage,detail,decided_at)
           values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb,
                  case when $6 in ('confirmed','rejected') then now() else null end)
           on conflict(subject_key,object_key,edge_type,source,source_url) do update set
             object_name=excluded.object_name, direction=excluded.direction,
             valid_from=coalesce(excluded.valid_from,company_graph_edge.valid_from),
             valid_to=coalesce(excluded.valid_to,company_graph_edge.valid_to),
             evidence_tier=excluded.evidence_tier,evidence_id=coalesce(excluded.evidence_id,
               company_graph_edge.evidence_id),passage=coalesce(excluded.passage,company_graph_edge.passage),
             detail=company_graph_edge.detail || excluded.detail,
             status=case when company_graph_edge.status='rejected' then 'rejected'
                         when excluded.status='confirmed' then 'confirmed'
                         when company_graph_edge.status='confirmed' then 'confirmed'
                         else excluded.status end,
             decided_at=case when company_graph_edge.status='rejected'
                               then company_graph_edge.decided_at
                             when excluded.status in ('confirmed','rejected') then now()
                             else company_graph_edge.decided_at end
           returning id""",
        subject_key,
        object_key,
        object_name,
        edge_type,
        direction,
        status,
        valid_from,
        valid_to,
        evidence_tier,
        source,
        source_url,
        evidence_id,
        passage,
        json.dumps(detail or {}, default=str),
    )
    return new_id


async def store_patent_family(
    pool: asyncpg.Pool,
    *,
    family_key: str,
    priority_date: dt.date | None,
    title: str | None,
    cpc_codes: list[str],
    jurisdictions: list[str],
    forward_citations: int,
    backward_citations: int,
    legal_status: str,
    source: str,
    source_url: str,
    ticker: str,
    applicant: str,
    ownership_confidence: Decimal,
    ownership_basis: Literal["direct", "dated_subsidiary_path", "suggested"],
    ownership_valid_from: dt.date | None = None,
    evidence_id: int | None = None,
    raw: dict[str, object] | None = None,
) -> int:
    """Deduplicate a family across publications; only evidenced dated ownership is attributable."""
    if ownership_basis == "dated_subsidiary_path" and ownership_valid_from is None:
        raise ValueError("subsidiary patent attribution needs a dated ownership path")
    async with pool.acquire() as conn, conn.transaction():
        family_id: int = await conn.fetchval(
            """insert into public.patent_family
               (family_key,priority_date,first_publication_date,family_title,cpc_codes,jurisdictions,
                citations_forward,citations_backward,legal_status,source,source_url,known_at,raw)
               values($1,$2,null,$3,$4,$5,$6,$7,$8,$9,$10,now(),$11::jsonb)
               on conflict(family_key) do update set
                 legal_status=case when excluded.legal_status='unknown'
                   then patent_family.legal_status else excluded.legal_status end,
                 citations_forward=greatest(patent_family.citations_forward,excluded.citations_forward),
                 citations_backward=greatest(patent_family.citations_backward,excluded.citations_backward),
                 cpc_codes=(select array_agg(distinct c) from unnest(
                   patent_family.cpc_codes || excluded.cpc_codes) c),
                 jurisdictions=(select array_agg(distinct j) from unnest(
                   patent_family.jurisdictions || excluded.jurisdictions) j)
               returning id""",
            family_key,
            priority_date,
            title,
            cpc_codes,
            jurisdictions,
            forward_citations,
            backward_citations,
            legal_status,
            source,
            source_url,
            json.dumps(raw or {}, default=str),
        )
        await conn.execute(
            """insert into public.patent_family_owner
               (family_id,ticker,applicant,ownership_confidence,ownership_basis,valid_from,evidence_id)
               values($1,$2,$3,$4,$5,$6,$7)
               on conflict(family_id,ticker,applicant) do update set
                 ownership_confidence=greatest(patent_family_owner.ownership_confidence,
                                                excluded.ownership_confidence),
                 ownership_basis=case when excluded.ownership_basis='suggested'
                   then patent_family_owner.ownership_basis else excluded.ownership_basis end,
                 valid_from=coalesce(patent_family_owner.valid_from,excluded.valid_from),
                 evidence_id=coalesce(patent_family_owner.evidence_id,excluded.evidence_id)""",
            family_id,
            ticker,
            applicant,
            ownership_confidence,
            ownership_basis,
            ownership_valid_from,
            evidence_id,
        )
    return family_id


async def calculate_and_store_returns(
    pool: asyncpg.Pool,
    *,
    symbol: str,
    start: dt.date,
    end: dt.date,
    source: str,
    currency: str,
    actions_complete: bool,
) -> dict[str, object]:
    """Persist distinct price and total-return artifacts with exchange-session coverage."""
    bars = await pool.fetch(
        """select coalesce(session_date,date) session,close,currency,bar_type from public.price_bar
           where symbol=$1 and source=$2 and coalesce(session_date,date) between $3 and $4
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
    action_rows = await pool.fetch(
        """select action_type,ex_date,ratio,cash_amount,currency from public.corporate_action
           where symbol=$1 and status='confirmed' and ex_date between $2 and $3""",
        symbol,
        start,
        end,
    )
    actions = [
        Action(
            kind=r["action_type"],
            ex_date=r["ex_date"],
            ratio=Decimal(r["ratio"]) if r["ratio"] is not None else None,
            cash=Decimal(r["cash_amount"]) if r["cash_amount"] is not None else None,
            currency=r["currency"],
        )
        for r in action_rows
        if r["action_type"] in {"split", "cash_dividend", "stock_dividend"}
    ]
    expected = await pool.fetchval(
        """select count(distinct x.session_date) from public.exchange_session x
           join public.price_bar b on b.mic=x.mic
           where b.symbol=$1 and b.source=$2 and x.confirmed and x.state in ('open','half_day')
             and x.session_date between $3 and $4""",
        symbol,
        source,
        start,
        end,
    )
    expected = max(int(expected or 0), len(adjusted_points), len(raw_points))
    coverage = Decimal(len(adjusted_points)) / Decimal(expected) if expected else Decimal(0)
    output: dict[str, object] = {
        "symbol": symbol,
        "source": source,
        "start": start,
        "end": end,
        "currency": currency,
        "coverage": coverage,
        "expected_sessions": expected,
        "observed_sessions": len(adjusted_points),
        "raw_observations": len(raw_points),
    }
    raw_result = calculate_return(raw_points, actions)
    output["price_raw"] = raw_result.value
    await pool.execute(
        """insert into public.price_return_series
           (symbol,start_date,end_date,currency,adjustment_basis,source,value,coverage,
            expected_sessions,observed_sessions,missing_actions,known_at,details)
           values($1,$2,$3,$4,'price_raw',$5,$6,$7,$8,$9,$10,now(),$11::jsonb)
           on conflict(symbol,start_date,end_date,adjustment_basis,source) do update set
             value=excluded.value,coverage=excluded.coverage,
             expected_sessions=excluded.expected_sessions,observed_sessions=excluded.observed_sessions,
             missing_actions=excluded.missing_actions,known_at=excluded.known_at,details=excluded.details""",
        symbol,
        start,
        end,
        currency,
        source,
        raw_result.value,
        Decimal(len(raw_points)) / Decimal(expected) if expected else Decimal(0),
        expected,
        len(raw_points),
        [] if not raw_result.reason else [raw_result.reason],
        json.dumps({"reason": raw_result.reason, "bar_type": "raw"}, default=str),
    )
    price_result = calculate_return(adjusted_points, [])
    price_value = price_result.value
    output["price_adjusted"] = price_value
    price_reason = price_result.reason
    await pool.execute(
        """insert into public.price_return_series
           (symbol,start_date,end_date,currency,adjustment_basis,source,value,coverage,
            expected_sessions,observed_sessions,missing_actions,known_at,details)
           values($1,$2,$3,$4,'price_adjusted',$5,$6,$7,$8,$9,$10,now(),$11::jsonb)
           on conflict(symbol,start_date,end_date,adjustment_basis,source) do update set
             value=excluded.value,coverage=excluded.coverage,
             expected_sessions=excluded.expected_sessions,observed_sessions=excluded.observed_sessions,
             missing_actions=excluded.missing_actions,known_at=excluded.known_at,details=excluded.details""",
        symbol,
        start,
        end,
        currency,
        source,
        price_value,
        coverage,
        expected,
        len(adjusted_points),
        [] if not price_reason else [price_reason],
        json.dumps({"reason": price_reason, "bar_type": "adjusted"}, default=str),
    )
    total_result = calculate_return(raw_points, actions, reinvest_dividends=True)
    total_value = total_result.value if actions_complete and raw_points else None
    total_reason = total_result.reason
    if not raw_points:
        total_reason = "raw close history is unavailable"
    elif not actions_complete:
        total_reason = "corporate-action coverage is incomplete"
    output["total_return"] = total_value
    output["total_return_reason"] = total_reason
    await pool.execute(
        """insert into public.price_return_series
           (symbol,start_date,end_date,currency,adjustment_basis,source,value,coverage,
            expected_sessions,observed_sessions,missing_actions,known_at,details)
           values($1,$2,$3,$4,'total_return',$5,$6,$7,$8,$9,$10,now(),$11::jsonb)
           on conflict(symbol,start_date,end_date,adjustment_basis,source) do update set
             value=excluded.value,coverage=excluded.coverage,
             expected_sessions=excluded.expected_sessions,observed_sessions=excluded.observed_sessions,
             missing_actions=excluded.missing_actions,known_at=excluded.known_at,details=excluded.details""",
        symbol,
        start,
        end,
        currency,
        source,
        total_value,
        Decimal(len(raw_points)) / Decimal(expected) if expected else Decimal(0),
        expected,
        len(raw_points),
        [] if not total_reason else [total_reason],
        json.dumps(
            {"reason": total_reason, "bar_type": "raw", "actions": len(actions)}, default=str
        ),
    )
    output["price_adjusted_reason"] = price_reason
    return output
