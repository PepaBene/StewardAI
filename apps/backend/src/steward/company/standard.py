"""The standardized chart of accounts: one ordered list of lines per statement, so companies line
up. A line is a catalog line item (`steward.sources.xbrl_concepts`), so a mapped line always
names its source concepts. Anything the chart does not itemize stays visible as an "other" line
computed as the reported subtotal minus the lines shown, never dropped.
"""

# ruff: noqa: RUF001

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

Kind = Literal["line", "subtotal", "total", "other", "derived"]
UnitKind = Literal["money", "per_share", "shares"]
Get = Callable[[str], Decimal | None]


@dataclass(frozen=True)
class Row:
    key: str
    label: str
    kind: Kind = "line"
    unit: UnitKind = "money"
    derive: Callable[[Get], tuple[Decimal | None, str]] | None = None
    fallback: Callable[[Get], tuple[Decimal | None, str]] | None = (
        None  # when a reported total is absent
    )


def _first(get: Get, *keys: str) -> Decimal | None:
    for k in keys:
        v = get(k)
        if v is not None:
            return v
    return None


def residual(
    total: str, *parts: tuple[str, ...], sign: dict[str, int] | None = None
) -> Callable[[Get], tuple[Decimal | None, str]]:
    """total minus the parts that are known (each part: alternatives, the first known one counts).
    Unknown only when the total is unknown; an unreported part stays inside the residual."""
    signs = sign or {}

    def fn(get: Get) -> tuple[Decimal | None, str]:
        t = get(total)
        if t is None:
            return None, ""
        used: list[str] = []
        rest = t
        for alts in parts:
            for k in alts:
                v = get(k)
                if v is not None:
                    rest -= Decimal(signs.get(k, 1)) * v
                    used.append(k)
                    break
        shown = " − ".join([total, *used]) if used else total
        return rest, shown

    return fn


def _gross_profit(get: Get) -> tuple[Decimal | None, str]:
    rev, cost = get("revenue"), get("cost_of_revenue")
    if rev is None or cost is None:
        return None, ""
    return rev - cost, "revenue − cost of revenue"


def _free_cash_flow(get: Get) -> tuple[Decimal | None, str]:
    cfo, capex = get("cash_from_operations"), get("capex")
    if cfo is None or capex is None:
        return None, ""
    return cfo - abs(capex), "operating cash flow − capital expenditure"


def _liabilities(get: Get) -> tuple[Decimal | None, str]:
    both, equity = get("total_liabilities_and_equity"), get("equity")
    if both is None or equity is None:
        return None, ""
    nci = get("noncontrolling_interest") or Decimal(0)
    return both - equity - nci, "liabilities and equity − equity − non-controlling interest"


def _other_investing(get: Get) -> tuple[Decimal | None, str]:
    total = get("cash_from_investing")
    if total is None:
        return None, ""
    rest, used = total, ["cash from investing"]
    for key, sign, label in (
        ("capex", 1, "capital expenditure"),
        ("acquisitions_paid", 1, "acquisitions"),
        ("divestiture_proceeds", -1, "divestiture proceeds"),
    ):
        v = get(key)
        if v is not None:
            rest += Decimal(sign) * abs(v)
            used.append(label)
    if len(used) == 1:
        return total, used[0]
    if "capital expenditure" in used and len(used) == 2:
        return rest, "cash from investing + capital expenditure"
    return rest, used[0] + "".join(
        f" {'−' if label == 'divestiture proceeds' else '+'} {label}" for label in used[1:]
    )


def _other_financing(get: Get) -> tuple[Decimal | None, str]:
    total = get("cash_from_financing")
    if total is None:
        return None, ""
    rest, used = total, ["cash from financing"]
    for key, sign, label in (
        ("debt_issued", 1, "debt issued"),
        ("debt_repaid", -1, "debt repaid"),
        ("share_repurchases_paid", -1, "buybacks"),
        ("dividends_paid", -1, "dividends"),
    ):
        v = get(key)
        if v is not None:
            rest -= Decimal(sign) * abs(v)
            used.append(label)
    return rest, " − ".join(used) if len(used) > 1 else used[0]


INCOME: tuple[Row, ...] = (
    Row("revenue", "Revenue"),
    Row("cost_of_revenue", "Cost of revenue"),
    Row("gross_profit", "Gross profit", "subtotal", fallback=_gross_profit),
    Row("research_and_development", "Research and development"),
    Row("selling_general_administrative", "Selling, general and administrative"),
    Row(
        "other_operating",
        "Other operating items (not itemized)",
        "other",
        derive=residual(
            "gross_profit_resolved",
            ("research_and_development",),
            ("selling_general_administrative",),
        ),
    ),
    Row("operating_income", "Operating income", "subtotal"),
    Row("interest_expense", "Interest expense"),
    Row("interest_income", "Interest income"),
    Row("pretax_income", "Income before tax", "subtotal"),
    Row("income_tax", "Income tax"),
    Row("net_income", "Net income", "total"),
    Row("eps_basic", "EPS, basic", unit="per_share"),
    Row("eps_diluted", "EPS, diluted", unit="per_share"),
    Row("weighted_shares_basic", "Weighted shares, basic", unit="shares"),
    Row("weighted_shares_diluted", "Weighted shares, diluted", unit="shares"),
    Row("dividends_per_share", "Dividends per share", unit="per_share"),
)

