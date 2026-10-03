"""Research step 1 (code): gather what is stored about a ticker into a source registry, and
write the code sections from it.

Code sections state figures from stored records only, each sentence citing the records it
reads: financial trend (verified facts, growth computed here), valuation (computed here,
never taken from a model or a third-party site), ownership (13F), relationships (GLEIF,
confirmed Wikidata/EDGAR links), risk (Stooq bars) and liquidity (FITRS). The model
sections are written later from the same registry.
"""

import json
import re
from decimal import Decimal
from typing import Any

import asyncpg
from pydantic import BaseModel, Field

from steward.config.research import ResearchProfile
from steward.config.strategy import StrategyConfig
from steward.config.thesis import ThesisConfig
from steward.data_context.research import claim_sentence, company_context
from steward.earnings.metrics import BASE_METRICS
from steward.ownership.read import holding_ownership
from steward.reference.read import holding_identity, price_history
from steward.research.sources import CitedSentence, Registry, iso_known_at, sec_filing_url

SEC_13F_INDEX = "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"
VOTING_RIGHTS = re.compile(
    r"holding\(s\) in company|voting rights|stimmrechtsmitteilung|major shareholding",
    re.IGNORECASE,
)
METRIC_NAMES = {
    "revenue": "Revenue",
    "gross_profit": "Gross profit",
    "operating_income": "Operating income",
    "net_income": "Net income",
    "eps_diluted": "Diluted EPS",
}


class CodeSection(BaseModel):
    id: str
    sentences: list[CitedSentence] = Field(default_factory=list)
    note: str | None = None


class Valuation(BaseModel):
    method: str
    value: float | None
    display: str
    formula: str
    source_ids: list[str]
    note: str | None = None


class Gathered(BaseModel):
    ticker: str
    name: str
    owned: bool
    currency: str | None
    sources: list[dict[str, Any]]
    sections: dict[str, CodeSection]
    valuation: list[Valuation]
    peers: list[str]
    thesis_source: str | None
    strategy_source: str | None
    news_source_ids: list[str]
    context: str = Field(description="Holding, thesis and strategy text for the model.")


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def _num(x: Decimal | float | None, digits: int = 2) -> str:
    if x is None:
        return "n/a"
    return f"{float(x):,.{digits}f}"


def _quarter_index(fy: int, fp: str) -> int | None:
    return fy * 4 + int(fp[1]) if fp in ("Q1", "Q2", "Q3", "Q4") else None


def _qi(f: asyncpg.Record) -> int | None:
    """A fact's quarter as a running number (consecutive quarters differ by 1)."""
    return _quarter_index(f["fiscal_year"], f["fiscal_period"])


async def _facts(pool: asyncpg.Pool, ticker: str) -> list[asyncpg.Record]:
    rows: list[asyncpg.Record] = await pool.fetch(
        "select id, metric, fiscal_year, fiscal_period, period, value_units, value_as_written,"
        " scale, unit, currency, snippet, source_url, extracted_at"
        " from public.fact where ticker = $1"
        " and status = 'verified' and basis = 'reported' and metric = any($2::text[])"
        " order by fiscal_year desc, fiscal_period desc, id desc",
        ticker,
        list(BASE_METRICS),
    )
    return rows


def _fact_source(reg: Registry, f: asyncpg.Record) -> str:
    return reg.add(
        "fact",
        f["id"],
        f"{METRIC_NAMES.get(f['metric'], f['metric'])} {f['period']}: {f['value_as_written']}",
        url=f["source_url"],
        app_path=f"/evidence/{f['id']}",
        tier=1,
        snippet=f["snippet"],
        as_of=f["period"],
        known_at=iso_known_at(f["extracted_at"]),
    )


