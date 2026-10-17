"""Each holding's sector and country for the strategy's caps (35% per sector, 70% per country),
when the owner has not classified it in the strategy.

Without this every holding counted as "Unclassified": that one bucket held 57% of the
portfolio, so the sector cap read as permanently breached, every purchase deepened it and the
deposit allocator could buy nothing. Sources, first that knows wins:

- sector: the GICS sector an ETF holdings file lists the stock under (iShares files); else the
  stock's Wikidata industries mapped to a GICS sector; a fund is "Commodities" or "Multi-sector
  fund";
- country: the country an ETF holdings file lists; else the issuer's country from its LEI
  record (GLEIF); a fund takes the country holding 60% or more of its own holdings, else
  "Global".

An owner's classification always wins. Nothing here is guessed from a name or a ticker.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from typing import Any

import asyncpg

from steward.config.strategy import StrategyConfig
from steward.portfolio.risk_tier import instrument_is_fund

log = logging.getLogger(__name__)

MULTI_SECTOR_FUND = "Multi-sector fund"
COMMODITIES = "Commodities"
GLOBAL = "Global"
FUND_COUNTRY_SHARE = 0.6

# Wikidata industry (lower case, substring) -> GICS sector. First match in this order wins, so
# the more specific phrase comes first ("health insurance" before "insurance").
INDUSTRY_SECTORS: tuple[tuple[str, str], ...] = (
    ("health insurance", "Health Care"),
    ("pharmaceutical", "Health Care"),
    ("biotechnology", "Health Care"),
    ("medical", "Health Care"),
    ("semiconductor", "Information Technology"),
    ("software", "Information Technology"),
    ("quantum computing", "Information Technology"),
    ("networking hardware", "Information Technology"),
    ("computer", "Information Technology"),
    ("streaming media", "Communication Services"),
    ("social network", "Communication Services"),
    ("financial services", "Financials"),
    ("bank", "Financials"),
    ("insurance", "Financials"),
    ("defence", "Industrials"),
    ("defense", "Industrials"),
    ("weapons", "Industrials"),
    ("aerospace", "Industrials"),
    ("aircraft", "Industrials"),
    ("industrial manufacturing", "Industrials"),
    ("beverage", "Consumer Staples"),
    ("tobacco", "Consumer Staples"),
    ("food", "Consumer Staples"),
    ("petroleum", "Energy"),
    ("oil and gas", "Energy"),
    ("mining", "Materials"),
    ("chemical", "Materials"),
    ("electric utility", "Utilities"),
    ("real estate", "Real Estate"),
)

COMMODITY_WORDS = ("commodit", "brent", "crude", "gold", "silver", "oil")


def sector_from_industries(industries: Iterable[str]) -> str | None:
    words = [w.lower() for w in industries if isinstance(w, str)]
    for phrase, sector in INDUSTRY_SECTORS:
        if any(phrase in w for w in words):
            return sector
    return None


def fund_sector(name: str | None) -> str:
    text = (name or "").lower()
    return COMMODITIES if any(w in text for w in COMMODITY_WORDS) else MULTI_SECTOR_FUND


def fund_country(weights: Mapping[str, float], sector: str) -> str:
    """The country holding most of a fund, when it holds at least `FUND_COUNTRY_SHARE` of it;
    a commodity fund's holdings are its collateral, not its exposure, so it is global."""
    total = sum(w for w in weights.values() if w > 0)
    if sector == COMMODITIES or total <= 0:
        return GLOBAL
    country, weight = max(weights.items(), key=lambda kv: kv[1])
    return country if weight / total >= FUND_COUNTRY_SHARE else GLOBAL


def _industries(raw: Any) -> list[str]:
    if raw is None:
        return []
    data = json.loads(raw) if isinstance(raw, str) else raw
    if isinstance(data, str):  # stored as a JSON string holding the object
        data = json.loads(data)
    found = data.get("industry") if isinstance(data, dict) else None
    return [w for w in found or [] if isinstance(w, str)]


async def derived_classes(pool: asyncpg.Pool) -> dict[str, dict[str, str]]:
    """{ticker: {"sector": ..., "geography": ...}} for every instrument the sources know."""
    rows = await pool.fetch(
        """select i.t212_ticker, i.isin, i.name, i.instrument_type, i.security_type,
                  i.home_country, i.wikidata, s.country as issuer_country,
                  e.sector as file_sector, e.country as file_country
             from public.instrument i
             left join public.issuer s on s.lei = i.issuer_lei and s.valid_to is null
             left join lateral (
                  select h.sector, h.country from public.etf_holding h
                    join public.etf_snapshot n on n.id = h.snapshot_id
                   where h.isin = i.isin and h.sector is not null
                   order by n.as_of desc, h.weight desc nulls last limit 1) e on true"""
    )
    funds = await pool.fetch(
        """select n.fund_isin, h.country, sum(h.weight) as weight
             from public.etf_holding h
             join public.etf_snapshot n on n.id = h.snapshot_id
            where n.id = (select m.id from public.etf_snapshot m where m.fund_isin = n.fund_isin
                           order by m.as_of desc, m.id desc limit 1)
              and h.country is not null and h.weight is not null
            group by 1, 2"""
    )
    by_fund: dict[str, dict[str, float]] = {}
    for f in funds:
        by_fund.setdefault(f["fund_isin"], {})[f["country"]] = float(f["weight"])
    out: dict[str, dict[str, str]] = {}
    for r in rows:
        found: dict[str, str] = {}
        if instrument_is_fund(r["instrument_type"], r["security_type"]):
            found["sector"] = fund_sector(r["name"])
            found["geography"] = fund_country(by_fund.get(r["isin"], {}), found["sector"])
        else:
            sector = r["file_sector"] or sector_from_industries(_industries(r["wikidata"]))
            country = r["file_country"] or r["issuer_country"]
            if sector:
                found["sector"] = sector
            if country:
                found["geography"] = country
        if found:
            out[r["t212_ticker"]] = found
    return out


def merge_classes(
    strategy: StrategyConfig, derived: Mapping[str, Mapping[str, str]]
) -> StrategyConfig:
    """The strategy with derived sectors and countries filled in where the owner set none."""
    merged = {t: dict(b) for t, b in strategy.classifications.items()}
    changed = False
    for ticker, classes in derived.items():
        bucket = merged.setdefault(ticker, {})
        for dimension, value in classes.items():
            if not bucket.get(dimension):
                bucket[dimension] = value
                changed = True
    return strategy.model_copy(update={"classifications": merged}) if changed else strategy


async def with_derived_classes(pool: asyncpg.Pool, strategy: StrategyConfig) -> StrategyConfig:
    """Read paths use this; it never fails a page."""
    try:
        derived = await derived_classes(pool)
    except Exception as exc:
        log.warning("could not derive sectors and countries: %s", exc)
        return strategy
    return merge_classes(strategy, derived)
