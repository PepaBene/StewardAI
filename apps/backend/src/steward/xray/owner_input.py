"""Owner-entered X-Ray inputs: regional splits, supply-chain edges and index weights.

No provider here supplies these, so they are what the owner typed, each with the address it came
from and the date it describes. They are shown as owner-entered everywhere and never presented as
official data. Stored in `xray_owner_input`; the newest non-archived row per (kind, key) is the one
used, and a row is archived, never edited or deleted.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field, field_validator, model_validator

from steward.lookthrough.classify import country_code

InputKind = Literal["region_split", "supply_edge", "index_weights"]
SHARE_TOLERANCE = 0.005  # how far from 1 a hand-typed set of shares may be before it is refused


class _Sourced(BaseModel):
    source_url: str = Field(description="Where the figures came from (a web address).")
    as_of: dt.date = Field(description="The date the figures describe.")
    note: str | None = Field(default=None, max_length=500)

    @field_validator("source_url")
    @classmethod
    def _web_address(cls, value: str) -> str:
        if not value.lower().startswith(("http://", "https://")):
            raise ValueError("source_url must be a web address")
        return value.strip()

    @field_validator("as_of")
    @classmethod
    def _not_future(cls, value: dt.date) -> dt.date:
        if value > dt.datetime.now(dt.UTC).date():
            raise ValueError("the date cannot be in the future")
        return value


class RegionSplitRequest(_Sourced):
    region: str = Field(min_length=1, max_length=60, description='A region such as "Europe".')
    countries: dict[str, float] = Field(
        min_length=1, max_length=80, description="Country (code or name) -> share of the region."
    )

    @field_validator("countries")
    @classmethod
    def _countries(cls, value: dict[str, float]) -> dict[str, float]:
        out: dict[str, float] = {}
        for label, share in value.items():
            code = country_code(label)
            if code is None:
                raise ValueError(f"{label!r} is not a country the app recognises")
            if not 0 < share <= 1:
                raise ValueError("each share must be above 0 and at most 1")
            out[code] = out.get(code, 0.0) + share
        if abs(sum(out.values()) - 1) > SHARE_TOLERANCE:
            raise ValueError(
                f"the shares must add up to 1 (they add up to {sum(out.values()):.3f})"
            )
        return out


class SupplyEdgeRequest(_Sourced):
    supplier: str = Field(min_length=1, max_length=40)
    customer: str = Field(min_length=1, max_length=40)
    product: str = Field(min_length=1, max_length=120, description="What is supplied.")
    customer_share: float | None = Field(
        default=None, gt=0, le=1, description="Share of the supplier's revenue this customer is."
    )

    @model_validator(mode="after")
    def _distinct(self) -> SupplyEdgeRequest:
        self.supplier = self.supplier.strip().upper()
        self.customer = self.customer.strip().upper()
        if self.supplier == self.customer:
            raise ValueError("a company cannot supply itself")
        return self


class IndexConstituent(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    isin: str | None = Field(default=None, pattern=r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
    ticker: str | None = Field(default=None, max_length=40)
    weight: float = Field(gt=0, le=1)


class IndexWeightsRequest(_Sourced):
    label: str = Field(min_length=1, max_length=120, description="The index's name.")
    constituents: list[IndexConstituent] = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _weights(self) -> IndexWeightsRequest:
        total = sum(c.weight for c in self.constituents)
        if total > 1 + SHARE_TOLERANCE:
            raise ValueError(f"the weights add up to {total:.3f}, which is more than 1")
        if total < 0.05:
            raise ValueError(
                "the weights add up to almost nothing; paste the constituents' weights"
            )
        return self


class OwnerInput(BaseModel):
    id: int
    kind: InputKind
    key: str
    payload: dict[str, Any]
    source_url: str
    as_of: dt.date
    note: str | None
    origin: Literal["owner", "derived"] = Field(
        default="owner",
        description="'derived' entries are computed from fund holdings the app stores and are "
        "recomputed when those holdings are refreshed.",
    )
    derived_from: dict[str, Any] | None = None
    created_at: dt.datetime


def _item(row: asyncpg.Record) -> OwnerInput:
    data = dict(row)
    for field in ("payload", "derived_from"):
        if isinstance(data.get(field), str):
            data[field] = json.loads(data[field])
    return OwnerInput(**data)


async def save(
    pool: asyncpg.Pool,
    owner_id: str,
    kind: InputKind,
    key: str,
    payload: dict[str, Any],
    body: _Sourced,
    *,
    derived_from: dict[str, Any] | None = None,
) -> OwnerInput:
    row = await pool.fetchrow(
        "insert into public.xray_owner_input"
        " (owner_id, kind, key, payload, source_url, as_of, note, origin, derived_from)"
        " values ($1,$2,$3,$4::jsonb,$5,$6,$7,$8,$9::jsonb)"
        " returning id, kind, key, payload, source_url, as_of, note, origin, derived_from,"
        " created_at",
        owner_id,
        kind,
        key.strip(),
        json.dumps(payload),
        body.source_url,
        body.as_of,
        body.note,
        "derived" if derived_from else "owner",
        json.dumps(derived_from) if derived_from else None,
    )
    assert row is not None
    return _item(row)


async def current(
    pool: asyncpg.Pool, owner_id: str, kind: InputKind | None = None
) -> list[OwnerInput]:
    """The row in force for each (kind, key): the newest that is not archived."""
    rows = await pool.fetch(
        "select distinct on (kind, key) id, kind, key, payload, source_url, as_of, note, origin,"
        " derived_from, created_at from public.xray_owner_input"
        " where owner_id=$1 and not archived and ($2::text is null or kind=$2)"
        " order by kind, key, created_at desc, id desc",
        owner_id,
        kind,
    )
    return sorted((_item(r) for r in rows), key=lambda x: (x.kind, x.key.lower()))


async def archive(pool: asyncpg.Pool, owner_id: str, item_id: int) -> bool:
    done = await pool.execute(
        "update public.xray_owner_input set archived=true"
        " where id=$1 and owner_id=$2 and not archived",
        item_id,
        owner_id,
    )
    return str(done).endswith("1")


async def region_splits(pool: asyncpg.Pool, owner_id: str) -> dict[str, dict[str, float]]:
    """Lower-cased region name -> country code -> share, for the revenue-country view."""
    return {
        i.key.lower(): {str(c): float(s) for c, s in i.payload["countries"].items()}
        for i in await current(pool, owner_id, "region_split")
    }
