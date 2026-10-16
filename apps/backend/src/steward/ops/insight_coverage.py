# ruff: noqa: E501
"""Insight coverage audit (insight plan I0.1): what the dividend engine, the checks and the
consensus features can honestly claim for each holding today.

Per holding and planned feature: `ready`, `partial` or `unavailable` with the reason, measured
from the stored rows (no provider call). The data-quality cockpit shows it; the methodology is in
`docs/data/insight_coverage.md`.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel

from steward.analytics.thresholds import THRESHOLDS
from steward.analytics.versions import version

State = Literal["ready", "partial", "unavailable"]
AREAS = {
    "dividend_history": "Confirmed dividend events and years of history",
    "dividend_dates": "Ex-date and pay-date completeness",
    "dividend_matching": "Trading212 dividends matched to a declared event",
    "statement_inputs": "Filed inputs the checks read (dividends, shares, debt, interest, accruals)",
    "estimates": "Published analyst estimates (FMP)",
    "consensus": "Analyst price target snapshot",
    "peers": f"Peer medians (at least {THRESHOLDS.min_peers_for_median} comparable peers)",
    "fair_value": "Recorded fair value on any basis",
}
# Per-feature readiness (insight I14.2): what each insight screen can show for the holding,
# derived from the inputs above and the feature's own stored rows.
FEATURES = {
    "f_dividend_income": "Forward dividend income",
    "f_checks": "Company checks (five axes and risk)",
    "f_fair_value_history": "Fair-value history (at least 8 weekly points)",
    "f_multiple_path": "Multiple path scenario (estimates and 5 years of EPS)",
    "f_ownership": "Ownership by holder type",
    "f_pay_tenure": "CEO pay and tenure",
    "f_earnings_summary": "Earnings-release summary",
}
FV_HISTORY_READY = 8
EPS_YEARS_READY = 5
STATEMENT_KEYS = (
    "dividends_paid",
    "dividends_per_share",
    "weighted_shares_diluted",
    "interest_expense",
    "cash_from_operations",
    "net_income",
    "total_liabilities",
)


class CoverageCell(BaseModel):
    area: str
    state: State
    reason: str


class HoldingInsightCoverage(BaseModel):
    ticker: str
    name: str | None
    cells: list[CoverageCell]


class AreaSummary(BaseModel):
    area: str
    label: str
    kind: Literal["input", "feature"] = "input"
    ready: int
    partial: int
    unavailable: int


class InsightCoverage(BaseModel):
    as_of: dt.date
    holdings: list[HoldingInsightCoverage]
    areas: list[AreaSummary]
    method_version: str


def _cell(area: str, ok: bool, some: bool, reason: str) -> CoverageCell:
    return CoverageCell(
        area=area, state="ready" if ok else "partial" if some else "unavailable", reason=reason
    )


def feature_cells(
    ticker: str,
    inputs: list[CoverageCell],
    scores: Any,
    fv_points: int,
    eps_years: int,
    estimates: int,
    sec: Any,
    release: bool,
) -> list[CoverageCell]:
    """The per-feature cells of one holding, from its input cells and the feature counts."""
    state = {c.area: c.state for c in inputs}
    out: list[CoverageCell] = []
    hist, dates = state.get("dividend_history"), state.get("dividend_dates")
    out.append(CoverageCell(area="f_dividend_income",
        state="ready" if hist == "ready" and dates != "unavailable" else "partial" if hist != "unavailable" else "unavailable",
        reason="forecast from three or more years of confirmed events" if hist == "ready"
        else "a short or suggested-only history: the forecast is flagged low confidence" if hist == "partial"
        else "no dividend events: a non-payer or not collected, so income is shown as none"))  # fmt: skip
    if scores is None:
        out.append(
            CoverageCell(area="f_checks", state="unavailable", reason="checks not computed yet")
        )
    else:
        sc = json.loads(scores) if isinstance(scores, str) else scores
        states = list((sc.get("checks") or {}).values())
        known = sum(1 for s in states if s in ("pass", "fail", "na"))
        out.append(_cell("f_checks", bool(states) and known == len(states), known > 0,
                         f"{known} of {len(states)} checks could be decided"))  # fmt: skip
    out.append(_cell("f_fair_value_history", fv_points >= FV_HISTORY_READY, fv_points > 0,
                     f"{fv_points} recorded fair-value dates" if fv_points else "no fair value recorded yet"))  # fmt: skip
    out.append(_cell("f_multiple_path", estimates >= 2 and eps_years >= EPS_YEARS_READY, estimates > 0 or eps_years > 0,
                     f"{estimates} estimate rows, {eps_years} years of filed diluted EPS"))  # fmt: skip
    filer = bool(sec and sec["sec_filer"])
    out.append(_cell("f_ownership", filer and bool(sec["insiders"]) and bool(sec["outstanding"]), filer,
                     "13F, Form 4 and shares outstanding stored" if filer and sec["insiders"] and sec["outstanding"]
                     else "SEC filer; Form 4 or shares outstanding missing" if filer
                     else "not an SEC registrant: 13F and Form 4 do not cover it"))  # fmt: skip
    pay = bool(sec and (sec["pay"] or sec["pay_xbrl"]))
    out.append(_cell("f_pay_tenure", filer and pay and bool(sec["insiders"]), filer and (pay or bool(sec["insiders"])),
                     "not an SEC registrant: no proxy statement or Form 4" if not filer
                     else f"proxy pay {'stored' if pay else 'missing'}; Form 4 {'stored' if sec['insiders'] else 'missing'}"))  # fmt: skip
    out.append(_cell("f_earnings_summary", release, False,
                     "an earnings release is stored" if release else f"no earnings release stored for {ticker}"))  # fmt: skip
    return out


async def insight_coverage(
    pool: asyncpg.Pool, owner_id: Any, today: dt.date | None = None
) -> InsightCoverage:
    from steward.dividends.service import load_context

    today = today or dt.date.today()
    ctx = await load_context(pool, today)
    assert ctx.rec is not None
    tickers = sorted(ctx.holdings)
    events: dict[str, list[Any]] = defaultdict(list)
    for e in ctx.events:
        events[e.ticker].append(e)
    paid: dict[str, int] = defaultdict(int)
    matched: dict[str, int] = defaultdict(int)
    for m in ctx.rec.matches:
        paid[m.payment.ticker] += 1
        if m.group is not None:
            matched[m.payment.ticker] += 1
    facts = await pool.fetch(
        """select ticker, line_item, count(distinct period_end) as periods from public.financial_statement_fact
            where ticker = any($1::text[]) and fiscal_period ~ '^FY[0-9]{4}$' and line_item = any($2::text[])
            group by ticker, line_item""",
        tickers, list(STATEMENT_KEYS),
    )  # fmt: skip
    have: dict[str, set[str]] = defaultdict(set)
    for f in facts:
        have[f["ticker"]].add(f["line_item"])
    est = {r["ticker"]: r["n"] for r in await pool.fetch(
        """select ticker, count(*) as n from public.forward_observation where ticker = any($1::text[])
            and kind = 'published_analyst_consensus' and period_end > $2 group by ticker""", tickers, today)}  # fmt: skip
    cons = {r["ticker"]: r for r in await pool.fetch(
        """select distinct on (ticker) ticker, access_state, as_of, detail from public.consensus_snapshot
            where ticker = any($1::text[]) order by ticker, as_of desc, (target_mean is not null) desc""", tickers)}  # fmt: skip
    checks = {r["ticker"]: r["scores"] for r in await pool.fetch(
        """select distinct on (ticker) ticker, scores from public.company_check_snapshot where owner_id = $1
            and ticker = any($2::text[]) order by ticker, computed_at desc""", owner_id, tickers)}  # fmt: skip
    fair = {r["ticker"]: r["bases"] for r in await pool.fetch(
        """select ticker, array_agg(distinct basis) as bases from public.fair_value_point where owner_id = $1
            and ticker = any($2::text[]) and as_of >= $3 group by ticker""", owner_id, tickers, today - dt.timedelta(days=7))}  # fmt: skip
    fv_points = {r["ticker"]: r["n"] for r in await pool.fetch(
        """select ticker, count(distinct as_of) as n from public.fair_value_point where owner_id = $1
            and ticker = any($2::text[]) group by ticker""", owner_id, tickers)}  # fmt: skip
    eps_years = {r["ticker"]: r["n"] for r in await pool.fetch(
        """select ticker, count(distinct period_end) as n from public.financial_statement_fact
            where ticker = any($1::text[]) and line_item = 'eps_diluted' and fiscal_period ~ '^FY[0-9]{4}$'
              and coalesce(dimensions->'members', '[]'::jsonb) = '[]'::jsonb group by ticker""", tickers)}  # fmt: skip
    sec = {r["ticker"]: r for r in await pool.fetch(
        """select i.t212_ticker as ticker, i.cik is not null as sec_filer,
                  exists (select 1 from public.insider_filing f where f.ticker = i.t212_ticker
                          or exists (select 1 from public.insider_filing_ticker l where l.accession = f.accession and l.ticker = i.t212_ticker)) as insiders,
                  exists (select 1 from public.shares_outstanding s where s.ticker = i.t212_ticker and s.method <> 'manual') as outstanding,
                  exists (select 1 from public.company_research_observation o where o.ticker = i.t212_ticker and o.dataset = 'executive_compensation') as pay,
                  exists (select 1 from public.financial_statement_fact x where x.ticker = i.t212_ticker and x.concept = 'ecd:PeoTotalCompAmt') as pay_xbrl
             from public.instrument i where i.t212_ticker = any($1::text[])""", tickers)}  # fmt: skip
    releases = {r["ticker"]: True for r in await pool.fetch(
        """select distinct f.ticker from public.fact f join public.evidence e on e.sha256 = f.evidence_sha256
            where f.ticker = any($1::text[])""", tickers)}  # fmt: skip
    out: list[HoldingInsightCoverage] = []
    for t in tickers:
        ev = events.get(t, [])
        confirmed = [e for e in ev if e.status == "confirmed"]
        years = len({e.ex_date.year for e in confirmed})
        cells = [
            _cell("dividend_history", years >= 3, bool(ev),
                  f"{len(confirmed)} confirmed events over {years} years; {len(ev) - len(confirmed)} suggested" if ev else "no dividend events stored (non-payer or not collected)"),
        ]  # fmt: skip
        dated = [e for e in ev if e.payment_date is not None]
        cells.append(_cell("dividend_dates", bool(ev) and len(dated) == len(ev), bool(dated),
                           f"{len(dated)} of {len(ev)} events have a pay date" if ev else "no events"))  # fmt: skip
        n = paid.get(t, 0)
        cells.append(_cell("dividend_matching", n > 0 and matched.get(t, 0) == n, matched.get(t, 0) > 0,
                           f"{matched.get(t, 0)} of {n} Trading212 dividends matched" if n else "no Trading212 dividends received"))  # fmt: skip
        got = have.get(t, set())
        cells.append(_cell("statement_inputs", len(got) == len(STATEMENT_KEYS), bool(got),
                           "all filed" if len(got) == len(STATEMENT_KEYS) else f"missing {', '.join(k for k in STATEMENT_KEYS if k not in got)}"))  # fmt: skip
        cells.append(_cell("estimates", est.get(t, 0) >= 2, est.get(t, 0) > 0,
                           f"{est.get(t, 0)} forward estimate rows" if est.get(t) else "no FMP estimates stored"))  # fmt: skip
        c = cons.get(t)
        cells.append(CoverageCell(area="consensus", state="ready" if c and c["access_state"] == "complete" else "partial" if c and c["access_state"] == "partial" else "unavailable",
                          reason=f"{c['access_state']} on {c['as_of'].isoformat()}: {c['detail'] or ''}".strip() if c else "no snapshot yet"))  # fmt: skip
        sc = checks.get(t)
        if sc is None:
            cells.append(
                CoverageCell(area="peers", state="unavailable", reason="checks not computed yet")
            )
        else:
            sc = json.loads(sc) if isinstance(sc, str) else sc
            states = sc.get("checks", {})
            peer_ids = (
                "value_multiple_vs_peers",
                "past_earnings_vs_peers",
                "dividend_yield_vs_peers",
                "future_earnings_vs_peers",
            )
            known = [i for i in peer_ids if states.get(i) in ("pass", "fail", "na")]
            cells.append(
                _cell(
                    "peers",
                    len(known) == len(peer_ids),
                    bool(known),
                    f"{len(known)} of {len(peer_ids)} peer comparisons possible",
                )
            )
        b = fair.get(t) or []
        cells.append(_cell("fair_value", "dcf_base" in b or "multiple_base" in b or "thesis_target" in b, bool(b),
                           f"bases recorded this week: {', '.join(sorted(b))}" if b else "nothing recorded this week"))  # fmt: skip
        cells.extend(
            feature_cells(
                t,
                cells,
                sc,
                fv_points.get(t, 0),
                eps_years.get(t, 0),
                est.get(t, 0),
                sec.get(t),
                releases.get(t, False),
            )
        )
        out.append(HoldingInsightCoverage(ticker=t, name=ctx.names.get(t), cells=cells))
    areas = [
        AreaSummary(area=a, label=label, kind="feature" if a in FEATURES else "input",
                    ready=sum(1 for h in out for c in h.cells if c.area == a and c.state == "ready"),
                    partial=sum(1 for h in out for c in h.cells if c.area == a and c.state == "partial"),
                    unavailable=sum(1 for h in out for c in h.cells if c.area == a and c.state == "unavailable"))
        for a, label in (AREAS | FEATURES).items()
    ]  # fmt: skip
    return InsightCoverage(
        as_of=today, holdings=out, areas=areas, method_version=version("insight_coverage")
    )
