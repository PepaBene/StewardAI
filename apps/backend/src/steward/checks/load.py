# ruff: noqa: E501
"""Fill `CheckInputs` from the stored read models (statements, ratios with peers, valuation,
saved scenarios, theses, consensus, estimates, dividends, insiders, prices, disclosures)."""

from __future__ import annotations

import datetime as dt
import json
import math
from decimal import Decimal
from typing import Any

import asyncpg

from steward.analytics.ratio_set import debt_of
from steward.checks.inputs import CheckInputs, RatioFacts
from steward.fx.convert import normalise_currency

D = Decimal


def ratio_facts(view: Any) -> dict[str, RatioFacts]:
    year_of = {c.id: c.end.year for c in view.columns if c.kind == "annual"}
    out: dict[str, RatioFacts] = {}
    for g in view.groups:
        for r in g.ratios:
            peer = r.peer
            out[r.id] = RatioFacts(
                latest=r.latest.value if r.latest else None,
                reason=r.latest.reason if r.latest else "no period",
                own_5y=r.own_median_5y,
                peer_median=peer.median if peer is not None and not peer.withheld_reason else None,
                peer_count=peer.count if peer is not None else 0,
                peer_reason=peer.withheld_reason if peer is not None else "no peer set",
                peer_scope=peer.scope if peer is not None else None,
                series=[(year_of[p.column], p.value) for p in r.points if p.column in year_of],
            )
    return out


def _same_ccy(a: str | None, b: str | None) -> Decimal | None:
    """Factor converting an amount in `a` into `b` when they are the same currency in different
    units (GBX → GBP = 0.01); None when they differ."""
    if not a or not b:
        return None
    ai, af = normalise_currency(a)
    bi, bf = normalise_currency(b)
    return af / bf if ai == bi else None


def volatility(closes: list[tuple[dt.date, Decimal]], today: dt.date) -> Decimal | None:
    pts = [c for d, c in closes if d >= today - dt.timedelta(days=365) and c > 0]
    if len(pts) < 200:
        return None
    rets = [math.log(float(pts[k] / pts[k - 1])) for k in range(1, len(pts))]
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
    return D(str(math.sqrt(var) * math.sqrt(252)))


async def _peer_forecasts(
    pool: asyncpg.Pool, peers: list[str], today: dt.date
) -> tuple[Decimal | None, Decimal | None, int]:
    """Median next-year EPS and revenue growth of peers with stored estimates and a filed year."""
    if not peers:
        return None, None, 0
    rows = await pool.fetch(
        """with est as (
             select distinct on (ticker, metric) ticker, metric, value, currency
               from public.forward_observation
              where ticker = any($1::text[]) and kind = 'published_analyst_consensus'
                and metric in ('earnings_per_share', 'revenue') and period_end > $2 and value is not null
              order by ticker, metric, period_end, known_at desc),
           act as (
             select distinct on (ticker, line_item) ticker, line_item, value, currency
               from public.financial_statement_fact
              where ticker = any($1::text[]) and line_item in ('eps_diluted', 'revenue')
                and fiscal_period ~ '^FY[0-9]{4}$' and coalesce(dimensions->'members', '[]'::jsonb) = '[]'::jsonb
              order by ticker, line_item, period_end desc, known_at desc)
           select e.ticker, e.metric, e.value as est, a.value as act, e.currency as ecur, a.currency as acur
             from est e join act a on a.ticker = e.ticker
              and a.line_item = case e.metric when 'earnings_per_share' then 'eps_diluted' else 'revenue' end""",
        peers,
        today,
    )
    eps: list[float] = []
    rev: list[float] = []
    for r in rows:
        if (
            r["act"] is None
            or r["act"] <= 0
            or (r["ecur"] and r["acur"] and r["ecur"] != r["acur"])
        ):
            continue
        g = float(r["est"] / r["act"] - 1)
        (eps if r["metric"] == "earnings_per_share" else rev).append(g)

    def med(xs: list[float]) -> Decimal | None:
        if not xs:
            return None
        s = sorted(xs)
        n = len(s)
        return D(str(s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2))

    return med(eps), med(rev), min(len(eps), len(rev)) if eps and rev else max(len(eps), len(rev))


