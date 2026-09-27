"""The validator: every extracted figure is checked in code before it is stored as verified.

Checks, in order (the name is what `fact.checks` records):

- `snippet_containment` - the quoted snippet (and the unit line, if one is quoted) occurs
  in the archived document's text. Hard.
- `value_in_snippet` - the number as written occurs in that snippet. Hard.
- `value_reading` - the model's numeric value equals the printed number read in code
  (sign from parentheses, an attached minus, or a sign word such as "loss" or "decreased"
  next to the number). A positive value for a signed metric the snippet states as a loss or
  a decline fails. Hard. The stored value is the one read in code, never the model's.
- `unit_scale` - the claimed scale (millions, ...) is stated by the snippet or unit line,
  and a scale printed right next to the number ("94.0 billion") wins over the unit line;
  percentages carry "%"; per-share figures are unscaled. Hard.
- `currency` - the currency is stated near the figure or elsewhere in the document; a
  currency other than the profile's reporting currency is held for review.
- `period_alignment` - the fiscal period maps (via the profile's fiscal year end) to the
  calendar month the document states; the main period ended before, and not long before,
  the report date. Hard.
- `consistency` - one document must not give two different values for one figure. Hard.
- `recomputation` - a ratio the document states (a margin, a growth rate) must agree with
  the one recomputed from its raw figures. Hard.
- `sanity_bound` - a quarter's figure that moves more than the configured band against the
  previous quarter's verified figure is held for owner review.
- `restatement` - a value that differs from one already stored for the same period is
  kept next to it and held for review, never overwriting it.

Outcome: any failed hard check -> `rejected`; else any hold -> `unverified`; else `verified`.
"""

import datetime as dt
import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from steward.config.verification import VerificationConfig
from steward.documents.text import contains, normalize_for_match
from steward.earnings.metrics import MARGINS, derivation
from steward.earnings.periods import (
    calendar_period_end,
    period_label,
    previous_quarter,
    sort_key,
)
from steward.earnings.schema import ExtractedFigure
from steward.earnings.units import (
    SCALE_FACTOR,
    conflicting_scales,
    currency_markers_present,
    parse_written_number,
    scale_markers_present,
    scale_next_to,
    uses_decimal_comma,
)

FactStatus = Literal["verified", "unverified", "rejected"]
Severity = Literal["reject", "hold"]


@dataclass(frozen=True)
class FactKey:
    metric: str
    basis: str
    fiscal_year: int
    fiscal_period: str


@dataclass(frozen=True)
class KnownFact:
    """A fact already stored for the ticker (latest non-rejected one per key)."""

    id: int
    value_units: Decimal
    unit: str
    currency: str | None
    status: str


@dataclass(frozen=True)
class RejectedFact:
    """A fact the owner rejected (or replaced with a correction)."""

    id: int
    value_units: Decimal
    unit: str


@dataclass
class Check:
    check: str
    ok: bool
    detail: str = ""
    severity: Severity = "reject"

    def as_json(self) -> dict[str, object]:
        return {"check": self.check, "ok": self.ok, "detail": self.detail}


@dataclass
class ValidatedFigure:
    figure: ExtractedFigure
    key: FactKey
    value: Decimal | None
    value_units: Decimal | None
    period_end: dt.date | None
    checks: list[Check] = field(default_factory=list)
    restates: int | None = None
    duplicate_of: int | None = None
    # The owner already rejected this exact value for this key: not stored again.
    owner_rejected: int | None = None

    @property
    def status(self) -> FactStatus:
        if any(not c.ok and c.severity == "reject" for c in self.checks):
            return "rejected"
        if any(not c.ok for c in self.checks):
            return "unverified"
        return "verified"

    @property
    def reason(self) -> str | None:
        failed = [c for c in self.checks if not c.ok]
        failed.sort(key=lambda c: c.severity != "reject")
        return f"{failed[0].check}: {failed[0].detail}" if failed else None

    def fail(self, check: str, detail: str, severity: Severity = "reject") -> None:
        self.checks.append(Check(check, False, detail, severity))

    def ok(self, check: str, detail: str = "") -> None:
        self.checks.append(Check(check, True, detail))

    @property
    def passed_hard_checks(self) -> bool:
        return not any(not c.ok and c.severity == "reject" for c in self.checks)


