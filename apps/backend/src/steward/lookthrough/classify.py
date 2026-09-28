"""Static classification tables for look-through: countries (ISO code, name, region, main
currency) and the GICS-style sector a published sector label normalises to.

Everything here is a mapping of published labels, not data: a label nobody maps stays
unclassified (None) rather than being guessed. Regions and currencies derived from a country
are flagged as derived wherever they are shown.
"""

from dataclasses import dataclass

REGIONS = (
    "North America",
    "Europe",
    "Asia Pacific",
    "Latin America",
    "Middle East & Africa",
)


@dataclass(frozen=True)
class Country:
    code: str
    name: str
    region: str
    currency: str | None


_EUR = "EUR"
_NA, _EU, _AP, _LA, _MEA = REGIONS

# code, name, region, currency (the main listing currency of the country's exchanges).
_COUNTRIES: tuple[tuple[str, str, str, str | None], ...] = (
    ("US", "United States", _NA, "USD"),
    ("CA", "Canada", _NA, "CAD"),
    ("GB", "United Kingdom", _EU, "GBP"),
    ("IE", "Ireland", _EU, _EUR),
    ("FR", "France", _EU, _EUR),
    ("DE", "Germany", _EU, _EUR),
    ("NL", "Netherlands", _EU, _EUR),
    ("BE", "Belgium", _EU, _EUR),
    ("LU", "Luxembourg", _EU, _EUR),
    ("AT", "Austria", _EU, _EUR),
    ("IT", "Italy", _EU, _EUR),
    ("ES", "Spain", _EU, _EUR),
    ("PT", "Portugal", _EU, _EUR),
    ("FI", "Finland", _EU, _EUR),
    ("GR", "Greece", _EU, _EUR),
    ("CY", "Cyprus", _EU, _EUR),
    ("MT", "Malta", _EU, _EUR),
    ("SK", "Slovakia", _EU, _EUR),
    ("SI", "Slovenia", _EU, _EUR),
    ("EE", "Estonia", _EU, _EUR),
    ("LV", "Latvia", _EU, _EUR),
    ("LT", "Lithuania", _EU, _EUR),
    ("HR", "Croatia", _EU, _EUR),
    ("CH", "Switzerland", _EU, "CHF"),
    ("SE", "Sweden", _EU, "SEK"),
    ("NO", "Norway", _EU, "NOK"),
    ("DK", "Denmark", _EU, "DKK"),
    ("IS", "Iceland", _EU, "ISK"),
    ("PL", "Poland", _EU, "PLN"),
    ("CZ", "Czech Republic", _EU, "CZK"),
    ("HU", "Hungary", _EU, "HUF"),
    ("RO", "Romania", _EU, "RON"),
    ("TR", "Turkey", _EU, "TRY"),
    ("RU", "Russia", _EU, "RUB"),
    ("JE", "Jersey", _EU, "GBP"),
    ("GG", "Guernsey", _EU, "GBP"),
    ("IM", "Isle of Man", _EU, "GBP"),
    ("JP", "Japan", _AP, "JPY"),
    ("AU", "Australia", _AP, "AUD"),
    ("NZ", "New Zealand", _AP, "NZD"),
    ("HK", "Hong Kong", _AP, "HKD"),
    ("MO", "Macau", _AP, "HKD"),
    ("SG", "Singapore", _AP, "SGD"),
    ("CN", "China", _AP, "CNY"),
    ("TW", "Taiwan", _AP, "TWD"),
    ("KR", "South Korea", _AP, "KRW"),
    ("IN", "India", _AP, "INR"),
    ("ID", "Indonesia", _AP, "IDR"),
    ("MY", "Malaysia", _AP, "MYR"),
    ("TH", "Thailand", _AP, "THB"),
    ("PH", "Philippines", _AP, "PHP"),
    ("PK", "Pakistan", _AP, "PKR"),
    ("VN", "Vietnam", _AP, "VND"),
    ("BR", "Brazil", _LA, "BRL"),
    ("MX", "Mexico", _LA, "MXN"),
    ("CL", "Chile", _LA, "CLP"),
    ("CO", "Colombia", _LA, "COP"),
    ("PE", "Peru", _LA, "PEN"),
    ("AR", "Argentina", _LA, "ARS"),
    ("UY", "Uruguay", _LA, "UYU"),
    ("PR", "Puerto Rico", _LA, "USD"),
    ("PA", "Panama", _LA, "USD"),
    ("BM", "Bermuda", _NA, None),
    ("KY", "Cayman Islands", _NA, None),
    ("CW", "Curacao", _LA, None),
    ("IL", "Israel", _MEA, "ILS"),
    ("SA", "Saudi Arabia", _MEA, "SAR"),
    ("AE", "United Arab Emirates", _MEA, "AED"),
    ("QA", "Qatar", _MEA, "QAR"),
    ("KW", "Kuwait", _MEA, "KWD"),
    ("BH", "Bahrain", _MEA, "BHD"),
    ("OM", "Oman", _MEA, "OMR"),
    ("ZA", "South Africa", _MEA, "ZAR"),
    ("EG", "Egypt", _MEA, "EGP"),
    ("MA", "Morocco", _MEA, "MAD"),
    ("NG", "Nigeria", _MEA, "NGN"),
    ("KE", "Kenya", _MEA, "KES"),
)