def financial_trend(reg: Registry, facts: list[asyncpg.Record]) -> CodeSection:
    section = CodeSection(id="financial_trend")
    quarterly = [f for f in facts if _qi(f)]
    if not quarterly:
        section.note = "No verified quarterly figures are stored for this ticker."
        return section
    latest = max(_qi(f) or 0 for f in quarterly)
    # Facts come newest first: keep the first (newest) per metric and quarter.
    by_key: dict[tuple[str, int | None], asyncpg.Record] = {}
    for f in quarterly:
        by_key.setdefault((f["metric"], _qi(f)), f)
    for metric in BASE_METRICS:
        now = by_key.get((metric, latest))
        if now is None:
            continue
        before = by_key.get((metric, latest - 4))
        ids = [_fact_source(reg, now)]
        scale = "" if now["unit"] == "per_share" or now["scale"] == "units" else f" {now['scale']}"
        text = (
            f"{METRIC_NAMES[metric]} was {now['value_as_written']}{scale} "
            f"{now['currency'] or ''} in {now['period']}".replace("  ", " ")
        )
        if before is not None and before["value_units"]:
            ids.append(_fact_source(reg, before))
            change = float(now["value_units"] / before["value_units"] - 1)
            if before["value_units"] > 0:
                text += f", {_pct(change)} on {before['period']} ({before['value_as_written']})"
        section.sentences.append(CitedSentence(text=text.strip() + ".", source_ids=ids))
    return section


async def _price(
    pool: asyncpg.Pool, reg: Registry, ticker: str, symbol: str | None, position: Any
) -> tuple[Decimal | None, str | None, str | None]:
    """(price, currency, source id): Trading212's price for a holding, else Stooq's close."""
    if position is not None:
        sid = reg.add(
            "position",
            f"{ticker}:{position['snapshot_date']}",
            f"Trading212 price on {position['snapshot_date']}",
            app_path=f"/holdings/{ticker}",
            tier=1,
            snippet=f"current price {position['current_price']} {position['instrument_currency']}",
            as_of=str(position["snapshot_date"]),
            known_at=iso_known_at(position["captured_at"]),
        )
        return position["current_price"], position["instrument_currency"], sid
    if symbol:
        bar = await pool.fetchrow(
            "select date, close, fetched_at from public.price_bar where symbol = $1"
            " and (bar_type = 'adjusted' or (source = 'eodhd' and bar_type = 'raw'))"
            " order by date desc limit 1",
            symbol,
        )
        if bar is not None:
            sid = reg.add(
                "price",
                f"{symbol}:{bar['date']}",
                f"Stooq daily close {symbol} {bar['date']}",
                url=f"https://stooq.com/q/?s={symbol}",
                tier=2,
                snippet=f"close {bar['close']} on {bar['date']}",
                as_of=str(bar["date"]),
                known_at=iso_known_at(bar["fetched_at"]),
            )
            return bar["close"], None, sid
    return None, None, None