@dataclass(frozen=True)
class ValidationContext:
    document_text: str
    report_date: dt.date
    fye_month: int
    reporting_currency: str | None
    config: VerificationConfig
    known: dict[FactKey, KnownFact] = field(default_factory=dict)
    owner_rejected: dict[FactKey, list[RejectedFact]] = field(default_factory=dict)


def validate(figures: list[ExtractedFigure], ctx: ValidationContext) -> list[ValidatedFigure]:
    results = [_validate_one(f, ctx) for f in figures]
    results = _check_consistency(results)
    _check_report_lag(results, ctx)
    _check_recomputation(results, ctx)
    _check_against_known(results, ctx)
    return results


# ---------------------------------------------------------------------------
# Per-figure checks
# ---------------------------------------------------------------------------


def _core_number(written: str) -> str:
    return written.strip().strip("()").lstrip("-\u2212\u2013").strip()


def _number_pattern(number: str) -> str:
    """`number` as a whole number: no digit, nor a separator followed by a digit, on either
    side ("4,036" is not in "$94,036")."""
    return rf"(?<!\d)(?<!\d[.,]){re.escape(number)}(?!\d)(?![.,]\d)"


def _number_in(text: str, number: str) -> list[re.Match[str]]:
    return list(
        re.finditer(_number_pattern(normalize_for_match(number)), normalize_for_match(text))
    )


# Words that make the number next to them negative. A caption such as "income (loss)",
# "profit/(loss)" or "increase (decrease)" only names a signed line and does not count.
_SIGN_WORDS = (
    r"loss|losses|decrease|decreased|decreases|decline|declined|declines|fell|down|negative"
    r"|outflow|outflows"
)
_SIGN_WORD = re.compile(rf"(?<![a-z/(])(?<!/ )(?<!\( )(?:{_SIGN_WORDS})(?![a-z)])(?! \))")
# A sign word stated right before the figure: "net loss of $171 million", "revenue
# decreased 3.2%", "down 4%". Nothing but filler, a currency and spaces in between.
_SIGN_BEFORE = re.compile(
    rf"(?<![a-z/(])(?P<word>{_SIGN_WORDS})"
    r"(?: per (?:diluted |basic )?share)?"
    r"(?: (?:of|was|were|by|totall?ing|totall?ed|amounting to|amounted to|approximately|about))*"
    r" ?(?:(?:usd|eur|gbp|chf|jpy|cad|aud|sek|nok|dkk|czk|pln|hkd|sgd|cny|rmb) ?"
    r"|[a-z]{0,2}[$€£¥] ?)?$"
)
# A sign word stated right after the figure: "3.2% decrease", "a 4 percent decline".
_SIGN_AFTER = re.compile(
    r"^ ?(?:%|per ?cent|percent)? ?(?P<word>decrease|decline|drop|fall|lower)(?![a-z])"
)
_SIGN_WINDOW = 50


def _signed_metric(metric: str) -> tuple[bool, bool]:
    """(a loss makes it negative, a decline makes it negative) for the metric."""
    kind = derivation(metric).kind
    if kind.startswith("growth") or "growth" in metric or "change" in metric:
        return False, True
    loss = (
        metric in MARGINS
        or kind == "margin"
        or any(w in metric for w in ("income", "profit", "eps", "earnings", "ebit", "cash_flow"))
    )
    return loss, False


def _sign_word_near(snippet_n: str, core_n: str) -> bool:
    for m in _number_in(snippet_n, core_n):
        before = snippet_n[max(0, m.start() - _SIGN_WINDOW) : m.start()]
        after = snippet_n[m.end() : m.end() + 20]
        if _SIGN_WORD.search(before) or _SIGN_AFTER.search(after):
            return True
    return False


def _stated_negative(snippet_n: str, core_n: str, metric: str) -> str | None:
    """The sign word that states this figure as negative ("loss", "decreased"), for a
    metric whose sign it decides; None if the snippet does not."""
    loss_signed, decline_signed = _signed_metric(metric)
    if not (loss_signed or decline_signed):
        return None
    for m in _number_in(snippet_n, core_n):
        before = snippet_n[max(0, m.start() - _SIGN_WINDOW) : m.start()]
        after = snippet_n[m.end() : m.end() + 20]
        found = _SIGN_BEFORE.search(before) or (
            _SIGN_AFTER.search(after) if decline_signed else None
        )
        if found is None:
            continue
        word = found.group("word")
        is_loss = word.startswith("loss") or word.startswith("outflow")
        if (is_loss and loss_signed) or (not is_loss and decline_signed):
            return word
    return None