COUNTRIES: dict[str, Country] = {c[0]: Country(*c) for c in _COUNTRIES}

# Other spellings the issuers use for a country name (lower case).
_NAME_ALIASES: dict[str, str] = {
    "usa": "US",
    "united states of america": "US",
    "uk": "GB",
    "great britain": "GB",
    "korea": "KR",
    "korea (south)": "KR",
    "korea, republic of": "KR",
    "republic of korea": "KR",
    "czechia": "CZ",
    "czech rep.": "CZ",
    "turkiye": "TR",
    "türkiye": "TR",
    "russian federation": "RU",
    "taiwan, province of china": "TW",
    "china (mainland)": "CN",
    "hong kong sar": "HK",
    "hong kong, china": "HK",
    "macao": "MO",
    "curaçao": "CW",
    "uae": "AE",
    "viet nam": "VN",
}

_BY_NAME: dict[str, str] = {c.name.lower(): c.code for c in COUNTRIES.values()} | _NAME_ALIASES


def country_code(value: str | None) -> str | None:
    """ISO code for a country code or name as an issuer publishes it; None when unknown
    (including pseudo-countries such as "European Union" on a cash line)."""
    if not value:
        return None
    text = value.strip()
    if len(text) == 2 and text.upper() in COUNTRIES:
        return text.upper()
    return _BY_NAME.get(text.lower())


def country_name(code: str | None) -> str | None:
    if code is None:
        return None
    country = COUNTRIES.get(code)
    return country.name if country else code


def region_of(code: str | None) -> str | None:
    """Region derived from the country (the same map for every source)."""
    country = COUNTRIES.get(code or "")
    return country.region if country else None


def currency_of(code: str | None) -> str | None:
    """Main listing currency of a country; None when a country has no single one."""
    country = COUNTRIES.get(code or "")
    return country.currency if country else None


# GICS sectors (the 2023 structure), in their official names.
GICS_SECTORS = (
    "Information Technology",
    "Financials",
    "Health Care",
    "Consumer Discretionary",
    "Communication Services",
    "Industrials",
    "Consumer Staples",
    "Energy",
    "Utilities",
    "Materials",
    "Real Estate",
)

_SECTOR_ALIASES: dict[str, str] = {
    # iShares' short labels.
    "communication": "Communication Services",
    "communications": "Communication Services",
    "telecommunication services": "Communication Services",
    "telecommunications": "Communication Services",
    "it": "Information Technology",
    "information tech": "Information Technology",
    "technology": "Information Technology",
    "healthcare": "Health Care",
    "health care": "Health Care",
    "consumer discretionary": "Consumer Discretionary",
    "consumer staples": "Consumer Staples",
    "basic materials": "Materials",
    "real estate": "Real Estate",
    # Free-form labels an owner uses in the strategy's classifications.
    "semiconductors": "Information Technology",
    "semis": "Information Technology",
    "software": "Information Technology",
    "hardware": "Information Technology",
    "banks": "Financials",
    "bank": "Financials",
    "banking": "Financials",
    "insurance": "Financials",
    "pharma": "Health Care",
    "pharmaceuticals": "Health Care",
    "biotech": "Health Care",
    "biotechnology": "Health Care",
    "oil & gas": "Energy",
    "oil and gas": "Energy",
    "reits": "Real Estate",
    "reit": "Real Estate",
    "media": "Communication Services",
    "telecom": "Communication Services",
    "chemicals": "Materials",
    "mining": "Materials",
}

_BY_SECTOR: dict[str, str] = {s.lower(): s for s in GICS_SECTORS} | _SECTOR_ALIASES


def normalize_sector(value: str | None) -> str | None:
    """The GICS sector a published label means; None for anything that is not one
    (e.g. "Cash and/or Derivatives", or an ambiguous label such as "Internet")."""
    if not value:
        return None
    return _BY_SECTOR.get(value.strip().lower())