async def valuation(
    pool: asyncpg.Pool,
    reg: Registry,
    ticker: str,
    facts: list[asyncpg.Record],
    methods: list[str],
    symbol: str | None,
    position: Any,
    instrument_currency: str | None,
) -> tuple[CodeSection, list[Valuation]]:
    section = CodeSection(id="valuation")
    out: list[Valuation] = []
    price, price_ccy, price_sid = await _price(pool, reg, ticker, symbol, position)
    price_ccy = price_ccy or instrument_currency
    eps = sorted(
        (f for f in facts if f["metric"] == "eps_diluted" and _qi(f)),
        key=lambda f: _qi(f) or 0,
        reverse=True,
    )
    # The latest four consecutive quarters (one fact per quarter: the newest stored).
    last4: list[asyncpg.Record] = []
    for f in eps:
        if last4 and _qi(f) == _qi(last4[-1]):
            continue
        if last4 and _qi(f) != (_qi(last4[-1]) or 0) - 1:
            break
        last4.append(f)
        if len(last4) == 4:
            break
    ttm = sum((f["value_units"] for f in last4), Decimal(0)) if len(last4) == 4 else None
    eps_ccy = last4[0]["currency"] if last4 else None
    pe: float | None = None
    reason = None
    if price is None:
        reason = "no stored price"
    elif ttm is None:
        reason = "fewer than four consecutive quarters of verified diluted EPS"
    elif eps_ccy and price_ccy and eps_ccy != price_ccy:
        reason = f"the price is in {price_ccy} and EPS in {eps_ccy}"
    elif ttm <= 0:
        reason = "trailing EPS is not positive"
    else:
        pe = float(price / ttm)
    eps_ids = [_fact_source(reg, f) for f in last4] if len(last4) == 4 else []
    ids = ([price_sid] if price_sid else []) + eps_ids
    if "pe_ttm" in methods:
        display = f"{pe:.1f}x" if pe is not None else "not computable"
        out.append(
            Valuation(
                method="pe_ttm",
                value=pe,
                display=display,
                formula="price / sum of the last four quarters' diluted EPS",
                source_ids=ids,
                note=reason,
            )
        )
        if pe is not None:
            section.sentences.append(
                CitedSentence(
                    text=f"At a price of {_num(price)} {price_ccy} and trailing diluted EPS of "
                    f"{_num(ttm)} {eps_ccy}, the trailing P/E is {pe:.1f}x.",
                    source_ids=ids,
                )
            )
        else:
            section.sentences.append(
                CitedSentence(text=f"Trailing P/E is not computable: {reason}.", source_ids=[])
            )
    if "earnings_yield" in methods:
        ey = 1 / pe if pe else None
        out.append(
            Valuation(
                method="earnings_yield",
                value=ey,
                display=f"{ey * 100:.2f}%" if ey is not None else "not computable",
                formula="trailing diluted EPS / price",
                source_ids=ids,
                note=reason if ey is None else None,
            )
        )
        if ey is not None:
            section.sentences.append(
                CitedSentence(text=f"The earnings yield is {ey * 100:.2f}%.", source_ids=ids)
            )
    if "dividend_yield_ttm" in methods:
        rows = await pool.fetch(
            "select id, occurred_at, created_at,"
            " (raw ->> 'grossAmountPerShare')::numeric as per_share"
            " from public.transaction where type = 'dividend' and ticker = $1"
            " and occurred_at >= now() - interval '365 days'"
            " and raw ? 'grossAmountPerShare' order by occurred_at",
            ticker,
        )
        per_share = sum((r["per_share"] or Decimal(0) for r in rows), Decimal(0))
        dy = float(per_share / price) if rows and price else None
        div_ids = [
            reg.add(
                "position",
                f"dividend:{r['id']}",
                f"Dividend received {r['occurred_at'].date()}",
                app_path="/transactions",
                tier=1,
                snippet=f"gross {r['per_share']} per share",
                as_of=str(r["occurred_at"].date()),
                known_at=iso_known_at(r["created_at"]),
            )
            for r in rows
        ]
        note = None if dy is not None else "no dividends received in the last 12 months"
        out.append(
            Valuation(
                method="dividend_yield_ttm",
                value=dy,
                display=f"{dy * 100:.2f}%" if dy is not None else "not computable",
                formula="gross dividends per share received in the last 12 months / price",
                source_ids=div_ids + ([price_sid] if price_sid else []),
                note=note,
            )
        )
        if dy is not None:
            section.sentences.append(
                CitedSentence(
                    text=f"Dividends of {_num(per_share, 4)} per share in the last 12 months "
                    f"give a trailing yield of {dy * 100:.2f}%.",
                    source_ids=div_ids + ([price_sid] if price_sid else []),
                )
            )
    return section, out