def _validate_one(fig: ExtractedFigure, ctx: ValidationContext) -> ValidatedFigure:
    key = FactKey(fig.metric, fig.basis, fig.fiscal_year, fig.fiscal_period)
    result = ValidatedFigure(fig, key, None, None, None)
    doc = ctx.document_text

    # snippet_containment
    if not contains(doc, fig.source_snippet):
        result.fail("snippet_containment", "the quoted snippet is not in the document")
        return result
    if fig.unit_snippet and not contains(doc, fig.unit_snippet):
        result.fail("snippet_containment", "the quoted unit line is not in the document")
        return result
    result.ok("snippet_containment")

    # value_in_snippet
    core = _core_number(fig.value_as_written)
    if not core or not _number_in(fig.source_snippet, core):
        result.fail("value_in_snippet", f"{fig.value_as_written!r} is not in the snippet")
        return result
    result.ok("value_in_snippet")

    # value_reading
    decimal_comma = uses_decimal_comma(fig.source_snippet)
    if decimal_comma is None and fig.unit_snippet:
        decimal_comma = uses_decimal_comma(fig.unit_snippet)
    printed = parse_written_number(core, decimal_comma=bool(decimal_comma))
    if printed is None:
        result.fail("value_reading", f"{fig.value_as_written!r} is not a number")
        return result
    snippet_n = normalize_for_match(fig.source_snippet)
    core_n = normalize_for_match(core)
    negative_printed = (
        fig.value_as_written.strip().startswith(("(", "-", "\u2212", "\u2013"))
        or f"({core_n})" in snippet_n
        or f"( {core_n} )" in snippet_n
        # A minus sign attached to the number. Checked on the raw snippet: em dashes and
        # spaced en dashes mark nil cells in tables ("\u2014 | 33,389") and must not read as
        # a minus, nor may a spaced hyphen ("Revenue - 5,000"); an en dash attached to the
        # number ("\u2013171") is a minus. A true minus sign (U+2212) may be spaced.
        or re.search(
            rf"(?<![\w.,])(?:[-\u2013]|\u2212\s?){_number_pattern(core)}", fig.source_snippet
        )
        is not None
    )
    model_value = Decimal(repr(fig.value))
    if not _close(abs(model_value), printed):
        result.fail(
            "value_reading",
            f"model value {fig.value} does not match the printed {fig.value_as_written!r}",
        )
        return result
    if model_value < 0 and not negative_printed and not _sign_word_near(snippet_n, core_n):
        result.fail("value_reading", "negative value, but the snippet prints it as positive")
        return result
    if model_value > 0 and not negative_printed:
        sign_word = _stated_negative(snippet_n, core_n, fig.metric)
        if sign_word is not None:
            result.fail(
                "value_reading",
                f"positive value, but the snippet states it as negative ({sign_word!r})",
            )
            return result
    signed = -printed if (negative_printed or model_value < 0) else printed
    result.value = signed
    result.value_units = signed * SCALE_FACTOR[fig.scale]
    result.ok("value_reading")

    # unit_scale
    evidence = f"{fig.source_snippet}\n{fig.unit_snippet or ''}"
    if fig.unit == "percent":
        if "%" not in evidence and not re.search(r"per ?cent", evidence, re.IGNORECASE):
            result.fail("unit_scale", "a percentage, but the snippet has no % sign")
            return result
        if fig.scale != "units":
            result.fail("unit_scale", "percentages are not scaled")
            return result
    elif fig.unit == "per_share" and fig.scale != "units":
        result.fail("unit_scale", "per-share figures are not scaled")
        return result
    elif (own := scale_next_to(fig.source_snippet, core)) is not None and own != fig.scale:
        # The snippet's own "94.0 billion" beats a table header's "(In millions)".
        result.fail("unit_scale", f"the snippet prints the number in {own}, not {fig.scale}")
        return result
    elif fig.scale != "units" and not scale_markers_present(evidence, fig.scale):
        others = conflicting_scales(evidence, fig.scale)
        detail = f"'{fig.scale}' is not stated next to the figure"
        if others:
            detail += f" (the text states {', '.join(others)})"
        result.fail("unit_scale", detail)
        return result
    result.ok("unit_scale", fig.scale)

    # currency
    if fig.unit in ("currency", "per_share"):
        if not fig.currency or not re.fullmatch(r"[A-Z]{3}", fig.currency):
            result.fail("currency", "money figure without an ISO currency code")
            return result
        if currency_markers_present(evidence, fig.currency):
            result.ok("currency", f"{fig.currency} stated next to the figure")
        elif currency_markers_present(doc, fig.currency):
            result.ok("currency", f"{fig.currency} stated elsewhere in the document")
        else:
            result.fail("currency", f"{fig.currency} does not appear in the document")
            return result
        if ctx.reporting_currency and fig.currency != ctx.reporting_currency:
            result.fail(
                "currency",
                f"{fig.currency}, but the profile's reporting currency is {ctx.reporting_currency}",
                "hold",
            )
    elif fig.currency:
        result.fail("currency", f"a {fig.unit} figure cannot carry a currency")
        return result

    # period_alignment
    year = ctx.report_date.year
    if not year - 3 <= fig.fiscal_year <= year + 2:
        result.fail("period_alignment", f"fiscal year {fig.fiscal_year} is implausible")
        return result
    expected = calendar_period_end(fig.fiscal_year, fig.fiscal_period, ctx.fye_month)
    label = period_label(fig.fiscal_year, fig.fiscal_period)
    stated = _parse_date(fig.period_end)
    if stated is not None:
        slack = abs((stated - expected).days)
        # 52/53-week years end on a weekday near month end; allow the configured slack
        # around the expected month end (or its start, for years ending early in a month).
        month_start = expected.replace(day=1)
        near_start = abs((stated - month_start).days)
        if min(slack, near_start) > ctx.config.period_end_tolerance_days:
            result.fail(
                "period_alignment",
                f"{label} should end around {expected.isoformat()} for a fiscal year ending "
                f"in month {ctx.fye_month}, but the document says {stated.isoformat()}; "
                "check fiscal_year_end_month in the holding profile",
            )
            return result
    result.period_end = stated or expected
    if result.period_end > ctx.report_date:
        result.fail("period_alignment", f"{label} ends after the report date")
        return result
    result.ok("period_alignment", f"{label} ends {result.period_end.isoformat()}")
    return result


