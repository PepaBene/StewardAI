"""Low-severity deep-audit fixes: D23 net-debt limitation, D25 growth before FX, D26 announced
dividends by fiscal year, D27 thesis draft ordering and numbers, D29 version-number races."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest

from steward.company import service
from steward.company import valuation as val
from steward.company.evidence import announced_by_fiscal_year
from steward.company.statements import Column, StatementTable, build_view
from steward.thesis import draft

from .test_company_statements import rows

D = Decimal


# --------------------------------------------------------------------------------- D23


def test_net_debt_without_a_debt_concept_carries_its_date_and_says_it_is_an_assumption() -> None:
    filed = {"cash": D("50"), "total_assets": D("500")}
    value, note = val._net_debt(filed, dt.date(2024, 12, 31))
    assert value == D("-50")
    assert "taken as zero" in note and "2024-12-31" in note
    assert "unmapped" in val.NO_DEBT_LIMITATION


# --------------------------------------------------------------------------------- D25


def test_yoy_growth_is_computed_before_currency_translation() -> None:
    def fx(tables: list[StatementTable], columns: list[Column], native: str) -> list[str]:
        for table in tables:
            for ln in table.lines:
                for col in columns:
                    cell = ln.cells[col.id]
                    if cell.unit == "money" and cell.value is not None:
                        # the later year is worth twice as much in the target currency
                        cell.value = cell.value * (2 if col.end.year == 2024 else 1)
        return []

    v = build_view("AAA", rows(), units=1, transform="yoy", convert=("EUR", fx))
    table = next(t for t in v.statements if t.statement == "income")
    revenue = next(ln for ln in table.lines if ln.key == "revenue")
    assert revenue.cells[v.columns[1].id].value == D("0.2")  # 1200 / 1000 - 1, no FX move in it


# --------------------------------------------------------------------------------- D26

ENDS = [dt.date(2023, 6, 30), dt.date(2024, 6, 30)]


def div(ex: dt.date, amount: str, listing: int = 1, currency: str | None = "USD") -> dict[str, Any]:
    return {"ex_date": ex, "cash_amount": D(amount), "currency": currency, "listing_id": listing}


def test_announced_dividends_follow_the_fiscal_year_not_the_calendar_year() -> None:
    got = announced_by_fiscal_year(
        [div(dt.date(2023, 9, 1), "0.25"), div(dt.date(2024, 3, 1), "0.25")], ENDS, "USD"
    )
    # Both fall in the year ending June 2024; a calendar-year key would split them 2023/2024.
    assert got == {dt.date(2024, 6, 30): D("0.50")}


def test_announced_dividends_ignore_other_currencies_and_second_listings() -> None:
    got = announced_by_fiscal_year(
        [
            div(dt.date(2024, 3, 1), "0.25", listing=1),
            div(dt.date(2024, 3, 1), "0.23", listing=2),  # the same dividend on a dual listing
            div(dt.date(2024, 3, 1), "0.22", listing=1, currency="EUR"),
        ],
        ENDS,
        "USD",
    )
    assert got == {dt.date(2024, 6, 30): D("0.25")}


# --------------------------------------------------------------------------------- D27


def fake_report(n_research: int, n_conflicts: int) -> Any:
    sentences = [SimpleNamespace(text=f"Sentence {i}") for i in range(n_research)]
    return SimpleNamespace(
        research=SimpleNamespace(
            body=SimpleNamespace(sections=[SimpleNamespace(title="T", sentences=sentences)])
        ),
        key_figures=[],
        segments=[],
        conflicts=[SimpleNamespace(area="a", text=f"c{i}") for i in range(n_conflicts)],
    )


def test_truncation_drops_research_before_conflict_warnings() -> None:
    facts = draft.facts_for(fake_report(100, 3))
    assert len(facts) == draft.MAX_FACTS
    assert [f for f in facts if f.startswith("Conflict")] == [
        "Conflict (a): c0",
        "Conflict (a): c1",
        "Conflict (a): c2",
    ]


def test_a_line_with_a_number_that_is_in_no_fact_is_dropped() -> None:
    facts = ["Figure, Operating margin FY2024: 0.2134 fraction", "Research, Moat: Switching costs."]
    model = draft.ModelDraft(
        why_owned=draft.DraftLine(text="Margin is 21.3% and costs are sticky.", facts=[1, 2]),
        exit_conditions=[
            draft.DraftLine(text="Margin falls below 15%.", facts=[1]),  # 15 is in no fact
            draft.DraftLine(text="Margin falls below 21.3%.", facts=[1]),
        ],
    )
    out = draft.resolve(model, facts, "m", "AAA")
    assert out.why_owned is not None  # 21.3% is the fact's 0.2134 as a percentage
    assert [line.text for line in out.exit_conditions] == ["Margin falls below 21.3%."]
    assert out.dropped == 1


# --------------------------------------------------------------------------------- D29


class FakeCon:
    def __init__(self, log: list[str], *, fail: bool = False) -> None:
        self.log, self.fail = log, fail

    async def execute(self, sql: str, *args: Any) -> None:
        self.log.append(sql)

    async def fetchval(self, sql: str, *args: Any) -> int:
        self.log.append(sql)
        return 1

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.log.append(sql)
        if self.fail:
            raise asyncpg.UniqueViolationError("duplicate key")
        return None

    def transaction(self) -> Any:
        class Tx:
            async def __aenter__(self) -> None: ...
            async def __aexit__(self, *exc: Any) -> None: ...

        return Tx()


class FakePool:
    def __init__(self, con: FakeCon) -> None:
        self.con = con

    def acquire(self) -> Any:
        con = self.con

        class Acq:
            async def __aenter__(self) -> FakeCon:
                return con

            async def __aexit__(self, *exc: Any) -> None: ...

        return Acq()


@pytest.mark.asyncio
async def test_scenario_save_takes_an_advisory_lock_first_and_maps_a_race_to_a_conflict() -> None:
    log: list[str] = []
    body = service.SaveScenarioIn(name="base", request=val.ValuationRequest(), note=None)
    with pytest.raises(service.VersionConflict):
        await service.save_scenario(FakePool(FakeCon(log, fail=True)), "o", "AAA", body, None)  # type: ignore[arg-type]
    assert "pg_advisory_xact_lock" in log[0]
