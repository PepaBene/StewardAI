"""Is the process adding value? Attribution by segment and a skill test (quant plan Q6.2, Q6.4).

Brinson-Fachler (`quant.skill`) is run on your own stored history. A period's segment return is

    r = (V1 - flow) / V0 - 1        flow = net buys at fill value - dividends + fees

(the sleeve arithmetic of `performance.sleeves`), and a segment's weight is its opening share of
the invested value, so the segment returns add up to the invested portfolio's return exactly.
Positions bought during the period have no opening value: their gain is shown on its own line
("opened in the period") so the effects still add up to the excess return. Cash belongs to no
segment, so the portfolio return here is the return on invested money.

The benchmark is one index. It has no sector or tier breakdown of returns in the stored data, so
every segment is given the index's return and the benchmark weights equal the portfolio's own:
allocation and interaction are zero by construction and the table shows each segment's
contribution to the excess return (weight x (segment return - index return)). The Brinson
machinery is in place for a benchmark with segment returns; no such data is stored.

The Probabilistic Sharpe Ratio compares the weekly Sharpe ratio of the whole portfolio (flows
removed, cash included, no risk-free rate subtracted from either side) with the benchmark's.
"""

import datetime as dt
import logging
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal

import asyncpg
from pydantic import BaseModel, Field

from steward.analytics.returns import DayValue
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.config.strategy import StrategyConfig
from steward.performance import service
from steward.performance.benchmark import series_return
from steward.performance.settings import PerformanceSettings, load_latest_settings
from steward.quant import skill as sk

log = logging.getLogger(__name__)

METHOD_VERSION = "evaluation-1"
UNCLASSIFIED = "Unclassified"
MONTHS = 12
MIN_PSR_WEEKS = 26
ZERO = Decimal(0)


class SegmentOut(BaseModel):
    name: str
    opening_weight: float
    return_: float = Field(alias="return")
    contribution: float = Field(description="Weight x (segment return - benchmark return).")
    allocation: float
    selection: float
    interaction: float

    model_config = {"populate_by_name": True}


class PeriodOut(BaseModel):
    label: str
    start: dt.date
    end: dt.date
    portfolio_return: float
    benchmark_return: float
    excess_return: float
    allocation: float
    selection: float
    interaction: float
    opened_in_period: float = Field(description="Gain on positions with no opening value.")
    reconciliation_error_bp: float = Field(
        description="Excess return minus the sum of the effects, in basis points."
    )
    segments: list[SegmentOut]


class PsrOut(BaseModel):
    weeks: int
    portfolio_sharpe: float | None = Field(description="Annualised.")
    benchmark_sharpe: float | None = Field(description="Annualised.")
    probability: float | None = Field(
        description="Probability that the portfolio's true Sharpe ratio is above the benchmark's."
    )
    min_track_record_weeks: float | None
    distinguishable: bool | None
    verdict: str


class FactorAlpha(BaseModel):
    """What is left of the return once the factor exposures are paid for (quant plan Q6.3)."""

    alpha_annual: float
    t_stat: float
    observations: int
    significant: bool = Field(description="Absolute t-statistic of 2 or more.")
    verdict: str


class Evaluation(BaseModel):
    available: bool
    reason: str | None
    method_version: str = METHOD_VERSION
    dimension: str
    benchmark_label: str | None = None
    periods: list[PeriodOut] = Field(default_factory=list)
    since_inception: PeriodOut | None = None
    skill: PsrOut | None = None
    factor_alpha: FactorAlpha | None = None
    notes: list[str] = Field(default_factory=list)


def factor_alpha(alpha_annual: float, t_stat: float, observations: int) -> FactorAlpha:
    """Alpha is the intercept of the factor regression (annualised log return); its
    Newey-West t-statistic says whether it is distinguishable from zero."""
    significant = abs(t_stat) >= 2.0
    sign = "positive" if alpha_annual > 0 else "negative"
    if significant:
        verdict = (
            f"After the factor exposures the portfolio earned a {sign} {alpha_annual:+.1%} a year "
            f"(t = {t_stat:.1f}), distinguishable from zero."
        )
    else:
        verdict = (
            f"After the factor exposures the portfolio earned {alpha_annual:+.1%} a year, but "
            f"t = {t_stat:.1f} is too small to tell it from luck over {observations} weeks."
        )
    return FactorAlpha(
        alpha_annual=alpha_annual, t_stat=t_stat, observations=observations,
        significant=significant, verdict=verdict,
    )  # fmt: skip


