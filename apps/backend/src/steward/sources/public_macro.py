"""HTTP adapters for keyless phase-2 macro sources and registered UN Comtrade."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import httpx

from steward.macro.geo import ISO3_TO_ISO2
from steward.macro.providers import parse_comtrade, parse_sdmx_json, parse_world_bank
from steward.sources.base import (
    AccessResult,
    AccessState,
    FetchResult,
    SourceObservation,
    access_from_http,
)


def _json_body(response: httpx.Response) -> Any:
    """The JSON body, or None when the answer is not JSON (an HTML error page) - never an
    exception, so the caller maps the outcome to a typed access state."""
    try:
        return response.json()
    except ValueError:
        return None


NOT_JSON = AccessResult(AccessState.TEMPORARILY_FAILED, "provider answered with a non-JSON body")


class PublicMacroAdapter:
    provider = "public_macro"

    def __init__(self, http: httpx.AsyncClient, *, api_key: str | None = None) -> None:
        self.http = http
        self.api_key = api_key

    async def access(self) -> AccessResult:
        return AccessResult(AccessState.AVAILABLE)

    async def discover(self, query: str) -> list[dict[str, Any]]:
        del query
        return []  # series discovery is curated config, never an unbounded mirror

    async def fetch(self, request: dict[str, Any]) -> FetchResult:
        raise NotImplementedError

    def normalize(self, payload: Any) -> list[SourceObservation]:
        raise NotImplementedError

    async def coverage(self, request: dict[str, Any]) -> AccessResult:
        result = await self.fetch(request)
        return result.access

    def validate(self, records: list[SourceObservation]) -> list[str]:
        errors = []
        seen = set()
        for row in records:
            key = (row.metric, row.geography, row.period_end, row.vintage)
            if key in seen:
                errors.append(f"duplicate observation: {key}")
            seen.add(key)
            if row.period_start > row.period_end:
                errors.append(f"invalid period: {key}")
        return errors


class WorldBankAdapter(PublicMacroAdapter):
    provider = "world_bank"

    async def fetch(self, request: dict[str, Any]) -> FetchResult:
        country = request["country"]
        indicators = ";".join(request["indicators"])
        url = f"https://api.worldbank.org/v2/country/{country}/indicator/{indicators}"
        response = await self.http.get(
            url,
            params={"format": "json", "per_page": 20_000, "date": request.get("date", "2000:2030")},
        )
        body = _json_body(response)
        if response.status_code == 200 and body is None:
            return FetchResult(NOT_JSON)
        # World Bank answers an invalid request with HTTP 200 and `[{"message": [...]}]`.
        has_rows = (
            response.status_code == 200
            and isinstance(body, list)
            and len(body) > 1
            and isinstance(body[1], list)
            and bool(body[1])
        )
        state = access_from_http(response.status_code, has_rows=has_rows)
        if state.state == AccessState.NOT_COVERED and isinstance(body, list) and len(body) == 1:
            state = AccessResult(AccessState.TEMPORARILY_FAILED, "World Bank error envelope")
        return FetchResult(state, {"body": body, "url": str(response.url)})

    def normalize(self, payload: Any) -> list[SourceObservation]:
        now = dt.datetime.now(dt.UTC)
        return [
            SourceObservation(
                "macro",
                row.series_id,
                self.provider,
                dt.datetime.combine(row.period, dt.time(), dt.UTC),
                dt.datetime.combine(row.period, dt.time(), dt.UTC),
                row.value,
                row.unit,
                row.source_url,
                now,
                geography=row.geography,
                vintage=row.vintage,
                raw=row.metadata,
            )
            for row in parse_world_bank(payload["body"], payload["url"])
        ]


class SdmxAdapter(PublicMacroAdapter):
    """OECD/BIS SDMX adapter; callers must provide exact flow/key and semantic metadata."""

    def __init__(self, http: httpx.AsyncClient, provider: str, base_url: str) -> None:
        super().__init__(http)
        if provider not in ("oecd", "bis"):
            raise ValueError("SDMX provider must be oecd or bis")
        self.provider = provider
        self.base_url = base_url.rstrip("/")

    async def fetch(self, request: dict[str, Any]) -> FetchResult:
        url = f"{self.base_url}/data/{request['flow']}/{request['key']}"
        response = await self.http.get(
            url,
            params={
                "startPeriod": request.get("start", "2000"),
                "format": "sdmx-json",
                # One observation per dimension combination: the area comes from the response.
                "dimensionAtObservation": "AllDimensions",
            },
            headers={"Accept": "application/vnd.sdmx.data+json;version=2.0.0"},
        )
        body = _json_body(response) if response.status_code == 200 else {}
        if body is None:
            return FetchResult(NOT_JSON)
        access = access_from_http(
            response.status_code, has_rows=response.status_code == 200 and bool(body)
        )
        return FetchResult(
            access,
            {
                "body": body,
                "url": str(response.url),
                "request": request,
            },
        )

    def normalize(self, payload: Any) -> list[SourceObservation]:
        request = payload["request"]
        points = parse_sdmx_json(
            payload["body"],
            source=self.provider,
            series_id=request["series_id"],
            geography=request["geography"],
            unit=request["unit"],
            frequency=request["frequency"],
            source_url=payload["url"],
            vintage=request["vintage"],
            expect_geography=request.get("area_code"),
            geography_map=ISO3_TO_ISO2 if self.provider == "oecd" else None,
        )
        now = dt.datetime.now(dt.UTC)
        return [
            SourceObservation(
                "macro",
                p.series_id,
                self.provider,
                dt.datetime.combine(p.period, dt.time(), dt.UTC),
                dt.datetime.combine(p.period, dt.time(), dt.UTC),
                p.value,
                p.unit,
                p.source_url,
                now,
                geography=p.geography,
                vintage=p.vintage,
            )
            for p in points
        ]


class ComtradeAdapter(PublicMacroAdapter):
    provider = "un_comtrade"

    async def access(self) -> AccessResult:
        return AccessResult(
            AccessState.AVAILABLE if self.api_key else AccessState.ACCESS_UNVERIFIED,
            None if self.api_key else "UN_COMTRADE_API_KEY is not configured",
        )

    async def fetch(self, request: dict[str, Any]) -> FetchResult:
        if not self.api_key:
            return FetchResult(await self.access())
        url = "https://comtradeapi.un.org/data/v1/get/C/A/HS"
        params = {
            "reporterCode": request["reporter"],
            "period": request["period"],
            "partnerCode": request.get("partner", 0),
            "cmdCode": request["hs_code"],
            "flowCode": request.get("flow_code", "X,M"),
            "customsCode": "C00",
            "motCode": "0",
            "partner2Code": "0",
            "subscription-key": self.api_key,
        }
        response = await self.http.get(url, params=params)
        body = _json_body(response) if response.status_code == 200 else {}
        if body is None:
            return FetchResult(NOT_JSON)
        rows = body.get("data") if isinstance(body, dict) else None
        access = access_from_http(response.status_code, has_rows=bool(rows))
        # Do not retain response.url: it may include the subscription key.
        return FetchResult(access, {"body": body if isinstance(body, dict) else {}})

    def normalize(self, payload: Any) -> list[SourceObservation]:
        now = dt.datetime.now(dt.UTC)
        out = []
        for row in parse_comtrade(payload["body"]):
            period = dt.datetime.combine(row.period, dt.time(), dt.UTC)
            out.append(
                SourceObservation(
                    "trade",
                    f"{row.flow}:{row.hs_revision}:{row.hs_code}",
                    self.provider,
                    period,
                    period,
                    row.value,
                    "USD",
                    "https://comtradeapi.un.org/",
                    now,
                    # ISO alpha-2 where the M49 code is one country (exposure mappings use ISO).
                    geography=row.reporter_iso or row.reporter,
                    scale=Decimal(1),
                    dimensions={
                        "partner": row.partner_iso or row.partner,
                        "reporter_m49": row.reporter,
                        "partner_m49": row.partner,
                        "flow": row.flow,
                        "hs_revision": row.hs_revision,
                        "hs_code": row.hs_code,
                    },
                    raw={
                        "quantity": str(row.quantity) if row.quantity is not None else None,
                        "quantity_unit": row.quantity_unit,
                        "net_weight_kg": str(row.net_weight)
                        if row.net_weight is not None
                        else None,
                    },
                )
            )
        return out
