"""P8.3: portfolio look-through to confirmed issuer relationships, deal impacts and customers.

A held company's exposure is its direct weight plus the weight reached through held funds (the
fund's weight times the fund's reported share of that company). Every figure is a share of the whole
portfolio's market value, and that denominator is returned beside each total. A total is made only
from what is known: a relationship with no stated share keeps an unknown weight, a fund line with
no percentage stays unknown, and fund holdings are as old as the fund's own report. Nothing here
estimates a missing figure.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import asyncpg

from steward.disclosure import coverage_report as cr
from steward.disclosure import deal_read

type Pool = asyncpg.Pool | asyncpg.Connection

METHOD_VERSION = "look_through.v1"
#: a customer at or above this share of revenue is a concentrated customer
CONCENTRATION_SHARE = Decimal("0.10")
HUNDRED = Decimal(100)


@dataclass(frozen=True)
class FundLine:
    fund_ticker: str
    held_ticker: str | None
    issuer_name: str | None
    pct: Decimal | None
    """The fund's share in this company as a fraction (0.05 = 5 %); None when not reported."""
    report_period_end: dt.date
    known_at: dt.datetime
    note: str | None = None


@dataclass(frozen=True)
class Relationship:
    ticker: str
    relationship: str
    counterparty_key: str | None
    counterparty_name: str | None
    anonymous: bool
    share: Decimal | None
    share_basis: str | None
    fiscal_period: str | None
    known_at: dt.datetime
    source_url: str | None = None
    denominator_kind: str | None = None
    period_end: dt.date | None = None
    claim_label: str | None = None

    @property
    def revenue_share(self) -> bool:
        """The share is a share of the reporter's total revenue. A share of segment revenue,
        purchases or receivables is a different quantity and never meets the concentration bar."""
        if self.denominator_kind is not None:
            return self.denominator_kind == "total_revenue"
        return self.share_basis == "issuer_revenue"


def latest_relationships(rels: Sequence[Relationship]) -> list[Relationship]:
    """Keep only the latest fiscal period per (reporter, relationship, counterparty).

    An older year's customer share must not count as today's. A counterparty with no key, name or
    label cannot be matched across periods, so each such row stays as it is."""
    groups: dict[tuple[str, str, str], list[Relationship]] = defaultdict(list)
    out: list[Relationship] = []
    for r in rels:
        ident = r.counterparty_key or r.counterparty_name or r.claim_label
        if ident is None:
            out.append(r)
        else:
            groups[(r.ticker, r.relationship, ident.lower())].append(r)
    for rows in groups.values():
        latest = max((r.period_end or dt.date.min, r.fiscal_period or "") for r in rows)
        out.extend(
            r for r in rows if (r.period_end or dt.date.min, r.fiscal_period or "") == latest
        )
    out.sort(key=lambda r: (r.ticker, r.relationship, r.known_at))
    return out


def assign_fund_lines(rows: Sequence[Mapping[str, Any]], held: set[str]) -> list[FundLine]:
    """One FundLine per fund holding line, however many held instruments share the issuer.

    `rows` are LATEST_FUND_SQL rows: one per (fund holding line, candidate instrument). A line
    whose ISIN equals exactly one candidate's ISIN goes to that candidate; otherwise it goes to the
    lowest ticker among the held candidates (or among all, if none is held) with a note. The line
    is never counted twice."""
    lines: dict[tuple[Any, Any], list[Mapping[str, Any]]] = {}
    for r in rows:
        lines.setdefault((r["accession"], r["row_index"]), []).append(r)
    out: list[FundLine] = []
    for cands in lines.values():
        first = cands[0]
        tickers = sorted({c["held_ticker"] for c in cands if c["held_ticker"]})
        pool = [t for t in tickers if t in held] or tickers
        chosen: str | None = None
        note: str | None = None
        if len(pool) == 1:
            chosen = pool[0]
        elif pool:
            line_isin = first.get("line_isin")
            exact = sorted(
                {
                    c["held_ticker"]
                    for c in cands
                    if c["held_ticker"] in pool
                    and line_isin
                    and c.get("instrument_isin") == line_isin
                }
            )
            if len(exact) == 1:
                chosen = exact[0]
            else:
                chosen = pool[0]
                note = f"shared by {len(pool)} held share classes; not split"
        out.append(
            FundLine(
                first["fund_ticker"],
                chosen,
                first["issuer_name"],
                None if first["pct_value"] is None else Decimal(first["pct_value"]) / HUNDRED,
                first["report_period_end"],
                first["known_at"],
                note,
            )
        )
    return out


def _s(v: Decimal | None) -> str | None:
    return None if v is None else str(v)


def _aware(value: dt.datetime | None) -> dt.datetime:
    now = value or dt.datetime.now(dt.UTC)
    return now if now.tzinfo else now.replace(tzinfo=dt.UTC)


