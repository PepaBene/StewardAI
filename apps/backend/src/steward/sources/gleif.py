"""GLEIF (`api.gleif.org/api/v1`, no key): legal entities (Level 1) and their accounting-
consolidation parents and children (Level 2).

Level 2 describes consolidation, not shareholdings: a "parent" here is the consolidating
parent. Where no parent is reported, GLEIF states why (a reporting exception such as
NATURAL_PERSONS or NO_KNOWN_PERSON). Tier 1.
"""

import asyncio
import datetime as dt
from dataclasses import dataclass, field
from typing import Any

import httpx

BASE_URL = "https://api.gleif.org/api/v1"
PAGE_SIZE = 200  # GLEIF's maximum page[size]
# A safety bound only: large groups have hundreds of direct children (Siemens AG ~460).
MAX_CHILDREN = 5000


class GleifError(Exception):
    pass


@dataclass(frozen=True)
class LegalEntity:
    lei: str
    legal_name: str
    other_names: list[str]
    jurisdiction: str | None
    legal_form: str | None
    category: str | None
    hq_address: dict[str, Any] | None
    legal_address: dict[str, Any] | None
    status: str | None
    successor_lei: str | None
    registration_status: str | None
    next_renewal: dt.datetime | None
    raw: dict[str, Any] = field(repr=False)

    @property
    def hq_country(self) -> str | None:
        return (self.hq_address or {}).get("country")


@dataclass(frozen=True)
class ReportingException:
    category: str  # DIRECT_ / ULTIMATE_ACCOUNTING_CONSOLIDATION_PARENT
    reason: str


def _date(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_record(data: dict[str, Any]) -> LegalEntity:
    a = data["attributes"]
    entity = a.get("entity", {})
    registration = a.get("registration", {})
    names = [n.get("name") for n in entity.get("otherNames", []) + entity.get(
        "transliteratedOtherNames", []
    ) if n.get("name")]  # fmt: skip
    successor = (entity.get("successorEntity") or {}).get("lei")
    if not successor:
        successors = [s.get("lei") for s in entity.get("successorEntities", []) if s.get("lei")]
        successor = successors[0] if successors else None
    return LegalEntity(
        lei=a["lei"],
        legal_name=entity.get("legalName", {}).get("name", ""),
        other_names=list(dict.fromkeys(names)),
        jurisdiction=entity.get("jurisdiction"),
        legal_form=(entity.get("legalForm") or {}).get("id"),
        category=entity.get("category"),
        hq_address=entity.get("headquartersAddress"),
        legal_address=entity.get("legalAddress"),
        status=entity.get("status"),
        successor_lei=successor,
        registration_status=registration.get("status"),
        next_renewal=_date(registration.get("nextRenewalDate")),
        raw=data,
    )


class GleifClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http
        self.children_total = 0  # GLEIF's count for the last children() call

    async def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any] | None:
        """The JSON body, or None for a 404 (no such record or relationship)."""
        last: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._http.get(f"{BASE_URL}{path}", params=params)
            except httpx.TransportError as exc:  # GLEIF drops connections now and then
                last = exc
                await asyncio.sleep(1 + attempt)
                continue
            if response.status_code == 404:
                return None
            if response.status_code != 200:
                raise GleifError(f"GLEIF {path} returned HTTP {response.status_code}")
            body: dict[str, Any] = response.json()
            return body
        raise GleifError(f"GLEIF {path} unreachable ({type(last).__name__})")

    async def record(self, lei: str) -> LegalEntity | None:
        body = await self._get(f"/lei-records/{lei}")
        return parse_record(body["data"]) if body and body.get("data") else None

    async def for_isin(self, isin: str) -> LegalEntity | None:
        body = await self._get("/lei-records", {"filter[isin]": isin})
        data = (body or {}).get("data") or []
        return parse_record(data[0]) if data else None

    async def parent(self, lei: str, kind: str) -> LegalEntity | ReportingException | None:
        """kind 'direct' or 'ultimate': the parent's record, or the reporting exception
        stating why none is reported."""
        body = await self._get(f"/lei-records/{lei}/{kind}-parent")
        if body and body.get("data"):
            return parse_record(body["data"])
        exception = await self._get(f"/lei-records/{lei}/{kind}-parent-reporting-exception")
        attrs = ((exception or {}).get("data") or {}).get("attributes")
        if attrs:
            return ReportingException(attrs.get("category", ""), attrs.get("reason", ""))
        return None

    async def children(self, lei: str, kind: str = "direct") -> list[LegalEntity]:
        """Every child, paging until the last page (up to MAX_CHILDREN; `children_total`
        then tells how many GLEIF has)."""
        found: list[LegalEntity] = []
        page = 1
        self.children_total = 0
        while len(found) < MAX_CHILDREN:
            body = await self._get(
                f"/lei-records/{lei}/{kind}-children",
                {"page[size]": str(PAGE_SIZE), "page[number]": str(page)},
            )
            if not body:
                break
            data = body.get("data", [])
            found.extend(parse_record(d) for d in data)
            pagination = body.get("meta", {}).get("pagination", {})
            self.children_total = int(pagination.get("total", len(found)))
            if not data or page >= int(pagination.get("lastPage", page)):
                break
            page += 1
        return found[:MAX_CHILDREN]
