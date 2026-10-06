import datetime as dt

from steward.config.jobs import MacroSettings
from steward.exposures.filing_text import EXPOSURE_WORDS, FilingText, sections_for
from steward.macro.commodity_series import COMMODITY_FRED, series_keys_for


def test_commodity_names_link_to_series() -> None:
    assert series_keys_for("Copper") == ["commodity_copper"]
    assert series_keys_for("aluminium") == ["commodity_aluminum"]
    assert series_keys_for("Natural gas") == ["commodity_gas_us", "commodity_gas_eu"]
    assert "commodity_wti" in series_keys_for("crude oil")
    assert series_keys_for("wafers") == []  # no series: listed as unlinked, never guessed
    assert series_keys_for("tinplate") == []  # a word, not a substring


def test_every_commodity_series_is_a_default_macro_series() -> None:
    keys = {s.key for s in MacroSettings().fred}
    assert {key for key, *_rest in COMMODITY_FRED} <= keys
    assert len(keys) == len(MacroSettings().fred)


def test_a_results_document_without_items_is_read_as_one_section() -> None:
    text = "Revenue grew. " * 50 + "\nWe buy copper and report in euros, a currency we hedge.\n"
    doc = FilingText(
        "CSG", "sha", "document", "https://x.example/r.pdf", dt.date(2026, 6, 30), text
    )
    assert "copper" in sections_for(doc, EXPOSURE_WORDS)["document"]
