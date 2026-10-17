import datetime as dt

from steward.exposures.filing_text import FilingText
from steward.graph.dependencies import (
    DependencyFinding,
    candidates_prompt,
    check_findings,
    holding_for,
    reported_concentrations,
)

TEXT = (
    "Sales to Acme Corporation accounted for 22% of our net sales in fiscal 2025. "
    "We purchase wafers from Taiwan Semiconductor, our sole source, which represented 40% of "
    "our purchases. Customer A accounted for 12% of revenue. " * 3
)


def filing() -> FilingText:
    return FilingText(
        "AAA_US_EQ", "0000000000-26-000001", "10-K", "https://sec.example/a.htm",
        dt.date(2025, 12, 31), TEXT,
    )  # fmt: skip


def finding(**kw: object) -> DependencyFinding:
    base: dict[str, object] = {
        "relationship": "customer",
        "counterparty": "Acme Corporation",
        "share": 0.22,
        "period": "FY2025",
        "quote": "Sales to Acme Corporation accounted for 22% of our net sales in fiscal 2025.",
    }
    return DependencyFinding.model_validate({**base, **kw})


def test_a_quoted_named_customer_with_its_share_is_kept() -> None:
    out = check_findings([finding()], filing())
    assert len(out.kept) == 1 and out.rejected == []


def test_a_share_not_in_the_quote_is_rejected() -> None:
    out = check_findings([finding(share=0.5)], filing())
    assert out.kept == [] and "percentage" in out.rejected[0]


def test_an_invented_quote_or_name_is_rejected() -> None:
    assert not check_findings(
        [finding(quote="Acme is a huge customer of ours, a lot of sales.")], filing()
    ).kept
    assert not check_findings([finding(counterparty="Globex")], filing()).kept


def test_the_share_basis_must_fit_the_relationship() -> None:
    supplier = finding(
        relationship="supplier",
        counterparty="Taiwan Semiconductor",
        share=0.22,
        quote="Sales to Acme Corporation accounted for 22% of our net sales in fiscal 2025.",
    )
    out = check_findings([supplier], filing())
    assert out.kept == [] and out.rejected  # a revenue share is not a purchases share


def test_an_unnamed_customer_needs_a_share_and_the_company_itself_is_not_a_customer() -> None:
    anon = finding(counterparty=None, share=0.12, quote="Customer A accounted for 12% of revenue.")
    assert len(check_findings([anon], filing()).kept) == 1
    nameless = finding(
        counterparty=None, share=None, quote="Customer A accounted for 12% of revenue."
    )
    assert not check_findings([nameless], filing()).kept
    own = check_findings([finding()], filing(), ("Acme Corp.",))
    assert own.kept == [] and "itself" in own.rejected[0]


def test_duplicates_are_listed_once() -> None:
    out = check_findings([finding(), finding()], filing())
    assert len(out.kept) == 1 and "listed twice" in out.rejected[0]


def fact(who: str, benchmark: str, value: float = 0.18) -> dict[str, object]:
    return {
        "concept": "us-gaap:ConcentrationRiskPercentage1",
        "value": value,
        "period_start": dt.date(2024, 9, 29),
        "period_end": dt.date(2025, 9, 27),
        "members": [
            ["srt:MajorCustomersAxis", who],
            ["us-gaap:ConcentrationRiskByBenchmarkAxis", benchmark],
        ],
        "source_url": "https://sec.example/x.htm",
        "accession": "0000000000-26-000001",
    }


def test_xbrl_concentration_basis_and_names() -> None:
    rows = [
        fact("aapl:CustomerOneMember", "us-gaap:SalesRevenueNetMember"),
        fact("aapl:VendorOneMember", "aapl:NonTradeReceivableMember", 0.46),
        fact("aapl:CellularNetworkCarriersMember", "us-gaap:TradeAccountsReceivableMember"),
        fact("tsla:PanasonicMember", "us-gaap:SalesRevenueNetMember", 0.3),
        fact("x:BadMember", "us-gaap:SalesRevenueNetMember", 7),
    ]
    out = reported_concentrations(rows)
    assert [(c.relationship, c.counterparty, c.basis) for c in out] == [
        ("customer", None, "issuer_revenue"),
        ("supplier", None, "other"),  # a receivables share is not a purchases share
        ("customer", "Panasonic", "issuer_revenue"),
    ]


def test_a_residual_bucket_is_skipped_and_a_lettered_customer_is_anonymous() -> None:
    # Production, 17 Oct 2026: CELH's "All Other" 46% was stored as a named customer (its
    # shares then summed to 143%), and NVDA's "Accounts Receivable Customer F" as a name.
    rows = [
        fact("celh:AllOtherMember", "us-gaap:SalesRevenueNetMember", 0.46),
        fact("celh:PepsicoIncMember", "us-gaap:SalesRevenueNetMember", 0.43),
        fact("nvda:AccountsReceivableCustomerFMember", "us-gaap:AccountsReceivableMember", 0.25),
    ]
    out = reported_concentrations(rows)
    assert [(c.counterparty, c.basis) for c in out] == [
        ("Pepsico Inc", "issuer_revenue"),
        (None, "other"),
    ]


def test_a_counterparty_that_is_a_holding_is_resolved() -> None:
    holdings = [
        {"ticker": "TSM_US_EQ", "names": ["Taiwan Semiconductor Manufacturing Co., Ltd."]},
        {"ticker": "AAA_US_EQ", "names": ["Acme Corporation"]},
    ]
    assert holding_for("Taiwan Semiconductor Manufacturing Company", holdings, "AAA_US_EQ") == (
        "TSM_US_EQ"
    )
    assert holding_for("Acme Corp", holdings, "AAA_US_EQ") is None  # the issuer itself
    assert holding_for(None, holdings, "AAA_US_EQ") is None


def test_the_candidates_prompt_numbers_each_candidate() -> None:
    prompt = candidates_prompt(filing(), [finding(), finding(counterparty=None)])
    assert 'index="1"' in prompt and "not named" in prompt