async def ownership(pool: asyncpg.Pool, reg: Registry, ticker: str, top: int = 3) -> CodeSection:
    section = CodeSection(id="ownership")
    data = await holding_ownership(pool, ticker, periods=2, top=top)
    if data.periods:
        p = data.periods[0]
        sid = reg.add(
            "filing13f",
            f"13F:{p.period}:{','.join(data.cusips)}",
            f"SEC Form 13F data sets, period {p.period}",
            url=SEC_13F_INDEX,
            app_path=f"/holdings/{ticker}",
            tier=1,
            snippet=f"{p.holders} holders, {_num(p.shares, 0)} shares, value "
            f"${_num(p.value_usd, 0)}, latest filing {p.latest_filed}",
            as_of=str(p.period),
        )
        text = (
            f"In 13F filings for the period ending {p.period}, {p.holders:,} institutional "
            f"managers reported {_num(p.shares, 0)} shares"
        )
        if p.holders_change is not None:
            text += f" ({p.holders_change:+,} holders on the previous period)"
        section.sentences.append(CitedSentence(text=text + ".", source_ids=[sid]))
        for h in data.top_holders[:top]:
            hid = reg.add(
                "filing13f",
                h.accession,
                f"13F filing {h.accession} ({h.manager_name or h.cik})",
                url=sec_filing_url(h.accession, h.cik),
                tier=1,
                snippet=f"{h.manager_name}: {_num(h.shares, 0)} shares",
                as_of=str(p.period),
            )
            section.sentences.append(
                CitedSentence(
                    text=f"{h.manager_name or 'CIK ' + h.cik} reported {_num(h.shares, 0)} shares.",
                    source_ids=[hid],
                )
            )
    notices = await pool.fetch(
        "select id, url, headline, summary, outlet, tier, published_at, first_seen_at"
        " from public.news_item"
        " where $1 = any(tickers) and published_at >= now() - interval '365 days'"
        " order by published_at desc limit 200",
        ticker,
    )
    for n in [n for n in notices if VOTING_RIGHTS.search(n["headline"] or "")][:3]:
        nid = reg.add(
            "news_item",
            n["id"],
            n["headline"],
            url=n["url"],
            tier=n["tier"],
            snippet=n["summary"],
            as_of=str(n["published_at"].date()),
            known_at=iso_known_at(n["first_seen_at"]),
        )
        section.sentences.append(
            CitedSentence(
                text=f"Voting-rights notice of {n['published_at'].date()}: {n['headline']}.",
                source_ids=[nid],
            )
        )
    if not section.sentences:
        section.note = "No 13F data or voting-rights notices are stored for this ticker."
    return section


async def relationships(
    pool: asyncpg.Pool, reg: Registry, ticker: str
) -> tuple[CodeSection, list[str]]:
    section = CodeSection(id="relationships")
    identity = await holding_identity(pool, ticker)
    peers: list[str] = []
    if identity is None:
        section.note = "No identity data is stored for this ticker."
        return section, peers
    tier_of = {"gleif": 1, "edgar": 1, "owner": 1, "wikidata": 3}
    created = {
        r["id"]: r["created_at"]
        for r in await pool.fetch(
            "select id, created_at from public.entity_relationship where ticker = $1", ticker
        )
    }

    def rel_source(r: Any) -> str:
        return reg.add(
            "relationship",
            r.id,
            f"{r.type.replace('_', ' ')}: {r.object_name} ({', '.join(r.sources)})",
            url=r.source_ref,
            app_path=f"/holdings/{ticker}",
            tier=r.tier,
            snippet=f"{r.type} {r.object_name}, source {r.source}, as of {r.as_of}",
            as_of=str(r.as_of) if r.as_of else None,
            known_at=iso_known_at(created.get(r.id)),
        )

    for r in identity.parents:
        label = (
            "ultimate consolidating parent"
            if r.type == "ultimate_parent"
            else "direct consolidating parent"
        )
        section.sentences.append(
            CitedSentence(text=f"Its {label} is {r.object_name}.", source_ids=[rel_source(r)])
        )
        if r.type == "ultimate_parent":
            others = await pool.fetch(
                "select distinct ticker from public.entity_relationship where type ="
                " 'ultimate_parent' and status = 'confirmed' and object_key = ("
                " select object_key from public.entity_relationship where id = $1)"
                " and ticker <> $2",
                r.id,
                ticker,
            )
            if others:
                names = ", ".join(o["ticker"] for o in others)
                section.sentences.append(
                    CitedSentence(
                        text=f"Also held with the same ultimate parent, {r.object_name}: "
                        f"{names} (concentration across holdings).",
                        source_ids=[rel_source(r)],
                    )
                )
    if identity.subsidiary_count:
        subs = identity.subsidiaries[:5]
        section.sentences.append(
            CitedSentence(
                text=f"GLEIF lists {identity.subsidiary_count} direct subsidiaries, including "
                f"{', '.join(s.object_name for s in subs)}.",
                source_ids=[rel_source(s) for s in subs],
            )
        )
    for r in identity.related:
        peers.append(r.object_name)
        section.sentences.append(
            CitedSentence(text=f"Confirmed {r.type}: {r.object_name}.", source_ids=[rel_source(r)])
        )
    del tier_of
    if not section.sentences:
        section.note = "No confirmed relationships are stored for this ticker."
    return section, peers


