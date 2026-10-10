"""Database-facing assembly for the fund workbench: loads what each view needs and hands it to the
pure modules."""

# ruff: noqa: E501
from __future__ import annotations

import datetime as dt
import json
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import asyncpg

from steward.analytics.returns import Dividend
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.discovery import read as discovery_read
from steward.discovery.read import _member, provider_states
from steward.fundresearch import analysis, compare, performance
from steward.fundresearch.data import FundData, load_fund
from steward.funds.aggregate import IssuerRef, config_groups, load_issuer_register
from steward.fx.convert import normalise_currency

EQUITY_LIMIT = 200  # issuers beyond the largest 200 are not scored; the coverage says so


async def _groups(pool: asyncpg.Pool) -> dict[str, IssuerRef]:
    stored = await load_active(pool, "jobs", JobsConfig)
    config = stored.body if stored else JobsConfig()
    return config_groups(config.look_through)


async def holdings(
    pool: asyncpg.Pool, data: FundData, top: int = analysis.TOP_DEFAULT
) -> tuple[list[analysis.HoldingRow], analysis.HoldingsView]:
    snap = data.snapshot
    register = (
        await load_issuer_register(pool, sorted({r.isin for r in snap.rows if r.isin}))
        if snap
        else {}
    )
    rows = analysis.holding_rows(data, await _groups(pool), register)
    return rows, analysis.build_holdings(data, rows, top)


async def _eur_rates(pool: asyncpg.Pool) -> dict[str, Decimal]:
    rows = await pool.fetch(
        "select distinct on (currency) currency, rate from public.fx_rate where source='ecb' order by currency, rate_date desc"
    )
    out = {r["currency"]: r["rate"] for r in rows}
    out["EUR"] = Decimal(1)
    return out


async def style(
    pool: asyncpg.Pool,
    data: FundData,
    rows: Sequence[analysis.HoldingRow],
    view: analysis.HoldingsView,
    as_of: dt.date | None = None,
) -> analysis.StyleView:
    day = as_of or dt.datetime.now(dt.UTC).date()
    totals: dict[str, Decimal] = {}
    first_isin: dict[str, str] = {}
    labels: dict[str, str] = {}
    sector: dict[str, str | None] = {}
    for r in rows:
        if r.asset_class == "equity" and r.issuer_key:
            totals[r.issuer_key] = totals.get(r.issuer_key, Decimal(0)) + r.weight
            labels.setdefault(r.issuer_key, r.issuer_label or r.issuer_key)
            sector.setdefault(r.issuer_key, r.sector)
            if r.isin:
                first_isin.setdefault(r.issuer_key, r.isin)
    ranked = sorted(totals, key=lambda k: -totals[k])
    scored = set(ranked[:EQUITY_LIMIT])
    isins = sorted({first_isin[k] for k in scored if k in first_isin})
    members = []
    if isins:
        rec = await pool.fetch(
            "select * from public.investment_universe where isin = any($1::text[])", isins
        )
        by_isin: dict[str, Any] = {}
        for r in rec:
            by_isin.setdefault(r["isin"], _member(r))
        members = list(by_isin.values())
    records = {}
    if members:
        recs = await discovery_read.stock_records(
            pool,
            members,
            day,
            cutoff=None,
            reconciled_only=False,
            access=await provider_states(pool),
        )
        records = {m.isin: (m, rec_) for m, rec_ in zip(members, recs, strict=True)}
    rates = await _eur_rates(pool) if records else {}
    lines: list[analysis.StyleLine] = []
    for key in ranked:
        isin = first_isin.get(key)
        hit = records.get(isin) if isin else None
        cap_eur = ey = pb = prof = None
        covered = hit is not None
        if hit:
            m, rec_ = hit

            def val(metric: str, rec: Any = rec_) -> Decimal | None:
                mv = rec.values.get(metric)
                return mv.value if mv is not None and isinstance(mv.value, Decimal) else None

            cap = val("market_cap")
            if cap is not None and m.currency:
                ccy, factor = normalise_currency(m.currency)
                rate = rates.get(ccy)
                cap_eur = cap * factor * rate if rate else None
            ey, pb, prof = (
                val("earnings_yield"),
                val("price_to_book"),
                val("gross_profit_to_assets"),
            )
        lines.append(
            analysis.StyleLine(
                key=key,
                label=labels[key],
                weight=totals[key],
                sector=sector.get(key),
                market_cap_eur=cap_eur,
                earnings_yield=ey,
                price_to_book=pb,
                profitability=prof,
                covered=covered,
            )
        )
    out = analysis.build_style(data.isin, view.as_of, lines, view.unidentified_equity_weight)
    if len(ranked) > EQUITY_LIMIT:
        out.warnings.append(
            f"Only the {EQUITY_LIMIT} largest of {len(ranked)} issuers are scored for size and valuation proxies."
        )
    covered_w = sum((x.weight for x in lines if x.covered), Decimal(0))
    eq_w = sum((x.weight for x in lines), Decimal(0))
    if eq_w > 0 and covered_w < eq_w:
        out.warnings.append(
            f"The app holds statements and prices for holdings worth {covered_w / eq_w:.0%} of the fund's identified equity; the rest is left out of the size and valuation proxies."
        )
    return out