def _value_on(series: Sequence[DayValue], day: dt.date) -> DayValue | None:
    best: DayValue | None = None
    for d in series:
        if d.date <= day:
            best = d
        else:
            break
    return best


def period_return(
    series: Sequence[DayValue], start: dt.date, end: dt.date
) -> tuple[Decimal, Decimal, Decimal] | None:
    """(opening value, closing value, net flow) of a series between two snapshot days, or None
    when it has no value at the start."""
    ordered = sorted(series, key=lambda d: d.date)
    opening, closing = _value_on(ordered, start), _value_on(ordered, end)
    if opening is None or closing is None:
        return None
    flow = sum((d.flow for d in ordered if start < d.date <= end), ZERO)
    return opening.value, closing.value, flow


def attribute_period(
    label: str,
    segments: Mapping[str, Sequence[DayValue]],
    start: dt.date,
    end: dt.date,
    benchmark_return: float,
) -> PeriodOut | None:
    """Brinson-Fachler for one period, reconciled to the invested portfolio's own return."""
    opening: dict[str, Decimal] = {}
    parts: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
    for name, series in segments.items():
        got = period_return(series, start, end)
        if got is not None:
            parts[name] = got
            opening[name] = got[0]
    total_open = sum(opening.values(), ZERO)
    if total_open <= 0:
        return None
    rows: list[sk.Segment] = []
    opened_gain = ZERO
    for name, (v0, v1, flow) in parts.items():
        if v0 <= 0:
            opened_gain += v1 - flow - v0
            continue
        weight = float(v0 / total_open)
        ret = float((v1 - flow) / v0 - 1)
        rows.append(sk.Segment(name, weight, ret, weight, benchmark_return))
    if not rows:
        return None
    result = sk.brinson_fachler(rows)
    new = float(opened_gain / total_open)
    whole_gain = sum((v1 - flow - v0 for v0, v1, flow in parts.values()), ZERO)
    portfolio = float(whole_gain / total_open)
    excess = portfolio - benchmark_return
    effects = result.allocation + result.selection + result.interaction + new
    return PeriodOut(
        label=label, start=start, end=end, portfolio_return=portfolio,
        benchmark_return=benchmark_return, excess_return=excess,
        allocation=result.allocation, selection=result.selection, interaction=result.interaction,
        opened_in_period=new, reconciliation_error_bp=(excess - effects) * 10_000,
        segments=[
            SegmentOut(
                name=s.name, opening_weight=s.portfolio_weight, **{"return": s.portfolio_return},
                contribution=e.total, allocation=e.allocation, selection=e.selection,
                interaction=e.interaction,
            )
            for s, e in zip(rows, result.segments, strict=True)
        ],
    )  # fmt: skip


def month_windows(
    days: Sequence[dt.date], months: int = MONTHS
) -> list[tuple[str, dt.date, dt.date]]:
    """(label, start day, end day) for each of the last `months` calendar months that has at least
    two snapshot days around it; start is the last snapshot day before the month."""
    if len(days) < 2:
        return []
    ordered = sorted(days)
    last = ordered[-1]
    out: list[tuple[str, dt.date, dt.date]] = []
    year, month = last.year, last.month
    for _ in range(months):
        first = dt.date(year, month, 1)
        before = [d for d in ordered if d < first]
        inside = [d for d in ordered if d.year == year and d.month == month]
        start = before[-1] if before else (inside[0] if inside else None)
        if inside and start is not None and inside[-1] > start:
            out.append((f"{year}-{month:02d}", start, inside[-1]))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(out))


def weekly_pairs(
    days: Sequence[DayValue], bench: Iterable[tuple[dt.date, Decimal]]
) -> tuple[list[float], list[float]]:
    """Weekly portfolio returns (flows removed) and the benchmark's over the same weeks; weeks
    where either is missing are dropped."""
    ordered = sorted(days, key=lambda d: d.date)
    if not ordered:
        return [], []
    series = sorted(bench)
    portfolio: list[float] = []
    benchmark: list[float] = []
    anchor = ordered[0]
    for d in ordered[1:]:
        if (d.date - anchor.date).days < 7:
            continue
        flow = sum((x.flow for x in ordered if anchor.date < x.date <= d.date), ZERO)
        b = series_return(series, anchor.date, d.date)
        if anchor.value > 0 and b is not None:
            portfolio.append(float((d.value - flow) / anchor.value - 1))
            benchmark.append(float(b))
        anchor = d
    return portfolio, benchmark


