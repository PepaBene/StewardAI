"""Fundamentals stream (audit A9-A14, B27-B32, D4.8-D4.10, F17): offline unit tests."""

import datetime as dt
import time
from decimal import Decimal

import pytest

from steward.db_json import decode_jsonb, decode_structured
from steward.forward_consensus import consensus_series, observation_key
from steward.fundamentals import ir
from steward.fundamentals.read import latest_filing_rows
from steward.fundamentals.statements import (
    FilingMeta,
    apply_reconciliation,
    esef_statement_facts,
    grid_fiscal_period,
    internal_checks,
    sec_fiscal_period,
    sec_known_at,
    sec_statement_facts,
)
from steward.market_analysis import compatible_peer_mean, financial_peer_metrics
from steward.peer_metrics import latest_period, peer_comparison
from steward.sources.edgar import Filing, amended_filing, amendments_to_check, latest_periodic
from steward.sources.esef import parse_xbrl_json
from steward.sources.xbrl import (
    RevenueRow,
    XbrlError,
    instance_shares,
    mark_subtotals,
    parse_instance,
    parse_labels,
)
from steward.sources.xbrl import Labels as _Labels
from steward.sources.xbrl_concepts import classify, currency_of
from steward.valuation.fundamentals import Fact, _by_concept, compute
from steward.valuation.read import price_to_sales
from tests.fund_stream_helpers import Ctx, Fact as XFact, instance_xml, pdf_bytes, xlsx_bytes

D = Decimal
Y25 = dt.date(2025, 1, 1), dt.date(2025, 12, 31)


def row(
    concept: str,
    value: str | int,
    *,
    ticker: str = "AAA",
    start: dt.date | None = Y25[0],
    end: dt.date = Y25[1],
    unit: str | None = "USD",
    basis: str = "US_GAAP",
    dims: object = None,
    validation: object = None,
    known: int = 0,
) -> dict[str, object]:
    return {
        "ticker": ticker,
        "concept": concept,
        "value": D(value),
        "period_start": start,
        "period_end": end,
        "unit": unit,
        "basis": basis,
        "dimensions": dims if dims is not None else {"members": []},
        "validation": validation if validation is not None else {},
        "known_at": dt.datetime(2026, 2, 1, tzinfo=dt.UTC) + dt.timedelta(days=known),
    }


# ----------------------------------------------------------------------------- A9: jsonb


def test_jsonb_text_is_decoded_and_plain_strings_survive() -> None:
    assert decode_jsonb('{"members": []}') == {"members": []}
    assert decode_jsonb({"members": []}) == {"members": []}
    assert decode_jsonb("not json", {}) == {}
    # decode_structured never destroys a plain string, and is safe to apply twice.
    assert decode_structured("plain text") == "plain text"
    assert decode_structured(decode_structured('{"a": 1}')) == {"a": 1}


def test_peer_metrics_exclude_segment_facts_when_dimensions_arrive_as_text() -> None:
    """A9: asyncpg delivers jsonb as text; the `isinstance(dims, dict)` guard never fired and a
    product line replaced consolidated revenue (7.0e9 vs 1.9e11, growth -87%)."""
    rows = [
        row("us-gaap:Revenues", 190_000),
        row(
            "us-gaap:Revenues", 7_000, dims='{"members": [["srt:ProductOrServiceAxis", "x:Cloud"]]}'
        ),
        row("us-gaap:GrossProfit", 95_000),
    ]
    period = financial_peer_metrics(rows)["AAA"]["periods"][0]  # type: ignore[index]
    assert period["revenue"] == D(190_000)
    assert period["gross_margin"] == D("0.5")


# ------------------------------------------------------------------------------ A10 / A11


def test_three_and_six_month_facts_with_one_end_date_do_not_collapse() -> None:
    q = dt.date(2025, 4, 1), dt.date(2025, 6, 30)
    h = dt.date(2025, 1, 1), dt.date(2025, 6, 30)
    rows = [
        row("us-gaap:Revenues", 100, start=q[0], end=q[1]),
        row("us-gaap:GrossProfit", 40, start=q[0], end=q[1]),
        row("us-gaap:Revenues", 300, start=h[0], end=h[1]),
        row("us-gaap:GrossProfit", 90, start=h[0], end=h[1]),
    ]
    periods = financial_peer_metrics(rows)["AAA"]["periods"]  # type: ignore[index]
    by_start = {p["period_start"]: p for p in periods}
    assert by_start[q[0]]["gross_margin"] == D("0.4")
    assert by_start[h[0]]["gross_margin"] == D("0.3")