async def nport_mix(
    pool: asyncpg.Pool, data: FundData
) -> tuple[list[tuple[str, Decimal]], str | None]:
    tickers = [x.ticker for x in data.listings]
    filing = await pool.fetchrow(
        """select accession, report_period_end from public.nport_filing
           where reconcile_status='confirmed' and (fund_isin=$1 or matched_ticker = any($2::text[]))
           order by report_period_end desc, filed desc limit 1""",
        data.isin,
        tickers,
    )
    if filing is None:
        return [], None
    rows = await pool.fetch(
        "select coalesce(asset_cat,'unknown') as cat, sum(pct_value) as w from public.nport_holding where accession=$1 group by 1 order by 2 desc",
        filing["accession"],
    )
    return [
        (r["cat"], (r["w"] or Decimal(0)) / 100) for r in rows
    ], f"N-PORT {filing['accession']}, period ending {filing['report_period_end']}"


async def nport_risk(pool: asyncpg.Pool, data: FundData) -> analysis.NportRisk | None:
    tickers = [x.ticker for x in data.listings]
    row = await pool.fetchrow(
        """select accession, report_period_end, net_assets, risk_metrics from public.nport_filing
           where reconcile_status='confirmed' and (fund_isin=$1 or matched_ticker = any($2::text[]))
           order by report_period_end desc, filed desc limit 1""",
        data.isin,
        tickers,
    )
    if row is None:
        return None
    metrics = row["risk_metrics"]
    if isinstance(metrics, str):
        metrics = json.loads(metrics)
    if not isinstance(metrics, dict) or not metrics:
        return None
    return analysis.NportRisk(
        source=f"N-PORT {row['accession']}, period ending {row['report_period_end']}",
        period_end=row["report_period_end"],
        net_assets=row["net_assets"],
        metrics=metrics,
    )


async def performance_view(
    pool: asyncpg.Pool,
    data: FundData,
    ticker: str | None,
    benchmark_symbol: str | None,
    as_of: dt.date | None = None,
) -> performance.PerformanceView:
    day = as_of or dt.datetime.now(dt.UTC).date()
    options = await performance.benchmark_options(pool)
    suggested = performance.suggest(options, (data.factsheet or {}).get("benchmark_name"))
    listing = performance.pick_listing(data, ticker)
    since = day - dt.timedelta(days=6 * 365)
    closes = (
        await performance.load_prices(pool, listing.price_symbol, since, day)
        if listing and listing.price_symbol
        else []
    )
    divs: list[Dividend] = []
    ok = True
    if listing:
        raw = await performance.load_dividends(pool, listing.ticker)
        iso = normalise_currency(listing.currency)[0] if listing.currency else None
        ok = all(c is None or c == iso for _, _, c in raw)
        divs = [Dividend(ex_date=d, amount=a) for d, a, _ in raw]
    info = None
    bench: performance.Closes = []
    chosen = benchmark_symbol
    recorded = data.facts.get("benchmark_symbol")
    if chosen is None and recorded is not None:
        chosen = recorded.value
    if chosen:
        opt = next((o for o in options if o.symbol == chosen), None)
        # The recorded basis and currency describe the recorded benchmark only; a different index
        # picked with ?benchmark= must not inherit them.
        is_recorded = recorded is not None and recorded.value == chosen
        fact_basis = data.facts.get("benchmark_basis") if is_recorded else None
        fact_ccy = data.facts.get("benchmark_currency") if is_recorded else None
        from_ledger = opt is not None and opt.basis != "unknown"
        basis: performance.Basis = (
            opt.basis
            if from_ledger and opt
            else (
                fact_basis.value
                if fact_basis and fact_basis.value in ("price", "total")
                else "unknown"
            )  # type: ignore[assignment]
        )
        currency = opt.currency if opt and opt.currency else (fact_ccy.value if fact_ccy else None)
        notes = []
        notes.append(
            "Basis from the index coverage ledger."
            if from_ledger
            else "Basis you recorded for this benchmark."
            if basis != "unknown"
            else "The recorded basis applies only to the benchmark you recorded, and its return basis (price or total) is not recorded for this one."
            if recorded is not None and not is_recorded
            else "The series' return basis (price or total) is not recorded."
        )
        if recorded is not None and benchmark_symbol is None:
            notes.append("The benchmark is the one you recorded for this fund.")
        info = performance.BenchmarkInfo(
            symbol=chosen,
            label=opt.label if opt else chosen,
            basis=basis,
            currency=currency,
            note=" ".join(notes),
        )
        bench = await performance.load_prices(pool, chosen, since, day)
    view = performance.build_performance(
        data, listing, closes, divs, ok, info, bench, options, suggested, day
    )
    if data.nav:
        view.nav = performance.build_nav_for_listing(
            data.nav, closes, listing.currency if listing else None
        )
        view.nav_available = True
    return view