async def load_inputs(pool: asyncpg.Pool, owner_id: Any, ticker: str, *, div_ctx: Any = None, theses: dict[str, Any] | None = None,
                      today: dt.date | None = None) -> CheckInputs | None:  # fmt: skip
    from steward.company import evidence, ratios, service, valuation
    from steward.company.ratios import figures_by_column
    from steward.sources.consensus import _estimates
    from steward.valuation.fair_value import load_theses

    today = today or dt.date.today()
    company = await service.load_company(pool, ticker)
    if company is None:
        return None
    annual = company.annual
    acols = [c for c in annual.columns if c.kind == "annual"]
    fig_all = figures_by_column(annual)
    peers, scope, pre = await service.resolve_peers(pool, company, "global", owner_id, None)
    peer_values, label, excluded = await service.peer_values_for(
        pool, company, "global", owner_id, None
    )
    fwd = await evidence.forward_eps(pool, ticker, company.market.currency)
    view = ratios.build_ratios(
        ticker, annual, company.quarterly, company.market, sector=company.sector, industry=company.industry,
        peers=peer_values, peer_scope=label, peer_excluded=excluded, forward_eps=fwd,
    )  # fmt: skip
    inp = CheckInputs(ticker=ticker, name=company.name, currency=annual.currency, sector=company.sector,
                      is_financial=view.is_financial, today=today, ratios=ratio_facts(view))  # fmt: skip
    if acols:
        inp.fiscal_year = acols[-1].end.year
        inp.fig = dict(fig_all[acols[-1].id])
        if len(acols) >= 2:
            inp.fig_prev = dict(fig_all[acols[-2].id])
        for key in (
            "revenue",
            "net_income",
            "eps_diluted",
            "dividends_per_share",
            "total_assets",
            "total_liabilities_and_equity",
        ):
            inp.series[key] = [(c.end.year, fig_all[c.id].get(key)) for c in acols]
        if not any(v for _, v in inp.series["total_assets"]):
            inp.series["total_assets"] = inp.series["total_liabilities_and_equity"]
        fcf: list[tuple[int, Decimal | None]] = []
        for c in acols:
            f = fig_all[c.id]
            cfo, capex = f.get("cash_from_operations"), f.get("capex")
            fcf.append(
                (c.end.year, cfo - abs(capex) if cfo is not None and capex is not None else None)
            )
        inp.series["fcf"] = fcf
        inp.debt = debt_of(inp.fig)
    # Valuation: the default base DCF, and the owner's latest saved set or thesis.
    val = valuation.build_valuation(
        ticker,
        annual,
        company.quarterly,
        company.market,
        view,
        valuation.ValuationRequest(selected="base"),
    )
    inp.price = val.price
    if val.price is None:
        inp.price_note = (
            "; ".join(w for w in val.warnings if "currency" in w.lower()) or "no stored price"
        )
    base = next((s for s in val.scenarios if s.name == "base"), None)
    if base is not None:
        inp.dcf_base = base.value_per_share.value
        inp.dcf_reason = "; ".join(base.value_per_share.warnings) or None
        inp.risk_free = base.assumptions.risk_free
    saved = await pool.fetchrow(
        """select id, name, version, assumptions from public.company_valuation_scenario
            where owner_id = $1 and ticker = $2 and method = 'dcf' order by created_at desc, id desc limit 1""",
        owner_id, ticker,
    )  # fmt: skip
    if saved is not None:
        body = (
            json.loads(saved["assumptions"])
            if isinstance(saved["assumptions"], str)
            else saved["assumptions"]
        )
        req = valuation.ValuationRequest(
            scenarios=[valuation.Assumptions(**a) for a in body["scenarios"]],
            selected=body.get("selected"),
        )
        sv = valuation.build_valuation(ticker, annual, company.quarterly, company.market, view, req)
        pick = next((s for s in sv.scenarios if s.name == req.selected), None) or next(
            (s for s in sv.scenarios if s.name.lower() == "base"), None
        )
        if pick is not None and pick.value_per_share.value is not None:
            inp.owner_value = pick.value_per_share.value
            inp.owner_basis = f"your '{pick.name}' scenario ({saved['name']} v{saved['version']})"
    if inp.owner_value is None:
        theses = theses if theses is not None else await load_theses(pool)
        target = theses.get(ticker)
        if target is not None:
            raw = target.scenarios.base if target.scenarios is not None else target.sell_target
            factor = _same_ccy(target.currency, annual.currency)
            if raw is None:
                inp.owner_reason = "the thesis has no base scenario or sell target"
            elif factor is None:
                inp.owner_reason = (
                    f"thesis target in {target.currency}, statements in {annual.currency}"
                )
            else:
                inp.owner_value = D(str(raw)) * factor
                inp.owner_basis = f"your thesis {'base scenario' if target.scenarios is not None else 'sell target'} (set {target.set_on.isoformat()})"
        else:
            inp.owner_reason = "no saved scenario or thesis price target"
    # Consensus target (latest snapshot with a target), in the statement currency.
    snap = await pool.fetchrow(
        """select as_of, source, coalesce(target_mean, target_median) as target, target_currency, analyst_count
             from public.consensus_snapshot where ticker = $1 and coalesce(target_mean, target_median) is not null
            order by as_of desc limit 1""",
        ticker,
    )
    if snap is not None:
        factor = _same_ccy(snap["target_currency"], annual.currency)
        if factor is None:
            inp.consensus_reason = (
                f"target in {snap['target_currency']}, statements in {annual.currency}"
            )
        else:
            inp.consensus_target = snap["target"] * factor
            inp.consensus_basis = f"{snap['source']}, {snap['analyst_count'] or '?'} analysts, {snap['as_of'].isoformat()}"
            inp.consensus_reason = None
    else:
        state = await pool.fetchval(
            "select access_state from public.consensus_snapshot where ticker = $1 order by as_of desc limit 1",
            ticker,
        )
        inp.consensus_reason = f"consensus {state}" if state else "no consensus snapshot stored"
    eps, rev, count, _period, ccy = await _estimates(pool, ticker, today)
    if ccy and annual.currency and ccy != annual.currency:
        inp.estimates_reason = f"estimates in {ccy}, statements in {annual.currency}"
    else:
        inp.eps_next, inp.revenue_next, inp.analyst_count = eps, rev, count
        inp.estimates_reason = (
            None
            if (eps is not None or rev is not None)
            else "no stored analyst estimates (FMP covers part of the holdings)"
        )
    inp.eps_trailing = inp.fig.get("eps_diluted")
    inp.revenue_trailing = inp.fig.get("revenue")
    inp.peer_eps_growth, inp.peer_revenue_growth, inp.peer_forecast_count = await _peer_forecasts(
        pool, list(peers), today
    )
    # Dividends: event history (when a ledger context is given) and the filed per-share series.
    annual_dps: dict[int, Decimal] = {}
    recent = False
    if div_ctx is not None and div_ctx.rec is not None:
        from steward.dividends.forecast import history_points, mark_specials
        from steward.dividends.metrics import annual_dps as by_year

        pts = history_points(ticker, div_ctx.rec, div_ctx.groups, None)
        mark_specials(pts)
        annual_dps, _ = by_year(pts)
        recent = any(p.pay_date >= today - dt.timedelta(days=550) and not p.special for p in pts)
    filed = {y: v for y, v in inp.series.get("dividends_per_share", []) if v is not None and v > 0}
    if len(filed) >= 2 and len(filed) >= len(annual_dps):
        annual_dps = filed
    inp.dps_by_year = sorted(annual_dps.items())
    paid = inp.fig.get("dividends_paid")
    if recent or (paid is not None and paid != 0) or (filed and max(filed) >= today.year - 2):
        inp.payer = True
    elif acols and inp.fig.get("cash_from_operations") is not None:
        inp.payer = False
    from steward.dividends.metrics import years_without_cut

    inp.years_without_cut = (
        years_without_cut(dict(inp.dps_by_year), today) if inp.dps_by_year else None
    )
    # Insiders (US Form 4), market, listing, disclosures.
    window = today - dt.timedelta(days=90)
    has = await pool.fetchval(
        "select exists(select 1 from public.insider_filing where ticker = $1)", ticker
    )
    inp.insider_has_filings = bool(has)
    if has:
        row = await pool.fetchrow(
            """select coalesce(sum(case when t.economic_class = 'open_market_sell' then t.shares * coalesce(t.price_per_share, 0)
                                        when t.economic_class = 'open_market_buy' then -t.shares * coalesce(t.price_per_share, 0) else 0 end), 0) as net_sold,
                      count(distinct case when t.economic_class = 'open_market_sell' then t.reporting_owner_cik end) as sellers
                 from public.insider_filing f join public.insider_transaction t using (accession)
                where f.ticker = $1 and not t.derivative and coalesce(t.transaction_date, f.filed) >= $2""",
            ticker, window,
        )  # fmt: skip
        inp.insider_net_sold_value = row["net_sold"] if row else None
        inp.insider_sellers = int(row["sellers"]) if row else 0
    m = company.market
    hit = m.price_on(today)
    if hit and m.shares:
        iso, factor = normalise_currency(m.currency) if m.currency else (None, D(1))
        inp.market_cap = hit[0] * m.shares * factor  # pence quotes become pounds
        inp.market_cap_currency = iso
    inp.volatility_1y = volatility(list(m.closes), today)
    uni = await pool.fetchrow(
        "select isin, price_symbol, currency from public.investment_universe where ticker = $1",
        ticker,
    )
    if uni is not None:
        liq = (
            await pool.fetchrow(
                "select avg_daily_turnover, turnover_currency from public.listing_liquidity where isin = $1",
                uni["isin"],
            )
            if uni["isin"]
            else None
        )
        if liq is not None and liq["avg_daily_turnover"] is not None:
            inp.avg_turnover = liq["avg_daily_turnover"]
            inp.turnover_basis = f"ESMA FITRS, {liq['turnover_currency'] or 'EUR'}"
        else:
            bars = await pool.fetch(
                "select close, volume from public.price_bar where symbol = $1 and volume is not null order by date desc limit 60",
                uni["price_symbol"] or ticker,
            )
            if len(bars) >= 20:
                _, factor = normalise_currency(uni["currency"]) if uni["currency"] else (None, D(1))
                inp.avg_turnover = (
                    sum((b["close"] * b["volume"] for b in bars), D(0)) / len(bars) * factor
                )
                inp.turnover_basis = (
                    f"{len(bars)} daily bars, {uni['currency'] or 'listing currency'}"
                )
        first = await pool.fetchval(
            "select min(date) from public.price_bar where symbol = $1",
            uni["price_symbol"] or ticker,
        )
        if first is not None and first > dt.date(1990, 1, 1):
            # Stored history starts at the first bar; only a start inside the window says "young".
            inp.years_listed = D(str(round((today - first).days / 365.25, 2)))
    runway = inp.r("cash_runway")
    inp.cash_runway_years, inp.cash_runway_reason = runway.latest, runway.reason
    amended = await pool.fetchval(
        """select count(distinct accession) from public.financial_statement_fact
            where ticker = $1 and form like '%/A' and known_at >= $2""",
        ticker, dt.datetime.combine(today - dt.timedelta(days=365), dt.time(), tzinfo=dt.UTC),
    )  # fmt: skip
    has_facts = await pool.fetchval(
        "select exists(select 1 from public.financial_statement_fact where ticker = $1)", ticker
    )
    inp.amendments_12m = int(amended or 0) if has_facts else None
    inp.amendment_basis = "amended filings (form /A) received in the last 12 months"
    nxt = await pool.fetchval(
        "select next_report_date from public.instrument where t212_ticker = $1", ticker
    )
    inp.next_report = nxt
    due = await pool.fetchval(
        """select value from public.financial_statement_fact where ticker = $1 and line_item = 'debt_maturity_y1'
            order by period_end desc, known_at desc limit 1""",
        ticker,
    )
    inp.debt_due_1y = due
    conc = await pool.fetchrow(
        """select count(*) as n, max(reported_share) as top from public.relationship_disclosure
            where ticker = $1 and relationship = 'customer' and status = 'confirmed'""",
        ticker,
    )
    if conc is not None and conc["n"]:
        inp.customer_known = True
        inp.customer_max_share = conc["top"]
    inp.extra = {
        "peer_scope": scope,
        "peer_notes": [str(p) for p in pre][:5],
        "statements_hash": annual.input_hash,
    }
    return inp