def build(
    holdings: Sequence[cr.Holding],
    fund_lines: Sequence[FundLine],
    relationships: Sequence[Relationship],
    deal_rows: Sequence[Mapping[str, Any]],
    as_of: dt.datetime,
    snapshot_date: dt.date | None,
) -> dict[str, Any]:
    total = sum((h.value for h in holdings), Decimal(0))
    direct = {h.ticker: h.value / total for h in holdings} if total > 0 else {}

    # fund look-through ----------------------------------------------------------------------
    fund_weight = dict(direct)
    reached: dict[str, list[dict[str, Any]]] = defaultdict(list)
    fund_view: dict[str, dict[str, Any]] = {}
    for ln in fund_lines:
        view = fund_view.setdefault(
            ln.fund_ticker,
            {
                "fund_ticker": ln.fund_ticker,
                "weight": _s(fund_weight.get(ln.fund_ticker)),
                "report_period_end": ln.report_period_end,
                "known_at": ln.known_at,
                "lag_days": (as_of.date() - ln.report_period_end).days,
                "mapped_pct": Decimal(0),
                "unmapped_pct": Decimal(0),
                "unknown_lines": 0,
                "notes": [],
            },
        )
        if ln.note and ln.note not in view["notes"]:
            view["notes"].append(ln.note)
        if ln.pct is None:
            view["unknown_lines"] += 1
            continue
        if ln.held_ticker is None:
            view["unmapped_pct"] += ln.pct
            continue
        view["mapped_pct"] += ln.pct
        w_fund = fund_weight.get(ln.fund_ticker)
        contribution = None if w_fund is None else w_fund * ln.pct
        reached[ln.held_ticker].append(
            {
                "fund_ticker": ln.fund_ticker,
                "fund_weight": _s(w_fund),
                "fund_pct": str(ln.pct),
                "contribution": _s(contribution),
                "report_period_end": ln.report_period_end,
                "lag_days": view["lag_days"],
                "note": ln.note,
            }
        )
    exposure: dict[str, dict[str, Any]] = {}
    for ticker in sorted(set(direct) | set(reached)):
        d = direct.get(ticker, Decimal(0))
        via = sum(
            (Decimal(c["contribution"]) for c in reached.get(ticker, []) if c["contribution"]),
            Decimal(0),
        )
        exposure[ticker] = {
            "ticker": ticker,
            "direct_weight": str(d),
            "held_directly": ticker in direct,
            "fund_weight": str(via),
            "total_weight": str(d + via),
            "funds": reached.get(ticker, []),
        }
    weight_of = {t: Decimal(e["total_weight"]) for t, e in exposure.items()}

    # relationships --------------------------------------------------------------------------
    rel_rows: list[dict[str, Any]] = []
    by_ticker: dict[str, list[Relationship]] = defaultdict(list)
    relationships = latest_relationships(relationships)
    for r in relationships:
        if r.ticker in weight_of:
            by_ticker[r.ticker].append(r)
    for ticker, rels in sorted(by_ticker.items()):
        for r in rels:
            w = weight_of[ticker]
            rel_rows.append(
                {
                    "ticker": ticker,
                    "relationship": r.relationship,
                    "counterparty_key": r.counterparty_key,
                    "counterparty_name": None if r.anonymous else r.counterparty_name,
                    "anonymous": r.anonymous,
                    "share": _s(r.share),
                    "share_basis": r.share_basis,
                    "denominator_kind": r.denominator_kind,
                    "fiscal_period": r.fiscal_period,
                    "known_at": r.known_at,
                    "source_url": r.source_url,
                    "holding_weight": str(w),
                    "weighted_share": _s(None if r.share is None else w * r.share),
                    "weight_state": "unknown" if r.share is None else "reported",
                    "concentrated_customer": (
                        r.relationship == "customer"
                        and r.revenue_share
                        and r.share is not None
                        and r.share >= CONCENTRATION_SHARE
                    ),
                }
            )
    rel_tickers = set(by_ticker)
    known_share_tickers = {t for t, rs in by_ticker.items() if any(r.share is not None for r in rs)}
    unknown_only = rel_tickers - known_share_tickers
    concentrated = {r["ticker"] for r in rel_rows if r["concentrated_customer"]}
    customer_known = {
        t
        for t, rs in by_ticker.items()
        if any(r.relationship == "customer" and r.revenue_share and r.share is not None for r in rs)
    }

    # deals ----------------------------------------------------------------------------------
    deals_out = [
        {
            "ticker": d["ticker"],
            "deal_id": d["deal_id"],
            "title": d["title"],
            "deal_type": d["deal_type"],
            "state": d["state"],
            "direct_weight": str(d["weight"]),
            "holding_weight": str(weight_of.get(d["ticker"], Decimal(str(d["weight"])))),
        }
        for d in deal_rows
    ]
    deal_tickers = {d["ticker"] for d in deals_out}

    def tw(tickers: set[str]) -> str:
        return str(sum((weight_of.get(t, Decimal(0)) for t in tickers), Decimal(0)))

    largest = {
        t: weight_of[t]
        * max(
            r.share
            for r in rs
            if r.relationship == "customer" and r.revenue_share and r.share is not None
        )
        for t, rs in by_ticker.items()
        if t in customer_known
    }
    dates: dict[str, set[str]] = defaultdict(set)
    if snapshot_date is not None:
        dates["position_snapshot"].add(snapshot_date.isoformat())
    for ln in fund_lines:
        dates["fund_report"].add(ln.report_period_end.isoformat())
    for r in relationships:
        if r.fiscal_period:
            dates["relationship_period"].add(r.fiscal_period)
    as_of_dates = [{"kind": k, "dates": sorted(v)} for k, v in sorted(dates.items())]
    distinct = {d for v in dates.values() for d in v}
    lags = [int(v["lag_days"]) for v in fund_view.values()]
    unmapped = sum(
        (
            Decimal(v["weight"]) * v["unmapped_pct"]
            for v in fund_view.values()
            if v["weight"] is not None
        ),
        Decimal(0),
    )
    return {
        "as_of": as_of,
        "snapshot_date": snapshot_date,
        "denominator_value": str(total),
        "holdings": len(holdings),
        "exposures": [
            exposure[t]
            for t in exposure
            if exposure[t]["funds"] or t in rel_tickers or t in deal_tickers
        ],
        "relationships": rel_rows,
        "deals": deals_out,
        "funds": [
            {**v, "mapped_pct": str(v["mapped_pct"]), "unmapped_pct": str(v["unmapped_pct"])}
            for v in fund_view.values()
        ],
        "totals": {
            "denominator_weight": "1" if total > 0 else None,
            "relationship_covered_weight": tw(rel_tickers),
            "relationship_covered_holdings": len(rel_tickers),
            "unknown_weight_holdings": sorted(unknown_only),
            "unknown_weight": tw(unknown_only),
            "customer_known_weight": tw(customer_known),
            "concentrated_customer_weight": tw(concentrated),
            "concentrated_customer_holdings": sorted(concentrated),
            "largest_customer_weighted": str(sum(largest.values(), Decimal(0))),
            "deal_exposed_weight": tw(deal_tickers),
            "deal_exposed_holdings": sorted(deal_tickers),
            "fund_look_through_weight": str(
                sum((Decimal(e["fund_weight"]) for e in exposure.values()), Decimal(0))
            ),
            "unmapped_fund_weight": str(unmapped),
        },
        "as_of_dates": as_of_dates,
        "mixed_as_of": len(distinct) > 1,
        "max_fund_lag_days": max(lags) if lags else None,
        "concentration_share": str(CONCENTRATION_SHARE),
        "method_version": METHOD_VERSION,
        "notes": [
            "Every total is a share of the whole portfolio market value; "
            "the denominator is returned.",
            "A relationship with no stated share has an unknown weight "
            "and is not in a weighted total.",
            "Fund holdings are as old as the fund's report; the lag is shown per fund.",
            "Fund lines not matched to a company by identifier are counted as unmapped, "
            "not guessed.",
        ],
    }