def _close(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= Decimal("1e-9") * max(Decimal(1), abs(b))


def _parse_date(text: str | None) -> dt.date | None:
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Batch checks
# ---------------------------------------------------------------------------


def _check_consistency(results: list[ValidatedFigure]) -> list[ValidatedFigure]:
    """Drop exact repeats of one figure; reject a figure the document gives two values for."""
    by_key: dict[FactKey, list[ValidatedFigure]] = defaultdict(list)
    for r in results:
        by_key[r.key].append(r)
    kept: list[ValidatedFigure] = []
    for group in by_key.values():
        passing = [r for r in group if r.passed_hard_checks]
        values = {r.value_units for r in passing}
        if len(values) > 1:
            shown = ", ".join(sorted(str(v) for v in values))
            for r in passing:
                r.fail("consistency", f"the document gives different values: {shown}")
            kept.extend(passing)
        elif passing:
            first = passing[0]
            first.ok("consistency")
            kept.append(first)
        else:
            kept.extend(group)
    return kept


def _check_report_lag(results: list[ValidatedFigure], ctx: ValidationContext) -> None:
    """The document's main period must have ended shortly before the report date; a stale
    document (last year's release) is rejected as a whole."""
    passing = [r for r in results if r.passed_hard_checks and r.period_end is not None]
    if not passing:
        return
    main = max(passing, key=lambda r: sort_key(r.key.fiscal_year, r.key.fiscal_period))
    assert main.period_end is not None
    lag = (ctx.report_date - main.period_end).days
    if lag > ctx.config.max_report_lag_days:
        label = period_label(main.key.fiscal_year, main.key.fiscal_period)
        for r in passing:
            r.fail(
                "period_alignment",
                f"the document's latest period ({label}, ended {main.period_end}) is {lag} "
                f"days before the report date: not the release for this report",
            )


def _check_recomputation(results: list[ValidatedFigure], ctx: ValidationContext) -> None:
    raw = {r.key: r for r in results if r.passed_hard_checks and r.value_units is not None}
    for r in results:
        if not r.passed_hard_checks or r.figure.unit != "percent" or r.value is None:
            continue
        d = derivation(r.key.metric)
        if d.kind == "direct":
            continue
        k = r.key
        recomputed: Decimal | None = None
        if d.kind == "margin" and d.denominator:
            num = raw.get(FactKey(d.base, k.basis, k.fiscal_year, k.fiscal_period))
            den = raw.get(FactKey(d.denominator, k.basis, k.fiscal_year, k.fiscal_period))
            if num and den and num.value_units is not None and den.value_units:
                recomputed = num.value_units / den.value_units
        else:
            prior_fy, prior_fp = (
                (k.fiscal_year - 1, k.fiscal_period)
                if d.kind == "growth_yoy"
                else previous_quarter(k.fiscal_year, k.fiscal_period)
            )
            cur = raw.get(FactKey(d.base, k.basis, k.fiscal_year, k.fiscal_period))
            prev = raw.get(FactKey(d.base, k.basis, prior_fy, prior_fp))
            if cur and prev and cur.value_units is not None and prev.value_units:
                recomputed = cur.value_units / prev.value_units - 1
        if recomputed is None:
            r.ok("recomputation", "no raw figures in the document to recompute from")
            continue
        decimals = len(_core_number(r.figure.value_as_written).partition(".")[2])
        allowance = Decimal(ctx.config.stated_ratio_tolerance_pp) + Decimal(5) / Decimal(
            10 ** (decimals + 1)
        )
        diff = abs(r.value - recomputed * 100)
        if diff > allowance:
            r.fail(
                "recomputation",
                f"stated {r.value}% but the document's own figures give {(recomputed * 100):.2f}%",
            )
        else:
            r.ok("recomputation", f"recomputed {(recomputed * 100):.2f}%")


def _check_against_known(results: list[ValidatedFigure], ctx: ValidationContext) -> None:
    batch = {r.key: r for r in results if r.passed_hard_checks}
    for r in results:
        if not r.passed_hard_checks or r.value_units is None:
            continue
        k = r.key

        rejected = next(
            (
                x
                for x in ctx.owner_rejected.get(k, [])
                if _close(x.value_units, r.value_units) and x.unit == r.figure.unit
            ),
            None,
        )
        if rejected is not None:
            r.owner_rejected = rejected.id
            continue

        existing = ctx.known.get(k)
        if existing is not None:
            if _close(existing.value_units, r.value_units) and existing.unit == r.figure.unit:
                r.duplicate_of = existing.id
            else:
                r.restates = existing.id
                r.fail(
                    "restatement",
                    f"differs from stored fact #{existing.id} ({existing.value_units}); "
                    "both are kept, the new value awaits review",
                    "hold",
                )

        band = ctx.config.sanity_band(k.metric)
        if band is None or k.fiscal_period not in ("Q1", "Q2", "Q3", "Q4"):
            continue
        if r.figure.unit not in ("currency", "per_share", "count"):
            continue
        pfy, pfp = previous_quarter(k.fiscal_year, k.fiscal_period)
        prev_key = FactKey(k.metric, k.basis, pfy, pfp)
        prev_value: Decimal | None = None
        known_prev = ctx.known.get(prev_key)
        if known_prev is not None and known_prev.status == "verified":
            if known_prev.unit != r.figure.unit or known_prev.currency != r.figure.currency:
                r.fail(
                    "sanity_bound",
                    f"unit/currency changed from the previous quarter "
                    f"({known_prev.currency or known_prev.unit})",
                    "hold",
                )
                continue
            prev_value = known_prev.value_units
        elif prev_key in batch:
            prev_value = batch[prev_key].value_units
        if not prev_value:
            continue
        change = r.value_units / prev_value - 1
        if abs(change) > Decimal(str(band)):
            r.fail(
                "sanity_bound",
                f"{change:+.0%} against {period_label(pfy, pfp)} is beyond the ±{band:.0%} "
                "band; held for review",
                "hold",
            )
        else:
            r.ok("sanity_bound", f"{change:+.1%} against {period_label(pfy, pfp)}")
