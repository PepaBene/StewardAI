"""Scales, currencies and the units a thesis KPI threshold is written in.

Every figure is compared in code, so a KPI's unit has to say unambiguously what its
threshold means: "%" (10 = 10 %), "x" (a plain ratio), a currency with an optional scale
("EUR bn", "USD m", "USD" for per-share amounts), or "count".
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal, cast

from steward.documents.text import normalize_for_match

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
    "TWD": ("nt$", "twd", "ntd", "nt dollar", "taiwan dollar"),
    "KRW": ("₩", "krw", "korean won"),
    "BRL": ("r$", "brl", "reais", "brazilian real"),
    "MXN": ("mxn", "mex$", "peso"),
    "INR": ("₹", "inr", "rupee"),
    "ZAR": ("zar", "rand"),
    "ILS": ("₪", "ils", "nis", "shekel"),
    "HUF": ("huf", "ft", "forint"),
    "RON": ("ron", "lei"),
    "TRY": ("₺", "try", "lira"),
}


# Currency codes and symbols a short scale abbreviation may follow ("EUR m", "$94.0 b").
_SCALE_PREFIX = (
    r"(?:[0-9]|[$€£¥]|(?<![a-z])(?:"
    + "|".join(
        sorted({c.casefold() for c in CURRENCY_MARKERS} | {"us$"}, key=len, reverse=True)
    ).replace("$", r"\$")
    + r"))"
)


def scale_markers_present(text: str, scale: Scale) -> bool:
    """Does `text` state the given scale? 'units' needs no statement."""
    if scale == "units":
        return True
    lowered = text.casefold()
    for word in SCALE_WORDS[scale]:
        if len(word) <= 2:
            # Short abbreviations only count right after a digit, a currency symbol or a
            # currency code ("7.5bn", "EUR m", "$94.0 b"), never inside ordinary words or
            # after one ("Class B", "Series B").
            if re.search(rf"{_SCALE_PREFIX}\s?{re.escape(word)}(?![a-z0-9])", lowered):
                return True
        elif re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", lowered):
            return True
    return False


def scale_next_to(text: str, number: str) -> Scale | None:
    """The scale printed right after `number` in `text` ("94.0 billion", "$1.2bn"), if any.
    `number` must be the printed number; it is matched on digit boundaries."""
    lowered = normalize_for_match(text)
    words: list[tuple[str, Scale]] = sorted(
        ((w, cast(Scale, s)) for s, ws in SCALE_WORDS.items() for w in ws if w[0].isalpha()),
        key=lambda ws: len(ws[0]),
        reverse=True,
    )
    alternatives = "|".join(re.escape(w) for w, _ in words)
    pattern = (
        rf"(?<![\d])(?<!\d[.,]){re.escape(normalize_for_match(number))}(?![\d])(?![.,]\d)"
        rf"\s?({alternatives})(?![a-z0-9])"
    )
    match = re.search(pattern, lowered)
    if match is None:
        return None
    word = match.group(1)
    return next(s for w, s in words if w == word)


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


_DOT_THOUSANDS = re.compile(r"\d{1,3}(?:\.\d{3})+")


def parse_written_number(written: str, *, decimal_comma: bool = False) -> Decimal | None:
    """The numeric reading of a figure as printed: '94,036' -> 94036, '(171)' -> -171,
    '1.234,5' -> 1234.5, '1.234.567' -> 1234567, '7.52' -> 7.52, '\u22120.4' -> -0.4.
    None if it is not a number.

    A single dot group ('1.234') is ambiguous: a decimal point in English, a thousands
    separator in most of Europe. It is read as a decimal point unless `decimal_comma` says
    the surrounding text writes decimals with a comma."""
    text = (
        written.strip()
        .replace("\u2212", "-")
        .replace("\u2013", "-")
        .replace("\xa0", " ")
        .replace(" ", "")
    )
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
        if len(tail) == 3 and head.replace(",", "").isdigit() and not decimal_comma:
            text = text.replace(",", "")
        elif text.count(",") == 1:
            text = text.replace(",", ".")
        else:
            return None
    elif text.count(".") > 1:
        # "1.234.567": only whole groups of three are thousands separators.
        if not _DOT_THOUSANDS.fullmatch(text):
            return None
        text = text.replace(".", "")
    elif decimal_comma and _DOT_THOUSANDS.fullmatch(text):
        text = text.replace(".", "")
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return -value if negative else value


def uses_decimal_comma(text: str) -> bool | None:
    """Does `text` write decimals with a comma ('7,52', '1.234,5')? True / False when its
    numbers say so unambiguously, None when they don't (or disagree)."""
    comma = re.search(r"(?<![\d.,])\d+,\d{1,2}(?![\d])|\d\.\d{3},\d", text) is not None
    point = re.search(r"(?<![\d.,])\d+\.\d{1,2}(?![\d])|\d,\d{3}\.\d", text) is not None
    if comma == point:
        return None
    return comma


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
