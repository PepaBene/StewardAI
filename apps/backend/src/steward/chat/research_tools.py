"""Read-only research tools over the datasets the chat did not reach before: filed evidence,
fundamentals and valuation, price history, macro, the strategy's rules, transactions, risk
history, a holding's thesis with its latest reports, and data freshness.

Every function reuses an existing read service (nothing here writes, and nothing takes SQL
from the model), bounds its rows and dates, registers what it returns in the source registry
so the answer can cite it, and reports each figure in the shared envelope of
`steward.chat.envelope`: a status, a unit, a period, an `as_of` and a reason when it is absent.
"""

import datetime as dt
import json
from decimal import Decimal
from typing import Any

import asyncpg

from steward.analytics.contract import AnalyticsResult, Status
from steward.chat import envelope as env
from steward.company import ratios, service, valuation
from steward.config.jobs import DEFAULT_FRESHNESS_DAYS, JobsConfig
from steward.config.store import load_active
from steward.config.strategy import StrategyConfig
from steward.config.thesis import ThesesConfig
from steward.data_context import evidence
from steward.news.read_analysis import like_pattern
from steward.portfolio.transactions import TransactionFilters, list_transactions
from steward.research.sources import Registry

REPORT_PATHS = {"research": "/research/{}", "review": "/reviews/{}"}
EVIDENCE_SECTIONS = (
    "filed_notes",
    "control_stakes",
    "institutional",
    "fund_positions",
    "insider",
    "dependencies",
    "deals",
    "consensus",
)
PRICE_STALE_DAYS = 7
MAX_PRICE_POINTS = 120
MAX_PRICE_DAYS = 3650
MAX_RATIOS = 80
MAX_SOURCED_ITEMS = 15
TEXT_CHARS = 600
RISK_KINDS = ("ex_ante", "replay", "factors", "shock")


def parse_cutoff(as_of: str | None) -> dt.datetime | None:
    """End of the given ISO day, UTC. Raises ValueError for anything that is not a date."""
    if not as_of:
        return None
    return dt.datetime.combine(dt.date.fromisoformat(as_of), dt.time.max, dt.UTC)


def _today() -> dt.date:
    return dt.datetime.now(dt.UTC).date()


# ------------------------------------------------------------------ filed evidence


def _shrink(node: Any, limit: int) -> tuple[Any, bool]:
    """Lists cut to `limit` items and long strings to TEXT_CHARS, recursively; whether
    anything was cut."""
    if isinstance(node, list):
        cut = len(node) > limit
        out = []
        for item in node[:limit]:
            value, inner = _shrink(item, limit)
            out.append(value)
            cut = cut or inner
        return out, cut
    if isinstance(node, dict):
        cut = False
        result = {}
        for key, item in node.items():
            value, inner = _shrink(item, limit)
            result[key] = value
            cut = cut or inner
        return result, cut
    if isinstance(node, str) and len(node) > TEXT_CHARS:
        return node[:TEXT_CHARS] + "…", True
    return node, False


def _attach_sources(
    registry: Registry, node: Any, ticker: str, kind: str, budget: list[int]
) -> None:
    """Gives each record that names its own source URL a registry id, up to `budget` records."""
    if isinstance(node, list):
        for item in node:
            _attach_sources(registry, item, ticker, kind, budget)
    elif isinstance(node, dict):
        url = node.get("source_url")
        if isinstance(url, str) and url and budget[0] > 0:
            budget[0] -= 1
            label = node.get("label") or node.get("concept") or node.get("relationship") or kind
            passage = node.get("passage") or node.get("source_passage")
            node["source_id"] = registry.add(
                "observation",
                url,
                f"{ticker} {label}"[:200],
                url=url,
                tier=1,
                snippet=passage[:TEXT_CHARS] if isinstance(passage, str) else None,
                as_of=str(node.get("known_at") or node.get("period_end") or "") or None,
            )
        for item in node.values():
            _attach_sources(registry, item, ticker, kind, budget)