def test_units_are_never_mixed_across_a_ratio() -> None:
    rows = [
        row("us-gaap:Revenues", 100, unit="USD"),
        row("us-gaap:GrossProfit", 40, unit="EUR"),
    ]
    periods = financial_peer_metrics(rows)["AAA"]["periods"]  # type: ignore[index]
    assert all(p["gross_margin"] is None for p in periods)


def test_dei_cover_facts_do_not_become_the_latest_period() -> None:
    rows = [
        row("us-gaap:Revenues", 100),
        row("us-gaap:GrossProfit", 40),
        row(
            "dei:EntityCommonStockSharesOutstanding",
            10,
            start=None,
            end=dt.date(2026, 2, 10),
            unit="shares",
            basis="cover_page",
        ),
        row("dei:EntityPublicFloat", 10, start=None, end=dt.date(2026, 2, 10), unit="USD"),
    ]
    periods = financial_peer_metrics(rows)["AAA"]["periods"]  # type: ignore[index]
    assert latest_period(periods)["period_end"] == Y25[1]  # type: ignore[index]


def test_revenue_concept_priority_and_restatement_wins() -> None:
    rows = [
        row("us-gaap:Revenues", 90),
        row("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", 100),
        row("us-gaap:GrossProfit", 50),
        row("us-gaap:GrossProfit", 40, known=30),  # a later filing restates gross profit
    ]
    period = financial_peer_metrics(rows)["AAA"]["periods"][0]  # type: ignore[index]
    assert period["revenue"] == D(100)
    assert period["gross_margin"] == D("0.4")


def test_debt_is_the_sum_of_components_not_the_last_alphabetical_concept() -> None:
    end = Y25[1]
    rows = [
        row("us-gaap:LongTermDebtNoncurrent", 800, start=None, end=end),
        row("us-gaap:LongTermDebtCurrent", 200, start=None, end=end),
        row("us-gaap:ShortTermBorrowings", 50, start=None, end=end),
        row("us-gaap:StockholdersEquity", 1000, start=None, end=end),
        row("us-gaap:CashAndCashEquivalentsAtCarryingValue", 150, start=None, end=end),
        row("us-gaap:Revenues", 10),
    ]
    period = financial_peer_metrics(rows)["AAA"]["periods"][0]  # type: ignore[index]
    # (800 + 200 + 50 - 150) / 1000
    assert period["net_debt_to_equity"] == D("0.9")
    # The total element wins over its own parts (it already includes the current portion).
    rows.append(row("us-gaap:LongTermDebt", 1000, start=None, end=end))
    period = financial_peer_metrics(rows)["AAA"]["periods"][0]  # type: ignore[index]
    assert period["net_debt_to_equity"] == D("0.9")


def test_non_positive_denominators_are_not_meaningful_with_a_reason() -> None:
    end = Y25[1]
    rows = [
        row("us-gaap:Revenues", 100),
        row("us-gaap:OperatingIncomeLoss", 10),
        row("us-gaap:IncomeTaxExpenseBenefit", 2),
        row(
            "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            -5,
        ),
        row("us-gaap:LongTermDebt", 100, start=None, end=end),
        row("us-gaap:StockholdersEquity", -50, start=None, end=end),
        row("us-gaap:CashAndCashEquivalentsAtCarryingValue", 10, start=None, end=end),
        row("us-gaap:NetCashProvidedByUsedInOperatingActivities", 30),
        row("us-gaap:PaymentsToAcquirePropertyPlantAndEquipment", 5),
        row("us-gaap:NetIncomeLoss", -20),
    ]
    period = financial_peer_metrics(rows)["AAA"]["periods"][0]  # type: ignore[index]
    reasons = period["not_meaningful"]
    assert period["net_debt_to_equity"] is None and "equity" in reasons["net_debt_to_equity"]
    assert period["free_cash_flow_conversion"] is None
    assert "net income" in reasons["free_cash_flow_conversion"]
    assert period["roic"] is None and "pretax" in reasons["roic"]


def test_roic_needs_a_full_year() -> None:
    end = dt.date(2025, 6, 30)
    start = dt.date(2025, 4, 1)
    rows = [
        row("us-gaap:OperatingIncomeLoss", 10, start=start, end=end),
        row("us-gaap:IncomeTaxExpenseBenefit", 2, start=start, end=end),
        row(
            "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            10,
            start=start,
            end=end,
        ),
        row("us-gaap:LongTermDebt", 100, start=None, end=end),
        row("us-gaap:StockholdersEquity", 100, start=None, end=end),
        row("us-gaap:CashAndCashEquivalentsAtCarryingValue", 10, start=None, end=end),
    ]
    period = financial_peer_metrics(rows)["AAA"]["periods"][0]  # type: ignore[index]
    assert period["roic"] is None and "full-year" in period["not_meaningful"]["roic"]


