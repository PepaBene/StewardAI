"""Cross-source reconciliation (`fact_reconcile`): press-release figures against the issuer's
later 10-Q / 10-K (or 20-F) XBRL data.

A results press release comes out weeks before the quarterly report is filed. Once the
report's XBRL is on EDGAR, every stored reported-basis fact of a standard metric is compared
with the XBRL value for the same period:

- the period must match: same end date (within a few days) and a duration that fits the
  fiscal period (a quarter, a half, a year); a fourth quarter that the 10-K does not tag on
  its own is derived as the full year minus the nine-month year-to-date figure;
- the value must agree within the precision the press release printed it with (a figure
  written "143.8 billion" allows +-0.05 billion);
- the XBRL value is taken from the first report that filed that period (later filings'
  comparatives may carry restatements; those are the restatement check's business).

A mismatch quarantines the fact (it leaves every verdict) and the holding's verdicts are
recomputed. Every comparison is stored in `fact_reconciliation`. A fact the owner has
reviewed is never touched again: owner overrides win.
"""

import datetime as dt
import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import asyncpg
import httpx

from steward import __version__
from steward.config.holding_profiles import HoldingProfile
from steward.earnings.check import load_config, resolve_cik
from steward.earnings.units import SCALE_FACTOR
from steward.earnings.verdicts import run_verdicts
from steward.jobs.runner import JobContext
from steward.settings import get_settings
from steward.sources.edgar import EdgarClient, EdgarError

logger = logging.getLogger(__name__)

# Our metric -> XBRL concepts, most specific first (us-gaap for 10-Q/10-K filers, ifrs-full
# for 20-F filers, whose XBRL has only annual figures).
CONCEPTS: dict[str, list[tuple[str, str]]] = {
    "revenue": [
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("us-gaap", "Revenues"),
        ("us-gaap", "SalesRevenueNet"),
        ("us-gaap", "RevenueFromContractWithCustomerIncludingAssessedTax"),
        ("ifrs-full", "Revenue"),
    ],
    "gross_profit": [("us-gaap", "GrossProfit"), ("ifrs-full", "GrossProfit")],
    "operating_income": [
        ("us-gaap", "OperatingIncomeLoss"),
        ("ifrs-full", "ProfitLossFromOperatingActivities"),
    ],
    "net_income": [
        ("us-gaap", "NetIncomeLoss"),
        ("ifrs-full", "ProfitLossAttributableToOwnersOfParent"),
    ],
    "eps_diluted": [
        ("us-gaap", "EarningsPerShareDiluted"),
        ("ifrs-full", "DilutedEarningsLossPerShare"),
    ],
}

REPORT_FORMS = {"10-Q", "10-K", "20-F", "40-F", "10-Q/A", "10-K/A", "20-F/A"}
# Days a period-end date may differ by (52/53-week fiscal years).
END_SLACK_DAYS = 7
DURATIONS: dict[str, tuple[int, int]] = {
    "Q1": (80, 100),
    "Q2": (80, 100),
    "Q3": (80, 100),
    "Q4": (80, 100),
    "H1": (170, 195),
    "H2": (170, 195),
    "FY": (350, 380),
}
NINE_MONTHS = (260, 285)
# When a report for a period is due: 10-Q within 40-45 days, 10-K within 60-90, 20-F 4 months.
NO_DATA_AFTER_DAYS = {"quarter": 60, "annual": 130}


@dataclass(frozen=True)
class XbrlPoint:
    concept: str
    start: dt.date
    end: dt.date
    value: Decimal
    accession: str
    form: str
    filed: dt.date

    @property
    def days(self) -> int:
        return (self.end - self.start).days


@dataclass(frozen=True)
class FactToCheck:
    id: int
    metric: str
    fiscal_period: str
    period_end: dt.date
    value_units: Decimal
    value_as_written: str
    scale: str
    unit: str
    currency: str | None


@dataclass(frozen=True)
class Comparison:
    outcome: str  # match, mismatch, no_data
    concept: str | None = None
    accession: str | None = None
    form: str | None = None
    start: dt.date | None = None
    end: dt.date | None = None
    derivation: str | None = None
    xbrl_value: Decimal | None = None
    difference: Decimal | None = None
    detail: str = ""


def concept_points(
    company_facts: dict[str, Any], taxonomy: str, concept: str, unit_key: str
) -> list[XbrlPoint]:
    """Duration facts from periodic reports for one XBRL concept."""
    facts = company_facts.get("facts", {})
    rows = facts.get(taxonomy, {}).get(concept, {}).get("units", {}).get(unit_key, [])
    found = []
    for r in rows:
        if r.get("form") not in REPORT_FORMS or "start" not in r:
            continue
        try:
            found.append(
                XbrlPoint(
                    concept=f"{taxonomy}:{concept}",
                    start=dt.date.fromisoformat(r["start"]),
                    end=dt.date.fromisoformat(r["end"]),
                    value=Decimal(str(r["val"])),
                    accession=r["accn"],
                    form=r["form"],
                    filed=dt.date.fromisoformat(r["filed"]),
                )
            )
        except (KeyError, ValueError, ArithmeticError):
            continue
    return found