async def risk(pool: asyncpg.Pool, reg: Registry, ticker: str) -> tuple[CodeSection, str | None]:
    section = CodeSection(id="risk")
    history = await price_history(pool, ticker, 730)
    if history.risk is None or not history.bars:
        section.note = history.gap or "No price history is stored for this ticker."
        return section, None
    first, last = history.bars[0].date, history.bars[-1].date
    sid = reg.add(
        "price",
        f"{history.symbol}:{first}:{last}",
        f"{history.source} daily closes {history.symbol} {first} to {last}",
        url=(
            f"https://stooq.com/q/?s={history.symbol}"
            if history.source == "Stooq"
            else f"https://finance.yahoo.com/quote/{history.symbol}/history"
        ),
        app_path=f"/holdings/{ticker}",
        tier=2 if history.source == "Stooq" else 3,
        snippet=f"{len(history.bars)} daily closes; last {history.bars[-1].close} on {last}",
        as_of=str(last),
        known_at=iso_known_at(
            await pool.fetchval(
                "select max(fetched_at) from public.price_bar where symbol = $1"
                " and date between $2 and $3",
                history.symbol,
                first,
                last,
            )
        ),
    )
    r = history.risk
    parts = []
    if r.returns.get("1y") is not None:
        parts.append(f"a one-year return of {_pct(r.returns['1y'] or 0)}")
    if r.volatility is not None:
        parts.append(f"annualised volatility of {r.volatility * 100:.1f}%")
    if r.max_drawdown is not None:
        parts.append(f"a maximum drawdown of {r.max_drawdown * 100:.1f}%")
    if r.beta is not None:
        parts.append(f"a beta of {r.beta:.2f} against {history.benchmark_symbol}")
    if parts:
        section.sentences.append(
            CitedSentence(
                text=f"Computed from {r.observations} daily closes to {last}: {', '.join(parts)}.",
                source_ids=[sid],
            )
        )
    top = sorted(
        (c for c in history.correlations if c.correlation is not None),
        key=lambda c: -(c.correlation or 0),
    )[:3]
    if top:
        section.sentences.append(
            CitedSentence(
                text="Most correlated holdings over the last year: "
                + ", ".join(f"{c.ticker} ({c.correlation:.2f})" for c in top)
                + ".",
                source_ids=[sid],
            )
        )
    return section, sid


