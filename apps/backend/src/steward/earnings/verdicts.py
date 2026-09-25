"""Verdicts: pass / breach / unknown per thesis KPI and fiscal period.

A KPI is an absolute threshold on a metric, so the judgement is arithmetic and is done
here, in code, from verified facts only: the value (recomputed for margins and growth
rates), converted to the KPI's unit, compared with the operator. No model call sees raw
pages or supplies a number. A KPI with no verified figure is `unknown`, with the reason,
never omitted.
"""

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Literal

import asyncpg

from steward.config.thesis import ThesesConfig, ThesisConfig, ThesisKPI
from steward.earnings.metrics import ADDITIVE, derivation
from steward.earnings.periods import QUARTERS, period_label, previous_quarter, sort_key
from steward.earnings.units import KpiUnit, parse_kpi_unit

VerdictStatus = Literal["pass", "breach", "unknown"]
ObservedKind = Literal["money", "per_share", "fraction", "ratio", "count"]

# How many past periods a KPI's history covers.
MAX_PERIODS = 8


@dataclass(frozen=True)
class FactRow:
    id: int
    metric: str
    basis: str
    fiscal_year: int
    fiscal_period: str
    value_units: Decimal
    unit: str
    currency: str | None


@dataclass
class Observed:
    value: Decimal
    kind: ObservedKind
    currency: str | None
    fact_ids: list[int]
    explanation: str


@dataclass
class VerdictDraft:
    ticker: str
    kpi_id: str
    metric: str
    basis: str
    kpi_period: str
    fiscal_year: int | None
    fiscal_period: str | None
    status: VerdictStatus
    observed_value: Decimal | None
    operator: str
    threshold: Decimal
    threshold_unit: str
    rationale: str
    fact_ids: list[int] = field(default_factory=list)

    @property
    def period(self) -> str | None:
        if self.fiscal_year is None or self.fiscal_period is None:
            return None
        return period_label(self.fiscal_year, self.fiscal_period)


_KIND_BY_UNIT: dict[str, ObservedKind] = {
    "currency": "money",
    "per_share": "per_share",
    "percent": "fraction",
    "ratio": "ratio",
    "count": "count",
}


def _fmt(value: Decimal, kind: ObservedKind, currency: str | None) -> str:
    if kind == "fraction":
        return f"{value * 100:.2f}%"
    if kind == "ratio":
        return f"{value:.2f}x"
    if kind == "count":
        return f"{value:,.0f}"
    if kind == "per_share":
        return f"{value:.2f} {currency} per share"
    magnitude = abs(value)
    for factor, suffix in ((Decimal(10) ** 9, "bn"), (Decimal(10) ** 6, "m")):
        if magnitude >= factor:
            return f"{value / factor:,.2f} {suffix} {currency}"
    return f"{value:,.2f} {currency}"


class _Facts:
    def __init__(self, facts: Iterable[FactRow]) -> None:
        # Latest fact per key wins (restatements the owner approved supersede older ones).
        self.by_key: dict[tuple[str, str, int, str], FactRow] = {}
        for f in sorted(facts, key=lambda f: f.id):
            self.by_key[(f.metric, f.basis, f.fiscal_year, f.fiscal_period)] = f

    def get(self, metric: str, basis: str, fy: int, fp: str) -> FactRow | None:
        return self.by_key.get((metric, basis, fy, fp))

    def periods(self, metrics: Iterable[str], basis: str) -> set[tuple[int, str]]:
        wanted = set(metrics)
        return {(k[2], k[3]) for k in self.by_key if k[0] in wanted and k[1] == basis}


def _single(facts: _Facts, metric: str, basis: str, fy: int, fp: str) -> Observed | str:
    f = facts.get(metric, basis, fy, fp)
    if f is None:
        return f"no verified {metric} for {period_label(fy, fp)}"
    value = f.value_units / 100 if f.unit == "percent" else f.value_units
    kind = _KIND_BY_UNIT[f.unit]
    return Observed(
        value,
        kind,
        f.currency,
        [f.id],
        f"{metric} {_fmt(value, kind, f.currency)} ({period_label(fy, fp)})",
    )


def _ttm(facts: _Facts, metric: str, basis: str, fy: int, fp: str) -> Observed | str:
    if metric not in ADDITIVE:
        return f"{metric} does not add up across quarters, so it has no trailing-12-month value"
    parts: list[Observed] = []
    year, quarter = fy, fp
    for _ in range(4):
        part = _single(facts, metric, basis, year, quarter)
        if isinstance(part, str):
            return part
        parts.append(part)
        year, quarter = previous_quarter(year, quarter)
    currencies = {p.currency for p in parts}
    if len(currencies) > 1 or len({p.kind for p in parts}) > 1:
        return f"{metric} quarters are in different units"
    total = sum((p.value for p in parts), Decimal(0))
    kind, currency = parts[0].kind, parts[0].currency
    return Observed(
        total,
        kind,
        currency,
        [i for p in parts for i in p.fact_ids],
        f"TTM {metric} {_fmt(total, kind, currency)} (4 quarters to {period_label(fy, fp)})",
    )


