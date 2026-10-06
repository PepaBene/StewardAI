"""Commodity price series collected from FRED and the commodity names that link to them.

(key, label, FRED series id, unit, the words a profile's `commodity_input` name can use).
The series are the IMF global price series FRED republishes (monthly) and the EIA spot prices
(daily). They are added to the macro series config as defaults, so the owner can edit or remove
them there; nothing else in the code names a commodity series.
"""

import re

COMMODITY_FRED: tuple[tuple[str, str, str, str, tuple[str, ...]], ...] = (
    ("commodity_copper", "Copper price", "PCOPPUSDM", "USD/t", ("copper",)),
    ("commodity_aluminum", "Aluminum price", "PALUMUSDM", "USD/t", ("aluminum", "aluminium")),
    ("commodity_nickel", "Nickel price", "PNICKUSDM", "USD/t", ("nickel",)),
    ("commodity_zinc", "Zinc price", "PZINCUSDM", "USD/t", ("zinc",)),
    ("commodity_tin", "Tin price", "PTINUSDM", "USD/t", ("tin",)),
    ("commodity_iron_ore", "Iron ore price", "PIORECRUSDM", "USD/t", ("iron ore", "iron")),
    ("commodity_uranium", "Uranium price", "PURANUSDM", "USD/lb", ("uranium",)),
    (
        "commodity_wti",
        "WTI crude oil price",
        "DCOILWTICO",
        "USD/bbl",
        ("oil", "crude", "crude oil", "petroleum", "wti"),
    ),
    ("commodity_brent", "Brent crude oil price", "DCOILBRENTEU", "USD/bbl", ("oil", "brent")),
    (
        "commodity_gas_us",
        "Henry Hub natural gas price",
        "DHHNGSP",
        "USD/MMBtu",
        ("natural gas", "gas", "lng"),
    ),
    (
        "commodity_gas_eu",
        "EU natural gas price",
        "PNGASEUUSDM",
        "USD/MMBtu",
        ("natural gas", "gas"),
    ),
    ("commodity_wheat", "Wheat price", "PWHEAMTUSDM", "USD/t", ("wheat",)),
    ("commodity_maize", "Maize price", "PMAIZMTUSDM", "USD/t", ("maize", "corn")),
    ("commodity_cotton", "Cotton price", "PCOTTINDUSDM", "USc/lb", ("cotton",)),
    ("commodity_rubber", "Rubber price", "PRUBBUSDM", "USc/lb", ("rubber",)),
    ("commodity_coffee", "Coffee price", "PCOFFOTMUSDM", "USc/lb", ("coffee",)),
)


# Other goods that share a word with a benchmark ("palm oil" is not crude oil, "tin plate" is
# steel): removed from the name before it is matched.
UNRELATED = re.compile(
    r"(?i)\b(?:(?:palm|olive|vegetable|cooking|soybean|canola|coconut|edible|essential|sunflower"
    r"|rapeseed|castor|fish) oils?|tin[ -]?(?:plate|foil|cans?|free)|corn (?:syrup|starch))\b"
)


def word_in(name: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?:s)?(?!\w)", name.lower()) is not None


def series_keys_for(name: str) -> list[str]:
    """The commodity series keys a `commodity_input` name links to (empty when none)."""
    cleaned = UNRELATED.sub(" ", name)
    return [key for key, *_rest, words in COMMODITY_FRED if any(word_in(cleaned, w) for w in words)]