def test_ifrs_peers_get_metrics_and_a_cross_basis_peer_is_excluded_with_a_reason() -> None:
    rows = [
        row("us-gaap:Revenues", 100, ticker="AAA"),
        row("us-gaap:GrossProfit", 50, ticker="AAA"),
        row("ifrs-full:Revenue", 200, ticker="BBB", unit="EUR", basis="IFRS"),
        row("ifrs-full:GrossProfit", 60, ticker="BBB", unit="EUR", basis="IFRS"),
        row("us-gaap:Revenues", 100, ticker="CCC"),
        row("us-gaap:GrossProfit", 30, ticker="CCC"),
        row("ifrs-full:Revenue", 100, ticker="DDD", basis="IFRS"),
        row("ifrs-full:ProfitLossFromOperatingActivities", 25, ticker="DDD", basis="IFRS"),
    ]
    metrics = financial_peer_metrics(rows)
    ddd = metrics["DDD"]["periods"][0]  # type: ignore[index]
    assert ddd["operating_margin"] == D("0.25")  # an IFRS operating profit alias exists now
    out = peer_comparison("AAA", ["BBB", "CCC", "ZZZ"], metrics)
    gross = out["metrics"]["gross_margin"]
    assert gross["included"] == ["CCC"] and gross["mean"] == D("0.3")
    assert "differs" in gross["excluded_reasons"]["BBB"]
    assert gross["excluded_reasons"]["ZZZ"] == "no filed statement facts"
    assert {e["peer"] for e in out["excluded_peers"]} == {"BBB", "ZZZ"}


def test_compatible_peer_mean_reports_reasons() -> None:
    result = compatible_peer_mean(
        [("A", D(4), "m"), ("B", None, "m")], metric="m", reasons={"B": "n/m: negative equity"}
    )
    assert result["excluded_reasons"] == {"B": "n/m: negative equity"}


def test_revenue_growth_requires_same_unit_and_positive_revenue() -> None:
    p24 = dt.date(2024, 1, 1), dt.date(2024, 12, 31)
    rows = [
        row("us-gaap:Revenues", 80, start=p24[0], end=p24[1]),
        row("us-gaap:Revenues", 100),
    ]
    assert financial_peer_metrics(rows)["AAA"]["periods"][0]["revenue_growth"] == D("0.25")  # type: ignore[index]
    rows[0]["unit"] = "EUR"
    assert financial_peer_metrics(rows)["AAA"]["periods"][0]["revenue_growth"] is None  # type: ignore[index]


# --------------------------------------------------------------------------- A13: reconcile


def _balance_instance(assets: str = "300", equity: str = "100", liabilities: str = "200") -> bytes:
    end = dt.date(2025, 12, 31)
    return instance_xml(
        [Ctx("i", None, end), Ctx("d", dt.date(2025, 1, 1), end)],
        [
            XFact("us-gaap:Assets", "i", assets, decimals="0"),
            XFact("us-gaap:Liabilities", "i", liabilities, decimals="0"),
            XFact("us-gaap:StockholdersEquity", "i", equity, decimals="0"),
            XFact("us-gaap:LiabilitiesAndStockholdersEquity", "i", assets, decimals="0"),
            XFact("us-gaap:Revenues", "d", "1000"),
            XFact("dei:EntityCentralIndexKey", "d", "0000320193", unit=None),
        ],
    )


def _meta() -> FilingMeta:
    return FilingMeta(
        ticker="AAA",
        accession="0000320193-26-000001",
        form="10-K",
        filing_date=dt.date(2026, 2, 1),
        filing_url="https://x/",
        source_url="https://x/",
        accepted_at=dt.datetime(2026, 2, 1, 21, 5, tzinfo=dt.UTC),
        cik="0000320193",
    )


def test_balance_sheet_identity_publishes_or_blocks_facts() -> None:
    good = sec_statement_facts(parse_instance(_balance_instance()), _meta())
    good = apply_reconciliation(good, internal_checks(good))
    assets = next(f for f in good if f.concept == "us-gaap:Assets")
    assert assets.published and assets.validation["reconciled"] is True
    revenue = next(f for f in good if f.concept == "us-gaap:Revenues")
    assert not revenue.published and revenue.validation["reconciled"] is None

    bad = sec_statement_facts(parse_instance(_balance_instance(equity="150")), _meta())
    bad = apply_reconciliation(bad, internal_checks(bad))
    assets = next(f for f in bad if f.concept == "us-gaap:Assets")
    assert not assets.published and assets.validation["reconciled"] is False
    assert "assets_equal_liabilities_plus_equity" in assets.validation["failed_checks"]