def _value(
    facts: _Facts, metric: str, basis: str, fy: int, fp: str, kpi_period: str
) -> Observed | str:
    if kpi_period == "ttm":
        return _ttm(facts, metric, basis, fy, fp)
    return _single(facts, metric, basis, fy, fp)


def observe(facts: _Facts, kpi: ThesisKPI, fy: int, fp: str) -> Observed | str:
    """The KPI's metric for one period, recomputed from raw figures where it is derived."""
    d = derivation(kpi.metric)
    basis, period = kpi.basis, kpi.period
    if d.kind == "direct":
        return _value(facts, kpi.metric, basis, fy, fp, period)

    if d.kind == "margin" and d.denominator:
        num = _value(facts, d.base, basis, fy, fp, period)
        den = _value(facts, d.denominator, basis, fy, fp, period)
        if isinstance(num, Observed) and isinstance(den, Observed):
            if num.currency != den.currency or num.kind != "money" or den.kind != "money":
                return f"{d.base} and {d.denominator} are not in the same currency"
            if den.value <= 0:
                return f"{d.denominator} is not positive"
            ratio = num.value / den.value
            return Observed(
                ratio,
                "fraction",
                None,
                num.fact_ids + den.fact_ids,
                f"{kpi.metric} {ratio * 100:.2f}% = {num.explanation} / {den.explanation}",
            )
        stated = _value(facts, kpi.metric, basis, fy, fp, period)
        if isinstance(stated, Observed):
            return stated
        return num if isinstance(num, str) else str(den)

    # Growth rates.
    if d.kind == "growth_qoq":
        if period != "quarter":
            return "quarter-over-quarter growth needs a quarterly KPI"
        pfy, pfp = previous_quarter(fy, fp)
    else:
        pfy, pfp = fy - 1, fp
    cur = _value(facts, d.base, basis, fy, fp, period)
    prev = _value(facts, d.base, basis, pfy, pfp, period)
    if isinstance(cur, Observed) and isinstance(prev, Observed):
        if cur.currency != prev.currency or cur.kind != prev.kind:
            return f"{d.base} changed unit between the two periods"
        if prev.value <= 0:
            return f"{d.base} for {period_label(pfy, pfp)} is not positive, so growth is undefined"
        growth = cur.value / prev.value - 1
        return Observed(
            growth,
            "fraction",
            None,
            cur.fact_ids + prev.fact_ids,
            f"{kpi.metric} {growth * 100:+.2f}% = {cur.explanation} vs {prev.explanation}",
        )
    stated = _value(facts, kpi.metric, basis, fy, fp, period)
    if isinstance(stated, Observed):
        return stated
    return cur if isinstance(cur, str) else str(prev)


def in_kpi_units(obs: Observed, unit: KpiUnit) -> Decimal | str:
    if unit.kind == "percent":
        if obs.kind == "fraction":
            return obs.value * 100
    elif unit.kind == "ratio":
        if obs.kind in ("ratio", "fraction"):
            return obs.value
    elif unit.kind == "count":
        if obs.kind == "count":
            return obs.value
    elif unit.kind in ("currency", "per_share"):
        if obs.currency != unit.currency:
            return f"the figure is in {obs.currency}, the KPI in {unit.currency}"
        if obs.kind == "money" and unit.kind == "currency":
            return obs.value / unit.factor
        if obs.kind == "per_share" and unit.scale == "units":
            return obs.value
    return f"a {obs.kind.replace('_', ' ')} figure cannot be compared with a KPI in {unit.kind}"


def compare(observed: Decimal, operator: str, threshold: Decimal) -> bool:
    if operator == "<":
        return observed < threshold
    if operator == "<=":
        return observed <= threshold
    if operator == ">":
        return observed > threshold
    if operator == ">=":
        return observed >= threshold
    return abs(observed - threshold) <= Decimal("1e-9") * max(Decimal(1), abs(threshold))


def _candidate_periods(facts: _Facts, kpi: ThesisKPI) -> list[tuple[int, str]]:
    d = derivation(kpi.metric)
    metrics = {d.base, kpi.metric}
    periods = facts.periods(metrics, kpi.basis)
    if kpi.period == "annual":
        chosen = {p for p in periods if p[1] == "FY"}
    else:
        chosen = {p for p in periods if p[1] in QUARTERS}
    ordered = sorted(chosen, key=lambda p: sort_key(*p), reverse=True)
    return ordered[:MAX_PERIODS]