BALANCE: tuple[Row, ...] = (
    Row("cash", "Cash and equivalents"),
    Row("receivables", "Receivables"),
    Row("inventory", "Inventory"),
    Row(
        "other_current_assets",
        "Other current assets (not itemized)",
        "other",
        derive=residual("current_assets", ("cash",), ("receivables",), ("inventory",)),
    ),
    Row("current_assets", "Current assets", "subtotal"),
    Row("property_plant_equipment", "Property, plant and equipment"),
    Row("goodwill", "Goodwill"),
    Row("intangibles", "Intangible assets"),
    Row("right_of_use_asset", "Right-of-use assets"),
    Row(
        "other_noncurrent_assets",
        "Other non-current assets (not itemized)",
        "other",
        derive=residual(
            "total_assets",
            ("current_assets",),
            ("property_plant_equipment",),
            ("goodwill",),
            ("intangibles",),
            ("right_of_use_asset",),
        ),
    ),
    Row("total_assets", "Total assets", "total"),
    Row("payables", "Payables"),
    Row("short_term_borrowings", "Short-term borrowings"),
    Row("commercial_paper", "Commercial paper"),
    Row("long_term_debt_current", "Current portion of long-term debt"),
    Row("borrowings_current", "Borrowings, current"),
    Row("lease_liability_current", "Lease liabilities, current"),
    Row(
        "other_current_liabilities",
        "Other current liabilities (not itemized)",
        "other",
        derive=residual(
            "current_liabilities",
            ("payables",),
            ("short_term_borrowings",),
            ("commercial_paper",),
            ("long_term_debt_current", "borrowings_current"),
            ("lease_liability_current",),
        ),
    ),
    Row("current_liabilities", "Current liabilities", "subtotal"),
    Row("long_term_debt_total", "Long-term debt, total (as filed)"),
    Row("borrowings_total", "Borrowings, total (as filed)"),
    Row("long_term_debt_noncurrent", "Long-term debt, non-current"),
    Row("borrowings_noncurrent", "Borrowings, non-current"),
    Row("lease_liability_noncurrent", "Lease liabilities, non-current"),
    Row(
        "other_noncurrent_liabilities",
        "Other non-current liabilities (not itemized)",
        "other",
        derive=residual(
            "total_liabilities_resolved",
            ("current_liabilities",),
            ("long_term_debt_noncurrent", "borrowings_noncurrent"),
            ("lease_liability_noncurrent",),
        ),
    ),
    Row("total_liabilities", "Total liabilities", "total", fallback=_liabilities),
    Row("equity", "Shareholders' equity"),
    Row("retained_earnings", "Retained earnings (accumulated deficit)"),
    Row("noncontrolling_interest", "Non-controlling interest"),
    Row("total_liabilities_and_equity", "Total liabilities and equity", "total"),
)

CASH_FLOW: tuple[Row, ...] = (
    Row("depreciation_amortization", "Depreciation and amortization"),
    Row("share_based_compensation", "Share-based compensation"),
    Row("cash_from_operations", "Cash from operations", "total"),
    Row("capex", "Capital expenditure"),
    Row("acquisitions_paid", "Acquisitions of businesses"),
    Row("divestiture_proceeds", "Proceeds from divestitures"),
    Row("other_investing", "Other investing (not itemized)", "other", derive=_other_investing),
    Row("cash_from_investing", "Cash from investing", "total"),
    Row("debt_issued", "Debt issued"),
    Row("debt_repaid", "Debt repaid"),
    Row("share_repurchases_paid", "Share repurchases"),
    Row("dividends_paid", "Dividends paid"),
    Row("other_financing", "Other financing (not itemized)", "other", derive=_other_financing),
    Row("cash_from_financing", "Cash from financing", "total"),
    Row("free_cash_flow", "Free cash flow", "derived", derive=_free_cash_flow),
)

CHART: dict[str, tuple[Row, ...]] = {
    "income": INCOME,
    "balance_sheet": BALANCE,
    "cash_flow": CASH_FLOW,
}

STATEMENT_LABEL = {
    "income": "Income statement",
    "balance_sheet": "Balance sheet",
    "cash_flow": "Cash-flow statement",
}

# Balance-sheet lines are instants; the others are durations. Used to pick a series.
INSTANT_STATEMENTS = frozenset({"balance_sheet"})

# Totals the identity check compares: (total, the lines it should equal).
IDENTITIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "balance_sheet": ("total_assets", ("total_liabilities_and_equity",)),
}
