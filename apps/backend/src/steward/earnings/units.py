"""Scales, currencies and the units a thesis KPI threshold is written in.

Every figure is compared in code, so a KPI's unit has to say unambiguously what its
threshold means: "%" (10 = 10 %), "x" (a plain ratio), a currency with an optional scale
("EUR bn", "USD m", "USD" for per-share amounts), or "count".
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

Scale = Literal["units", "thousands", "millions", "billions"]
UnitKind = Literal["currency", "per_share", "percent", "count", "ratio"]

SCALE_FACTOR: dict[str, Decimal] = {
    "units": Decimal(1),
    "thousands": Decimal(10) ** 3,
    "millions": Decimal(10) ** 6,
    "billions": Decimal(10) ** 9,
}

# Words a document uses to state a scale ("in millions", "EUR bn", "$94.0 billion").
SCALE_WORDS: dict[str, tuple[str, ...]] = {
    "thousands": ("thousand", "thousands", "000s", "'000", "k"),
    "millions": ("million", "millions", "mn", "mln", "mio", "m"),
    "billions": ("billion", "billions", "bn", "bln", "b"),
}

# Symbols that may stand for a currency in a snippet. "$" is ambiguous across dollar
# currencies, so it is accepted for any of them.
CURRENCY_MARKERS: dict[str, tuple[str, ...]] = {
    "USD": ("$", "us$", "usd", "dollar"),
    "CAD": ("$", "c$", "cad", "dollar"),
    "AUD": ("$", "a$", "aud", "dollar"),
    "HKD": ("$", "hk$", "hkd", "dollar"),
    "SGD": ("$", "s$", "sgd", "dollar"),
    "EUR": ("€", "eur", "euro"),
    "GBP": ("£", "gbp", "pound", "sterling"),
    "GBX": ("p", "gbx", "pence"),
    "JPY": ("¥", "jpy", "yen"),
    "CNY": ("¥", "rmb", "cny", "yuan", "renminbi"),
    "CHF": ("chf", "franc"),
    "CZK": ("kč", "czk", "koruna", "crown"),
    "DKK": ("dkk", "kr", "krone"),
    "SEK": ("sek", "kr", "krona"),
    "NOK": ("nok", "kr", "krone"),
    "PLN": ("pln", "zł", "zloty"),
}


def scale_markers_present(text: str, scale: Scale) -> bool:
    """Does `text` state the given scale? 'units' needs no statement."""
    if scale == "units":
        return True
    lowered = text.casefold()
    for word in SCALE_WORDS[scale]:
        if len(word) <= 2:
            # Short abbreviations only count right after a digit or currency marker
            # ("7.5bn", "EUR m", "$94.0 b"), never inside ordinary words.
            if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", lowered) and re.search(
                rf"([0-9]|[a-z]{{3}}|[$€£¥])\s?{re.escape(word)}(?![a-z])", lowered
            ):
                return True
        elif re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", lowered):
            return True
    return False


def conflicting_scales(text: str, scale: Scale) -> list[str]:
    """Other scales stated in `text` in their long form ('in thousands' when we expect
    millions). Short forms are too ambiguous to count as a conflict."""
    lowered = text.casefold()
    found = []
    for other, words in SCALE_WORDS.items():
        if other == scale:
            continue
        if any(re.search(rf"(?<![a-z]){w}(?![a-z])", lowered) for w in words if len(w) > 3):
            found.append(other)
    return found


def currency_markers_present(text: str, currency: str) -> bool:
    lowered = text.casefold()
    markers = CURRENCY_MARKERS.get(currency, (currency.casefold(),))
    for marker in markers:
        if marker.isalpha() and len(marker) <= 3:
            if re.search(rf"(?<![a-z]){re.escape(marker)}(?![a-z])", lowered):
                return True
        elif marker in lowered:
            return True
    return False


_NUMBER_CHARS = re.compile(r"[^0-9.,\-\u2212()]")


def parse_written_number(written: str) -> Decimal | None:
    """The numeric reading of a figure as printed: '94,036' -> 94036, '(171)' -> -171,
    '1.234,5' -> 1234.5, '7.52' -> 7.52, '\u22120.4' -> -0.4. None if it is not a number."""
    text = written.strip().replace("\u2212", "-").replace("\xa0", " ").replace(" ", "")
    negative = (text.startswith("(") and text.endswith(")")) or text.startswith("-")
    text = _NUMBER_CHARS.sub("", text).strip("()-")
    if not text or not any(c.isdigit() for c in text):
        return None
    if "," in text and "." in text:
        # The later separator is the decimal point.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        head, _, tail = text.rpartition(",")
        # "94,036" is a thousands separator; "7,52" is a decimal comma.
        if len(tail) == 3 and head.replace(",", "").isdigit():
            text = text.replace(",", "")
        elif text.count(",") == 1:
            text = text.replace(",", ".")
        else:
            return None
    elif text.count(".") > 1:
        text = text.replace(".", "")
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return -value if negative else value


@dataclass(frozen=True)
class KpiUnit:
    """What a KPI threshold's number means."""

    kind: UnitKind
    scale: Scale = "units"
    currency: str | None = None

    @property
    def factor(self) -> Decimal:
        return SCALE_FACTOR[self.scale]


_SCALE_ALIASES: dict[str, Scale] = {
    "k": "thousands", "thousand": "thousands", "thousands": "thousands",
    "m": "millions", "mn": "millions", "mln": "millions", "million": "millions",
    "millions": "millions",
    "b": "billions", "bn": "billions", "bln": "billions", "billion": "billions",
    "billions": "billions",
}  # fmt: skip


def parse_kpi_unit(unit: str) -> KpiUnit:
    """Parse a KPI unit string. Raises ValueError with the accepted forms."""
    text = unit.strip()
    lowered = text.casefold()
    if lowered in ("%", "pct", "percent"):
        return KpiUnit("percent")
    if lowered in ("x", "ratio", "times"):
        return KpiUnit("ratio")
    if lowered in ("count", "#", "units"):
        return KpiUnit("count")
    parts = text.replace("/", " ").split()
    if parts and re.fullmatch(r"[A-Z]{3}", parts[0]):
        currency = parts[0]
        rest = [p.casefold() for p in parts[1:]]
        if not rest:
            return KpiUnit("currency", "units", currency)
        if rest in (["per", "share"], ["share"]):
            return KpiUnit("per_share", "units", currency)
        if len(rest) == 1 and rest[0] in _SCALE_ALIASES:
            return KpiUnit("currency", _SCALE_ALIASES[rest[0]], currency)
    raise ValueError(
        f"unrecognised unit {unit!r}: use '%', 'x', 'count', or a currency code with an "
        "optional scale ('EUR', 'EUR m', 'USD bn', 'USD per share')"
    )
