import datetime as dt

from steward.exposures.filing_text import FilingText
from steward.graph.dependencies import DependencyFinding, candidates_prompt, check_findings

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


def test_the_candidates_prompt_numbers_each_candidate() -> None:
    prompt = candidates_prompt(filing(), [finding(), finding(counterparty=None)])
    assert 'index="1"' in prompt and "not named" in prompt
