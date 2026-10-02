"""Domain-specific error thresholds, sampled reconciliation and the enable gate (plan D8.3).

A new alert or valuation conclusion may only be switched on after sampled stored values were
compared with a primary source (an exchange file, a filing, a statistical release, a patent
register) and the error stayed inside the domain's threshold.  Thresholds are versioned:
the built-in defaults below apply until an active `domain_error_threshold` row overrides them,
and every recorded sample remembers the version it was judged against.  The gate never
assumes a pass - with no samples it blocks and says so.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import asyncpg

Domain = Literal["prices", "corporate_actions", "statements", "fund_weights", "macro", "patents"]
DOMAINS: tuple[Domain, ...] = (
    "prices",
    "corporate_actions",
    "statements",
    "fund_weights",
    "macro",
    "patents",
)
DEFAULT_VERSION = "2026.10-defaults"


@dataclass(frozen=True, slots=True)
class DomainThreshold:
    """Limits one sample must satisfy (every configured limit) and how many may fail."""

    domain: Domain
    version: str
    max_abs_error: Decimal | None
    max_rel_error: Decimal | None
    max_failing_fraction: Decimal
    min_samples: int
    max_sample_age_days: int
    note: str = ""

    def to_params(self) -> dict[str, Any]:
        return {
            "max_abs_error": None if self.max_abs_error is None else str(self.max_abs_error),
            "max_rel_error": None if self.max_rel_error is None else str(self.max_rel_error),
            "max_failing_fraction": str(self.max_failing_fraction),
            "min_samples": self.min_samples,
            "max_sample_age_days": self.max_sample_age_days,
        }

    @classmethod
    def from_params(
        cls, domain: Domain, version: str, params: Mapping[str, Any], note: str = ""
    ) -> DomainThreshold:
        def dec(key: str) -> Decimal | None:
            raw = params.get(key)
            return None if raw is None else Decimal(str(raw))

        fraction = dec("max_failing_fraction")
        if fraction is None or not Decimal(0) <= fraction <= Decimal(1):
            raise ValueError("max_failing_fraction must be between 0 and 1")
        abs_limit, rel_limit = dec("max_abs_error"), dec("max_rel_error")
        if abs_limit is None and rel_limit is None:
            raise ValueError("a threshold needs max_abs_error or max_rel_error")
        if any(v is not None and v < 0 for v in (abs_limit, rel_limit)):
            raise ValueError("error limits cannot be negative")
        return cls(
            domain,
            version,
            abs_limit,
            rel_limit,
            fraction,
            int(params.get("min_samples", 30)),
            int(params.get("max_sample_age_days", 120)),
            note,
        )


# Proposed starting points, to be tightened or confirmed by the owner after the shadow run.
# They are limits on *measured disagreement with a primary source*, not claims about quality.
DEFAULT_THRESHOLDS: dict[Domain, DomainThreshold] = {
    "prices": DomainThreshold(
        "prices",
        DEFAULT_VERSION,
        None,
        Decimal("0.0005"),
        Decimal("0.02"),
        30,
        120,
        "close price vs exchange official close, same currency and adjustment basis",
    ),
    "corporate_actions": DomainThreshold(
        "corporate_actions",
        DEFAULT_VERSION,
        None,
        Decimal(0),
        Decimal(0),
        20,
        180,
        "split ratio / cash amount must match the issuer notice exactly; no tolerated misses",
    ),
    "statements": DomainThreshold(
        "statements",
        DEFAULT_VERSION,
        None,
        Decimal("0.001"),
        Decimal("0.02"),
        30,
        180,
        "reported line item vs the filing's own tagged value (after scale)",
    ),
    "fund_weights": DomainThreshold(
        "fund_weights",
        DEFAULT_VERSION,
        Decimal("0.05"),
        None,
        Decimal("0.02"),
        30,
        90,
        "weight in percentage points vs the issuer holdings file for the same as-of date",
    ),
    "macro": DomainThreshold(
        "macro",
        DEFAULT_VERSION,
        None,
        Decimal("0.0001"),
        Decimal("0.01"),
        20,
        180,
        "release value vs the statistical office's own table for the same vintage",
    ),
    "patents": DomainThreshold(
        "patents",
        DEFAULT_VERSION,
        Decimal(0),
        None,
        Decimal("0.05"),
        20,
        365,
        "family identifiers, dates and counts vs the patent register (exact)",
    ),
}

# Which domains must pass before a kind of new conclusion may be enabled.
CONCLUSION_DOMAINS: dict[str, tuple[Domain, ...]] = {
    "price_alert": ("prices",),
    "return_alert": ("prices", "corporate_actions"),
    "corporate_action_alert": ("corporate_actions",),
    "valuation_conclusion": ("statements", "prices"),
    "fund_overlap_alert": ("fund_weights",),
    "macro_alert": ("macro",),
    "credit_stress_alert": ("macro",),
    "trade_exposure_alert": ("macro",),
    "patent_alert": ("patents",),
}


@dataclass(frozen=True, slots=True)
class PrimarySample:
    """One value read by a person from the primary source, with where it was read."""

    domain: Domain
    subject_key: str
    metric: str
    primary_value: Decimal | None
    primary_source: str
    primary_url: str | None = None
    period_end: dt.date | None = None
    # Only used by the pure `reconcile_samples` comparison.  `record_reconciliation` ignores it
    # and re-reads the stored value from the app's own tables (B47).
    stored_value: Decimal | None = None
    reviewer: str | None = None
    sampled_at: dt.datetime | None = None


class SampleRejected(ValueError):
    """A sample that cannot count as evidence: nothing is recorded."""


CLOCK_SKEW = dt.timedelta(minutes=5)


def _finite(value: Decimal | None) -> bool:
    return value is not None and value.is_finite()


def validate_sample(sample: PrimarySample, *, now: dt.datetime) -> None:
    """Refuse samples that lack provenance: a named reviewer, where the primary value was read
    (source and url), a finite primary value and a sampling time that is not in the future."""
    problems: list[str] = []
    if not (sample.reviewer or "").strip():
        problems.append("reviewer is required")
    if not (sample.primary_source or "").strip():
        problems.append("primary_source is required")
    if not (sample.primary_url or "").strip():
        problems.append("primary_url is required")
    if not _finite(sample.primary_value):
        problems.append("primary_value must be a finite number")
    if sample.sampled_at is not None and sample.sampled_at > now + CLOCK_SKEW:
        problems.append(f"sampled_at {sample.sampled_at.isoformat()} is in the future")
    if problems:
        raise SampleRejected(
            f"{sample.domain}/{sample.subject_key}/{sample.metric}: " + "; ".join(problems)
        )


@dataclass(frozen=True, slots=True)
class SampleResult:
    sample: PrimarySample
    matched: bool
    abs_error: Decimal | None
    rel_error: Decimal | None
    reason: str | None


def compare_sample(sample: PrimarySample, threshold: DomainThreshold) -> SampleResult:
    """Judge one stored value against its primary-source value."""
    primary, stored = sample.primary_value, sample.stored_value
    if primary is None:
        raise ValueError("a reconciliation sample needs the primary-source value")
    if not primary.is_finite():
        # NaN and Infinity would make every comparison below raise or silently pass.
        raise ValueError("the primary-source value must be a finite number")
    if stored is None:
        return SampleResult(sample, False, None, None, "no stored value for the sampled record")
    if not stored.is_finite():
        return SampleResult(sample, False, None, None, "stored value is not a finite number")
    abs_error = abs(stored - primary)
    rel_error = abs_error / abs(primary) if primary != 0 else (Decimal(0) if stored == 0 else None)
    return judge(sample, threshold, abs_error, rel_error)


def judge(
    sample: PrimarySample,
    threshold: DomainThreshold,
    abs_error: Decimal | None,
    rel_error: Decimal | None,
) -> SampleResult:
    reasons: list[str] = []
    if threshold.max_abs_error is not None and (
        abs_error is None or abs_error > threshold.max_abs_error
    ):
        reasons.append(f"absolute error {abs_error} > {threshold.max_abs_error}")
    if threshold.max_rel_error is not None and (
        rel_error is None or rel_error > threshold.max_rel_error
    ):
        reasons.append(f"relative error {rel_error} > {threshold.max_rel_error}")
    return SampleResult(sample, not reasons, abs_error, rel_error, "; ".join(reasons) or None)


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    domain: Domain
    threshold: DomainThreshold
    results: tuple[SampleResult, ...]

    @property
    def sample_count(self) -> int:
        return len(self.results)

    @property
    def failures(self) -> int:
        return sum(1 for r in self.results if not r.matched)

    @property
    def failing_fraction(self) -> Decimal | None:
        return Decimal(self.failures) / self.sample_count if self.sample_count else None

    @property
    def max_rel_error(self) -> Decimal | None:
        errors = [r.rel_error for r in self.results if r.rel_error is not None]
        return max(errors) if errors else None

    def decision(self) -> GateDecision:
        subjects = len({r.sample.subject_key for r in self.results})
        return decide(
            self.domain, self.threshold, self.sample_count, self.failures, subject_count=subjects
        )


def reconcile_samples(
    samples: Sequence[PrimarySample],
    threshold: DomainThreshold,
) -> ReconciliationReport:
    """Compare supplied samples (each carrying its stored value) to their primary values."""
    domains = {s.domain for s in samples}
    if len(domains) > 1 or (domains and domains != {threshold.domain}):
        raise ValueError("samples and threshold must belong to one domain")
    return ReconciliationReport(
        threshold.domain, threshold, tuple(compare_sample(s, threshold) for s in samples)
    )


@dataclass(frozen=True, slots=True)
class GateDecision:
    domain: Domain
    allowed: bool
    version: str
    sample_count: int
    failures: int
    reasons: tuple[str, ...] = field(default_factory=tuple)
    subject_count: int = 0


def decide(
    domain: Domain,
    threshold: DomainThreshold,
    sample_count: int,
    failures: int,
    *,
    subject_count: int | None = None,
    excluded: int = 0,
) -> GateDecision:
    """`min_samples` is checked against DISTINCT subjects (`subject_count`, defaulting to the
    sample count for callers that have no subjects): thirty metrics of one company are one
    subject, not thirty samples."""
    reasons: list[str] = []
    subjects = sample_count if subject_count is None else subject_count
    if sample_count == 0:
        reasons.append("no reconciliation samples recorded; nothing is verified")
    elif subjects < threshold.min_samples:
        reasons.append(f"{subjects} distinct subjects sampled, {threshold.min_samples} required")
    if sample_count:
        fraction = Decimal(failures) / sample_count
        if fraction > threshold.max_failing_fraction:
            reasons.append(
                f"{failures}/{sample_count} samples outside tolerance "
                f"(allowed fraction {threshold.max_failing_fraction})"
            )
    if reasons and excluded:
        reasons.append(
            f"{excluded} recorded samples were ignored (future-dated, unreviewed or without "
            "a named primary source)"
        )
    return GateDecision(
        domain, not reasons, threshold.version, sample_count, failures, tuple(reasons), subjects
    )


class GateBlocked(RuntimeError):
    def __init__(self, conclusion: str, decisions: Sequence[GateDecision]) -> None:
        self.conclusion = conclusion
        self.decisions = tuple(decisions)
        blocked = [d for d in decisions if not d.allowed]
        super().__init__(
            f"{conclusion} is blocked: "
            + "; ".join(f"{d.domain}: {', '.join(d.reasons)}" for d in blocked)
        )


# ----------------------------------------------------------------------------- persistence
async def load_threshold(pool: asyncpg.Pool, domain: Domain) -> DomainThreshold:
    row = await pool.fetchrow(
        "select version, params, note from public.domain_error_threshold"
        " where domain = $1 and active",
        domain,
    )
    if row is None:
        return DEFAULT_THRESHOLDS[domain]
    import json

    params = row["params"]
    params = json.loads(params) if isinstance(params, str) else params
    return DomainThreshold.from_params(domain, row["version"], params, row["note"] or "")


async def activate_threshold(pool: asyncpg.Pool, threshold: DomainThreshold) -> None:
    """Store a new threshold version and make it the only active one for its domain."""
    import json

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "update public.domain_error_threshold set active = false where domain = $1 and active",
            threshold.domain,
        )
        await conn.execute(
            """insert into public.domain_error_threshold(domain,version,params,active,note)
               values($1,$2,$3::jsonb,true,$4)
               on conflict (domain,version) do update set params=excluded.params,
                 active=true, note=excluded.note""",
            threshold.domain,
            threshold.version,
            json.dumps(threshold.to_params()),
            threshold.note or None,
        )


_UPSERT_SAMPLE = """insert into public.reconciliation_sample
   (domain,subject_key,metric,period_end,stored_value,primary_value,primary_source,primary_url,
    abs_error,rel_error,matched,failure_reason,threshold_version,reviewer,sampled_at)
   values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
   on conflict (domain,subject_key,metric,(coalesce(period_end, date '0001-01-01')))
   do update set stored_value=excluded.stored_value, primary_value=excluded.primary_value,
     primary_source=excluded.primary_source, primary_url=excluded.primary_url,
     abs_error=excluded.abs_error, rel_error=excluded.rel_error, matched=excluded.matched,
     failure_reason=excluded.failure_reason, threshold_version=excluded.threshold_version,
     reviewer=excluded.reviewer, sampled_at=excluded.sampled_at"""


async def record_reconciliation(
    pool: asyncpg.Pool, report: ReconciliationReport, *, now: dt.datetime | None = None
) -> int:
    """Store the primary-source samples of `report` - and only those.

    The caller supplies what a person read from the primary source.  What the app stored is NOT
    taken from the caller: every `stored_value` is re-read here from the app's own tables and
    every sample is judged again against the report's threshold, so a hand-written stored value
    cannot turn a mismatch into a pass.  A sample without a reviewer, source or url, with a
    non-finite primary value or a future `sampled_at` rejects the whole report (nothing is
    written).  Re-sampling the same (domain, subject, metric, period) replaces the earlier
    sample instead of adding a second copy."""
    now = now or dt.datetime.now(dt.UTC)
    samples = [r.sample for r in report.results]
    for sample in samples:
        validate_sample(sample, now=now)
    resolved = await resolve_stored_values(pool, samples, recompute=True)
    judged = [compare_sample(s, report.threshold) for s in resolved]
    async with pool.acquire() as conn, conn.transaction():
        for r in judged:
            s = r.sample
            await conn.execute(
                _UPSERT_SAMPLE,
                s.domain,
                s.subject_key,
                s.metric,
                s.period_end,
                s.stored_value,
                s.primary_value,
                s.primary_source,
                s.primary_url,
                r.abs_error,
                r.rel_error,
                r.matched,
                r.reason,
                report.threshold.version,
                s.reviewer,
                s.sampled_at or now,
            )
    return len(judged)


async def record_primary_samples(
    pool: asyncpg.Pool,
    domain: Domain,
    samples: Sequence[PrimarySample],
    *,
    now: dt.datetime | None = None,
) -> ReconciliationReport:
    """Judge and record primary-source samples of one domain against the active threshold."""
    threshold = await load_threshold(pool, domain)
    report = reconcile_samples(samples, threshold)
    await record_reconciliation(pool, report, now=now)
    return report


async def domain_gate(
    pool: asyncpg.Pool, domain: Domain, *, now: dt.datetime | None = None
) -> GateDecision:
    """Decide from recorded samples that are recent enough and were judged against the
    currently active threshold version.

    Every sample is re-judged here from its stored and primary values (not from the stored
    error columns), so tightening a limit takes effect without re-sampling and a hand-edited
    error column cannot hide a mismatch.  Samples dated in the future, or recorded without a
    reviewer, primary source or url, are ignored.  `min_samples` counts distinct subjects."""
    threshold = await load_threshold(pool, domain)
    now = now or dt.datetime.now(dt.UTC)
    since = now - dt.timedelta(days=threshold.max_sample_age_days)
    rows = await pool.fetch(
        """select subject_key, metric, period_end, stored_value, primary_value, sampled_at,
                  coalesce(btrim(reviewer), '') <> ''
                  and coalesce(btrim(primary_source), '') <> ''
                  and coalesce(btrim(primary_url), '') <> '' as attributed
             from public.reconciliation_sample
            where domain = $1 and sampled_at >= $2""",
        domain,
        since,
    )
    failures = 0
    counted = 0
    excluded = 0
    subjects: set[str] = set()
    for row in rows:
        if row["sampled_at"] > now + CLOCK_SKEW or not row["attributed"]:
            excluded += 1
            continue
        counted += 1
        subjects.add(row["subject_key"])
        primary = row["primary_value"]
        sample = PrimarySample(
            domain,
            row["subject_key"],
            row["metric"],
            None if primary is None else Decimal(primary),
            "recorded",
            None,
            row["period_end"],
            row["stored_value"],
        )
        if sample.primary_value is None or not sample.primary_value.is_finite():
            failures += 1
            continue
        failures += 0 if compare_sample(sample, threshold).matched else 1
    return decide(
        domain, threshold, counted, failures, subject_count=len(subjects), excluded=excluded
    )


async def require_domain_gate(
    pool: asyncpg.Pool, conclusion: str, *, now: dt.datetime | None = None
) -> list[GateDecision]:
    """Raise GateBlocked unless every domain the conclusion depends on passes."""
    domains = CONCLUSION_DOMAINS.get(conclusion)
    if domains is None:
        raise GateBlocked(
            conclusion,
            [
                GateDecision(
                    "prices", False, "-", 0, 0, (f"unknown conclusion kind {conclusion!r}",)
                )
            ],
        )
    decisions = [await domain_gate(pool, d, now=now) for d in domains]
    if not all(d.allowed for d in decisions):
        raise GateBlocked(conclusion, decisions)
    return decisions


# ----------------------------------------------------------------------------- resolvers
def _num(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


async def resolve_stored_values(
    pool: asyncpg.Pool, samples: Sequence[PrimarySample], *, recompute: bool = False
) -> list[PrimarySample]:
    """Fill `stored_value` from the app's own tables.

    By default only for samples that did not supply one; with `recompute=True` a supplied value
    is discarded and always re-read (what `record_reconciliation` does).

    subject_key formats: prices `SYMBOL:source:bar_type` or a bare `SYMBOL` that has a single
    stored bar that day (metric `close`), corporate_actions
    `SYMBOL:action_type` (metric `ratio`|`cash_amount`, period_end = ex-date), statements
    `TICKER` (metric = concept), fund_weights `SNAPSHOT_ID:ISIN` (metric `weight_pct`),
    macro `SOURCE:SERIES:GEO` (metric `value`), patents `FAMILY_KEY` (metric
    `citations_forward`|`citations_backward`|`exists`).  An unresolvable sample keeps
    `stored_value=None` and therefore fails - it is never skipped."""
    out: list[PrimarySample] = []
    for s in samples:
        keep = s.stored_value is not None and not recompute
        stored = s.stored_value if keep else await _resolve_one(pool, s)
        out.append(
            PrimarySample(
                s.domain,
                s.subject_key,
                s.metric,
                s.primary_value,
                s.primary_source,
                s.primary_url,
                s.period_end,
                stored,
                s.reviewer,
                s.sampled_at,
            )
        )
    return out


async def _resolve_one(pool: asyncpg.Pool, s: PrimarySample) -> Decimal | None:
    if s.domain == "prices" and s.period_end:
        # `SYMBOL:source:bar_type` names exactly which stored bar is being checked.  A bare
        # `SYMBOL` resolves only when one source/basis holds that day: with several (stooq raw
        # and adjusted, a fallback provider ...) the comparison would silently pick one, so the
        # sample stays unresolved and fails instead of passing against the wrong series.
        parts = s.subject_key.split(":")
        if len(parts) == 3:
            return _num(
                await pool.fetchval(
                    "select close from public.price_bar where symbol=$1 and date=$2"
                    " and source=$3 and bar_type=$4",
                    parts[0],
                    s.period_end,
                    parts[1],
                    parts[2],
                )
            )
        if len(parts) == 1:
            bars = await pool.fetch(
                "select close from public.price_bar where symbol=$1 and date=$2 limit 2",
                s.subject_key,
                s.period_end,
            )
            return _num(bars[0]["close"]) if len(bars) == 1 else None
        return None
    if s.domain == "corporate_actions" and s.period_end and ":" in s.subject_key:
        symbol, action = s.subject_key.split(":", 1)
        column = {"ratio": "ratio", "cash_amount": "cash_amount"}.get(s.metric)
        if column is None:
            return None
        return _num(
            await pool.fetchval(
                f"select {column} from public.corporate_action where symbol=$1"  # noqa: S608
                " and action_type=$2 and ex_date=$3 and status='confirmed'"
                " order by known_at desc limit 1",
                symbol,
                action,
                s.period_end,
            )
        )
    if s.domain == "statements" and s.period_end:
        return _num(
            await pool.fetchval(
                "select value from public.financial_statement_fact where ticker=$1"
                " and concept=$2 and period_end=$3 and value is not null"
                " order by known_at desc, revision desc limit 1",
                s.subject_key,
                s.metric,
                s.period_end,
            )
        )
    if s.domain == "fund_weights" and ":" in s.subject_key:
        snapshot, isin = s.subject_key.split(":", 1)
        if not snapshot.isdigit():
            return None
        weight = await pool.fetchval(
            "select sum(weight) from public.etf_holding where snapshot_id=$1 and isin=$2",
            int(snapshot),
            isin,
        )
        number = _num(weight)
        return number * 100 if number is not None else None
    if s.domain == "macro" and s.period_end and s.subject_key.count(":") == 2:
        source, series, geography = s.subject_key.split(":")
        return _num(
            await pool.fetchval(
                "select value from public.macro_observation where source=$1 and series_id=$2"
                " and geography=$3 and period=$4 order by known_at desc, revision desc limit 1",
                source,
                series,
                geography,
                s.period_end,
            )
        )
    if s.domain == "patents":
        row = await pool.fetchrow(
            "select citations_forward, citations_backward from public.patent_family"
            " where family_key=$1",
            s.subject_key,
        )
        if s.metric == "exists":
            return Decimal(1) if row else Decimal(0)
        if row is None:
            return None
        if s.metric in ("citations_forward", "citations_backward"):
            return _num(row[s.metric])
        return None
    return None
