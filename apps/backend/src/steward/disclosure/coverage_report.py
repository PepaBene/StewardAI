"""P8.1 / P9.7: what the disclosure datasets cover over the held tickers, and how good they are.

A held ticker is *covered* by a dataset when at least one record known by `as_of` exists for it.
Nothing is ever shown as zero for "missing": an uncovered ticker is counted as uncovered, a rate
with no denominator is null, and a dataset with no sample has no error rate. The counts here say
what is stored, not that nothing else exists, so every cell keeps its own denominator (the held
market value of that jurisdiction) beside the covered part.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import asyncpg

from steward.disclosure.adjudication import GATES

type Pool = asyncpg.Pool | asyncpg.Connection

METHOD_VERSION = "coverage.v1"

#: coverage states a cell can carry; `available` means stored records exist, `stale` means they are
#: stored but old, the others mirror the source-specific state machine and never read as "none".
STATES = (
    "available",
    "stale",
    "unresolved_identity",
    "not_disclosed",
    "not_covered",
    "paid_only",
    "authentication_failed",
    "retrieval_failed",
    "parse_failed",
    "under_review",
    "none_known",
)
FAILURE_STATES = ("authentication_failed", "retrieval_failed", "parse_failed")
#: when several states apply to one ticker, the one that explains most about the gap wins
_STATE_RANK = {s: i for i, s in enumerate(STATES)}


@dataclass(frozen=True)
class DatasetSpec:
    key: str
    label: str
    issuer_keyed: bool
    stale_days: int
    unit: str
    source: str
    method: str
    api_path: str
    page_path: str
    blind_spots: tuple[str, ...]


DATASETS: tuple[DatasetSpec, ...] = (
    DatasetSpec(
        "filings",
        "Filings",
        True,
        400,
        "documents",
        "Regulator filings (SEC EDGAR, other registered sources)",
        "discovery + content hash",
        "/disclosures/issuers/{issuer_id}/timeline",
        "/disclosures",
        ("Only issuers linked to a CIK are discovered; non-US issuers need a registered adapter",),
    ),
    DatasetSpec(
        "financials",
        "Financials and notes",
        True,
        400,
        "observations",
        "XBRL statements and note tables read from filings",
        "xbrl + note tables",
        "/disclosures/issuers/{issuer_id}/statements",
        "/disclosures",
        (
            "ESEF is not reconciled to 20-F",
            "Statement checks cover gross profit and free cash flow",
        ),
    ),
    DatasetSpec(
        "ownership",
        "Ownership",
        False,
        200,
        "filings",
        "Schedule 13D/13G and N-PORT fund holdings",
        "structured form fields",
        "/disclosures/holdings/{ticker}/ownership",
        "/disclosures/ownership",
        (
            "A US 13F or 13D result is not worldwide ownership",
            "National voting-rights lane is not covered outside reviewed UK examples",
        ),
    ),
    DatasetSpec(
        "insiders",
        "Insider activity",
        False,
        120,
        "filings",
        "SEC Forms 3, 4 and 5; UK PDMR notices",
        "structured form fields + classifier",
        "/disclosures/holdings/{ticker}/insiders",
        "/disclosures/insiders",
        (
            "No jurisdiction beyond the SEC and UK PDMR",
            "Buyback and earnings-window context missing",
        ),
    ),
    DatasetSpec(
        "dependencies",
        "Customer and supplier network",
        False,
        400,
        "claims",
        "Relationship passages in filings, procurement awards",
        "passage screen + review",
        "/disclosures/holdings/{ticker}/dependencies",
        "/disclosures/network",
        ("Anonymous counterparties never receive an invented company id",),
    ),
    DatasetSpec(
        "deals",
        "Deals and financing",
        False,
        365,
        "deals",
        "8-K and other public deal documents",
        "extractor + state ladder",
        "/disclosures/holdings/{ticker}/deals",
        "/disclosures/deals",
        ("Extractor is English and US-format only",),
    ),
    DatasetSpec(
        "estimates",
        "Published estimates",
        True,
        120,
        "snapshots",
        "Company guidance and published consensus files",
        "versioned file snapshots",
        "/disclosures/holdings/{ticker}/estimates",
        "/disclosures/estimates",
        ("Named analyst estimates have no free route", "A public consensus is not all analysts"),
    ),
)
BY_KEY = {d.key: d for d in DATASETS}

#: P9.7 release order: each stage names the datasets it unlocks
RELEASE_ORDER: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Verified SEC and point-in-time foundation", ("filings", "financials")),
    ("Ownership and insiders", ("ownership", "insiders")),
    ("Relationships and deals", ("dependencies", "deals")),
    ("Accessible estimates", ("estimates",)),
    ("Additional jurisdictions", ()),
)
#: which adjudication dataset gates which coverage dataset
GATE_FOR = {
    "financials": "statement_note",
    "ownership": "ownership_match",
    "insiders": "insider_class",
    "dependencies": "relationship_claim",
}

#: every source is free by construction (`disclosure_source.cost` is constrained to 'free')
COST_LEDGER: tuple[tuple[str, str], ...] = (
    ("SEC EDGAR submissions, XBRL and Financial Statement Data Sets", "https://www.sec.gov/edgar"),
    ("FCA National Storage Mechanism (UK TR-1 and PDMR)", "https://data.fca.org.uk"),
    ("filings.xbrl.org (ESEF)", "https://filings.xbrl.org"),
    ("GLEIF LEI lookup", "https://www.gleif.org"),
    ("OpenFIGI without a paid plan", "https://www.openfigi.com"),
    ("Issuer investor-relations pages", "per issuer"),
)


def _aware(value: dt.datetime | None) -> dt.datetime:
    now = value or dt.datetime.now(dt.UTC)
    return now if now.tzinfo else now.replace(tzinfo=dt.UTC)


def _s(v: Decimal | None) -> str | None:
    return None if v is None else str(v)


@dataclass(frozen=True)
class Holding:
    ticker: str
    value: Decimal
    jurisdiction: str
    issuer_id: int | None


@dataclass(frozen=True)
class Evidence:
    count: int
    first_known: dt.datetime | None
    last_known: dt.datetime | None
    first_period: str | None = None
    last_period: str | None = None


NO_EVIDENCE = Evidence(0, None, None)


def jurisdiction_of(isin: str | None, country: str | None) -> str:
    """ISIN country prefix first, then the issuer's country; `XX` when neither is known."""
    for candidate in ((isin or "")[:2], country or ""):
        if len(candidate) == 2 and candidate.isalpha() and candidate.isupper():
            return candidate
    return "XX"