# ------------------------------------------------------------------------------------- database

LATEST_FUND_SQL = """
select f.matched_ticker as fund_ticker, n.t212_ticker as held_ticker, h.issuer_name,
       h.pct_value, f.report_period_end, f.known_at, f.accession, h.row_index,
       h.isin as line_isin, n.isin as instrument_isin
  from (select distinct on (matched_ticker)
               accession, matched_ticker, report_period_end, known_at
          from public.nport_filing
         where reconcile_status = 'confirmed' and matched_ticker = any($1::text[])
           and known_at <= $2
         order by matched_ticker, report_period_end desc, filed desc) f
  join public.nport_holding h on h.accession = f.accession and not h.is_derivative
  left join public.issuer i on i.id = h.issuer_id
  left join public.instrument n on n.cik = i.cik
"""

REL_SQL = """
select ticker, relationship, counterparty_key, counterparty_name, anonymous_counterparty,
       reported_share, share_basis, denominator_kind, period_end, claim_label, fiscal_period,
       known_at, source_url
  from public.relationship_disclosure
 where ticker = any($1::text[]) and status = 'confirmed' and known_at <= $2
   and relationship in ('customer', 'supplier') order by ticker, id
"""


async def look_through_view(pool: Pool, as_of: dt.datetime | None = None) -> dict[str, Any]:
    when = _aware(as_of)
    holdings, snapshot = await cr.load_holdings(pool, when)
    tickers = sorted(h.ticker for h in holdings)
    fund_rows = await pool.fetch(LATEST_FUND_SQL, tickers, when) if tickers else []
    lines = assign_fund_lines(fund_rows, set(tickers))
    reach = sorted({ln.held_ticker for ln in lines if ln.held_ticker} | set(tickers))
    rels = [
        Relationship(
            r["ticker"],
            r["relationship"],
            r["counterparty_key"],
            r["counterparty_name"],
            bool(r["anonymous_counterparty"]),
            None if r["reported_share"] is None else Decimal(r["reported_share"]),
            r["share_basis"],
            r["fiscal_period"],
            r["known_at"],
            r["source_url"],
            r["denominator_kind"],
            r["period_end"],
            r["claim_label"],
        )
        for r in (await pool.fetch(REL_SQL, reach, when) if reach else [])
    ]
    deals = (await deal_read.portfolio_view(pool, when))["rows"]
    return build(holdings, lines, rels, deals, when, snapshot)
