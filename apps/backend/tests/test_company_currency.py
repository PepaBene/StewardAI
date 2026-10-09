"""Plan M2.4 / M2.14: the base-currency view. Figures are worked out by hand in the comments."""

import datetime as dt
from decimal import Decimal

from steward.company.currency import Translator, translate
from steward.company.statements import Cell, Column, Line, StatementTable
from steward.fx.history import FxHistory

D = Decimal
END = dt.date(2025, 12, 31)


def history() -> FxHistory:
    # EUR per USD: 0.90 every week of 2025, then 0.80 on the last day of the year.
    rows = [("USD", dt.date(2024, 12, 24) + dt.timedelta(days=7 * i), D("0.90")) for i in range(54)]
    rows.append(("USD", END, D("0.80")))
    return FxHistory("ecb", rows)


def tables() -> tuple[list[StatementTable], list[Column]]:
    def line(key: str, value: str, unit: str = "money") -> Line:
        return Line(
            key=key,
            label=key,
            kind="item",
            unit=unit,
            cells={"FY25": Cell(value=D(value), unit=unit)},
        )

    cols = [Column(id="FY25", kind="annual", end=END, label="FY2025")]
    income = StatementTable(
        statement="income",
        label="Income",
        lines=[
            line("revenue", "100"),
            line("eps", "2", "per_share"),
            line("shares", "50", "shares"),
        ],
    )
    balance = StatementTable(
        statement="balance_sheet", label="Balance", lines=[line("cash", "100")]
    )
    return [income, balance], cols


def figures(t: list[StatementTable]) -> dict[str, Decimal | None]:
    return {ln.key: ln.cells["FY25"].value for tab in t for ln in tab.lines}


def test_period_end_rate_converts_money_and_per_share_but_not_share_counts() -> None:
    t, cols = tables()
    notes = translate(t, cols, "USD", Translator("EUR", "period_end", "ecb", history()))
    # Every money and per-share cell at 0.80: 100 -> 80, 2 -> 1.6; the share count is untouched.
    assert figures(t) == {
        "revenue": D("80.00"),
        "eps": D("1.60"),
        "shares": D(50),
        "cash": D("80.00"),
    }
    assert "period" in notes[0] and "USD to EUR" in notes[0]


def test_average_rate_applies_to_flows_only() -> None:
    t, cols = tables()
    translate(t, cols, "USD", Translator("EUR", "average", "ecb", history()))
    # 53 weekly samples at 0.90 plus the closing 0.80 over 54 samples = 48.5 / 54 = 0.898148...
    avg = D("48.5") / D(54)
    f = figures(t)
    assert abs(f["revenue"] - D(100) * avg) < D("0.0001")  # type: ignore[operator]
    assert f["cash"] == D("80.00")  # balance sheet stays at the closing rate


def test_missing_rate_empties_the_cell_and_says_why() -> None:
    t, cols = tables()
    notes = translate(t, cols, "USD", Translator("EUR", "period_end", "ecb", FxHistory("ecb", [])))
    cell = t[0].lines[0].cells["FY25"]
    assert cell.value is None and "no ECB USD/EUR rate" in (cell.reason or "")
    assert any("No reference rate" in n for n in notes)