def state_for(
    spec: DatasetSpec,
    holding: Holding,
    ev: Evidence,
    coverage_state: str | None,
    as_of: dt.datetime,
) -> str:
    """The coverage state of one dataset for one holding. Never `available` without a record."""
    if ev.count > 0:
        if ev.last_known is not None and (as_of - ev.last_known).days > spec.stale_days:
            return "stale"
        return "available"
    if spec.issuer_keyed and holding.issuer_id is None:
        return "unresolved_identity"
    if coverage_state in STATES:
        return str(coverage_state)
    return "none_known"


def worst_state(states: Iterable[str]) -> str | None:
    found = [s for s in states if s in _STATE_RANK]
    return max(found, key=lambda s: _STATE_RANK[s]) if found else None


def _min_dt(a: dt.datetime | None, b: dt.datetime | None) -> dt.datetime | None:
    return b if a is None else a if b is None else min(a, b)


def _max_dt(a: dt.datetime | None, b: dt.datetime | None) -> dt.datetime | None:
    return b if a is None else a if b is None else max(a, b)


def company_evidence(
    ticker: str,
    holding: Holding | None,
    evidence: Mapping[str, Evidence],
    coverage: Mapping[str, str],
    as_of: dt.datetime,
    total_value: Decimal,
) -> dict[str, Any]:
    """One call per holding: for each dataset the count, newest known date, state and link."""
    held = holding or Holding(ticker, Decimal(0), "XX", None)
    rows: list[dict[str, Any]] = []
    for spec in DATASETS:
        ev = evidence.get(spec.key, NO_EVIDENCE)
        state = state_for(spec, held, ev, coverage.get(spec.key), as_of)
        path = spec.api_path.replace("{ticker}", ticker)
        link: str | None = path
        if "{issuer_id}" in path:
            link = (
                None if held.issuer_id is None else path.replace("{issuer_id}", str(held.issuer_id))
            )
        rows.append(
            {
                "dataset": spec.key,
                "label": spec.label,
                "count": ev.count,
                "unit": spec.unit,
                "first_known_at": ev.first_known,
                "latest_known_at": ev.last_known,
                "first_period": ev.first_period,
                "last_period": ev.last_period,
                "coverage_state": state,
                "stale": state == "stale",
                "source": spec.source,
                "method": spec.method,
                "method_version": METHOD_VERSION,
                "api_path": link,
                "page_path": spec.page_path,
            }
        )
    weight = held.value / total_value if holding is not None and total_value > 0 else None
    return {
        "ticker": ticker,
        "held": holding is not None,
        "issuer_id": held.issuer_id,
        "jurisdiction": held.jurisdiction if holding is not None else None,
        "weight": _s(weight),
        "as_of": as_of,
        "datasets": rows,
        "covered_datasets": sum(1 for r in rows if r["count"] > 0),
        "dataset_total": len(rows),
        "notes": [
            "A count is what is stored, not proof that nothing else exists.",
            *(
                []
                if holding is not None
                else ["This ticker is not in the latest position snapshot."]
            ),
        ],
    }