def judge_kpi(ticker: str, kpi: ThesisKPI, facts: _Facts) -> list[VerdictDraft]:
    threshold = Decimal(repr(kpi.threshold))

    def draft(
        fy: int | None,
        fp: str | None,
        status: VerdictStatus,
        rationale: str,
        observed: Decimal | None = None,
        fact_ids: list[int] | None = None,
    ) -> VerdictDraft:
        return VerdictDraft(
            ticker=ticker,
            kpi_id=kpi.id,
            metric=kpi.metric,
            basis=kpi.basis,
            kpi_period=kpi.period,
            fiscal_year=fy,
            fiscal_period=fp,
            status=status,
            observed_value=observed,
            operator=kpi.operator,
            threshold=threshold,
            threshold_unit=kpi.unit,
            rationale=rationale,
            fact_ids=fact_ids or [],
        )

    target = f"{kpi.operator} {kpi.threshold:g} {kpi.unit}"
    unit = parse_kpi_unit(kpi.unit)
    periods = _candidate_periods(facts, kpi)
    if not periods:
        needed = ", ".join(derivation(kpi.metric).components)
        return [
            draft(
                None,
                None,
                "unknown",
                f"No verified {kpi.basis} {needed} yet. Threshold: {target}.",
            )
        ]
    drafts = []
    for fy, fp in periods:
        obs = observe(facts, kpi, fy, fp)
        if isinstance(obs, str):
            drafts.append(draft(fy, fp, "unknown", f"Unknown: {obs}. Threshold: {target}."))
            continue
        value = in_kpi_units(obs, unit)
        if isinstance(value, str):
            drafts.append(
                draft(
                    fy, fp, "unknown", f"Unknown: {value}. {obs.explanation}.", None, obs.fact_ids
                )
            )
            continue
        status: VerdictStatus = "pass" if compare(value, kpi.operator, threshold) else "breach"
        # Compared at full precision, stored at a precision nobody needs more of.
        value = value.quantize(Decimal("0.000001"))
        drafts.append(
            draft(
                fy,
                fp,
                status,
                f"{obs.explanation}. Threshold: {target} -> {status}.",
                value,
                obs.fact_ids,
            )
        )
    return drafts


def judge_thesis(thesis: ThesisConfig, facts: Iterable[FactRow]) -> list[VerdictDraft]:
    indexed = _Facts(facts)
    return [d for kpi in thesis.kpis for d in judge_kpi(thesis.ticker, kpi, indexed)]


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


async def load_verified_facts(
    conn: asyncpg.Connection | asyncpg.Pool, ticker: str
) -> list[FactRow]:
    rows = await conn.fetch(
        "select id, metric, basis, fiscal_year, fiscal_period, value_units, unit, currency"
        " from public.fact where ticker = $1 and status = 'verified' order by id",
        ticker,
    )
    return [FactRow(**dict(r)) for r in rows]


async def write_verdicts(
    conn: asyncpg.Connection | asyncpg.Pool,
    drafts: list[VerdictDraft],
    config_version_id: int,
    job_id: int | None,
) -> int:
    """Insert each draft unless the latest verdict for its KPI and period already says the
    same thing under the same config version. Returns how many rows were written."""
    written = 0
    for d in drafts:
        latest = await conn.fetchrow(
            "select status, observed_value, operator, threshold, threshold_unit, fact_ids,"
            " config_version_id from public.verdict where ticker = $1 and kpi_id = $2"
            " and fiscal_year is not distinct from $3 and fiscal_period is not distinct from $4"
            " order by id desc limit 1",
            d.ticker,
            d.kpi_id,
            d.fiscal_year,
            d.fiscal_period,
        )
        if latest is not None and (
            latest["status"] == d.status
            and latest["observed_value"] == d.observed_value
            and latest["operator"] == d.operator
            and latest["threshold"] == d.threshold
            and latest["threshold_unit"] == d.threshold_unit
            and sorted(latest["fact_ids"]) == sorted(d.fact_ids)
            and latest["config_version_id"] == config_version_id
        ):
            continue
        await conn.execute(
            "insert into public.verdict (ticker, kpi_id, metric, basis, kpi_period, fiscal_year,"
            " fiscal_period, period, status, observed_value, operator, threshold, threshold_unit,"
            " rationale, fact_ids, config_version_id, job_id)"
            " values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)",
            d.ticker,
            d.kpi_id,
            d.metric,
            d.basis,
            d.kpi_period,
            d.fiscal_year,
            d.fiscal_period,
            d.period,
            d.status,
            d.observed_value,
            d.operator,
            d.threshold,
            d.threshold_unit,
            d.rationale,
            d.fact_ids,
            config_version_id,
            job_id,
        )
        written += 1
    return written


@dataclass(frozen=True)
class VerdictRunSummary:
    tickers: list[str]
    verdicts: int
    written: int
    by_status: dict[str, int]

    def as_detail(self) -> dict[str, object]:
        return asdict(self)


async def run_verdicts(
    pool: asyncpg.Pool,
    theses: ThesesConfig,
    config_version_id: int,
    job_id: int | None,
    tickers: list[str] | None = None,
) -> VerdictRunSummary:
    counts: dict[str, int] = {"pass": 0, "breach": 0, "unknown": 0}
    total = written = 0
    judged = []
    for thesis in theses.theses:
        if tickers is not None and thesis.ticker not in tickers:
            continue
        facts = await load_verified_facts(pool, thesis.ticker)
        drafts = judge_thesis(thesis, facts)
        written += await write_verdicts(pool, drafts, config_version_id, job_id)
        total += len(drafts)
        for d in drafts:
            counts[d.status] += 1
        judged.append(thesis.ticker)
    return VerdictRunSummary(judged, total, written, counts)