async def liquidity(
    pool: asyncpg.Pool, reg: Registry, isin: str | None, position: Any, ticker: str
) -> CodeSection:
    section = CodeSection(id="liquidity")
    row = (
        await pool.fetchrow("select * from public.listing_liquidity where isin = $1", isin)
        if isin
        else None
    )
    if row is None:
        section.note = "No ESMA FITRS liquidity record is stored (non-EU listings have none)."
        return section
    sid = reg.add(
        "listing",
        f"fitrs:{isin}",
        f"ESMA FITRS liquidity, {isin}",
        url="https://registers.esma.europa.eu/publication/searchRegister?core=esma_registers_fitrs_equities",
        app_path=f"/holdings/{ticker}",
        tier=1,
        snippet=f"liquid {row['liquid']}, average daily turnover {row['avg_daily_turnover']} "
        f"{row['turnover_currency']}, {row['period_from']} to {row['period_to']}",
        as_of=str(row["period_to"]) if row["period_to"] else None,
        known_at=iso_known_at(row["fetched_at"]),
    )
    text = (
        f"ESMA classifies the share as {'liquid' if row['liquid'] else 'not liquid'}, with "
        f"average daily turnover of {_num(row['avg_daily_turnover'], 0)} "
        f"{row['turnover_currency'] or ''}".strip()
    )
    ids = [sid]
    if (
        position is not None
        and row["avg_daily_turnover"]
        and row["turnover_currency"] == position["instrument_currency"]
    ):
        days = float(position["value"] / row["avg_daily_turnover"])
        text += f"; the position is {days:.4f} days of turnover"
        ids.append(
            reg.add(
                "position",
                f"{ticker}:{position['snapshot_date']}",
                f"Trading212 position on {position['snapshot_date']}",
                app_path=f"/holdings/{ticker}",
                tier=1,
                snippet=f"value {position['value']} {position['instrument_currency']}",
                as_of=str(position["snapshot_date"]),
                known_at=iso_known_at(position["captured_at"]),
            )
        )
    section.sentences.append(CitedSentence(text=text + ".", source_ids=ids))
    return section


async def data_context_section(
    pool: asyncpg.Pool, reg: Registry, ticker: str, max_sentences: int = 12
) -> CodeSection:
    """Stored free-source observations (financial history, forward data, confirmed network,
    patents) as cited sentences; every sentence states the date the record became known.

    Not a configured report section: it is offered to the writer as already-written material,
    so a model section can cite the claims without a new profile section.
    """
    section = CodeSection(id="data_context")
    context = await company_context(pool, ticker)
    for claim in context.claims[:max_sentences]:
        sid = reg.add(
            "observation",
            claim.claim_id,
            claim.label,
            url=claim.source_url,
            app_path=f"/holdings/{ticker}",
            tier=1 if claim.provenance == "primary" else 2,
            snippet=claim_sentence(claim),
            as_of=str(claim.known_as_of),
            known_at=iso_known_at(claim.known_as_of),
        )
        section.sentences.append(CitedSentence(text=claim_sentence(claim), source_ids=[sid]))
    if not section.sentences:
        section.note = "No free-source observation is stored for this ticker."
    return section