async def filing_evidence(
    pool: asyncpg.Pool,
    registry: Registry,
    ticker: str,
    section: str,
    as_of: str | None,
    limit: Any,
) -> dict[str, Any]:
    """One section of the stored filed evidence for a ticker, as the app's evidence pages
    read it: coverage first, then items that each carry their source URL and known-at date."""
    if section not in EVIDENCE_SECTIONS:
        return {"error": f"section must be one of {', '.join(EVIDENCE_SECTIONS)}"}
    try:
        cutoff = parse_cutoff(as_of)
    except ValueError:
        return {"error": "as_of must be an ISO date"}
    rows = env.clamp_limit(limit, default=10, maximum=25)
    data: Any
    coverage: Any
    if section == "filed_notes":
        data = await evidence.filed_notes(pool, ticker, cutoff)
        coverage = data.coverage
    elif section == "control_stakes":
        data = (await evidence.positions(pool, ticker, cutoff)).control
        coverage = data.coverage
    elif section == "institutional":
        data = (await evidence.positions(pool, ticker, cutoff)).institutional
        coverage = data.coverage
    elif section == "fund_positions":
        data = (await evidence.positions(pool, ticker, cutoff)).funds
        coverage = data.coverage
    elif section == "insider":
        data = await evidence.insider(pool, ticker, cutoff)
        coverage = data.coverage
    elif section == "dependencies":
        data = await evidence.dependencies(pool, ticker, cutoff)
        coverage = data.coverage
    elif section == "deals":
        data = await evidence.deals(pool, ticker, cutoff)
        coverage = data.coverage
    else:
        data = await evidence.consensus(pool, ticker, cutoff)
        coverage = data.coverage
    body, truncated = _shrink(data.model_dump(mode="json"), rows)
    _attach_sources(registry, body, ticker, section, [MAX_SOURCED_ITEMS])
    known = cutoff.date().isoformat() if cutoff else env.plain(_today())
    status = "missing" if coverage.state == "not_covered" else "ok"
    return {
        "ticker": ticker,
        "section": section,
        "as_of": known,
        "status": status,
        "coverage": {"state": coverage.state, "reason": coverage.reason},
        "data": body,
        "page": {"limit": rows, "truncated": truncated},
        "note": "Records learned after as_of are hidden. Control stakes, 13F and fund "
        "positions are separate channels and are never added together.",
    }


# ------------------------------------------------------------------ fundamentals


def _ratio_rows(view: ratios.RatiosView, wanted: set[str]) -> tuple[list[ratios.RatioGroup], int]:
    groups = [g for g in view.groups if not wanted or g.id in wanted]
    return groups, sum(len(g.ratios) for g in groups)


MAX_SERIES_PERIODS = 8
MAX_SERIES_POINTS = 300