def test_failed_reconciliation_keeps_facts_out_of_peer_metrics_and_unchecked_are_flagged() -> None:
    rows = [
        row("us-gaap:Revenues", 100, validation={"reconciled": True}),
        row("us-gaap:GrossProfit", 40, validation={"reconciled": False}),
        row("us-gaap:OperatingIncomeLoss", 20),
    ]
    period = financial_peer_metrics(rows)["AAA"]["periods"][0]  # type: ignore[index]
    assert period["gross_margin"] is None  # failed: never used
    assert period["operating_margin"] == D("0.2")
    assert period["unreconciled_inputs"] == ["operating_income"]
    strict = financial_peer_metrics(rows, reconciled_only=True)["AAA"]["periods"][0]  # type: ignore[index]
    assert strict["operating_margin"] is None


def test_cik_is_text_with_its_leading_zeros_and_statement_facts_are_classified() -> None:
    facts = sec_statement_facts(parse_instance(_balance_instance()), _meta())
    cik = next(f for f in facts if f.concept == "dei:EntityCentralIndexKey")
    assert cik.value is None and cik.value_text == "0000320193"
    assets = next(f for f in facts if f.concept == "us-gaap:Assets")
    assert (assets.statement, assets.line_item, assets.currency) == (
        "balance_sheet",
        "total_assets",
        "USD",
    )
    assert assets.cik == "0000320193" and assets.known_at == dt.datetime(
        2026, 2, 1, 21, 5, tzinfo=dt.UTC
    )
    assert classify("us-gaap:OperatingIncomeLoss", dimensional=True)[0] == "segment"
    assert currency_of("USD/shares") == "USD" and currency_of("shares") is None


def test_known_at_is_the_acceptance_time_never_midnight() -> None:
    day = dt.date(2026, 2, 1)
    assert sec_known_at(None, day) == dt.datetime(2026, 2, 1, 23, 59, 59, tzinfo=dt.UTC)
    accepted = dt.datetime(2026, 2, 1, 21, 5, tzinfo=dt.UTC)
    assert sec_known_at(accepted, day) == accepted


# ------------------------------------------------------------ A12: statement set, text blocks


def test_the_statement_set_is_retained_with_cited_text_blocks_and_the_cap_flagged() -> None:
    end = dt.date(2025, 12, 31)
    start = dt.date(2025, 1, 1)
    long_note = "Debt note. " * 3000
    data = instance_xml(
        [
            Ctx("d", start, end),
            Ctx("i", None, end),
            Ctx("seg", start, end, [("us-gaap:StatementBusinessSegmentsAxis", "co:CloudMember")]),
            Ctx("cust", start, end, [("srt:MajorCustomersAxis", "co:BigCoMember")]),
        ],
        [
            XFact("us-gaap:ResearchAndDevelopmentExpense", "d", "50"),
            XFact("us-gaap:OperatingLeaseLiability", "i", "70"),
            XFact("us-gaap:LongTermDebtMaturitiesRepaymentsOfPrincipalInYearTwo", "i", "30"),
            XFact("us-gaap:PaymentsForRepurchaseOfCommonStock", "d", "400"),
            XFact("us-gaap:DefinedBenefitPlanBenefitObligation", "i", "90"),
            XFact("us-gaap:OperatingIncomeLoss", "seg", "11"),
            XFact("us-gaap:ConcentrationRiskPercentage", "cust", "0.21", unit="pure"),
            XFact("us-gaap:DebtDisclosureTextBlock", "d", long_note, unit=None),
            XFact("us-gaap:UnrelatedPolicyTextBlock", "d", "ignored", unit=None),
        ],
    )
    instance = parse_instance(data, text_blocks=True)
    facts = {f.concept: f for f in sec_statement_facts(instance, _meta())}
    for name in (
        "us-gaap:ResearchAndDevelopmentExpense",
        "us-gaap:OperatingLeaseLiability",
        "us-gaap:LongTermDebtMaturitiesRepaymentsOfPrincipalInYearTwo",
        "us-gaap:PaymentsForRepurchaseOfCommonStock",
        "us-gaap:DefinedBenefitPlanBenefitObligation",
        "us-gaap:ConcentrationRiskPercentage",
    ):
        assert name in facts, name
    assert facts["us-gaap:OperatingIncomeLoss"].statement == "segment"
    block = facts["us-gaap:DebtDisclosureTextBlock"]
    assert block.statement == "note" and block.text_block is not None
    assert len(block.text_block) == 20_000
    assert block.validation["text_truncated"] is True
    assert block.validation["text_length"] == len(long_note.strip())
    assert "us-gaap:UnrelatedPolicyTextBlock" not in facts
    # Without text_blocks=True the parse keeps no blocks.
    plain = parse_instance(data)
    assert not [f for f in plain.facts if f.concept.endswith("TextBlock")]