def skill_verdict(weeks: int, psr: sk.Psr | None) -> str:
    if psr is None or psr.probability is None:
        return "The record has too little variation or too few weeks to test."
    if psr.distinguishable:
        return (
            f"The portfolio's risk-adjusted return is above the benchmark's with "
            f"{psr.probability:.0%} probability."
        )
    need = (
        f" At the current edge about {psr.min_track_record:.0f} weeks of record would be needed."
        if psr.min_track_record is not None
        else " The portfolio's Sharpe ratio is not above the benchmark's."
    )
    return (
        "Not yet distinguishable from the benchmark: the chance that the portfolio's true "
        f"Sharpe ratio is higher is {psr.probability:.0%} after {weeks} weeks." + need
    )


def skill_report(days: Sequence[DayValue], bench: Iterable[tuple[dt.date, Decimal]]) -> PsrOut:
    portfolio, benchmark = weekly_pairs(days, bench)
    weeks = len(portfolio)
    if weeks < MIN_PSR_WEEKS:
        return PsrOut(
            weeks=weeks, portfolio_sharpe=None, benchmark_sharpe=None, probability=None,
            min_track_record_weeks=None, distinguishable=None,
            verdict=f"Only {weeks} weeks of record; at least {MIN_PSR_WEEKS} are needed.",
        )  # fmt: skip
    moments = sk.sharpe_moments(benchmark)
    if moments is None:
        return PsrOut(
            weeks=weeks, portfolio_sharpe=None, benchmark_sharpe=None, probability=None,
            min_track_record_weeks=None, distinguishable=None,
            verdict="The benchmark has no variation over the weeks.",
        )  # fmt: skip
    psr = sk.probabilistic_sharpe(portfolio, moments[0])
    annual = 52**0.5
    return PsrOut(
        weeks=weeks,
        portfolio_sharpe=psr.sharpe * annual if psr else None,
        benchmark_sharpe=moments[0] * annual,
        probability=psr.probability if psr else None,
        min_track_record_weeks=psr.min_track_record if psr else None,
        distinguishable=psr.distinguishable if psr else None,
        verdict=skill_verdict(weeks, psr),
    )


async def evaluation(
    pool: asyncpg.Pool, dimension: str = "sector", settings: PerformanceSettings | None = None
) -> Evaluation:
    settings = settings or await load_latest_settings(pool)
    record = await service.load_record(pool, settings)
    if record is None:
        return Evaluation(
            available=False, reason="fewer than two daily snapshots are stored", dimension=dimension
        )
    stored = await load_active(pool, "strategy", StrategyConfig)
    strategy = stored.body if stored else StrategyConfig(base_currency=record.base_currency)
    jobs_cfg = await load_active(pool, "jobs", JobsConfig)
    jobs = jobs_cfg.body if jobs_cfg else JobsConfig()
    first = record.valuation_days[0]
    label, bench, _ccy, _basis, bench_notes = await service._benchmark_series(
        pool, settings.benchmark, jobs, first - dt.timedelta(days=10), record.base_currency
    )
    notes = list(bench_notes)
    if label is None or not bench:
        return Evaluation(
            available=False,
            reason="no benchmark prices are stored" + (f" ({'; '.join(notes)})" if notes else ""),
            dimension=dimension,
            notes=notes,
        )
    tickers = {p.ticker for p in record.positions}
    groups: dict[str, set[str]] = {}
    for t in tickers:
        name = strategy.classifications.get(t, {}).get(dimension) or UNCLASSIFIED
        groups.setdefault(name, set()).add(t)
    segments = {name: record.sleeve(members) for name, members in groups.items()}
    if set(groups) == {UNCLASSIFIED}:
        notes.append(
            f"No holding is classified by {dimension} in the strategy, so there is one segment."
        )
    periods: list[PeriodOut] = []
    for name, start, end in month_windows(record.valuation_days):
        b = series_return(bench, start, end)
        if b is None:
            continue
        got = attribute_period(name, segments, start, end, float(b))
        if got is not None:
            periods.append(got)
    since: PeriodOut | None = None
    b_all = series_return(bench, first, record.valuation_days[-1])
    if b_all is not None:
        since = attribute_period(
            "since inception", segments, first, record.valuation_days[-1], float(b_all)
        )
    notes.append(
        "The benchmark is one index, so allocation and interaction are zero by construction; "
        "each segment's contribution to the excess return is shown. Periods are not chain-linked."
    )
    return Evaluation(
        available=True, reason=None, dimension=dimension, benchmark_label=label,
        periods=periods, since_inception=since,
        skill=skill_report(record.day_values, bench), notes=notes,
    )  # fmt: skip
