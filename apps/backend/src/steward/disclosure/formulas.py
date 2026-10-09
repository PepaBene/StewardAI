"""P2.7: a versioned formula table for margins, ROIC, debt maturity coverage and cash conversion.

Pure functions. Each result names its inputs with a citation (accession and fact id), a coverage
count (inputs present of inputs required) and the formula version. When any required input is
missing the value is None and `reason` says which; a ratio is never computed from a guess, and a
zero or negative denominator that makes the ratio meaningless gives None with its reason.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

FORMULA_TABLE_VERSION = "formula-table-1"
_PLACES = Decimal("0.000001")


@dataclass(frozen=True)
class Formula:
    key: str
    version: str
    expression: str
    inputs: tuple[str, ...]
    rule: str


FORMULAS: tuple[Formula, ...] = (
    Formula(
        "gross_margin", "1", "gross_profit / revenue", ("gross_profit", "revenue"), "revenue > 0"
    ),
    Formula(
        "operating_margin",
        "1",
        "operating_income / revenue",
        ("operating_income", "revenue"),
        "revenue > 0",
    ),
    Formula("net_margin", "1", "net_income / revenue", ("net_income", "revenue"), "revenue > 0"),
    Formula(
        "roic",
        "1",
        "operating_income * (1 - tax_rate) / (total_equity + total_debt - cash)",
        ("operating_income", "pretax_income", "income_tax", "total_equity", "total_debt", "cash"),
        "tax_rate = income_tax / pretax_income, used only when pretax_income > 0 and the rate is "
        "between 0 and 0.5, otherwise null (a stated rate may be supplied and is recorded); "
        "invested capital uses period-end balances and must be positive; lease liabilities are "
        "not added because total_debt may already include them",
    ),
    Formula(
        "debt_maturity_coverage",
        "1",
        "cash / debt_due_year_1",
        ("cash", "debt_due_year_1"),
        "debt due in year 1 comes from the note's maturity schedule and must be positive",
    ),
    Formula(
        "cash_conversion",
        "1",
        "operating_cash_flow / net_income",
        ("operating_cash_flow", "net_income"),
        "net_income > 0; a loss makes the ratio meaningless, so it is null",
    ),
)
_BY_KEY = {f.key: f for f in FORMULAS}


@dataclass(frozen=True)
class FormulaInput:
    metric: str
    value: Decimal | None
    accession: str | None = None
    fact_id: int | None = None
    source: str = "statement"  # "statement" or "note"


@dataclass(frozen=True)
class FormulaResult:
    formula: str
    version: str
    table_version: str
    expression: str
    rule: str
    value: Decimal | None
    inputs: tuple[FormulaInput, ...]
    coverage_present: int
    coverage_required: int
    reason: str | None


def _q(value: Decimal) -> Decimal:
    return value.quantize(_PLACES)


def _val(inputs: Mapping[str, FormulaInput], key: str) -> Decimal | None:
    item = inputs.get(key)
    return None if item is None else item.value


def _result(
    f: Formula,
    inputs: Mapping[str, FormulaInput],
    required: tuple[str, ...],
    value: Decimal | None,
    reason: str | None,
    rule_suffix: str = "",
) -> FormulaResult:
    cited = tuple(inputs.get(k) or FormulaInput(k, None) for k in required)
    present = sum(1 for i in cited if i.value is not None)
    return FormulaResult(
        f.key,
        f.version,
        FORMULA_TABLE_VERSION,
        f.expression,
        f.rule + rule_suffix,
        None if value is None else _q(value),
        cited,
        present,
        len(required),
        reason,
    )


def _missing(inputs: Mapping[str, FormulaInput], required: tuple[str, ...]) -> str | None:
    gone = [k for k in required if _val(inputs, k) is None]
    return f"missing input: {', '.join(gone)}" if gone else None


def _ratio(
    f: Formula,
    inputs: Mapping[str, FormulaInput],
    num: str,
    den: str,
    den_rule: str,
    positive: bool,
) -> FormulaResult:
    if reason := _missing(inputs, f.inputs):
        return _result(f, inputs, f.inputs, None, reason)
    n, d = _val(inputs, num), _val(inputs, den)
    assert n is not None and d is not None
    if (d <= 0) if positive else (d == 0):
        return _result(f, inputs, f.inputs, None, den_rule)
    return _result(f, inputs, f.inputs, n / d, None)


def _roic(f: Formula, inputs: Mapping[str, FormulaInput], stated: Decimal | None) -> FormulaResult:
    required = (
        ("operating_income", "total_equity", "total_debt", "cash")
        if stated is not None
        else f.inputs
    )
    suffix = f" [stated tax rate {stated} supplied]" if stated is not None else ""
    if reason := _missing(inputs, required):
        return _result(f, inputs, required, None, reason, suffix)
    op = _val(inputs, "operating_income")
    equity, debt, cash = (_val(inputs, k) for k in ("total_equity", "total_debt", "cash"))
    assert op is not None and equity is not None and debt is not None and cash is not None
    rate = stated
    if rate is None:
        pretax, tax = _val(inputs, "pretax_income"), _val(inputs, "income_tax")
        assert pretax is not None and tax is not None
        if pretax <= 0:
            return _result(f, inputs, required, None, "pretax income is not positive: no tax rate")
        rate = tax / pretax
        if not Decimal(0) <= rate <= Decimal("0.5"):
            return _result(
                f, inputs, required, None, f"effective tax rate {_q(rate)} is outside 0 to 0.5"
            )
    capital = equity + debt - cash
    if capital <= 0:
        return _result(f, inputs, required, None, "invested capital is not positive", suffix)
    return _result(f, inputs, required, op * (1 - rate) / capital, None, suffix)


def compute_formulas(
    inputs: Mapping[str, FormulaInput], *, stated_tax_rate: Decimal | None = None
) -> list[FormulaResult]:
    """Every formula in the table for one period. `inputs` maps a metric key to its cited value;
    an absent key is a missing input."""
    out: list[FormulaResult] = []
    for f in FORMULAS:
        if f.key in ("gross_margin", "operating_margin", "net_margin"):
            out.append(_ratio(f, inputs, f.inputs[0], "revenue", "revenue is not positive", True))
        elif f.key == "roic":
            out.append(_roic(f, inputs, stated_tax_rate))
        elif f.key == "debt_maturity_coverage":
            out.append(
                _ratio(f, inputs, "cash", "debt_due_year_1", "nothing is due in year 1", True)
            )
        else:
            out.append(
                _ratio(
                    f,
                    inputs,
                    "operating_cash_flow",
                    "net_income",
                    "net income is not positive",
                    True,
                )
            )
    return out


def formula_by_key(key: str) -> Formula | None:
    return _BY_KEY.get(key)
