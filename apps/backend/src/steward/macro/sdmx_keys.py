"""SDMX request keys for the OECD and BIS APIs: pinning the area and validating the shape.

A wildcard area slot returns every country, which a single configured geography would then
mislabel (A5). `pin_key` fills the area slot with the configured country in the provider's own
code (BIS: ISO alpha-2; OECD: ISO alpha-3), after checking the key against the dataflow's
dimension order (`DOCUMENTED_DSD`). A flow that is not listed there is pinned only if the area
slot is the (empty) first one and is reported as `unvalidated`.

Every flow below was verified live on 2026-10-03: the dimension order from the provider's
structure endpoint (BIS `stats.bis.org/api/v2/structure/dataflow/BIS/<id>/+?references=all`,
OECD `sdmx.oecd.org/public/rest/v1/dataflow/<agency>/<id>/<version>?references=all`) and the key
by a real data request that returned the intended series
(docs/data/macro-release-calendar.md, "SDMX dimension orders"). The order is NOT the same across
flows: BIS puts FREQ first, OECD most often REF_AREA first but `DSD_RHPI` puts REF_AREA_TYPE
first, so the area slot is found from the dimension list, never assumed.

BIS serves its SDMX-JSON only as version 1.0.0 from `/data/dataflow/<agency>/<id>/<version>/<key>`
(`data_request`): the version 2.0.0 Accept header answers 406 and the `<agency>,<id>,<version>`
path form answers 500.
"""

from __future__ import annotations

from dataclasses import dataclass

from steward.macro.geo import ISO2_TO_ISO3

# Flow id (agency,id, version left out) -> dimensions in key order.
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
    "BIS,WS_LONG_CPI": ("FREQ", "REF_AREA", "UNIT_MEASURE"),
    "BIS,WS_LBS_D_PUB": (
        "FREQ",
        "L_MEASURE",
        "L_POSITION",
        "L_INSTR",
        "L_DENOM",
        "L_CURR_TYPE",
        "L_PARENT_CTY",
        "L_REP_BANK_TYPE",
        "L_REP_CTY",
        "L_CP_SECTOR",
        "L_CP_COUNTRY",
        "L_POS_TYPE",
    ),
    "BIS,WS_DEBT_SEC2_PUB": (
        "FREQ",
        "ISSUER_RES",
        "ISSUER_NAT",
        "ISSUER_BUS_IMM",
        "ISSUER_BUS_ULT",
        "MARKET",
        "ISSUE_TYPE",
        "ISSUE_CUR_GROUP",
        "ISSUE_CUR",
        "ISSUE_OR_MAT",
        "ISSUE_RE_MAT",
        "ISSUE_RATE",
        "ISSUE_RISK",
        "ISSUE_COL",
        "MEASURE",
    ),
    "BIS,WS_GLI": (
        "FREQ",
        "CURR_DENOM",
        "BORROWERS_CTY",
        "BORROWERS_SECTOR",
        "LENDERS_SECTOR",
        "L_POS_TYPE",
        "L_INSTR",
        "UNIT_MEASURE",
    ),
    "OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL": (
        "REF_AREA",
        "FREQ",
        "METHODOLOGY",
        "MEASURE",
        "UNIT_MEASURE",
        "EXPENDITURE",
        "ADJUSTMENT",
        "TRANSFORMATION",
    ),
    "OECD.SDD.TPS,DSD_LFS@DF_IALFS_UNE_M": (
        "REF_AREA",
        "MEASURE",
        "UNIT_MEASURE",
        "TRANSFORMATION",
        "ADJUSTMENT",
        "SEX",
        "AGE",
        "ACTIVITY",
        "FREQ",
    ),
    "OECD.SDD.TPS,DSD_PDB@DF_PDB": (
        "REF_AREA",
        "FREQ",
        "MEASURE",
        "ACTIVITY",
        "UNIT_MEASURE",
        "PRICE_BASE",
        "TRANSFORMATION",
        "ASSET_CODE",
        "CONVERSION_TYPE",
    ),
    "OECD.SDD.TPS,DSD_EAR@DF_HOU_EAR": (
        "REF_AREA",
        "MEASURE",
        "UNIT_MEASURE",
        "ADJUSTMENT",
        "ACTIVITY",
        "SECTOR",
        "FREQ",
    ),
    "OECD.SDD.STES,DSD_STES@DF_CS": (
        "REF_AREA",
        "FREQ",
        "MEASURE",
        "UNIT_MEASURE",
        "ACTIVITY",
        "ADJUSTMENT",
        "TRANSFORMATION",
        "TIME_HORIZ",
        "METHODOLOGY",
    ),
    "OECD.SDD.STES,DSD_STES@DF_BTS": (
        "REF_AREA",
        "FREQ",
        "MEASURE",
        "UNIT_MEASURE",
        "ACTIVITY",
        "ADJUSTMENT",
        "TRANSFORMATION",
        "TIME_HORIZ",
        "METHODOLOGY",
    ),
    "OECD.SDD.TPS,DSD_RHPI@DF_RHPI_ALL": (
        "REF_AREA_TYPE",
        "REF_AREA",
        "FREQ",
        "MEASURE",
        "UNIT_MEASURE",
        "ADJUSTMENT",
        "TRANSFORMATION",
        "VINTAGE",
        "DWELLINGS",
    ),
}
GEO_DIMENSION_IDS = (
    "REF_AREA",
    "BORROWERS_CTY",
    "L_CP_COUNTRY",
    "ISSUER_RES",
    "COUNTRY",
    "LOCATION",
)


