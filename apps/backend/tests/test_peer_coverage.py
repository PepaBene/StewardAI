from steward.company.peer_coverage import (
    peer_key,
    peer_symbol,
    price_symbol,
    resolve,
    symbol_index,
)

UNIVERSE = [
    ("FB_US_EQ", "meta.us"),
    ("GOOGL_US_EQ", "googl.us"),
    ("CSGa_EQ", "csg.nl"),
    ("SXRVd_EQ", None),
]


def test_a_symbol_maps_to_the_held_instrument_trading_under_it() -> None:
    by_symbol = symbol_index(UNIVERSE)
    universe = {t for t, _ in UNIVERSE}
    # META is held as FB_US_EQ: the price symbol names the listing, not the old ticker.
    assert resolve("META", universe, by_symbol) == "FB_US_EQ"
    assert resolve("GOOGL", universe, by_symbol) == "GOOGL_US_EQ"
    assert resolve("CSGa_EQ", universe, by_symbol) == "CSGa_EQ"


def test_an_unheld_us_peer_gets_a_key_and_a_foreign_one_stays_as_stored() -> None:
    by_symbol = symbol_index(UNIVERSE)
    universe = {t for t, _ in UNIVERSE}
    assert resolve("MSFT", universe, by_symbol) == "MSFT_US_EQ"
    assert resolve("GSK.L", universe, by_symbol) == "GSK.L"
    assert resolve("2303.TW", universe, by_symbol) == "2303.TW"


def test_peer_keys_round_trip() -> None:
    assert peer_key("msft") == "MSFT_US_EQ"
    assert peer_symbol("MSFT_US_EQ") == "MSFT"
    assert peer_symbol("CSGa_EQ") is None
    assert price_symbol("MSFT") == "msft.us"
