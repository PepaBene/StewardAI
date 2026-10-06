import datetime as dt
from decimal import Decimal

from steward.config.holding_profiles import Exposure, HoldingProfile
from steward.exposures.filing_text import (
    EXPOSURE_WORDS,
    FilingText,
    currency_in_quote,
    percent_bound,
    percent_in_quote,
    relevant,
    split_items,
    verified,
    word_in_quote,
)
from steward.exposures.hs import hs_weights, valid_heading
from steward.exposures.inputs import (
    Accepted,
    InputFinding,
    _drop_impossible_currency_shares,
    accept,
    added_keys,
    merge_exposures,
    tombstone_keys,
)

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


def test_a_deleted_row_stays_gone_across_later_filings() -> None:
    new = [Exposure(kind="commodity_input", name="Copper", origin="ai")]
    # run 2: the owner deleted copper after run 1 wrote it
    run1 = {"written": ["commodity_input:copper"]}
    merged, info = merge_exposures(profile(), new, added_keys(run1), tombstone_keys(run1))
    assert merged.exposures == [] and info["tombstones"] == ["commodity_input:copper"]
    # run 3 only sees run 2's record, which wrote nothing: the tombstone carries on
    run2 = {"written": info["written"], "tombstones": info["tombstones"]}
    merged, info = merge_exposures(profile(), new, added_keys(run2), tombstone_keys(run2))
    assert merged.exposures == [] and info["skipped_deleted"] == ["commodity_input:copper"]
    # the owner entering it again lifts the tombstone, and the row is theirs
    owner = Exposure(kind="commodity_input", name="Copper", share=0.1)
    merged, info = merge_exposures(profile(owner), new, set(), tombstone_keys(run2))
    assert [(e.origin, e.share) for e in merged.exposures] == [("owner", 0.1)]
    assert info["tombstones"] == []


def test_a_new_share_or_quote_counts_as_a_change() -> None:
    old = Exposure(kind="revenue_currency", name="EUR", share=0.2, origin="ai", quote="q" * 30)
    same = old.model_copy()
    assert (
        merge_exposures(profile(old), [same], {("revenue_currency", "eur")})[1]["changed"] is False
    )
    newer = old.model_copy(update={"share": 0.25, "quote": "r" * 30})
    merged, info = merge_exposures(profile(old), [newer], {("revenue_currency", "eur")})
    assert info["changed"] is True and merged.exposures[0].share == 0.25


def test_a_percentage_must_sit_next_to_its_own_currency() -> None:
    gap = " and the remaining sales were spread over many other regions of the world " * 3
    quote = f"About 22% of revenue was in euros,{gap}while Japanese yen made up 3% of our sales"
    assert percent_bound(0.22, ("euro",), quote) and not percent_bound(0.22, ("yen",), quote)
    far = "Euros matter. " + "x " * 200 + "Approximately 22% of revenue is in dollars"
    assert not percent_bound(0.22, ("euro",), far)


def test_currency_words_are_not_ordinary_english() -> None:
    quote = "We won the contract and sold real estate in Mexico worth 5% of revenue"
    assert not currency_in_quote("KRW", quote)
    assert not currency_in_quote("BRL", quote)
    assert currency_in_quote("KRW", "revenue in Korean won rose")


def test_impossible_currency_totals_lose_their_shares() -> None:
    out = Accepted(
        exposures=[
            Exposure(kind="revenue_currency", name="EUR", share=0.6, origin="ai", period="FY2025"),
            Exposure(kind="revenue_currency", name="USD", share=0.7, origin="ai", period="FY2025"),
            Exposure(kind="revenue_currency", name="JPY", share=0.1, origin="ai", period="FY2024"),
        ]
    )
    _drop_impossible_currency_shares(out)
    assert [(e.name, e.share) for e in out.exposures] == [
        ("EUR", None),
        ("USD", None),
        ("JPY", 0.1),
    ]
    assert len(out.rejected) == 1


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