async def checklist(
    pool: asyncpg.Pool, data: FundData, as_of: dt.date | None = None
) -> analysis.Checklist:
    rows, hv = await holdings(pool, data)
    st = await style(pool, data, rows, hv, as_of)
    perf = await performance_view(pool, data, None, None, as_of)
    suggested = perf.suggested_benchmark
    if suggested and perf.benchmark is None:
        perf = await performance_view(pool, data, None, suggested, as_of)
    diff, note = performance.tracking_headline(perf)
    return analysis.build_checklist(
        data,
        hv,
        st,
        today=as_of or dt.datetime.now(dt.UTC).date(),
        tracking_difference=diff,
        tracking_note=note,
    )


async def comparison(
    pool: asyncpg.Pool, isins: list[str], owner_id: Any, as_of: dt.date | None = None
) -> tuple[compare.FundComparison | None, list[str]]:
    day = as_of or dt.datetime.now(dt.UTC).date()
    prepared: list[compare.Prepared] = []
    missing: list[str] = []
    for isin in isins:
        data = await load_fund(pool, isin, owner_id, as_of)
        if data is None:
            missing.append(isin)
            continue
        rows, hv = await holdings(pool, data, top=10)
        st = await style(pool, data, rows, hv, as_of)
        perf = await performance_view(pool, data, None, None, as_of)
        f = data.factsheet or {}
        meta = data.meta or {}
        lag = (day - hv.as_of).days if hv.as_of else None
        c = st.concentration
        returns = [
            compare.ReturnCell(
                label=p.label,
                value=p.fund_price.value,
                reason=None
                if p.fund_price.value is not None
                else (p.fund_price.warnings[0] if p.fund_price.warnings else None),
            )
            for p in perf.periods
            if not p.label.startswith("Since")
        ] or [
            compare.ReturnCell(
                label="Price return",
                value=None,
                reason=perf.warnings[0] if perf.warnings else "no stored prices",
            )
        ]
        fund = compare.CompareFund(
            isin=isin,
            name=data.name,
            provider=f.get("provider") or meta.get("provider"),
            currency=f.get("fund_currency") or meta.get("fund_currency"),
            distribution_policy=f.get("distribution_policy"),
            expense_ratio=f.get("expense_ratio"),
            total_assets=f.get("total_assets"),
            total_assets_currency=f.get("total_assets_currency"),
            holdings_as_of=hv.as_of,
            holdings_lag_days=lag,
            stale=lag is not None and lag > compare.STALE_DAYS,
            portfolio_id=meta.get("portfolio_id"),
            duplicate_of=None,
            equity_weight=c.equity_weight if c else None,
            residual_weight=hv.residual_weight,
            unidentified_equity_weight=hv.unidentified_equity_weight if hv.as_of else None,
            issuers=c.issuers if c else None,
            top10_weight=c.top10_weight if c else None,
            effective_issuers=c.effective_issuers if c else None,
            returns=returns,
        )
        prepared.append(compare.Prepared(fund=fund, rows=rows, view=hv, style=st))
    if len(prepared) < 2:
        return None, missing
    return compare.build_comparison(prepared), missing