async def gather(
    pool: asyncpg.Pool,
    ticker: str,
    profile: ResearchProfile,
    thesis: ThesisConfig | None,
    strategy: StrategyConfig | None,
    strategy_version: int | None,
    name_hint: str | None = None,
) -> Gathered:
    reg = Registry()
    inst = await pool.fetchrow(
        "select t212_ticker, name, isin, currency, price_symbol from public.instrument"
        " where t212_ticker = $1",
        ticker,
    )
    position = await pool.fetchrow(
        "select * from public.position_snapshot where ticker = $1 and snapshot_date = coalesce("
        " (select max(snapshot_date) from public.account_snapshot),"
        " (select max(snapshot_date) from public.position_snapshot))",
        ticker,
    )
    name = (inst["name"] if inst else None) or name_hint or ticker
    facts = await _facts(pool, ticker)
    sections: dict[str, CodeSection] = {}
    wanted = set(profile.sections)
    if "financial_trend" in wanted:
        sections["financial_trend"] = financial_trend(reg, facts)
    vals: list[Valuation] = []
    if "valuation" in wanted:
        sections["valuation"], vals = await valuation(
            pool,
            reg,
            ticker,
            facts,
            list(profile.valuation_methods),
            inst["price_symbol"] if inst else None,
            position,
            inst["currency"] if inst else None,
        )
    if "ownership" in wanted:
        sections["ownership"] = await ownership(pool, reg, ticker, profile.top_holders)
    peers: list[str] = []
    if "relationships" in wanted or profile.include_peers:
        rel_section, peers = await relationships(pool, reg, ticker)
        if "relationships" in wanted:
            sections["relationships"] = rel_section
    if "risk" in wanted:
        sections["risk"], _ = await risk(pool, reg, ticker)
    if "liquidity" in wanted:
        sections["liquidity"] = await liquidity(
            pool, reg, inst["isin"] if inst else None, position, ticker
        )
    extra = await data_context_section(pool, reg, ticker)
    if extra.sentences:
        sections["data_context"] = extra

    thesis_sid = None
    if thesis is not None:
        kpis = "; ".join(
            f"{k.id}: {k.metric} {k.operator} {k.threshold} {k.unit}" for k in thesis.kpis
        )
        thesis_sid = reg.add(
            "thesis",
            ticker,
            f"Thesis for {ticker}",
            app_path=f"/holdings/{ticker}",
            tier=1,
            snippet=f"Why owned: {thesis.why_owned} KPIs: {kpis or 'none'}. Exit conditions: "
            f"{'; '.join(thesis.exit_conditions) or 'none'}.",
        )
        verdicts = await pool.fetch(
            "select distinct on (kpi_id) id, kpi_id, status, period, rationale, created_at from"
            " public.verdict where ticker = $1 order by kpi_id, id desc",
            ticker,
        )
        for v in verdicts:
            reg.add(
                "verdict",
                v["id"],
                f"KPI {v['kpi_id']} ({v['period'] or 'no period'}): {v['status']}",
                app_path=f"/holdings/{ticker}",
                tier=1,
                snippet=v["rationale"],
                known_at=iso_known_at(v["created_at"]),
            )
    strategy_sid = None
    if strategy is not None:
        rules = "; ".join(
            f"{r.id} ({r.type} {r.operator} {getattr(r, 'value', '')}): {r.description}"
            for r in strategy.enabled_rules
        )
        strategy_sid = reg.add(
            "strategy",
            strategy_version or "active",
            "Investment strategy",
            app_path="/config/strategy",
            tier=1,
            snippet=f"Goals: {strategy.goals or 'none'}. Horizon: "
            f"{strategy.horizon_years or 'n/a'} years. Notes: {strategy.notes or 'none'}. "
            f"Rules: {rules or 'none'}.",
        )
    news = await pool.fetch(
        "select id, url, headline, summary, outlet, tier, published_at, first_seen_at"
        " from public.news_item"
        " where $1 = any(tickers) and tier = any($2::int[])"
        " order by published_at desc limit 15",
        ticker,
        list(profile.source_tiers),
    )
    news_ids = [
        reg.add(
            "news_item",
            n["id"],
            n["headline"],
            url=n["url"],
            tier=n["tier"],
            snippet=(n["summary"] or "")[:800] or None,
            as_of=str(n["published_at"].date()),
            known_at=iso_known_at(n["first_seen_at"]),
        )
        for n in news
    ]
    context = json.dumps(
        {
            "ticker": ticker,
            "name": name,
            "owned": position is not None,
            "weight": float(position["weight"]) if position and position["weight"] else None,
            "peers": peers if profile.include_peers else [],
        },
        ensure_ascii=False,
    )
    return Gathered(
        ticker=ticker,
        name=name,
        owned=position is not None,
        currency=inst["currency"] if inst else None,
        sources=reg.dump(),
        sections=sections,
        valuation=vals,
        peers=peers if profile.include_peers else [],
        thesis_source=thesis_sid,
        strategy_source=strategy_sid,
        news_source_ids=news_ids,
        context=context,
    )
