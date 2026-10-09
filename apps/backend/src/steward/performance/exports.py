"""CSV tables behind the performance pages (plan M4.4): ledger, open lots, realized gains and
attribution. Built from the same report models the pages read, so a file never disagrees with
the screen."""

from __future__ import annotations

from collections.abc import Iterable

from steward.export import to_csv
from steward.performance.ledger import LedgerEntry
from steward.performance.reports import AttributionResponse, LotsResponse


def ledger_csv(entries: Iterable[LedgerEntry]) -> str:
    return to_csv(
        ["id", "occurred_at", "kind", "ticker", "quantity", "price", "gross", "fees",
         "cash_effect", "currency", "needs_fx", "flags"],
        (
            [e.id, e.occurred_at.isoformat(), str(e.kind), e.ticker, e.quantity, e.price,
             e.gross, e.fees, e.cash_effect, e.currency, e.needs_fx, ";".join(e.flags)]
            for e in entries
        ),
    )  # fmt: skip


def lots_csv(report: LotsResponse) -> str:
    return to_csv(
        ["lot_id", "ticker", "name", "bought_on", "quantity", "unit_cost", "cost", "passes_on",
         "months_remaining", "passed", "sell_target", "current_price", "target_reached"],
        (
            [x.lot_id, x.ticker, x.name, x.bought_on, x.quantity, x.unit_cost, x.cost,
             x.passes_on, x.months_remaining, x.passed, x.sell_target, x.current_price,
             x.target_reached]
            for x in report.lots
        ),
    )  # fmt: skip


def realized_csv(report: LotsResponse) -> str:
    return to_csv(
        ["sale_id", "ticker", "sold_on", "quantity", "proceeds", "cost", "gain", "holding_days",
         "gross_within_time_test"],
        (
            [x.sale_id, x.ticker, x.sold_on, x.quantity, x.proceeds, x.cost, x.gain,
             x.holding_days, x.gross_within_time_test]
            for x in report.realized
        ),
    )  # fmt: skip


def attribution_csv(report: AttributionResponse) -> str:
    return to_csv(
        ["ticker", "name", "price", "fx", "income", "fees", "total", "pct_of_start", "attributed"],
        (
            [x.ticker, x.name, x.price, x.fx, x.income, x.fees, x.total, x.pct_of_start,
             x.attributed]
            for x in report.holdings
        ),
    )  # fmt: skip
