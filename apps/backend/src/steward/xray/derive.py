"""X-Ray inputs computed from fund holdings the app already stores.

Two of the owner-entered inputs can be filled from real, dated data instead of by hand:

- *Index weights*: a fund that tracks an index (the configured benchmark proxies are exactly
  that) publishes its holdings; its equity rows, renormalised to 100%, stand in for the index's
  constituents. They are the fund's holdings as of the snapshot date, not the index provider's
  own file, and the entry says so.
- *Regional splits*: a region such as "Europe" is spread over countries the way a fund you name
  for that region spreads its own equity (for example a Europe ETF).

Each derived row records the fund it came from, so a refresh of that fund's holdings recomputes
it. A row the owner enters by hand for the same name is newer and is never overwritten.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlencode

import asyncpg

from steward.config.jobs import JobsConfig, LookThroughFund
from steward.config.store import load_active
from steward.lookthrough.classify import country_code
from steward.lookthrough.read import SnapshotIn, load_snapshots
from steward.sources.etf_issuers import ISHARES_API, VANGUARD_GRAPHQL
from steward.xray import owner_input
from steward.xray.owner_input import (
    IndexConstituent,
    IndexWeightsRequest,
    OwnerInput,
    RegionSplitRequest,
)

MAX_CONSTITUENTS = 2000
ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


class DeriveError(Exception):
    """The fund cannot supply this input; the message says why."""


# Public fund pages, each opened and matched to its ISIN (7 Oct 2026). A Vanguard page needs its
# name slug, so a fund not listed here falls back to the data address the holdings came from.
ISHARES_PAGE = "https://www.ishares.com/uk/individual/en/products/{portfolio_id}"
VANGUARD_PAGES = {
    "9679": "https://www.vanguard.co.uk/professional/product/etf/equity/9679/"
    "ftse-all-world-ucits-etf-usd-accumulating",
}


def source_url(fund: LookThroughFund) -> str:
    """The public page of the fund whose stored holdings the input was computed from; the issuer
    data address when no page is known."""
    if fund.provider == "ishares" and fund.portfolio_id.isdigit():
        return ISHARES_PAGE.format(portfolio_id=fund.portfolio_id)
    if fund.provider == "vanguard" and fund.portfolio_id in VANGUARD_PAGES:
        return VANGUARD_PAGES[fund.portfolio_id]
    if fund.provider == "ishares":
        query = urlencode(
            {
                "appType": "PRODUCT_PAGE",
                "appSubType": "ISHARES",
                "targetSite": "ishares-uk",
                "locale": "en_GB",
                "portfolioId": fund.portfolio_id,
                "userType": "individual",
                "component": "fundDownloadV2",
            }
        )
        return f"{ISHARES_API}?{query}"
    return f"{VANGUARD_GRAPHQL}?{urlencode({'portId': fund.portfolio_id})}"


def _equity(snapshot: SnapshotIn) -> list[tuple[str, str | None, str | None, float, str | None]]:
    return [
        (r.name or r.isin or r.ticker or "?", r.isin, r.ticker, r.weight, r.country)
        for r in snapshot.rows
        if r.asset_class == "equity" and r.weight > 0
    ]


def index_request(fund: LookThroughFund, snapshot: SnapshotIn) -> IndexWeightsRequest:
    rows = _equity(snapshot)
    total = sum(w for _, _, _, w, _ in rows)
    if total <= 0:
        raise DeriveError(f"{fund.label} has no stored equity holdings")
    rows.sort(key=lambda r: -r[3])
    constituents = [
        IndexConstituent(
            name=name[:120],
            isin=isin if isin and ISIN.match(isin) else None,
            ticker=(ticker or None) and ticker[:40],
            weight=min(weight / total, 1.0),
        )
        for name, isin, ticker, weight, _ in rows[:MAX_CONSTITUENTS]
    ]
    return IndexWeightsRequest(
        label=fund.label,
        constituents=constituents,
        source_url=source_url(fund),
        as_of=snapshot.as_of,
        note=(
            f"Derived from the stored holdings of {fund.label} ({snapshot.source}), equity rows "
            "renormalised to 100%. This is the fund's holdings on that date, not the index "
            "provider's own file."
        ),
    )


def region_request(region: str, fund: LookThroughFund, snapshot: SnapshotIn) -> RegionSplitRequest:
    by_country: dict[str, float] = {}
    for _, _, _, weight, country in _equity(snapshot):
        code = country_code(country)
        if code:
            by_country[code] = by_country.get(code, 0.0) + weight
    total = sum(by_country.values())
    if total <= 0:
        raise DeriveError(f"{fund.label} has no stored equity holdings with a country")
    shares = {c: w / total for c, w in by_country.items() if w / total >= 0.0005}
    scale = sum(shares.values())
    return RegionSplitRequest(
        region=region,
        countries={c: s / scale for c, s in shares.items()},
        source_url=source_url(fund),
        as_of=snapshot.as_of,
        note=(
            f"Derived: {region} is spread over countries the way {fund.label} ({snapshot.source}) "
            "spreads its equity. Countries under 0.05% are left out and the rest rescaled."
        ),
    )


async def _fund(pool: asyncpg.Pool, isin: str) -> tuple[LookThroughFund, SnapshotIn]:
    stored = await load_active(pool, "jobs", JobsConfig)
    config = stored.body if stored else JobsConfig()
    fund = config.look_through.fund(isin)
    if fund is None:
        raise DeriveError("that fund is not one of the funds looked through (Jobs config)")
    snapshot = (await load_snapshots(pool, [isin])).get(isin)
    if snapshot is None:
        raise DeriveError(f"{fund.label} has no stored holdings yet")
    return fund, snapshot


async def _store(
    pool: asyncpg.Pool,
    owner_id: str,
    kind: owner_input.InputKind,
    key: str,
    payload: dict[str, Any],
    body: IndexWeightsRequest | RegionSplitRequest,
    fund_isin: str,
) -> tuple[OwnerInput, bool]:
    """Save a derived row unless the one in force already says the same (same fund, same day)."""
    current = {(i.kind, i.key): i for i in await owner_input.current(pool, owner_id, kind)}
    held = current.get((kind, key))
    if (
        held is not None
        and held.origin == "derived"
        and held.as_of == body.as_of
        and held.payload == payload
        and (held.derived_from or {}).get("fund_isin") == fund_isin
    ):
        return held, False
    saved = await owner_input.save(
        pool, owner_id, kind, key, payload, body, derived_from={"fund_isin": fund_isin}
    )
    return saved, True


async def derive_index_weights(
    pool: asyncpg.Pool, owner_id: str, fund_isin: str, label: str | None = None
) -> tuple[OwnerInput, bool]:
    fund, snapshot = await _fund(pool, fund_isin)
    body = index_request(fund, snapshot)
    name = (label or fund.label).strip()
    payload = {"constituents": [c.model_dump() for c in body.constituents]}
    return await _store(pool, owner_id, "index_weights", name, payload, body, fund_isin)


async def derive_region_split(
    pool: asyncpg.Pool, owner_id: str, region: str, fund_isin: str
) -> tuple[OwnerInput, bool]:
    fund, snapshot = await _fund(pool, fund_isin)
    body = region_request(region.strip(), fund, snapshot)
    return await _store(
        pool, owner_id, "region_split", region.strip(), {"countries": body.countries}, body,
        fund_isin,
    )  # fmt: skip


async def derive_benchmark_indexes(pool: asyncpg.Pool, owner_id: str) -> int:
    """Index weights for every configured benchmark proxy that has stored holdings. A proxy's
    label is the index's name."""
    stored = await load_active(pool, "jobs", JobsConfig)
    config = stored.body if stored else JobsConfig()
    made = 0
    for proxy in config.look_through.benchmark_proxies:
        try:
            _, changed = await derive_index_weights(pool, owner_id, proxy.fund_isin, proxy.label)
        except DeriveError:
            continue
        made += changed
    return made


