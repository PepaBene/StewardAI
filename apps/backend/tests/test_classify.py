"""Sectors and countries for the strategy's caps come from the data, the owner's own win."""

from steward.config.strategy import StrategyConfig
from steward.decision.allocator import strategy_notes
from steward.portfolio.classify import (
    COMMODITIES,
    GLOBAL,
    MULTI_SECTOR_FUND,
    fund_country,
    fund_sector,
    merge_classes,
    sector_from_industries,
)


def test_wikidata_industries_map_to_gics_sectors_most_specific_first() -> None:
    assert sector_from_industries(["health insurance"]) == "Health Care"
    assert sector_from_industries(["pharmaceutical industry"]) == "Health Care"
    assert sector_from_industries(["industrial manufacturing", "defence industry"]) == "Industrials"
    assert sector_from_industries(["quantum computing industry"]) == "Information Technology"
    assert sector_from_industries(["financial services"]) == "Financials"
    assert sector_from_industries(["Q112165285"]) is None
    assert sector_from_industries([]) is None


def test_a_fund_is_its_own_sector_and_takes_the_country_most_of_it_holds() -> None:
    assert fund_sector("WisdomTree Brent Crude Oil") == COMMODITIES
    assert fund_sector("iShares NASDAQ 100 UCITS ETF") == MULTI_SECTOR_FUND
    assert fund_country({"US": 0.97, "NL": 0.03}, MULTI_SECTOR_FUND) == "US"
    assert fund_country({"US": 0.55, "JP": 0.45}, MULTI_SECTOR_FUND) == GLOBAL
    assert fund_country({"US": 1.0}, COMMODITIES) == GLOBAL  # collateral, not exposure
    assert fund_country({}, MULTI_SECTOR_FUND) == GLOBAL


def test_the_owners_classification_wins_and_gaps_are_filled() -> None:
    strategy = StrategyConfig(
        base_currency="CZK",
        categories=["sector", "geography", "risk"],
        classifications={"AAA": {"sector": "Mine", "risk": "core"}},
    )
    merged = merge_classes(
        strategy,
        {"AAA": {"sector": "Information Technology", "geography": "US"}, "BBB": {"sector": "X"}},
    )
    assert merged.classifications["AAA"] == {"sector": "Mine", "risk": "core", "geography": "US"}
    assert merged.classifications["BBB"] == {"sector": "X"}
    assert strategy.classifications["AAA"] == {"sector": "Mine", "risk": "core"}


def test_strategy_notes_say_what_the_principles_ask_when_nothing_is_bought() -> None:
    strategy = StrategyConfig(
        base_currency="CZK", cash_target={"target_weight": 0.05, "band": 0.05}
    )
    notes = strategy_notes(strategy, cash_weight=0.27, tiered=False, core_etf=False)
    assert notes[0].startswith("Cash is 27% of the portfolio against a 5% target.")
    assert "not the strategy's answer" in notes[0]
    assert "No holding has a risk tier" in notes[1]
    assert "No broad global equity ETF" in notes[2]
    assert strategy_notes(strategy, cash_weight=0.08, tiered=True, core_etf=True) == []