def coverage_cells(
    holdings: Sequence[Holding],
    evidence: Mapping[tuple[str, str], Evidence],
    coverage: Mapping[tuple[str, str], str],
    as_of: dt.datetime,
) -> list[dict[str, Any]]:
    """Per dataset x jurisdiction: covered/uncovered counts over held tickers and market value."""
    by_j: dict[str, list[Holding]] = defaultdict(list)
    for h in holdings:
        by_j[h.jurisdiction].append(h)
    cells: list[dict[str, Any]] = []
    for spec in DATASETS:
        for jur in sorted(by_j):
            group = by_j[jur]
            denominator = sum((h.value for h in group), Decimal(0))
            covered_v = Decimal(0)
            covered = stale = 0
            first: dt.datetime | None = None
            last: dt.datetime | None = None
            states: dict[str, int] = defaultdict(int)
            uncovered: list[str] = []
            for h in group:
                ev = evidence.get((h.ticker, spec.key), NO_EVIDENCE)
                st = state_for(spec, h, ev, coverage.get((h.ticker, spec.key)), as_of)
                states[st] += 1
                if ev.count > 0:
                    covered += 1
                    covered_v += h.value
                    stale += st == "stale"
                    first = _min_dt(first, ev.first_known)
                    last = _max_dt(last, ev.last_known)
                else:
                    uncovered.append(h.ticker)
            spots = list(spec.blind_spots)
            if uncovered:
                shown = ", ".join(sorted(uncovered)[:5])
                more = f" and {len(uncovered) - 5} more" if len(uncovered) > 5 else ""
                spots.insert(0, f"{len(uncovered)} held with no record: {shown}{more}")
            cells.append(
                {
                    "dataset": spec.key,
                    "label": spec.label,
                    "jurisdiction": jur,
                    "tickers": len(group),
                    "covered": covered,
                    "uncovered": len(group) - covered,
                    "stale": stale,
                    "denominator_value": str(denominator),
                    "covered_value": str(covered_v),
                    "uncovered_value": str(denominator - covered_v),
                    "covered_share": _s(covered_v / denominator) if denominator > 0 else None,
                    "first_known_at": first,
                    "last_known_at": last,
                    "states": dict(sorted(states.items())),
                    "blind_spots": spots,
                    "unit": spec.unit,
                    "source": spec.source,
                    "method": spec.method,
                }
            )
    return cells