async def refresh_all(pool: asyncpg.Pool) -> dict[str, int]:
    """Recompute every derived row still in force, for every owner, from the funds' newest stored
    holdings. Run after the look-through collection. A row that fails (the fund dropped from the
    config, holdings gone) is left as it was."""
    rows = await pool.fetch(
        "select distinct on (owner_id, kind, key) owner_id::text as owner_id, kind, key, origin,"
        " derived_from from public.xray_owner_input where not archived"
        " order by owner_id, kind, key, created_at desc, id desc"
    )
    counts = {"checked": 0, "updated": 0, "failed": 0}
    for row in rows:
        if row["origin"] != "derived":
            continue  # the owner's own newer entry wins
        counts["checked"] += 1

        raw = row["derived_from"]
        source = json.loads(raw) if isinstance(raw, str) else (raw or {})
        isin = str(source.get("fund_isin") or "")
        try:
            if row["kind"] == "index_weights":
                _, changed = await derive_index_weights(pool, row["owner_id"], isin, row["key"])
            elif row["kind"] == "region_split":
                _, changed = await derive_region_split(pool, row["owner_id"], row["key"], isin)
            else:
                continue
        except (DeriveError, ValueError):
            counts["failed"] += 1
            continue
        counts["updated"] += changed
    return counts


__all__ = [
    "DeriveError",
    "derive_benchmark_indexes",
    "derive_index_weights",
    "derive_region_split",
    "refresh_all",
]