def points(company_facts: dict[str, Any], metric: str, unit_key: str) -> list[XbrlPoint]:
    """Duration facts from periodic reports for the metric's first concept that has any."""
    for taxonomy, concept in CONCEPTS.get(metric, []):
        found = concept_points(company_facts, taxonomy, concept, unit_key)
        if found:
            return found
    return []


def match_concepts(
    fact: FactToCheck, company_facts: dict[str, Any], unit_key: str
) -> tuple[XbrlPoint, str, Decimal] | None:
    """The first concept in preference order that has a figure for the fact's period.
    Chosen per period: issuers switch concepts over the years (Revenues ->
    RevenueFromContractWithCustomer...), and each period is matched within one concept."""
    for taxonomy, concept in CONCEPTS.get(fact.metric, []):
        found = concept_points(company_facts, taxonomy, concept, unit_key)
        matched = match_period(fact, found) if found else None
        if matched is not None:
            return matched
    return None


def unit_key(unit: str, currency: str | None) -> str | None:
    if currency is None:
        return None
    if unit == "currency":
        return currency
    if unit == "per_share":
        return f"{currency}/shares"
    return None


def tolerance(value_as_written: str, scale: str) -> Decimal:
    """Half a unit of the last printed digit, at the printed scale."""
    core = value_as_written.strip().strip("()").lstrip("-\u2212").replace(",", "").strip()
    decimals = len(core.partition(".")[2]) if "." in core else 0
    return Decimal(5) / Decimal(10) ** (decimals + 1) * SCALE_FACTOR[scale]


def _near(a: dt.date, b: dt.date) -> bool:
    return abs((a - b).days) <= END_SLACK_DAYS


def _first_filed(candidates: list[XbrlPoint]) -> XbrlPoint | None:
    return min(candidates, key=lambda p: (p.filed, p.accession)) if candidates else None


def match_period(
    fact: FactToCheck, available: list[XbrlPoint]
) -> tuple[XbrlPoint, str, Decimal] | None:
    """The XBRL point for the fact's period: (point, derivation, value)."""
    low, high = DURATIONS[fact.fiscal_period]
    direct = _first_filed(
        [p for p in available if _near(p.end, fact.period_end) and low <= p.days <= high]
    )
    if direct is not None:
        return direct, "reported", direct.value
    if fact.fiscal_period != "Q4" or fact.unit == "per_share":
        return None
    year = _first_filed(
        [p for p in available if _near(p.end, fact.period_end) and 350 <= p.days <= 380]
    )
    if year is None:
        return None
    nine = _first_filed(
        [
            p
            for p in available
            if abs((p.start - year.start).days) <= END_SLACK_DAYS
            and NINE_MONTHS[0] <= p.days <= NINE_MONTHS[1]
        ]
    )
    if nine is None:
        return None
    return year, "derived_q4", year.value - nine.value


def compare(fact: FactToCheck, company_facts: dict[str, Any], today: dt.date) -> Comparison:
    key = unit_key(fact.unit, fact.currency)
    if key is None or fact.metric not in CONCEPTS:
        return Comparison("no_data", detail="not a reconcilable figure")
    matched = match_concepts(fact, company_facts, key)
    if matched is None:
        kind = "annual" if fact.fiscal_period in ("FY", "H2") else "quarter"
        due = fact.period_end + dt.timedelta(days=NO_DATA_AFTER_DAYS[kind])
        detail = (
            "no XBRL figure for this period although its report is due"
            if today > due
            else "report not filed yet"
        )
        return Comparison("no_data", detail=detail)
    point, derivation, value = matched
    difference = fact.value_units - value
    allowed = tolerance(fact.value_as_written, fact.scale)
    outcome = "match" if abs(difference) <= allowed else "mismatch"
    how = "full year minus nine months" if derivation == "derived_q4" else "as filed"
    detail = (
        f"{point.concept} in {point.form} {point.accession} ({how}): {value} vs stored "
        f"{fact.value_units} (allowed +-{allowed})"
    )
    return Comparison(
        outcome,
        point.concept,
        point.accession,
        point.form,
        point.start,
        point.end,
        derivation,
        value,
        difference,
        detail,
    )


