"""Exposure geography (ISO alpha-2) to the stored Comtrade reporter spellings.

UN Comtrade identifies reporters by M49 numerics (842 for the United States), while exposure
mappings, macro series and the UI use ISO 3166 alpha-2 codes, so a geography filter compared
directly against `trade_observation.reporter` never matches (`842` against `US`). The code
table is `steward.macro.geo` (owned by the macro stream); this module adds the reverse lookup
the readers need and the check for the `reporter_iso` / `partner_iso` columns added by
migration 20261005110000. Readers match `reporter_iso` when the column exists and the stored
reporter spellings otherwise, so rows written before that migration still match.
"""

from __future__ import annotations

import re

import asyncpg

from steward.macro.geo import (
    COMTRADE_ALIASES,
    ISO2_TO_ISO3,
    ISO2_TO_NUMERIC,
    ISO3_TO_ISO2,
    iso2_for_m49,
)

# Not a single country, but a Comtrade reporter an exposure may be mapped to.
AGGREGATES: dict[str, int] = {"EU": 97}


def to_iso(code: str | None) -> str | None:
    """ISO alpha-2 (or `EU`) of an M49 numeric, alpha-2 or alpha-3 code; None when the code is
    unknown or not one country (World, regions). Never guesses."""
    if code is None:
        return None
    text = code.strip().upper()
    if not text:
        return None
    if text.isdigit():
        if int(text) == 97:
            return "EU"
        iso = iso2_for_m49(text)
        return iso if iso is not None and len(iso) == 2 else None
    if text in AGGREGATES:
        return text
    if re.fullmatch(r"[A-Z]{2}", text):
        return text if text in ISO2_TO_NUMERIC else None
    if re.fullmatch(r"[A-Z]{3}", text):
        return ISO3_TO_ISO2.get(text)
    return None


def reporter_codes(geography: str) -> list[str]:
    """Every spelling under which `trade_observation.reporter` can hold this geography: the
    ISO code, its M49 / Comtrade numeric codes (padded and unpadded) and the text as given.
    An unknown geography matches only itself."""
    given = geography.strip()
    iso = to_iso(given)
    codes: set[str] = {given, given.upper()}
    if iso is not None:
        codes.add(iso)
        numbers: list[int] = [n for code, n in ((iso, ISO2_TO_NUMERIC.get(iso)),) if n is not None]
        numbers.extend(n for n, i in COMTRADE_ALIASES.items() if i == iso)
        if iso in AGGREGATES:
            numbers.append(AGGREGATES[iso])
        for n in numbers:
            codes.update((str(n), f"{n:03d}"))
        alpha3 = ISO2_TO_ISO3.get(iso)
        if alpha3:
            codes.add(alpha3)
    return sorted(codes)


async def has_column(pool: asyncpg.Pool, table: str, column: str) -> bool:
    return bool(
        await pool.fetchval(
            "select exists (select 1 from information_schema.columns"
            " where table_schema = 'public' and table_name = $1 and column_name = $2)",
            table,
            column,
        )
    )


async def has_iso_columns(pool: asyncpg.Pool) -> bool:
    """Whether `trade_observation` has `reporter_iso` (added by the macro migration)."""
    return await has_column(pool, "trade_observation", "reporter_iso")


def reporter_match_sql(has_iso: bool, param: int, alias: str = "") -> str:
    """A WHERE fragment matching the reporter against a list of `reporter_codes` bound as
    `$param` (as text[]), and `reporter_iso` as well when that column exists."""
    prefix = f"{alias}." if alias else ""
    clause = f"{prefix}reporter = any(${param}::text[])"
    if has_iso:
        clause = f"({clause} or {prefix}reporter_iso = any(${param}::text[]))"
    return clause
