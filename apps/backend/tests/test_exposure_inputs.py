import datetime as dt
from decimal import Decimal

from steward.config.holding_profiles import Exposure, HoldingProfile
from steward.exposures.filing_text import (
    EXPOSURE_WORDS,
    FilingText,
    percent_in_quote,
    relevant,
    split_items,
    verified,
    word_in_quote,
)
from steward.exposures.hs import hs_weights, valid_heading
from steward.exposures.inputs import InputFinding, accept, added_keys, merge_exposures

D = Decimal
FILLER = "Our business is described elsewhere in this report and in the notes. " * 80
REPORT = (
    "Item 1. Business\nTable of contents entry.\n"
    "Item 7A. Quantitative and Qualitative Disclosures About Market Risk\nshort toc\n"
    "Item 1. Business\n"
    + FILLER
    + "\nWe buy copper and aluminum for our enclosures and cables every year.\n"
    "Item 7A. Quantitative and Qualitative Disclosures About Market Risk\n"
    "Approximately 22% of our revenue in fiscal 2025 was denominated in euros, so a stronger "
    "dollar reduces reported results.\n" + FILLER + "\nItem 8. Financial Statements\n" + FILLER
)


def filing() -> FilingText:
    return FilingText(
        "AAA_US_EQ",
        "0000000000-26-000001",
        "10-K",
        "https://sec.example/a.htm",
        dt.date(2025, 12, 31),
        REPORT,
    )


def test_the_longest_stretch_of_an_item_wins_over_the_table_of_contents() -> None:
    items = split_items(REPORT)
    assert "copper and aluminum" in items["1"] and "short toc" not in items["1"]
    assert "denominated in euros" in items["7A"]


def test_relevant_keeps_only_paragraphs_about_the_topic() -> None:
    text = relevant(split_items(REPORT)["1"], EXPOSURE_WORDS)
    assert "copper" in text and "described elsewhere" not in text


def test_quote_checks() -> None:
    quote = "Approximately 22% of our revenue in fiscal 2025 was denominated in euros"
    assert percent_in_quote(0.22, quote) and not percent_in_quote(0.25, quote)
    assert word_in_quote("copper", "We buy copper and aluminum for our enclosures") is True
    assert word_in_quote("space", "the aerospace division") is False
    assert word_in_quote("wafer", "we buy silicon wafers") is True
    assert verified(quote, REPORT, 0.22, ("euro",)) == []
    assert verified(quote, REPORT, 0.30, ()) == ["the quote does not print that percentage"]
    assert verified("An invented sentence about currencies in the filing.", REPORT, None, ())


def test_only_verified_findings_become_exposures() -> None:
    good_ccy = InputFinding(
        kind="revenue_currency",
        name="eur",
        share=0.22,
        period="FY2025",
        quote="Approximately 22% of our revenue in fiscal 2025 was denominated in euros",
    )
    good_input = InputFinding(
        kind="commodity_input",
        name="copper",
        share=None,
        period=None,
        quote="We buy copper and aluminum for our enclosures and cables every year.",
    )
    bad_quote = InputFinding(
        kind="commodity_input",
        name="lithium",
        share=None,
        period=None,
        quote="We rely heavily on lithium for batteries across all of our product lines.",
    )
    wrong_share = InputFinding(
        kind="revenue_currency",
        name="EUR",
        share=0.5,
        period=None,
        quote="Approximately 22% of our revenue in fiscal 2025 was denominated in euros",
    )
    out = accept([good_ccy, good_input, bad_quote, wrong_share], filing())
    assert [(e.kind, e.name, e.share, e.origin) for e in out.exposures] == [
        ("revenue_currency", "EUR", 0.22, "ai"),
        ("commodity_input", "Copper", None, "ai"),
    ]
    assert len(out.rejected) == 2 and out.exposures[0].source_url == "https://sec.example/a.htm"


def profile(*exposures: Exposure) -> HoldingProfile:
    return HoldingProfile.model_validate(
        {"ticker": "AAA_US_EQ", "exposures": [e.model_dump() for e in exposures]}
    )


def test_merge_keeps_owner_rows_and_replaces_ai_rows() -> None:
    owner_eur = Exposure(kind="revenue_currency", name="EUR", share=0.4)
    old_ai = Exposure(kind="commodity_input", name="Zinc", origin="ai", quote="x" * 30)
    new = [
        Exposure(kind="revenue_currency", name="EUR", share=0.22, origin="ai"),
        Exposure(kind="commodity_input", name="Copper", origin="ai"),
    ]
    merged, info = merge_exposures(profile(owner_eur, old_ai), new, set())
    assert [(e.name, e.origin, e.share) for e in merged.exposures] == [
        ("EUR", "owner", 0.4),
        ("Copper", "ai", None),
    ]
    assert info["written"] == ["commodity_input:copper"] and info["skipped_owner"] == [
        "revenue_currency:eur"
    ]


def test_a_row_the_owner_deleted_is_not_added_back() -> None:
    new = [Exposure(kind="commodity_input", name="Copper", origin="ai")]
    previously = added_keys({"written": ["commodity_input:copper"]})
    merged, info = merge_exposures(profile(), new, previously)
    assert merged.exposures == [] and info["skipped_deleted"] == ["commodity_input:copper"]


def test_hs_headings_and_weights() -> None:
    assert valid_heading("8542") == "8542" and valid_heading("7701") is None
    assert valid_heading("99") is None and valid_heading(None) is None
    lines = [("GPUs", D(60)), ("Networking", D(30)), ("Software", D(10))]
    out = hs_weights(lines, {"GPUs": "8542", "Networking": "8517"}, D(100))
    assert isinstance(out, dict) and out["8542"][0] == D("0.6") and "software" not in str(out)
    same = hs_weights(lines, {"GPUs": "8542", "Networking": "8542"}, D(100))
    assert isinstance(same, dict) and same["8542"][0] == D("0.9")
    assert hs_weights(lines, {}, D(100)) == "no product line maps to one HS heading"
    assert hs_weights(lines, {"GPUs": "8542"}, D(10)) == "mapped revenue exceeds total revenue"