# ---------------------------------------------------------------------- A14: P/S and ADS


def test_price_to_sales_divides_the_ads_price_by_the_ratio() -> None:
    # TSMC: ADS = 5 ordinary shares. 25.9bn shares at an ADS price of 250 -> 50 per share.
    shares, revenue = 25.9e9, 3.8e12
    ps = price_to_sales(250.0, shares, 5.0, revenue)
    assert ps.value == pytest.approx(250 / 5 * shares / revenue)
    # The bug multiplied by the ratio: a factor ratio**2 too high.
    assert ps.value * 25 == pytest.approx(250 * 5 * shares / revenue)
    assert price_to_sales(250.0, shares, 0.0, revenue).value is None


# ---------------------------------------------------------------------- B27: concepts / debt


def _fact(
    concept: str,
    end: dt.date,
    value: int,
    *,
    start: dt.date | None = None,
    filed: dt.date | None = None,
) -> Fact:
    return Fact(
        concept=concept,
        start=start,
        end=end,
        value=D(value),
        unit="USD",
        source="sec",
        ref="0000000000-26-000001",
        filed=filed or end,
    )


def test_concept_selection_follows_the_latest_period_not_list_order() -> None:
    old = _fact(
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", dt.date(2018, 12, 31), 1
    )
    new = _fact("us-gaap:Revenues", dt.date(2024, 12, 31), 2)
    chosen = _by_concept(
        [old, new],
        ["us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "us-gaap:Revenues"],
    )
    assert chosen == [new]


def test_valuation_debt_sums_current_and_noncurrent_components() -> None:
    end = dt.date(2025, 12, 31)
    year = (dt.date(2025, 1, 1), end)
    eps = Fact(
        "us-gaap:EarningsPerShareDiluted", year[0], end, D("2.0"), "USD/shares", "sec", "a", end
    )
    facts = [
        eps,
        _fact("us-gaap:LongTermDebtCurrent", end, 200),
        _fact("us-gaap:LongTermDebtNoncurrent", end, 800),
        # Older, larger "current" fact must not win either.
        _fact("us-gaap:LongTermDebtCurrent", dt.date(2019, 12, 31), 999),
    ]
    figures = compute("sec", facts, ["USD"], dt.date(2026, 3, 1))
    assert figures is not None and figures.debt_value is not None
    assert figures.debt_value.value == 1000 and figures.debt_value.period_end == end
    only_current = compute(
        "sec", [eps, _fact("us-gaap:LongTermDebtCurrent", end, 200)], ["USD"], dt.date(2026, 3, 1)
    )
    assert only_current is not None and only_current.debt_value is None  # not a complete figure


# --------------------------------------------------------------------------- B28: 10-K/A


def _filing(form: str, accession: str, filed: str, report: str = "2025-12-31") -> Filing:
    return Filing(
        cik="0000000001",
        form=form,
        accession=accession,
        filing_date=dt.date.fromisoformat(filed),
        report_date=dt.date.fromisoformat(report),
        primary_document="x.htm",
        items="",
    )


def test_a_part_iii_amendment_does_not_replace_the_annual_report() -> None:
    original = _filing("10-K", "0000000001-26-000001", "2026-02-01")
    amendment = _filing("10-K/A", "0000000001-26-000009", "2026-04-20")
    filings = [original, amendment]
    assert latest_periodic(filings)[0] == original
    assert amendments_to_check(filings) == [amendment]
    # Once its instance is known to carry the statements, the restatement is the report.
    assert latest_periodic(filings, {amendment.accession})[0] == amendment
    assert amended_filing(filings, amendment) == original


# --------------------------------------------------------------------- B30: fiscal periods


def test_fiscal_period_follows_the_dei_focus_and_the_duration() -> None:
    doc_end = dt.date(2026, 6, 30)
    q2 = (dt.date(2026, 4, 1), doc_end)
    assert sec_fiscal_period(*q2, doc_end, 2026, "Q2") == "FY2026Q2"
    assert sec_fiscal_period(dt.date(2026, 1, 1), doc_end, doc_end, 2026, "Q2") == "FY2026H1"
    assert (
        sec_fiscal_period(dt.date(2025, 4, 1), dt.date(2025, 6, 30), doc_end, 2026, "Q2")
        == "FY2025Q2"
    )
    # The prior year-end balance sheet in the 10-Q.
    assert sec_fiscal_period(None, dt.date(2025, 12, 31), doc_end, 2026, "Q2") == "FY2025"
    assert sec_fiscal_period(None, doc_end, doc_end, 2026, "Q2") == "FY2026Q2"
    # An annual report whose year ends in September (Apple).
    fy_end = dt.date(2025, 9, 27)
    assert sec_fiscal_period(dt.date(2024, 9, 29), fy_end, fy_end, 2025, "FY") == "FY2025"
    assert sec_fiscal_period(None, fy_end, None, 2025, "FY") is None


def test_esef_fiscal_period_from_the_fact_duration_not_the_end_year() -> None:
    assert grid_fiscal_period(dt.date(2026, 1, 1), dt.date(2026, 3, 31), 12) == ("FY2026Q1", False)
    assert grid_fiscal_period(dt.date(2025, 1, 1), dt.date(2025, 12, 31), 12) == ("FY2025", False)
    assert grid_fiscal_period(dt.date(2026, 1, 1), dt.date(2026, 6, 30), 12)[0] == "FY2026H1"
    # A March year end: the quarter ending in June belongs to the next fiscal year.
    assert grid_fiscal_period(dt.date(2025, 4, 1), dt.date(2025, 6, 30), 3)[0] == "FY2026Q1"
    assert grid_fiscal_period(None, dt.date(2026, 3, 31), 3)[0] == "FY2026"
    assert grid_fiscal_period(None, dt.date(2026, 6, 30), None) == ("FY2026Q2", True)


def test_segment_history_uses_one_filing_per_period_so_recast_members_are_not_unioned() -> None:
    def seg(member: str, filed: str, accession: str, value: int) -> dict[str, object]:
        return {
            "kind": "segment",
            "member": member,
            "period_start": dt.date(2024, 1, 1),
            "period_end": dt.date(2024, 12, 31),
            "unit": "USD",
            "filed": dt.date.fromisoformat(filed),
            "accession": accession,
            "value": D(value),
        }

    rows = [
        seg("co:Old", "2025-02-01", "a", 60),
        seg("co:Rest", "2025-02-01", "a", 40),
        seg("co:New", "2026-02-01", "b", 70),  # recast: Old no longer exists
        seg("co:Rest", "2026-02-01", "b", 30),
    ]
    kept = latest_filing_rows(rows)
    assert {r["member"] for r in kept} == {"co:New", "co:Rest"}
    assert {r["accession"] for r in kept} == {"b"}


# ------------------------------------------------------------- D4.9: ESEF dimensional facts


def _esef_doc() -> dict[str, object]:
    return {
        "documentInfo": {
            "namespaces": {"ifrs-full": "https://xbrl.ifrs.org/taxonomy/2025/ifrs-full"}
        },
        "facts": {
            "f1": {
                "value": "100",
                "decimals": "-3",
                "dimensions": {
                    "concept": "ifrs-full:Revenue",
                    "entity": "e",
                    "period": "2025-01-01T00:00:00/2026-01-01T00:00:00",
                    "unit": "iso4217:EUR",
                    "ifrs-full:SegmentsAxis": "co:CloudMember",
                },
            },
            "f2": {
                "value": "<p>Borrowings note</p>",
                "dimensions": {
                    "concept": "ifrs-full:DisclosureOfBorrowingsExplanatory",
                    "entity": "e",
                    "period": "2025-01-01T00:00:00/2026-01-01T00:00:00",
                },
            },
            "f3": {
                "value": None,
                "dimensions": {
                    "concept": "ifrs-full:DisclosureOfLeasesExplanatoryTextBlock",
                    "entity": "e",
                    "period": "2025-01-01T00:00:00/2026-01-01T00:00:00",
                },
            },
            "f4": {
                "value": "5",
                "dimensions": {
                    "concept": "ifrs-full:Assets",
                    "entity": "e",
                    "period": "2025-12-31T00:00:00",
                    "unit": "iso4217:EUR",
                },
            },
        },
    }


def test_esef_dimensional_facts_and_unavailable_text_blocks_are_stored_not_dropped() -> None:
    parsed = parse_xbrl_json(_esef_doc(), None, include_dimensions=True, include_text_blocks=True)
    by_concept = {f.concept: f for f in parsed}
    assert by_concept["ifrs-full:Revenue"].dimensions == {
        "ifrs-full:SegmentsAxis": "co:CloudMember"
    }
    unavailable = [f for f in parsed if f.availability == "unavailable"]
    assert [f.concept for f in unavailable] == ["ifrs-full:DisclosureOfLeasesExplanatoryTextBlock"]
    meta = FilingMeta(
        ticker="BBB",
        accession="f-1",
        form="ESEF",
        filing_date=dt.date(2026, 3, 1),
        filing_url="https://x",
        source_url="https://x",
        accepted_at=None,
        cik=None,
    )
    rows = esef_statement_facts(
        parsed,
        meta,
        year_end_month=None,
        published_at=None,
        known_at=dt.datetime(2026, 3, 1, tzinfo=dt.UTC),
    )
    revenue = next(r for r in rows if r.concept == "ifrs-full:Revenue")
    assert revenue.statement == "segment" and revenue.fiscal_period == "FY2025"
    assert revenue.dimensions == {"members": [["ifrs-full:SegmentsAxis", "co:CloudMember"]]}
    gap = next(r for r in rows if r.concept.endswith("LeasesExplanatoryTextBlock"))
    assert gap.value_text == "unavailable" and gap.validation["text_block"] == "unavailable"


# ------------------------------------------------------------------------------ B32 / forward


def test_forward_observation_key_is_stable_and_content_based() -> None:
    base = dict(
        kind="published_analyst_consensus",
        provider="p",
        metric="revenue",
        fiscal_period="FY2026",
        basis=None,
        currency="USD",
        vintage="v1",
        period_end=None,
        value=D("10.50"),
        low=None,
        high=None,
        analyst_count=12,
    )
    same = observation_key(**{**base, "value": D("10.5")})  # type: ignore[arg-type]
    assert observation_key(**base) == same  # type: ignore[arg-type]
    assert observation_key(**{**base, "value": D("10.6")}) != same  # type: ignore[arg-type]
    assert observation_key(**{**base, "currency": "EUR"}) != same  # type: ignore[arg-type]


def test_consensus_series_never_mixes_providers_currencies_or_bases() -> None:
    def obs(i: int, provider: str, currency: str, value: int, day: int) -> dict[str, object]:
        return {
            "id": i,
            "kind": "published_analyst_consensus",
            "metric": "revenue",
            "fiscal_period": "FY2026",
            "provider": provider,
            "currency": currency,
            "basis": None,
            "value": D(value),
            "known_at": dt.datetime(2026, 1, day, tzinfo=dt.UTC),
        }

    series = consensus_series(
        [
            obs(1, "a", "USD", 100, 1),
            obs(2, "b", "USD", 200, 2),
            obs(3, "a", "USD", 110, 3),
            obs(4, "a", "EUR", 90, 4),
        ]
    )
    by_key = {(s["provider"], s["currency"]): s for s in series}
    assert len(series) == 3
    assert by_key[("a", "USD")]["revision_since_previous"] == D(10)  # not 110 - 200
    assert by_key[("b", "USD")]["revision_since_previous"] is None
    assert by_key[("a", "EUR")]["revision_since_previous"] is None


# ------------------------------------------------------------------------------ F17


def test_utf16_documents_cannot_smuggle_a_dtd_past_the_guard() -> None:
    xml = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x [<!ENTITY a "b">]><x/>'
    with pytest.raises(XbrlError):
        parse_instance(xml.encode("utf-16"))
    with pytest.raises(XbrlError):
        parse_labels(xml.encode("utf-16"))


def _row(member: str, value: int) -> RevenueRow:
    return RevenueRow(
        kind="segment",
        axis="a",
        member=member,
        label=member,
        label_source="filing",
        concept="us-gaap:Revenues",
        start=dt.date(2025, 1, 1),
        end=dt.date(2025, 12, 31),
        value=D(value),
        unit="USD",
        decimals="0",
        context_id="c",
        fact_id=None,
    )


def test_mark_subtotals_is_bounded_on_a_pathological_breakdown() -> None:
    """24 members with no subset structure: the search must give up within its budget."""
    import random

    rng = random.Random(7)
    members = [_row(f"m{i}", rng.randint(10_000, 99_999) * 7 + 1) for i in range(24)]
    total = D(1)  # forces `excess()` for ever
    started = time.monotonic()
    out = mark_subtotals(members, total)
    assert time.monotonic() - started < 5
    assert len(out) == 24


def test_mark_subtotals_still_finds_real_subtotals() -> None:
    members = [
        _row("all-intl", 60),
        _row("eu", 30),
        _row("apac", 20),
        _row("row", 10),
        _row("us", 40),
    ]
    out = mark_subtotals(members, D(100))
    assert [m.subtotal for m in out] == [True, False, False, False, False]


def test_instance_shares_keep_a_class_dated_before_the_latest_cover_date() -> None:
    end_a, end_b = dt.date(2026, 7, 15), dt.date(2026, 7, 10)
    data = instance_xml(
        [
            Ctx(
                "a",
                None,
                end_a,
                [("us-gaap:StatementClassOfStockAxis", "us-gaap:CommonClassAMember")],
            ),
            Ctx(
                "b",
                None,
                end_b,
                [("us-gaap:StatementClassOfStockAxis", "us-gaap:CommonClassBMember")],
            ),
        ],
        [
            XFact("dei:EntityCommonStockSharesOutstanding", "a", "100", unit="shares"),
            XFact("dei:EntityCommonStockSharesOutstanding", "b", "50", unit="shares"),
        ],
    )
    shares = instance_shares(parse_instance(data), _Labels())
    assert shares is not None and shares.as_of == end_a
    assert {c.member: c.shares for c in shares.classes} == {
        "us-gaap:CommonClassAMember": D(100),
        "us-gaap:CommonClassBMember": D(50),
    }


# -------------------------------------------------------------------- D4.10: IR parsers


GUIDANCE = [
    "ACME Corp. reports third quarter results.",
    "For the fourth quarter of fiscal 2026, the Company expects revenue of between "
    "$10.5 billion and $11.0 billion.",
    "The Company expects non-GAAP diluted EPS of $2.10 to $2.20 for FY2026.",
    "The Board authorized a $5 billion share repurchase program.",
]


def test_guidance_sentences_parse_ranges_periods_and_capital_return() -> None:
    parsed = ir.parse_release(GUIDANCE, "guidance")
    revenue = next(p for p in parsed.forward if p.metric == "revenue")
    assert (revenue.fiscal_period, revenue.low, revenue.high, revenue.currency) == (
        "FY2026Q4",
        D(10_500_000_000),
        D(11_000_000_000),
        "USD",
    )
    eps = next(p for p in parsed.forward if p.metric == "eps_diluted")
    assert (eps.fiscal_period, eps.low, eps.high, eps.basis) == (
        "FY2026",
        D("2.10"),
        D("2.20"),
        "non-GAAP",
    )
    assert parsed.capital_return[0]["type"] == "repurchase"
    assert parsed.capital_return[0]["amount"] == "5000000000"


CONSENSUS_ROWS = [
    ["Metric", "Period", "Mean", "Low", "High", "Analysts", "Currency"],
    ["Revenue", "FY2026Q4", 10.7, 10.2, 11.1, 14, "USD"],
    ["Diluted EPS", "FY2026", 2.15, 2.0, 2.3, 0, "USD"],
]


def test_xlsx_consensus_table_parses_with_analyst_counts() -> None:
    lines = ir.document_lines(xlsx_bytes(CONSENSUS_ROWS))
    assert lines[0] == "Metric | Period | Mean | Low | High | Analysts | Currency"
    parsed = ir.parse_release(lines, "consensus")
    revenue = next(p for p in parsed.forward if p.metric == "revenue")
    assert revenue.analyst_count == 14 and revenue.value == D("10.7") and revenue.currency == "USD"
    eps = next(p for p in parsed.forward if p.metric == "eps_diluted")
    assert eps.analyst_count == 0  # zero analysts is a valid, stored count


def test_html_table_and_pdf_text_parse_the_same_figures() -> None:
    html = (
        "<html><body><h1>Consensus</h1><table><tr><th>Metric</th><th>Period</th><th>Mean</th>"
        "<th>Analysts</th></tr><tr><td>Revenue</td><td>FY2026Q4</td><td>10.7</td><td>14</td></tr>"
        "</table></body></html>"
    ).encode()
    parsed = ir.parse_release(ir.document_lines(html, "text/html"), "consensus")
    assert parsed.forward[0].analyst_count == 14 and parsed.forward[0].value == D("10.7")
    pdf = ir.parse_release(ir.document_lines(pdf_bytes(GUIDANCE)), "guidance")
    assert {p.metric for p in pdf.forward} == {"revenue", "eps_diluted"}


def test_consensus_without_an_analyst_count_is_skipped_with_a_note() -> None:
    parsed = ir.parse_release(["Metric | Period | Mean", "Revenue | FY2026 | 10.7"], "consensus")
    assert parsed.forward == [] and "analyst count" in parsed.notes[0]


def test_ir_fetch_guard_refuses_private_hosts_and_plain_http() -> None:
    for url in (
        "http://ir.example.test/x",
        "https://127.0.0.1/x",
        "https://10.0.0.5/x",
        "https://localhost/x",
        "https://db.internal/x",
    ):
        with pytest.raises(ir.IrError):
            ir.assert_fetchable(url)
    ir.assert_fetchable("https://ir.example.test/x")