@dataclass(frozen=True, slots=True)
class Correction:
    """A configured (flow, key) that never addressed what its label says, and its replacement.
    `unit` / `definition` replace the configured text when the old text no longer fits."""

    flow: str
    key: str  # '{area}' marks the area slot
    unit: str | None = None
    definition: str | None = None


# Defaults shipped with earlier configs whose flow or key does not match the provider's data
# structure (a stored config keeps the old text): (flow, key) -> the verified replacement.
KEY_CORRECTIONS: dict[tuple[str, str], Correction] = {
    # area slot was written first (OECD order); BIS WS_TC starts with FREQ
    ("BIS,WS_TC,2.0", ".Q.P.A.M.770.A"): Correction("BIS,WS_TC,2.0", "Q.{area}.P.A.M.770.A"),
    # DSR_BORROWERS is P (private non-financial sector); "N" is not a code of this dimension
    ("BIS,WS_DSR,1.0", ".Q.P.N"): Correction("BIS,WS_DSR,1.0", "Q.{area}.P"),
    # the residential property price index is dataflow WS_SPP (selected residential property
    # prices), not the long CPI flow: that flow holds consumer prices. Q.<area>.R.628 = real
    # index, 2010 = 100 (VALUE R, UNIT_MEASURE 628).
    ("BIS,WS_LONG_CPI,1.0", ".Q.R"): Correction(
        "BIS,WS_SPP,1.0",
        "Q.{area}.R.628",
        unit="index, 2010 = 100 (real)",
        definition="Real residential property price index, 2010 = 100 (BIS selected "
        "residential property prices, WS_SPP)",
    ),
    # FREQ.L_MEASURE.L_POSITION.L_INSTR.L_DENOM.L_CURR_TYPE.L_PARENT_CTY.L_REP_BANK_TYPE.
    # L_REP_CTY.L_CP_SECTOR.L_CP_COUNTRY.L_POS_TYPE: stocks of total cross-border claims of all
    # reporting banks, all currencies and sectors, on the area's residents
    ("BIS,WS_LBS_D_PUB,1.0", ".Q.S.A.TO1"): Correction(
        "BIS,WS_LBS_D_PUB,1.0",
        "Q.S.C.A.TO1.A.5J.A.5A.A.{area}.N",
        definition="Cross-border claims (stocks) of all BIS reporting banks on residents of the "
        "area, all instruments, currencies and sectors; millions of USD",
    ),
    # amounts outstanding of international debt securities issued by residents of the area
    # (all issuers, all currencies, all maturities)
    ("BIS,WS_DEBT_SEC2_PUB,1.0", ".Q.C.1"): Correction(
        "BIS,WS_DEBT_SEC2_PUB,1.0",
        "Q.{area}.3P.1.1.C.A.A.TO1.A.A.A.A.A.I",
        definition="International debt securities outstanding (BIS-compiled), issued by "
        "residents of the area, all sectors, currencies and maturities; millions of USD",
    ),
    # USD-denominated credit (loans and debt securities) to non-financial sector borrowers in
    # the area, from all sectors of lenders; millions of USD
    ("BIS,WS_GLI,1.0", ".Q.US"): Correction(
        "BIS,WS_GLI,1.0",
        "Q.USD.{area}.P.A.A.B.USD",
        definition="Global liquidity indicator: USD-denominated credit (loans and debt "
        "securities) to non-financial sector borrowers in the area; millions of USD",
    ),
    ("OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0", ".M.CPI.PC_CHGPP"): Correction(
        "OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0",
        "{area}.M.N.CPI.PA._T.N.GY",
        unit="% y/y",
        definition="Consumer price index, all items, national methodology, not seasonally "
        "adjusted, annual growth rate (OECD DF_PRICES_ALL)",
    ),
    ("OECD.SDD.TPS,DSD_LFS@DF_IALFS_UNE_M,1.0", ".M.Y_GE15.PS._T._T"): Correction(
        "OECD.SDD.TPS,DSD_LFS@DF_IALFS_UNE_M,1.0",
        "{area}.UNE_LF_M.PT_LF_SUB._Z.Y._T.Y_GE15._Z.M",
        unit="% of labour force",
        definition="Monthly unemployment rate, 15 years and over, both sexes, seasonally "
        "adjusted (OECD DF_IALFS_UNE_M)",
    ),
    ("OECD.SDD.PDB,DSD_PDB@DF_PDB_LV,", ".A...."): Correction(
        "OECD.SDD.TPS,DSD_PDB@DF_PDB,2.0",
        "{area}.A.GDPHRS._T.USD_PPP_H.LR.N._Z.PPP",
        unit="USD PPP per hour (constant prices)",
        definition="GDP per hour worked, total economy, USD at constant prices and PPPs "
        "(rebased) (OECD productivity database DF_PDB)",
    ),
    ("OECD.SDD.TPS,DSD_EARNINGS@DF_EARNINGS,", ".Q...."): Correction(
        "OECD.SDD.TPS,DSD_EAR@DF_HOU_EAR,1.0",
        "{area}.EAR.IX.Y._Z.S1D.Q",
        unit="index",
        definition="Hourly earnings index, private sector, all activities, seasonally adjusted "
        "(OECD DF_HOU_EAR)",
    ),
    ("OECD.SDD.STES,DSD_STES@DF_CLI,", ".M.CSCICP03"): Correction(
        "OECD.SDD.STES,DSD_STES@DF_CS,4.0",
        "{area}.M.CCICP.PB._Z.Y._Z._Z.N",
        unit="percentage balance",
        definition="Composite consumer confidence indicator, seasonally adjusted, national "
        "methodology (OECD DF_CS)",
    ),
    ("OECD.SDD.STES,DSD_STES@DF_CLI,", ".M.BSCICP03"): Correction(
        "OECD.SDD.STES,DSD_STES@DF_BTS,4.0",
        "{area}.M.BCICP.PB.C.Y._Z._Z.N",
        unit="percentage balance",
        definition="Composite business confidence indicator (manufacturing), seasonally "
        "adjusted, national methodology (OECD DF_BTS)",
    ),
    ("OECD.SDD.TPS,DSD_RHPI@DF_RHPI,", ".Q.RHPI"): Correction(
        "OECD.SDD.TPS,DSD_RHPI@DF_RHPI_ALL,1.1",
        "COU.{area}.Q.RHPI.IX.N._Z.EXISTING.SINGLE_F",
        unit="index",
        definition="House price index, existing single-family dwellings, not seasonally "
        "adjusted (OECD DF_RHPI_ALL)",
    ),
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
    unit: str | None = None  # replaces the configured unit when a correction changed the series
    definition: str | None = None


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
    unit = definition = None
    if correction is not None:
        flow, series_key = correction.flow, correction.key.replace("{area}", code)
        unit, definition = correction.unit, correction.definition
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
    return PinnedKey(flow, ".".join(slots), code, validated, corrected, unit, definition)


BIS_ACCEPT = "application/vnd.sdmx.data+json;version=1.0.0"
OECD_ACCEPT = "application/vnd.sdmx.data+json;version=2.0.0"


def data_request(provider: str, base_url: str, flow: str, key: str) -> tuple[str, str]:
    """(URL, Accept header) of a data request, as each provider's live API accepts it.

    OECD: `<base>/data/<agency>,<id>,<version>/<key>` with SDMX-JSON 2.0.0. BIS:
    `<base>/data/dataflow/<agency>/<id>/<version>/<key>` with SDMX-JSON 1.0.0 only (verified
    2026-10-03: version 2.0.0 answers 406, the comma path answers 500)."""
    base = base_url.rstrip("/")
    if provider == "bis":
        parts = flow.split(",")
        if len(parts) != 3 or not all(parts):
            raise SdmxKeyError(f"BIS flow {flow!r} must be 'agency,id,version'")
        return f"{base}/data/dataflow/{parts[0]}/{parts[1]}/{parts[2]}/{key}", BIS_ACCEPT
    return f"{base}/data/{flow}/{key}", OECD_ACCEPT
