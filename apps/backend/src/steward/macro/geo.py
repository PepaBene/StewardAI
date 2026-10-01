"""Country codes for trade and SDMX data.

UN Comtrade identifies reporters and partners by UN M49 numbers; exposure mappings and the
macro series use ISO 3166-1 alpha-2. `M49_TO_ISO2` is the bridge (A18/B14). Most M49 numbers
equal the ISO 3166-1 numeric code; Comtrade deviates for a handful of large economies, which
are listed under `COMTRADE_ALIASES`. Codes that are not a single country (regions, "areas nes",
the historical Belgium-Luxembourg) map to nothing rather than to a guess.
"""

from __future__ import annotations

WORLD_ISO = "W00"  # Comtrade's own ISO3 label for partner 0 ("World"); never a country

# ISO 3166-1 alpha-2 -> numeric (also the M49 code of the country).
ISO2_TO_NUMERIC: dict[str, int] = {
    "AF": 4, "AL": 8, "DZ": 12, "AS": 16, "AD": 20, "AO": 24, "AG": 28, "AZ": 31, "AR": 32,
    "AU": 36, "AT": 40, "BS": 44, "BH": 48, "BD": 50, "AM": 51, "BB": 52, "BE": 56, "BM": 60,
    "BT": 64, "BO": 68, "BA": 70, "BW": 72, "BR": 76, "BZ": 84, "SB": 90, "VG": 92, "BN": 96,
    "BG": 100, "MM": 104, "BI": 108, "BY": 112, "KH": 116, "CM": 120, "CA": 124, "CV": 132,
    "KY": 136, "CF": 140, "LK": 144, "TD": 148, "CL": 152, "CN": 156, "TW": 158, "CO": 170,
    "KM": 174, "YT": 175, "CG": 178, "CD": 180, "CK": 184, "CR": 188, "HR": 191, "CU": 192,
    "CY": 196, "CZ": 203, "BJ": 204, "DK": 208, "DM": 212, "DO": 214, "EC": 218, "SV": 222,
    "GQ": 226, "ET": 231, "ER": 232, "EE": 233, "FO": 234, "FK": 238, "FJ": 242, "FI": 246,
    "FR": 250, "GF": 254, "PF": 258, "DJ": 262, "GA": 266, "GE": 268, "GM": 270, "PS": 275,
    "DE": 276, "GH": 288, "GI": 292, "KI": 296, "GR": 300, "GL": 304, "GD": 308, "GP": 312,
    "GU": 316, "GT": 320, "GN": 324, "GY": 328, "HT": 332, "HN": 340, "HK": 344, "HU": 348,
    "IS": 352, "IN": 356, "ID": 360, "IR": 364, "IQ": 368, "IE": 372, "IL": 376, "IT": 380,
    "CI": 384, "JM": 388, "JP": 392, "KZ": 398, "JO": 400, "KE": 404, "KP": 408, "KR": 410,
    "KW": 414, "KG": 417, "LA": 418, "LB": 422, "LS": 426, "LV": 428, "LR": 430, "LY": 434,
    "LI": 438, "LT": 440, "LU": 442, "MO": 446, "MG": 450, "MW": 454, "MY": 458, "MV": 462,
    "ML": 466, "MT": 470, "MQ": 474, "MR": 478, "MU": 480, "MX": 484, "MC": 492, "MN": 496,
    "MD": 498, "ME": 499, "MS": 500, "MA": 504, "MZ": 508, "OM": 512, "NA": 516, "NR": 520,
    "NP": 524, "NL": 528, "CW": 531, "AW": 533, "SX": 534, "NC": 540, "VU": 548, "NZ": 554,
    "NI": 558, "NE": 562, "NG": 566, "NU": 570, "NF": 574, "NO": 578, "MP": 580, "FM": 583,
    "MH": 584, "PW": 585, "PK": 586, "PA": 591, "PG": 598, "PY": 600, "PE": 604, "PH": 608,
    "PN": 612, "PL": 616, "PT": 620, "GW": 624, "TL": 626, "PR": 630, "QA": 634, "RE": 638,
    "RO": 642, "RU": 643, "RW": 646, "SH": 654, "KN": 659, "AI": 660, "LC": 662, "PM": 666,
    "VC": 670, "SM": 674, "ST": 678, "SA": 682, "SN": 686, "RS": 688, "SC": 690, "SL": 694,
    "SG": 702, "SK": 703, "VN": 704, "SI": 705, "SO": 706, "ZA": 710, "ZW": 716, "ES": 724,
    "SS": 728, "SD": 729, "SR": 740, "SZ": 748, "SE": 752, "CH": 756, "SY": 760, "TJ": 762,
    "TH": 764, "TG": 768, "TO": 776, "TT": 780, "AE": 784, "TN": 788, "TR": 792, "TM": 795,
    "TC": 796, "TV": 798, "UG": 800, "UA": 804, "MK": 807, "EG": 818, "GB": 826, "TZ": 834,
    "US": 840, "BF": 854, "UY": 858, "UZ": 860, "VE": 862, "WF": 876, "WS": 882, "YE": 887,
    "ZM": 894,
}  # fmt: skip

# Comtrade's own numbers where they differ from the ISO numeric code (the ISO number is also
# accepted, as older files and the test fixtures use it), and "Other Asia, nes" (490), which
# Comtrade reports for Taiwan.
COMTRADE_ALIASES: dict[int, str] = {
    251: "FR",
    381: "IT",
    579: "NO",
    757: "CH",
    842: "US",
    699: "IN",
    490: "TW",
}

M49_TO_ISO2: dict[int, str] = {n: iso for iso, n in ISO2_TO_NUMERIC.items()} | COMTRADE_ALIASES

# ISO 3166-1 alpha-3 for the economies the OECD API is queried with (OECD codes countries by
# alpha-3). A country outside this table cannot be pinned on an OECD request and the step
# says so instead of fetching every country.
ISO2_TO_ISO3: dict[str, str] = {
    "AU": "AUS", "AT": "AUT", "BE": "BEL", "BR": "BRA", "CA": "CAN", "CH": "CHE", "CL": "CHL",
    "CN": "CHN", "CO": "COL", "CR": "CRI", "CZ": "CZE", "DE": "DEU", "DK": "DNK", "EE": "EST",
    "ES": "ESP", "FI": "FIN", "FR": "FRA", "GB": "GBR", "GR": "GRC", "HU": "HUN", "ID": "IDN",
    "IE": "IRL", "IL": "ISR", "IN": "IND", "IS": "ISL", "IT": "ITA", "JP": "JPN", "KR": "KOR",
    "LT": "LTU", "LU": "LUX", "LV": "LVA", "MX": "MEX", "NL": "NLD", "NO": "NOR", "NZ": "NZL",
    "PL": "POL", "PT": "PRT", "RU": "RUS", "SA": "SAU", "SE": "SWE", "SI": "SVN", "SK": "SVK",
    "TR": "TUR", "US": "USA", "ZA": "ZAF",
}  # fmt: skip
ISO3_TO_ISO2: dict[str, str] = {v: k for k, v in ISO2_TO_ISO3.items()}


def iso2_for_m49(code: object) -> str | None:
    """ISO alpha-2 for a Comtrade/M49 reporter or partner code; `W00` for World (0); None for
    codes that are not one country."""
    try:
        number = int(str(code).strip())
    except ValueError:
        return None
    if number == 0:
        return WORLD_ISO
    return M49_TO_ISO2.get(number)


def is_world(partner: object, partner_iso: object = None) -> bool:
    return str(partner).strip() in ("0", WORLD_ISO, "WLD") or partner_iso == WORLD_ISO