async def facts_to_check(pool: asyncpg.Pool, ticker: str) -> list[FactToCheck]:
    """Extracted, reported-basis facts not yet matched or quarantined by reconciliation and
    never reviewed by the owner."""
    rows = await pool.fetch(
        "select f.id, f.metric, f.fiscal_period, f.period_end, f.value_units,"
        " f.value_as_written, f.scale, f.unit, f.currency from public.fact f"
        " where f.ticker = $1 and f.basis = 'reported' and f.origin = 'extraction'"
        " and f.status in ('verified', 'unverified') and f.period_end is not null"
        " and f.metric = any($2::text[])"
        " and not exists (select 1 from public.fact_review r where r.fact_id = f.id)"
        " and not exists (select 1 from public.fact_reconciliation c where c.fact_id = f.id"
        "   and c.outcome in ('match', 'mismatch'))"
        " order by f.id",
        ticker,
        list(CONCEPTS),
    )
    return [FactToCheck(**dict(r)) for r in rows]


@dataclass
class ReconcileSummary:
    checked: int = 0
    matched: int = 0
    quarantined: list[int] | None = None
    pending: int = 0

    def as_detail(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "matched": self.matched,
            "quarantined": self.quarantined or [],
            "not_yet_filed": self.pending,
        }


async def reconcile_ticker(
    pool: asyncpg.Pool,
    ticker: str,
    company_facts: dict[str, Any],
    today: dt.date,
    job_id: int | None,
) -> ReconcileSummary:
    summary = ReconcileSummary(quarantined=[])
    for fact in await facts_to_check(pool, ticker):
        result = compare(fact, company_facts, today)
        summary.checked += 1
        if result.outcome == "no_data" and result.detail == "report not filed yet":
            summary.pending += 1
            continue
        if result.outcome == "no_data":
            already = await pool.fetchval(
                "select exists (select 1 from public.fact_reconciliation where fact_id = $1"
                " and outcome = 'no_data')",
                fact.id,
            )
            if already:
                continue
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "insert into public.fact_reconciliation (fact_id, source, concept, accession,"
                " form, period_start, period_end, derivation, xbrl_value, fact_value, difference,"
                " outcome, detail, job_id) values ($1, 'xbrl', $2, $3, $4, $5, $6, $7, $8, $9,"
                " $10, $11, $12, $13)",
                fact.id,
                result.concept,
                result.accession,
                result.form,
                result.start,
                result.end,
                result.derivation,
                result.xbrl_value,
                fact.value_units,
                result.difference,
                result.outcome,
                result.detail,
                job_id,
            )
            if result.outcome == "mismatch":
                await conn.execute(
                    "update public.fact set status = 'quarantined', status_reason = $2"
                    " where id = $1",
                    fact.id,
                    f"reconciliation: {result.detail}",
                )
                assert summary.quarantined is not None
                summary.quarantined.append(fact.id)
        if result.outcome == "match":
            summary.matched += 1
    return summary


async def fact_reconcile_handler(ctx: JobContext) -> None:
    settings = get_settings()
    pool = ctx.pool
    async with ctx.step("load_config") as step:
        config = await load_config(ctx)
        only = ctx.params.get("ticker")
        tickers = [
            r["ticker"]
            for r in await pool.fetch(
                "select distinct ticker from public.fact where basis = 'reported'"
                " and origin = 'extraction' and status in ('verified', 'unverified')"
                " order by ticker"
            )
            if not only or r["ticker"] == only
        ]
        step.detail["tickers"] = tickers
        if settings.sec_user_agent is None:
            step.detail["skipped"] = "SEC_USER_AGENT is not set"
            return
    today = dt.datetime.now(dt.UTC).date()
    async with httpx.AsyncClient(
        timeout=60.0, headers={"User-Agent": f"StewardAI/{__version__}"}
    ) as http:
        edgar = EdgarClient(http, settings.sec_user_agent)
        for ticker in tickers:
            async with ctx.step(f"reconcile:{ticker}") as step:
                profile = config.profiles.for_ticker(ticker) or HoldingProfile(ticker=ticker)
                instrument = await pool.fetchrow(
                    "select t212_ticker, cik from public.instrument where t212_ticker = $1",
                    ticker,
                )
                cik = await resolve_cik(pool, edgar, profile, instrument)
                if cik is None:
                    step.detail["skipped"] = "no SEC CIK (not an EDGAR filer)"
                    continue
                try:
                    company_facts = await edgar.company_facts(cik)
                except (EdgarError, httpx.HTTPError, json.JSONDecodeError) as exc:
                    step.detail["error"] = str(exc)[:300]
                    continue
                summary = await reconcile_ticker(pool, ticker, company_facts, today, ctx.job_id)
                step.detail.update(summary.as_detail())
                if summary.quarantined and config.theses_version_id is not None:
                    verdicts = await run_verdicts(
                        pool, config.theses, config.theses_version_id, ctx.job_id, [ticker]
                    )
                    step.detail["verdicts"] = verdicts.as_detail()
