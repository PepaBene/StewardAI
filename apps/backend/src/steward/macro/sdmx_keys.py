"""SDMX request keys for the OECD and BIS APIs: pinning the area and validating the shape.

A wildcard area slot returns every country, which a single configured geography would then
mislabel (A5). `pin_key` fills the area slot with the configured country in the provider's own
code (BIS: ISO alpha-2; OECD: ISO alpha-3), after checking the key against the dataflow's
documented dimension order where it is known. Flows whose dimension order is not listed in
`DOCUMENTED_DSD` are pinned only if the area slot is the (empty) first one, as in every OECD
dataflow; they are reported as `unvalidated` so operators know the key was not checked.

Verified offline against the published BIS data structure definitions only for the flows in
`DOCUMENTED_DSD`; nothing here has been exercised against the live APIs.
"""

from __future__ import annotations

from dataclasses import dataclass

from steward.macro.geo import ISO2_TO_ISO3

# Flow id (agency,id,version without version) -> dimensions in key order.
DOCUMENTED_DSD: dict[str, tuple[str, ...]] = {
    "BIS,WS_TC": (
        "FREQ",
        "BORROWERS_CTY",
        "TC_BORROWERS",
        "TC_LENDERS",
        "VALUATION",
        "UNIT_TYPE",
        "TC_ADJUST",
    ),
    "BIS,WS_DSR": ("FREQ", "BORROWERS_CTY", "DSR_BORROWERS"),
    "BIS,WS_SPP": ("FREQ", "REF_AREA", "VALUE", "UNIT_MEASURE"),
    "BIS,WS_LONG_CPI": ("FREQ", "REF_AREA"),
}
GEO_DIMENSION_IDS = ("REF_AREA", "BORROWERS_CTY", "COUNTRY", "LOCATION")

# Defaults shipped with earlier configs whose flow or key does not match the documented
# structure: (flow, key) -> (flow, key template with {area}).
KEY_CORRECTIONS: dict[tuple[str, str], tuple[str, str]] = {
    # area slot was written first (OECD order); BIS WS_TC starts with FREQ
    ("BIS,WS_TC,2.0", ".Q.P.A.M.770.A"): ("BIS,WS_TC,2.0", "Q.{area}.P.A.M.770.A"),
    # DSR_BORROWERS is P (private non-financial sector); "N" is not a code of this dimension
    ("BIS,WS_DSR,1.0", ".Q.P.N"): ("BIS,WS_DSR,1.0", "Q.{area}.P"),
    # the residential property price index is dataflow WS_SPP, not the long CPI flow
    ("BIS,WS_LONG_CPI,1.0", ".Q.R"): ("BIS,WS_SPP,1.0", "Q.{area}.R.628"),
}


class SdmxKeyError(ValueError):
    """The configured flow/key cannot be pinned to the configured geography."""


@dataclass(frozen=True, slots=True)
class PinnedKey:
    flow: str
    key: str
    area_code: str  # the provider's code for the configured geography
    validated: bool
    corrected: bool = False


def area_code(provider: str, geography: str) -> str:
    """The provider's own code for an ISO alpha-2 country (BIS alpha-2, OECD alpha-3).
    Aggregates (EA20, EU27_2020, ...) pass through unchanged."""
    if provider == "oecd" and len(geography) == 2:
        try:
            return ISO2_TO_ISO3[geography]
        except KeyError as exc:
            raise SdmxKeyError(
                f"no OECD (ISO alpha-3) code known for {geography}; add it to "
                "steward.macro.geo.ISO2_TO_ISO3 before configuring it"
            ) from exc
    return geography


def _flow_id(flow: str) -> str:
    return ",".join(flow.split(",")[:2])


def pin_key(provider: str, flow: str, series_key: str, geography: str) -> PinnedKey:
    code = area_code(provider, geography)
    corrected = False
    correction = KEY_CORRECTIONS.get((flow, series_key))
    if correction is not None:
        flow, series_key = correction[0], correction[1].replace("{area}", code)
        corrected = True
    slots = series_key.split(".")
    dsd = DOCUMENTED_DSD.get(_flow_id(flow))
    if dsd is not None:
        if len(slots) != len(dsd):
            raise SdmxKeyError(
                f"{flow}: key {series_key!r} has {len(slots)} dimensions, the data structure "
                f"has {len(dsd)} ({'.'.join(dsd)})"
            )
        position = next(i for i, d in enumerate(dsd) if d in GEO_DIMENSION_IDS)
        validated = True
    else:
        position, validated = 0, False  # OECD dataflows put REF_AREA first
    if slots[position] in ("", code):
        slots[position] = code
    else:
        raise SdmxKeyError(
            f"{flow}: area slot {position} of {series_key!r} is {slots[position]!r}, "
            f"expected {code!r} for {geography}"
        )
    return PinnedKey(flow, ".".join(slots), code, validated, corrected)