async def fundamentals(
    pool: asyncpg.Pool,
    registry: Registry,
    ticker: str,
    groups: list[str] | None,
    periods: Any = None,
) -> dict[str, Any]:
    """The latest value of each financial ratio, from the filed statements, with its formula,
    period and the company's own five-year median. Without `groups` the group list is
    returned together with every ratio's latest value. With `periods` (1-8) each ratio also
    carries its last N values as a series; the ratio count is then reduced so the whole
    answer stays within 300 series points."""
    company = await service.load_company(pool, ticker)
    if company is None:
        return {"error": f"{ticker} is not in the covered universe"}
    view = ratios.build_ratios(
        ticker,
        company.annual,
        company.quarterly,
        company.market,
        sector=company.sector,
        industry=company.industry,
    )
    wanted = {g for g in (groups or []) if isinstance(g, str)}
    known_groups = {g.id for g in view.groups}
    if wanted - known_groups:
        return {
            "error": f"unknown groups {sorted(wanted - known_groups)}; use {sorted(known_groups)}"
        }
    chosen, total = _ratio_rows(view, wanted)
    out_groups: list[dict[str, Any]] = []
    n_periods = env.clamp_limit(periods, default=1, maximum=MAX_SERIES_PERIODS) if periods else 0
    max_ratios = min(MAX_RATIOS, MAX_SERIES_POINTS // n_periods) if n_periods else MAX_RATIOS
    budget = max_ratios
    for group in chosen:
        rows: list[dict[str, Any]] = []
        lines: list[str] = []
        for row in group.ratios:
            if budget <= 0:
                break
            budget -= 1
            latest = row.latest
            money = row.unit in ("money", "per_share")
            if latest is None or latest.value is None:
                figure = env.absent(
                    "missing",
                    (latest.reason if latest and latest.reason else "no value for this ratio"),
                    unit=row.unit,
                    period=latest.label if latest else None,
                )
            else:
                figure = env.datum(
                    latest.value,
                    unit=row.unit,
                    currency=view.statement_currency if money else None,
                    period=latest.label,
                    as_of=view.as_of,
                )
                lines.append(f"{row.label} {latest.value:.4g} ({latest.label})")
            entry: dict[str, Any] = {
                "id": row.id,
                "label": row.label,
                "formula": row.formula,
                "latest": figure,
                "own_median_5y": env.plain(row.own_median_5y),
            }
            if n_periods:
                series: list[dict[str, Any]] = []
                for point in row.points[-n_periods:]:
                    if point.value is None:
                        series.append(
                            env.absent(
                                "missing",
                                point.reason or "no value for this period",
                                unit=row.unit,
                                period=point.label,
                            )
                        )
                        continue
                    series.append(
                        env.datum(
                            point.value,
                            unit=row.unit,
                            currency=view.statement_currency if money else None,
                            period=point.label,
                        )
                    )
                    lines.append(f"{row.label} {point.value:.4g} ({point.label})")
                entry["series"] = series
            rows.append(entry)
        sid = registry.add(
            "observation",
            f"ratios:{ticker}:{group.id}:{view.input_hash[:12]}",
            f"{ticker} {group.label} ratios",
            app_path=f"/companies/{ticker}",
            tier=1,
            snippet="; ".join(lines)[:3000] or None,
            as_of=str(view.as_of) if view.as_of else None,
        )
        out_groups.append({"id": group.id, "label": group.label, "source_id": sid, "ratios": rows})
    return {
        "ticker": ticker,
        "as_of": env.plain(view.as_of),
        "statement_currency": view.statement_currency,
        "price": env.datum(
            view.price, currency=view.price_currency, as_of=view.price_date, unit="price"
        )
        if view.price is not None
        else env.absent("missing", "no price stored for this ticker", unit="price"),
        "groups": out_groups,
        "page": {
            "ratios_returned": max_ratios - budget,
            "ratios_available": total,
            **({"periods": n_periods} if n_periods else {}),
        },
        "warnings": view.warnings[:8],
        "method_version": view.method_version,
        "note": "Ratios are computed in code from filed statements; a ratio without a value "
        "says why and is never zero.",
    }


def _result_datum(
    result: AnalyticsResult, source_id: str, currency: str | None = None
) -> dict[str, Any]:
    reason = result.warnings[0] if result.warnings else None
    if result.value is None or result.status in (Status.UNAVAILABLE, Status.PAID_ONLY):
        return env.absent(
            "missing", reason or "not available", unit=result.unit, source_id=source_id
        )
    return env.datum(
        result.value,
        source_id=source_id,
        as_of=result.as_of,
        unit=result.unit,
        currency=currency,
        reason=reason if result.status != Status.COMPLETE else None,
    )


async def valuation_scenarios(
    pool: asyncpg.Pool, registry: Registry, ticker: str
) -> dict[str, Any]:
    """The app's scenario DCF under default assumptions built from the company's own filed
    growth: a range across scenarios, never a single target. The assumptions are placeholders
    to edit, not forecasts, and the answer must say so."""
    company = await service.load_company(pool, ticker)
    if company is None:
        return {"error": f"{ticker} is not in the covered universe"}
    view = ratios.build_ratios(
        ticker,
        company.annual,
        company.quarterly,
        company.market,
        sector=company.sector,
        industry=company.industry,
    )
    defaults = valuation.default_scenarios(company.annual)
    result = valuation.build_valuation(
        ticker,
        company.annual,
        company.quarterly,
        company.market,
        view,
        valuation.ValuationRequest(scenarios=defaults),
    )
    lines = [
        f"{s.name}: {s.value_per_share.value:.4g} per share"
        for s in result.scenarios
        if s.value_per_share.value is not None
    ]
    sid = registry.add(
        "observation",
        f"valuation:{ticker}:{result.method_version}:{result.price_date}",
        f"{ticker} scenario valuation (default assumptions)",
        app_path=f"/companies/{ticker}",
        tier=2,
        snippet="; ".join(lines)[:1500] or None,
        as_of=str(result.price_date) if result.price_date else None,
    )
    currency = result.currency
    scenarios = [
        {
            "name": s.name,
            "assumptions": s.assumptions.model_dump(
                mode="json", include={"name", "years", "growth", "terminal_growth"}
            ),
            "discount_rate": env.plain(s.discount_rate),
            "discount_rate_build": s.discount_rate_build,
            "value_per_share": _result_datum(s.value_per_share, sid, currency),
        }
        for s in result.scenarios
    ]
    return {
        "ticker": ticker,
        "source_id": sid,
        "price": env.datum(result.price, currency=currency, as_of=result.price_date, unit="price")
        if result.price is not None
        else env.absent("missing", "no price stored for this ticker", unit="price"),
        "range_low": env.datum(result.range_low, currency=currency, unit="per_share")
        if result.range_low is not None
        else env.absent("missing", "no scenario produced a value", unit="per_share"),
        "range_high": env.datum(result.range_high, currency=currency, unit="per_share")
        if result.range_high is not None
        else env.absent("missing", "no scenario produced a value", unit="per_share"),
        "scenarios": scenarios,
        "filed_inputs": [
            {"label": i.label, "value": env.plain(i.value), "detail": i.detail}
            for i in result.inputs[:12]
        ],
        "warnings": result.warnings[:8],
        "method_version": result.method_version,
        "note": result.note,
    }


# ------------------------------------------------------------------ prices


def _thin(closes: list[tuple[dt.date, Decimal]]) -> tuple[list[tuple[dt.date, Decimal]], str]:
    """At most MAX_PRICE_POINTS closes: daily if they fit, else the last close of each week,
    else of each month."""
    if len(closes) <= MAX_PRICE_POINTS:
        return closes, "daily"
    for label, key in (
        ("weekly", lambda d: d.isocalendar()[:2]),
        ("monthly", lambda d: (d.year, d.month)),
    ):
        last: dict[Any, tuple[dt.date, Decimal]] = {}
        for day, close in closes:
            last[key(day)] = (day, close)
        points = list(last.values())
        if len(points) <= MAX_PRICE_POINTS or label == "monthly":
            return points[-MAX_PRICE_POINTS:], label
    return closes[-MAX_PRICE_POINTS:], "daily"  # unreachable; keeps the type checker calm


def max_drawdown(closes: list[tuple[dt.date, Decimal]]) -> float | None:
    """The deepest peak-to-trough fall as a fraction (-0.2 is a 20% fall); null below two closes."""
    if len(closes) < 2:
        return None
    peak = closes[0][1]
    worst = Decimal(0)
    for _, close in closes:
        peak = max(peak, close)
        worst = min(worst, close / peak - 1)
    return float(worst)


MAX_RELATIVE_POINTS = 24


def relative_performance(
    closes: list[tuple[dt.date, Decimal]], bench: list[tuple[dt.date, Decimal]]
) -> dict[str, Any] | None:
    """The ticker against a benchmark over the days both have a close: each return, their
    difference in percentage points of return, and at most 24 points of the two series
    indexed to 100 at the first common day. None when fewer than two common days exist."""
    bench_by_day = dict(bench)
    common = [(d, c, bench_by_day[d]) for d, c in closes if d in bench_by_day]
    if len(common) < 2 or not common[0][1] or not common[0][2]:
        return None
    first, last = common[0], common[-1]
    own = float(last[1] / first[1] - 1)
    other = float(last[2] / first[2] - 1)
    step = max(1, -(-len(common) // MAX_RELATIVE_POINTS))
    picked = common[::step]
    if picked[-1][0] != last[0]:
        picked.append(last)
    return {
        "from": first[0],
        "to": last[0],
        "common_days": len(common),
        "own_return": own,
        "benchmark_return": other,
        "excess_return": own - other,
        "series": [
            [
                d.isoformat(),
                round(float(c / first[1]) * 100, 2),
                round(float(b / first[2]) * 100, 2),
            ]
            for d, c, b in picked
        ],
    }


async def price_history(
    pool: asyncpg.Pool,
    registry: Registry,
    ticker: str,
    days: Any,
    benchmark: str | None = None,
) -> dict[str, Any]:
    """Closing prices over the last `days` days (thinned to at most 120 points), with the
    return, high, low and deepest drawdown computed in code over every stored close. With
    `benchmark` (another covered ticker) it also reports the relative performance."""
    span = env.clamp_limit(days, default=365, maximum=MAX_PRICE_DAYS)
    got = await service.load_market(pool, ticker)
    if got is None:
        return {"error": f"{ticker} is not in the covered universe"}
    _, market = got
    if not market.closes:
        return {
            "ticker": ticker,
            "status": "missing",
            "reason": "no daily closes are stored for this ticker",
        }
    last_day = market.closes[-1][0]
    since = last_day - dt.timedelta(days=span)
    closes = [(d, c) for d, c in market.closes if d >= since]
    first, last = closes[0], closes[-1]
    points, interval = _thin(closes)
    change = float(last[1] / first[1] - 1) if first[1] else None
    drawdown = max_drawdown(closes)
    high = max(closes, key=lambda p: p[1])
    low = min(closes, key=lambda p: p[1])
    status = env.age_status(last_day, _today(), PRICE_STALE_DAYS)
    snippet = (
        f"{ticker} close {last[1]:.4g} {market.currency} on {last[0]}; "
        f"{'change' if change is not None else 'no change'} "
        f"{f'{change * 100:+.1f}%' if change is not None else ''} since {first[0]}; "
        f"high {high[1]:.4g} on {high[0]}, low {low[1]:.4g} on {low[0]}"
        + (f"; deepest drawdown {drawdown * 100:.1f}%" if drawdown is not None else "")
    )
    sid = registry.add(
        "price",
        f"price:{ticker}:{last[0]}:{span}",
        f"{ticker} closing prices to {last[0]}",
        app_path=f"/holdings/{ticker}",
        tier=2,
        snippet=snippet,
        as_of=str(last[0]),
    )
    relative: dict[str, Any] | None = None
    if benchmark:
        relative = await _relative_block(pool, registry, ticker, benchmark, closes, since)
    return {
        "ticker": ticker,
        "source_id": sid,
        "currency": market.currency,
        **({"relative_to_benchmark": relative} if relative is not None else {}),
        "last_close": env.datum(
            last[1],
            status=status,
            as_of=last[0],
            currency=market.currency,
            unit="price",
            source_id=sid,
            reason="latest stored close is more than a week old" if status == "stale" else None,
        ),
        "window": {"from": first[0].isoformat(), "to": last[0].isoformat(), "closes": len(closes)},
        "change_over_window": env.datum(change, unit="fraction", source_id=sid)
        if change is not None
        else env.absent("missing", "fewer than two closes"),
        "max_drawdown": env.datum(drawdown, unit="fraction", source_id=sid)
        if drawdown is not None
        else env.absent("missing", "fewer than two closes"),
        "high": {"close": float(high[1]), "date": high[0].isoformat()},
        "low": {"close": float(low[1]), "date": low[0].isoformat()},
        "interval": interval,
        "points": [[d.isoformat(), float(c)] for d, c in points],
        "page": env.page(points, MAX_PRICE_POINTS, len(closes)),
        "note": "Closes are in major currency units; return and drawdown are computed over "
        "every stored close in the window, not just the points shown.",
    }


async def _relative_block(
    pool: asyncpg.Pool,
    registry: Registry,
    ticker: str,
    benchmark: str,
    closes: list[tuple[dt.date, Decimal]],
    since: dt.date,
) -> dict[str, Any]:
    got = await service.load_market(pool, benchmark)
    if got is None:
        return {"benchmark": benchmark, **env.absent("missing", "benchmark is not covered")}
    bench = [(d, c) for d, c in got[1].closes if d >= since]
    rel = relative_performance(closes, bench)
    if rel is None:
        return {
            "benchmark": benchmark,
            **env.absent("missing", "fewer than two days with a close for both"),
        }
    sid = registry.add(
        "price",
        f"relative:{ticker}:{benchmark}:{rel['from']}:{rel['to']}",
        f"{ticker} against {benchmark}, {rel['from']} to {rel['to']}",
        app_path=f"/holdings/{ticker}",
        tier=2,
        snippet=(
            f"{ticker} {rel['own_return'] * 100:+.1f}% and {benchmark} "
            f"{rel['benchmark_return'] * 100:+.1f}% from {rel['from']} to {rel['to']}; "
            f"difference {rel['excess_return'] * 100:+.1f} points"
        ),
        as_of=str(rel["to"]),
    )
    return {
        "benchmark": benchmark,
        "benchmark_currency": got[1].currency,
        "window": {"from": rel["from"].isoformat(), "to": rel["to"].isoformat()},
        "common_days": rel["common_days"],
        "own_return": env.datum(rel["own_return"], unit="fraction", source_id=sid),
        "benchmark_return": env.datum(rel["benchmark_return"], unit="fraction", source_id=sid),
        "excess_return": env.datum(rel["excess_return"], unit="fraction", source_id=sid),
        "indexed_series": {"columns": ["date", "own", "benchmark"], "rows": rel["series"]},
        "note": "Price returns in each instrument's own currency, indexed to 100 at the first "
        "common day; dividends and currency effects are not included.",
    }


# ------------------------------------------------------------------ macro


async def macro_series(
    pool: asyncpg.Pool,
    registry: Registry,
    series_id: str | None,
    query: str | None,
    geography: str | None,
    limit: Any,
) -> dict[str, Any]:
    """Without `series_id`: the stored macro series matching `query` (id, source, geography,
    unit, latest period). With it: the latest vintage of each of its most recent periods."""
    rows_cap = env.clamp_limit(limit, default=12, maximum=60)
    geo = geography.upper() if geography else None
    if not series_id:
        found = await pool.fetch(
            "select series_id, source, geography, max(unit) as unit, max(frequency) as frequency,"
            " max(period) as latest_period, count(distinct period) as periods"
            " from public.macro_observation"
            " where ($1::text is null or series_id ilike $1 escape '\\')"
            " and ($2::text is null or geography = $2)"
            " group by series_id, source, geography order by max(period) desc, series_id"
            " limit $3",
            like_pattern(query) if query else None,
            geo,
            rows_cap,
        )
        return {
            "series": [
                {
                    "series_id": r["series_id"],
                    "source": r["source"],
                    "geography": r["geography"],
                    "unit": r["unit"],
                    "frequency": r["frequency"],
                    "latest_period": env.plain(r["latest_period"]),
                    "periods": r["periods"],
                }
                for r in found
            ],
            "page": {"limit": rows_cap, "truncated": len(found) == rows_cap},
            "note": "Pass series_id to read the observations.",
        }
    rows = await pool.fetch(
        "select distinct on (period) series_id, source, geography, period, value, unit,"
        " unit_multiplier, frequency, vintage, revision, known_at, release_at, source_url"
        " from public.macro_observation where series_id = $1 and ($2::text is null or"
        " geography = $2) order by period desc, known_at desc, revision desc limit $3",
        series_id,
        geo,
        rows_cap,
    )
    if not rows:
        return {
            "series_id": series_id,
            "status": "missing",
            "reason": "no stored observations for this series (and geography)",
        }
    head = rows[0]
    observations = [
        {
            "period": env.plain(r["period"]),
            "value": env.datum(
                r["value"] if r["value"] is not None else None,
                status="ok" if r["value"] is not None else "missing",
                reason=None if r["value"] is not None else "the source published no value",
                unit=r["unit"],
                period=str(r["period"]),
                as_of=r["known_at"].date(),
            ),
            "vintage": r["vintage"],
            "revision": r["revision"],
        }
        for r in rows
    ]
    lines = [
        f"{r['period']}: {float(r['value']):.6g} {r['unit']}"
        for r in rows[:6]
        if r["value"] is not None
    ]
    sid = registry.add(
        "observation",
        f"macro:{head['series_id']}:{head['source']}:{head['geography']}:{head['period']}",
        f"{head['series_id']} ({head['source']}, {head['geography']})",
        url=head["source_url"],
        tier=1,
        snippet="; ".join(lines)[:1000] or None,
        as_of=str(head["known_at"].date()),
    )
    return {
        "series_id": head["series_id"],
        "source": head["source"],
        "geography": head["geography"],
        "frequency": head["frequency"],
        "source_id": sid,
        "observations": observations,
        "page": {"returned": len(rows), "limit": rows_cap, "truncated": len(rows) == rows_cap},
        "note": "The newest vintage of each period known so far; earlier vintages are not shown.",
    }


# ------------------------------------------------------------------ strategy


async def strategy_rules(pool: asyncpg.Pool, registry: Registry) -> dict[str, Any]:
    """The owner's active strategy: goals, targets with bands, hard limits, rules and risk
    limits, with the config version they come from."""
    stored = await load_active(pool, "strategy", StrategyConfig)
    if stored is None:
        return env.absent("missing", "no strategy is configured", unit="config")
    body = stored.body
    dump = body.model_dump(mode="json", exclude_none=True)
    keep = (
        "base_currency", "weight_basis", "goals", "horizon_years", "notes", "cash_target",
        "target_positions", "target_categories", "rules", "risk", "pretrade",
    )  # fmt: skip
    out = {k: dump[k] for k in keep if k in dump}
    shrunk, truncated = _shrink(out, 60)
    rule_lines = [
        f"{r.id}: {r.description or r.type} ({r.operator} {getattr(r, 'value', '')})"
        for r in body.rules
        if r.enabled
    ]
    sid = registry.add(
        "strategy",
        f"strategy:{stored.id}",
        f"Strategy version {stored.id}",
        app_path="/config",
        tier=1,
        snippet=" | ".join([body.goals[:300], *rule_lines])[:1500] or None,
        as_of=str(stored.created_at.date()),
    )
    return {
        "source_id": sid,
        "config_version": stored.id,
        "as_of": stored.created_at.date().isoformat(),
        "status": "ok",
        "strategy": shrunk,
        "page": {"truncated": truncated},
        "note": "Rules and targets are the owner's; quote the rule id and the version when "
        "judging fit, and treat `notes` as guidance, not as a checked rule.",
    }


# ------------------------------------------------------------------ transactions


async def transactions(
    pool: asyncpg.Pool,
    registry: Registry,
    ticker: str | None,
    days: Any,
    kind: str | None,
    limit: Any,
    cursor: str | None,
) -> dict[str, Any]:
    """The owner's orders, dividends and cash movements, newest first, one page at a time."""
    span = env.clamp_limit(days, default=90, maximum=3650)
    rows_cap = env.clamp_limit(limit, default=25, maximum=50)
    if kind is not None and kind not in ("order", "dividend", "cash"):
        return {"error": "kind must be order, dividend or cash"}
    decoded = None
    if cursor:
        from steward.portfolio.transactions import decode_cursor

        try:
            decoded = decode_cursor(cursor)
        except (ValueError, TypeError):
            return {"error": "cursor is not one this tool returned"}
    now = dt.datetime.now(dt.UTC)
    page_ = await list_transactions(
        pool,
        TransactionFilters(
            types=[kind] if kind else [],
            ticker=ticker,
            start=now - dt.timedelta(days=span),
            end=now,
            cursor=decoded,
            limit=rows_cap,
        ),
    )
    items = [
        {
            "type": t.type,
            "subtype": t.subtype,
            "ticker": t.ticker,
            "quantity": t.quantity,
            "price": t.price,
            "amount": t.amount,
            "currency": t.currency,
            "at": t.occurred_at.isoformat(timespec="minutes"),
        }
        for t in page_.items
    ]
    totals = [t.model_dump(mode="json") for t in page_.totals][:20]
    scope = f"{ticker or 'all'} {kind or 'all types'}, last {span} days"
    sid = registry.add(
        "position",
        f"transactions:{ticker or 'all'}:{kind or 'all'}:{span}:{now.date()}",
        f"Transactions ({scope})",
        app_path="/transactions",
        tier=1,
        snippet=f"{page_.total_count} transactions; totals {json.dumps(totals)[:600]}",
        as_of=str(now.date()),
    )
    return {
        "source_id": sid,
        "scope": scope,
        "as_of": now.date().isoformat(),
        "transactions": items,
        "totals_over_all_matches": totals,
        "dividends_over_all_matches": [d.model_dump(mode="json") for d in page_.dividends][:10],
        "total_count": page_.total_count,
        "page": {
            "returned": len(items),
            "limit": rows_cap,
            "truncated": page_.next_cursor is not None,
            "next_cursor": page_.next_cursor,
        },
        "note": "Amounts are as Trading212 reports them, in `currency`; totals cover every "
        "matching row, not only this page.",
    }


# ------------------------------------------------------------------ risk history


def _scalars(payload: Any, cap: int = 12) -> dict[str, Any]:
    """The top-level numbers of a stored risk report, as its headline."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return {}
    if not isinstance(payload, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, int | float) and not isinstance(value, bool):
            out[key] = value
        if len(out) >= cap:
            break
    return out


async def risk_history(
    pool: asyncpg.Pool, registry: Registry, kind: str, scope: str | None, limit: Any
) -> dict[str, Any]:
    """Stored risk reports of one kind, newest first, each with its method version and its
    headline numbers: how the risk figures moved over time."""
    if kind not in RISK_KINDS:
        return {"error": f"kind must be one of {', '.join(RISK_KINDS)}"}
    rows_cap = env.clamp_limit(limit, default=8, maximum=30)
    rows = await pool.fetch(
        "select id, kind, scope, as_of, method_version, payload, created_at"
        " from public.risk_report_snapshot where kind = $1 and ($2::text is null or scope = $2)"
        " order by as_of desc, id desc limit $3",
        kind,
        scope,
        rows_cap,
    )
    if not rows:
        return {"kind": kind, "status": "missing", "reason": "no stored risk report of this kind"}
    snapshots = []
    for r in rows:
        headline = _scalars(r["payload"])
        sid = registry.add(
            "observation",
            f"risk_report:{r['id']}",
            f"Risk report {r['kind']} {r['as_of']}",
            app_path="/risk",
            tier=1,
            snippet=json.dumps(headline)[:600] or None,
            as_of=str(r["as_of"]),
        )
        snapshots.append(
            {
                "source_id": sid,
                "as_of": env.plain(r["as_of"]),
                "scope": r["scope"],
                "method_version": r["method_version"],
                "headline": headline,
            }
        )
    return {
        "kind": kind,
        "snapshots": snapshots,
        "page": {"returned": len(rows), "limit": rows_cap, "truncated": len(rows) == rows_cap},
        "note": "Headlines are the report's top-level numbers; compare only snapshots with the "
        "same method_version.",
    }


# ------------------------------------------------------------------ thesis and reports


async def thesis_and_reports(pool: asyncpg.Pool, registry: Registry, ticker: str) -> dict[str, Any]:
    """A holding's thesis, the latest verdict of each of its KPIs, and its last two research
    or review reports, in one call."""
    theses = await load_active(pool, "theses", ThesesConfig)
    thesis = next((t for t in (theses.body.theses if theses else []) if t.ticker == ticker), None)
    thesis_part: dict[str, Any]
    if thesis is None or theses is None:
        thesis_part = env.absent("missing", f"no thesis is configured for {ticker}")
    else:
        dump = thesis.model_dump(
            mode="json",
            exclude_none=True,
            include={"why_owned", "kpis", "exit_conditions", "review_date", "conviction"},
        )
        sid = registry.add(
            "thesis",
            f"thesis:{ticker}:{theses.id}",
            f"{ticker} thesis (config version {theses.id})",
            app_path=f"/holdings/{ticker}",
            tier=1,
            snippet=thesis.why_owned[:TEXT_CHARS],
            as_of=str(theses.created_at.date()),
        )
        thesis_part = {
            "status": "ok",
            "source_id": sid,
            "config_version": theses.id,
            "as_of": theses.created_at.date().isoformat(),
            **_shrink(dump, 20)[0],
        }
    verdict_rows = await pool.fetch(
        "select distinct on (kpi_id) id, kpi_id, metric, status, observed_value, operator,"
        " threshold, threshold_unit, period, rationale, created_at from public.verdict"
        " where ticker = $1 order by kpi_id, created_at desc",
        ticker,
    )
    verdicts = []
    for r in verdict_rows:
        sid = registry.add(
            "verdict",
            r["id"],
            f"{ticker} {r['metric']}: {r['status']}",
            app_path=f"/holdings/{ticker}",
            tier=1,
            snippet=r["rationale"][:TEXT_CHARS],
            as_of=str(r["created_at"].date()),
        )
        observed = (
            env.datum(
                r["observed_value"],
                unit=r["threshold_unit"],
                period=r["period"],
                as_of=r["created_at"].date(),
                source_id=sid,
            )
            if r["observed_value"] is not None
            else env.absent("missing", "no verified figure for this period", source_id=sid)
        )
        verdicts.append(
            {
                "kpi_id": r["kpi_id"],
                "metric": r["metric"],
                "status": r["status"],
                "rule": f"{r['operator']} {float(r['threshold'])} {r['threshold_unit']}",
                "observed": observed,
                "source_id": sid,
            }
        )
    report_rows = await pool.fetch(
        "select id, type, title, period, created_at, left(body_md, $2) as excerpt"
        " from public.report where ticker = $1 and type in ('research', 'review')"
        " order by created_at desc limit 2",
        ticker,
        TEXT_CHARS,
    )
    reports = []
    for r in report_rows:
        sid = registry.add(
            "report",
            r["id"],
            r["title"],
            app_path=REPORT_PATHS[r["type"]].format(r["id"]),
            tier=1,
            snippet=r["excerpt"],
            as_of=str(r["created_at"].date()),
        )
        reports.append(
            {
                "source_id": sid,
                "type": r["type"],
                "title": r["title"],
                "date": r["created_at"].date().isoformat(),
                "excerpt": r["excerpt"],
            }
        )
    return {
        "ticker": ticker,
        "thesis": thesis_part,
        "kpi_verdicts": verdicts
        or env.absent("missing", "no KPI has been judged yet", unit="verdict"),
        "latest_reports": reports,
        "page": {"reports_limit": 2},
    }


# ------------------------------------------------------------------ freshness


FRESHNESS_PROBES: tuple[tuple[str, str, str], ...] = (
    ("portfolio_snapshot", "select max(snapshot_date) from public.account_snapshot", "date"),
    ("news", "select max(published_at) from public.news_item", "ts"),
    ("macro", "select max(known_at) from public.macro_observation", "ts"),
    ("fundamentals", "select max(fetched_at) from public.fundamentals", "ts"),
    ("price_bars", "select max(date) from public.price_bar", "date"),
    ("fx", "select max(rate_date) from public.fx_rate", "date"),
    ("insider_filings", "select max(filed) from public.insider_filing", "ts"),
    ("transactions", "select max(occurred_at) from public.transaction", "ts"),
)


async def freshness_limits(pool: asyncpg.Pool) -> dict[str, int]:
    """Stale-after days per dataset: the built-in limits with `chat.freshness_days` of the
    active jobs config on top."""
    stored = await load_active(pool, "jobs", JobsConfig)
    configured = (stored.body if stored else JobsConfig()).chat.freshness_days
    return {**DEFAULT_FRESHNESS_DAYS, **configured}


async def data_freshness(pool: asyncpg.Pool) -> dict[str, Any]:
    """When each research dataset was last updated, and whether that is stale for it."""
    today = _today()
    limits = await freshness_limits(pool)
    datasets: list[dict[str, Any]] = []
    for name, sql, _ in FRESHNESS_PROBES:
        max_age = limits[name]
        try:
            latest = await pool.fetchval(sql)
        except asyncpg.PostgresError as exc:
            datasets.append(
                {"dataset": name, **env.absent("failed", f"could not read: {type(exc).__name__}")}
            )
            continue
        status = env.age_status(latest, today, max_age)
        if status == "missing":
            datasets.append({"dataset": name, **env.absent("missing", "nothing stored")})
        else:
            datasets.append(
                {
                    "dataset": name,
                    **env.datum(
                        None if latest is None else env.plain(latest),
                        status=status,
                        as_of=latest,
                        unit="latest_record",
                        reason=f"older than {max_age} days" if status == "stale" else None,
                    ),
                    "stale_after_days": max_age,
                }
            )
    coverage = await pool.fetch(
        "select state, count(*) as n from public.coverage_status group by state order by state"
    )
    return {
        "as_of": today.isoformat(),
        "datasets": datasets,
        "coverage_states": {r["state"]: r["n"] for r in coverage},
        "note": "`value` is the newest stored record's date; a stale or missing dataset limits "
        "what an answer from it can claim.",
    }