def period_rows(
    holdings: Sequence[Holding], issuer_years: Iterable[tuple[int, str]]
) -> list[dict[str, Any]]:
    """Financial coverage by fiscal year: how many held issuers (and how much value) have it."""
    value_by_issuer = {h.issuer_id: h.value for h in holdings if h.issuer_id is not None}
    total_issuers = len(value_by_issuer)
    denominator = sum((h.value for h in holdings), Decimal(0))
    years: dict[str, set[int]] = defaultdict(set)
    for issuer_id, year in issuer_years:
        if issuer_id in value_by_issuer:
            years[year].add(issuer_id)
    return [
        {
            "dataset": "financials",
            "period": year,
            "issuers_covered": len(ids),
            "issuers_total": total_issuers,
            "covered_value": str(sum((value_by_issuer[i] for i in ids), Decimal(0))),
            "denominator_value": str(denominator),
        }
        for year, ids in sorted(years.items())
    ]


def quality_rows(labels: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Sampled error rates from a person's labels; the latest label per item counts.

    Numerator is the incorrect labels, denominator the labelled items. A dataset with no labels has
    a null rate (not sampled), never 0.
    """
    latest: dict[tuple[str, str, str], bool] = {}
    for r in labels:
        latest[(r["dataset"], r["sample_key"], r["item_ref"])] = bool(r["correct"])
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for (dataset, _sample, _item), ok in latest.items():
        counts[dataset][1] += 1
        counts[dataset][0] += 0 if ok else 1
    out: list[dict[str, Any]] = []
    for dataset, (needed, bar) in GATES.items():
        errors, labelled = counts.get(dataset, [0, 0])
        rate = Decimal(errors) / Decimal(labelled) if labelled else None
        if labelled == 0:
            state = "not_sampled"
        elif labelled < needed:
            state = "sample_too_small"
        else:
            state = "meets_bar" if rate is not None and (1 - rate) >= bar else "below_bar"
        out.append(
            {
                "dataset": dataset,
                "labelled": labelled,
                "errors": errors,
                "error_rate": _s(rate.quantize(Decimal("0.0001")) if rate is not None else None),
                "needed": needed,
                "bar": str(bar),
                "state": state,
            }
        )
    return out


def release_order(quality: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Stages with the measured state of the gate behind each dataset (null: no gate defined)."""
    by_ds = {q["dataset"]: q["state"] for q in quality}
    stages: list[dict[str, Any]] = []
    for i, (name, datasets) in enumerate(RELEASE_ORDER, start=1):
        gates = {d: by_ds.get(GATE_FOR.get(d, "")) for d in datasets}
        measured = [s for s in gates.values() if s is not None]
        if not datasets:
            state = "not_started"
        elif (
            measured
            and all(s == "meets_bar" for s in measured)
            and len(measured) == len([d for d in datasets if d in GATE_FOR])
        ):
            state = "gates_met"
        else:
            state = "gates_open"
        stages.append(
            {"order": i, "stage": name, "datasets": list(datasets), "gates": gates, "state": state}
        )
    return stages


# ------------------------------------------------------------------------------------- database

HOLDINGS_SQL = """
select p.ticker, p.value_account, n.isin, i.country, i.id as issuer_id, p.snapshot_date
  from public.position_snapshot p
  join public.instrument n on n.t212_ticker = p.ticker
  left join public.issuer i on i.cik = n.cik
 where p.snapshot_date = (select max(snapshot_date) from public.account_snapshot
                           where snapshot_date <= $1::date)
"""

_ISSUER_JOIN = "from public.instrument n join public.issuer i on i.cik = n.cik"

EVIDENCE_SQL: dict[str, str] = {
    "filings": f"""
select n.t212_ticker as ticker, count(distinct d.id)::int as n, min(v.known_at) as first_known,
       max(v.known_at) as last_known, min(d.fiscal_period) as first_period,
       max(d.fiscal_period) as last_period
  {_ISSUER_JOIN}
  join public.source_document d on d.issuer_id = i.id
  join public.source_document_version v on v.document_id = d.id
 where n.t212_ticker = any($1::text[]) and v.known_at <= $2 group by 1""",
    "financials": f"""
select n.t212_ticker as ticker, count(*)::int as n, min(o.known_at) as first_known,
       max(o.known_at) as last_known, min(o.period_end)::text as first_period,
       max(o.period_end)::text as last_period
  {_ISSUER_JOIN}
  join public.disclosure_observation o on o.issuer_id = i.id
 where n.t212_ticker = any($1::text[]) and o.known_at <= $2
   and o.review_state not in ('rejected', 'superseded') group by 1""",
    "ownership": """
select ticker, sum(n)::int as n, min(first_known) as first_known, max(last_known) as last_known,
       min(first_period) as first_period, max(last_period) as last_period
  from (
    select n.t212_ticker as ticker, count(*) as n, min(c.known_at) as first_known,
           max(c.known_at) as last_known, min(c.filed)::text as first_period,
           max(c.filed)::text as last_period
      from public.instrument n join public.control_stake_filing c on c.issuer_cik = n.cik
     where n.t212_ticker = any($1::text[]) and c.known_at <= $2 group by 1
    union all
    select h.held_ticker, count(*), min(f.known_at), max(f.known_at),
           min(f.report_period_end)::text, max(f.report_period_end)::text
      from public.nport_holding h join public.nport_filing f on f.accession = h.accession
     where h.held_ticker = any($1::text[]) and f.known_at <= $2 group by 1
    union all
    -- A 13F carries only a filing date, so it is known at the end of that day (UTC) at the
    -- earliest.
    select ih.ticker, count(*), min(k.known), max(k.known), min(f13.period)::text,
           max(f13.period)::text
      from public.institutional_holding ih
      join public.form13f_filing f13 on f13.accession = ih.accession
      cross join lateral (select ((f13.filed + 1)::timestamp at time zone 'UTC') as known) k
     where ih.ticker = any($1::text[]) and k.known <= $2 group by 1
  ) u group by 1""",
    "insiders": """
select f.ticker, count(*)::int as n, min(f.known_at) as first_known, max(f.known_at) as last_known,
       min(f.filed)::text as first_period, max(f.filed)::text as last_period
  from public.insider_filing f
 where f.ticker = any($1::text[]) and f.known_at <= $2 group by 1""",
    "dependencies": """
select r.ticker, count(*)::int as n, min(r.known_at) as first_known, max(r.known_at) as last_known,
       min(r.fiscal_period) as first_period, max(r.fiscal_period) as last_period
  from public.relationship_disclosure r
 where r.ticker = any($1::text[]) and r.known_at <= $2 and r.status <> 'rejected' group by 1""",
    "deals": """
select e.ticker, count(*)::int as n, min(e.known_at) as first_known,
       max(e.known_at) as last_known,
       min(e.announced_at)::date::text as first_period,
       max(e.announced_at)::date::text as last_period
  from public.deal_event e
 where e.ticker = any($1::text[]) and e.known_at <= $2 group by 1""",
    "estimates": f"""
select n.t212_ticker as ticker, count(*)::int as n, min(s.known_at) as first_known,
       max(s.known_at) as last_known, min(s.published_at)::date::text as first_period,
       max(s.published_at)::date::text as last_period
  {_ISSUER_JOIN}
  join public.public_estimate_snapshot s on s.issuer_id = i.id
 where n.t212_ticker = any($1::text[]) and s.known_at <= $2 group by 1""",
}


WATCHLIST_SQL = """
select distinct upper(w.ticker) as ticker, n.t212_ticker, n.isin, i.country, i.id as issuer_id
  from public.watchlist_item w
  left join public.instrument n on upper(n.t212_ticker) = upper(w.ticker)
  left join public.issuer i on i.cik = n.cik
"""


async def load_holdings(
    pool: Pool, when: dt.datetime, *, include_watchlist: bool = False
) -> tuple[list[Holding], dt.date | None]:
    """Held positions at the snapshot. With `include_watchlist`, watchlist tickers that are not held
    are added with a value of zero: they count toward holdings covered and uncovered, never toward
    the value denominator."""
    rows = await pool.fetch(HOLDINGS_SQL, when)
    holdings = [
        Holding(
            r["ticker"],
            Decimal(r["value_account"]),
            jurisdiction_of(r["isin"], r["country"]),
            None if r["issuer_id"] is None else int(r["issuer_id"]),
        )
        for r in rows
    ]
    if include_watchlist:
        held = {h.ticker.upper() for h in holdings}
        for w in await pool.fetch(WATCHLIST_SQL):
            if w["ticker"] in held:
                continue
            held.add(w["ticker"])
            holdings.append(
                Holding(
                    w["t212_ticker"] or w["ticker"],
                    Decimal(0),
                    jurisdiction_of(w["isin"], w["country"]),
                    None if w["issuer_id"] is None else int(w["issuer_id"]),
                )
            )
    return holdings, (rows[0]["snapshot_date"] if rows else None)


async def load_evidence(
    pool: Pool, tickers: list[str], when: dt.datetime
) -> dict[tuple[str, str], Evidence]:
    out: dict[tuple[str, str], Evidence] = {}
    if not tickers:
        return out
    for key, sql in EVIDENCE_SQL.items():
        for r in await pool.fetch(sql, tickers, when):
            out[(r["ticker"], key)] = Evidence(
                int(r["n"]), r["first_known"], r["last_known"], r["first_period"], r["last_period"]
            )
    return out


async def load_coverage_states(
    pool: Pool, holdings: Sequence[Holding]
) -> dict[tuple[str, str], str]:
    """The recorded coverage state per (ticker, dataset); the worst one when several exist."""
    by_issuer = {h.issuer_id: h.ticker for h in holdings if h.issuer_id is not None}
    if not by_issuer:
        return {}
    rows = await pool.fetch(
        """select issuer_id, dataset, state from public.disclosure_coverage
            where issuer_id = any($1::bigint[]) and dataset = any($2::text[])""",
        list(by_issuer),
        list(BY_KEY),
    )
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for r in rows:
        grouped[(by_issuer[int(r["issuer_id"])], r["dataset"])].append(r["state"])
    return {k: s for k, v in grouped.items() if (s := worst_state(v)) is not None}


async def company_evidence_view(
    pool: Pool, ticker: str, as_of: dt.datetime | None = None
) -> dict[str, Any]:
    when = _aware(as_of)
    holdings, _snap = await load_holdings(pool, when)
    mine = next((h for h in holdings if h.ticker == ticker), None)
    total = sum((h.value for h in holdings), Decimal(0))
    ev = await load_evidence(pool, [ticker], when)
    states = await load_coverage_states(pool, [mine] if mine else [])
    return company_evidence(
        ticker,
        mine,
        {ds: e for (t, ds), e in ev.items() if t == ticker},
        {ds: s for (t, ds), s in states.items() if t == ticker},
        when,
        total,
    )


async def coverage_view(
    pool: Pool, as_of: dt.datetime | None = None, *, include_watchlist: bool = False
) -> dict[str, Any]:
    when = _aware(as_of)
    holdings, snapshot = await load_holdings(pool, when, include_watchlist=include_watchlist)
    watch_only = sum(1 for h in holdings if h.value == 0) if include_watchlist else 0
    tickers = sorted(h.ticker for h in holdings)
    evidence = await load_evidence(pool, tickers, when)
    states = await load_coverage_states(pool, holdings)
    cells = coverage_cells(holdings, evidence, states, when)
    issuer_ids = [h.issuer_id for h in holdings if h.issuer_id is not None]
    years = (
        await pool.fetch(
            """select distinct issuer_id, extract(year from period_end)::int::text as yr
                 from public.disclosure_observation
                where issuer_id = any($1::bigint[]) and known_at <= $2
                  and review_state not in ('rejected', 'superseded')""",
            issuer_ids,
            when,
        )
        if issuer_ids
        else []
    )
    failures = [
        {
            "kind": "coverage",
            "dataset": r["dataset"],
            "jurisdiction": r["jurisdiction"],
            "state": r["state"],
            "count": int(r["n"]),
            "last_checked_at": r["last_checked"],
            "detail": r["reason"],
        }
        for r in await pool.fetch(
            """select dataset, coalesce(jurisdiction, 'XX') as jurisdiction, state, count(*) as n,
                      max(checked_at) as last_checked, max(reason) as reason
                 from public.disclosure_coverage
                where state = any($1::text[]) group by 1, 2, 3 order by 1, 2, 3""",
            list(FAILURE_STATES),
        )
    ] + [
        {
            "kind": "source",
            "dataset": r["provider"],
            "jurisdiction": r["jurisdiction"],
            "state": r["access_state"],
            "count": 1,
            "last_checked_at": r["last_checked_at"],
            "detail": r["access_detail"],
        }
        for r in await pool.fetch(
            """select provider, jurisdiction, access_state, access_detail, last_checked_at
                 from public.disclosure_source
                where access_state in ('authentication_failed', 'retrieval_failed', 'paid_only',
                                       'not_covered') order by source_key"""
        )
    ]
    labels = await pool.fetch(
        """select dataset, sample_key, item_ref, correct from public.disclosure_adjudication
            order by adjudicated_at, id"""
    )
    quality = quality_rows([dict(r) for r in labels])
    sources = await pool.fetchrow(
        """select count(*) as total, count(*) filter (where cost <> 'free') as paid
             from public.disclosure_source"""
    )
    known = [c["first_known_at"] for c in cells if c["first_known_at"] is not None]
    latest = [c["last_known_at"] for c in cells if c["last_known_at"] is not None]
    return {
        "as_of": when,
        "snapshot_date": snapshot,
        "holdings": len(holdings),
        "watchlist_only": watch_only,
        "denominator_value": str(sum((h.value for h in holdings), Decimal(0))),
        "cells": cells,
        "by_period": period_rows(holdings, [(int(r["issuer_id"]), r["yr"]) for r in years]),
        "failures": failures,
        "quality": quality,
        "cost_ledger": {
            "total_usd": "0.00",
            "basis": "Every registered source is constrained to cost = 'free'.",
            "registered_sources": int(sources["total"]) if sources else 0,
            "paid_sources": int(sources["paid"]) if sources else 0,
            "sources": [{"name": n, "reference": u, "cost_usd": "0.00"} for n, u in COST_LEDGER],
        },
        "release_order": release_order(quality),
        "first_known_at": min(known) if known else None,
        "last_known_at": max(latest) if latest else None,
        "method_version": METHOD_VERSION,
        "notes": [
            "Covered means a record known by the date is stored; "
            "uncovered is not proof none exists.",
            "A rate or share with no denominator is null, never zero.",
            *(
                [
                    f"{watch_only} watchlist-only tickers are counted as holdings but add no "
                    "value to any denominator."
                ]
                if watch_only
                else []
            ),
        ],
    }
