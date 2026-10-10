"""The fetch governor for public-disclosure sources (plan P1.2).

One governed GET per document: the source must be enabled with a passing access test, the shared
provider gate and request budget apply, a declared User-Agent is sent where the source requires
one, conditional-fetch validators are honoured, the body is size-capped and checksummed, the
original bytes are archived under their SHA-256, and every failure is classified into the
disclosure state machine (`authentication_failed`, `paid_only`, `not_covered`, `retrieval_failed`).
Errors never carry the request URL's query string (some registries take keys there).
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import httpx

from steward.disclosure.registry import SourceNotEnabled
from steward.sources.base import AccessResult, AccessState, Priority, retry_after_at
from steward.sources.governed_http import gate_or_probe
from steward.sources.governor import Budget, PostgresRequestGovernor, QuotaExceeded
from steward.sources.store import record_access
from steward.storage import EvidenceStore, evidence_path

FailureState = Literal["authentication_failed", "paid_only", "not_covered", "retrieval_failed"]

PAID_WORDS = ("subscription", "upgrade your plan", "paid plan", "premium", "payment required")
DEFAULT_MAX_BYTES = 64 * 1024 * 1024

_EXTENSIONS = {
    "application/pdf": "pdf",
    "text/html": "html",
    "application/xhtml+xml": "html",
    "application/json": "json",
    "application/xml": "xml",
    "text/xml": "xml",
    "text/plain": "txt",
    "application/zip": "zip",
}


class FetchFailure(Exception):
    """A classified failure; `state` is the disclosure state, `retry_at` any server wait."""

    def __init__(
        self,
        state: FailureState,
        message: str,
        *,
        status_code: int | None = None,
        retry_at: dt.datetime | None = None,
    ) -> None:
        super().__init__(message)
        self.state = state
        self.status_code = status_code
        self.retry_at = retry_at


@dataclass(frozen=True, slots=True)
class Validators:
    """Conditional-fetch validators remembered for a URL."""

    etag: str | None = None
    last_modified: str | None = None

    def headers(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.etag:
            out["If-None-Match"] = self.etag
        if self.last_modified:
            out["If-Modified-Since"] = self.last_modified
        return out


@dataclass(frozen=True, slots=True)
class FetchOutcome:
    url: str
    status_code: int
    modified: bool
    content: bytes = b""
    sha256: str | None = None
    content_type: str | None = None
    validators: Validators = Validators()
    archive_path: str | None = None
    retrieved_at: dt.datetime | None = None


def classify_failure(
    status_code: int,
    body: str = "",
    retry_after: str | None = None,
    *,
    now: dt.datetime | None = None,
) -> tuple[FailureState, dt.datetime | None]:
    """Map an HTTP failure to the disclosure state machine. A bare 403 is an authentication
    problem (bad key / missing User-Agent), never a licensing verdict; only an explicit plan
    wording or 402 means `paid_only`."""
    lowered = body.casefold()
    if status_code == 402 or (status_code in (401, 403) and any(w in lowered for w in PAID_WORDS)):
        return "paid_only", None
    if status_code in (401, 403):
        return "authentication_failed", None
    if status_code in (404, 410):
        return "not_covered", None
    return "retrieval_failed", retry_after_at(retry_after, now=now) if status_code == 429 else None


FAIR_ACCESS_COOLDOWN = dt.timedelta(hours=1)
SERVER_ERROR_BACKOFF = dt.timedelta(minutes=5)


def backoff_until(status_code: int, now: dt.datetime) -> dt.datetime | None:
    """The wait to record when the server gave none: a 403 (SEC fair-access block) latches the
    endpoint for an hour, a 5xx for a few minutes, so a run does not keep hammering it."""
    if status_code == 403:
        return now + FAIR_ACCESS_COOLDOWN
    if status_code >= 500:
        return now + SERVER_ERROR_BACKOFF
    return None


def safe_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def extension_for(content_type: str | None, url: str) -> str:
    if content_type:
        mapped = _EXTENSIONS.get(content_type.split(";")[0].strip().lower())
        if mapped:
            return mapped
    tail = urlsplit(url).path.rsplit(".", 1)
    return tail[1].lower()[:5] if len(tail) == 2 and tail[1].isalnum() else "bin"


_ACCESS_FOR: dict[str, AccessState] = {
    "authentication_failed": AccessState.ACCESS_UNVERIFIED,
    "paid_only": AccessState.PAID_ONLY,
    "not_covered": AccessState.NOT_COVERED,
    "retrieval_failed": AccessState.TEMPORARILY_FAILED,
}


class DisclosureFetcher:
    def __init__(
        self,
        pool: asyncpg.Pool,
        http: httpx.AsyncClient,
        *,
        user_agent: str | None = None,
        store: EvidenceStore | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self._pool = pool
        self._http = http
        self._user_agent = user_agent
        self._store = store
        self._max_bytes = max_bytes

    async def _source(self, source_key: str) -> asyncpg.Record:
        row = await self._pool.fetchrow(
            "select provider, endpoint, enabled, access_state, requires_user_agent,"
            " rate_limit_per_second from public.disclosure_source where source_key=$1",
            source_key,
        )
        if row is None:
            raise SourceNotEnabled(f"unknown disclosure source {source_key!r}")
        if not row["enabled"] or row["access_state"] != "available":
            raise SourceNotEnabled(
                f"{source_key} is disabled (access state {row['access_state']!r}); "
                "run its access test first"
            )
        return row

    async def fetch(
        self,
        source_key: str,
        url: str,
        *,
        validators: Validators | None = None,
        headers: dict[str, str] | None = None,
    ) -> FetchOutcome:
        source = await self._source(source_key)
        provider, endpoint = source["provider"], source["endpoint"]
        send = dict(headers or {})
        if source["requires_user_agent"]:
            if not self._user_agent or "@" not in self._user_agent:
                raise FetchFailure(
                    "authentication_failed",
                    f"{source_key} needs a User-Agent with a contact address",
                )
            send["User-Agent"] = self._user_agent
        if validators:
            send.update(validators.headers())
        await gate_or_probe(self._pool, provider, endpoint)
        per_second = source["rate_limit_per_second"]
        budget = Budget(per_minute=Decimal(str(per_second)) * 60 if per_second else None)
        try:
            await PostgresRequestGovernor(self._pool).reserve(
                provider, endpoint, Decimal(1), budget, priority=Priority.BACKGROUND
            )
        except QuotaExceeded:
            await record_access(
                self._pool,
                provider,
                endpoint,
                AccessResult(AccessState.QUOTA_EXHAUSTED, "request budget used"),
            )
            raise
        started = dt.datetime.now(dt.UTC)
        try:
            return await self._get(provider, endpoint, url, send, started)
        except httpx.HTTPError as exc:
            await record_access(
                self._pool,
                provider,
                endpoint,
                AccessResult(AccessState.TEMPORARILY_FAILED, type(exc).__name__),
            )
            raise FetchFailure(
                "retrieval_failed", f"{source_key} request failed ({type(exc).__name__})"
            ) from exc

    async def _get(
        self, provider: str, endpoint: str, url: str, headers: dict[str, str], started: dt.datetime
    ) -> FetchOutcome:
        async with self._http.stream(
            "GET", url, headers=headers, follow_redirects=True
        ) as response:
            status = response.status_code
            if status == 304:
                await self._ok(provider, endpoint, status, 0)
                return FetchOutcome(safe_url(url), 304, False, retrieved_at=started)
            if status != 200:
                body = (await response.aread())[:2048].decode("utf-8", "replace")
                state, retry_at = classify_failure(
                    status, body, response.headers.get("Retry-After")
                )
                if retry_at is None and state != "paid_only":
                    retry_at = backoff_until(status, started)
                await record_access(
                    self._pool,
                    provider,
                    endpoint,
                    AccessResult(
                        AccessState.QUOTA_EXHAUSTED if status == 429 else _ACCESS_FOR[state],
                        f"HTTP {status}",
                        retry_at,
                    ),
                    status_code=status,
                )
                raise FetchFailure(
                    state,
                    f"{provider} returned HTTP {status} for {safe_url(url)}",
                    status_code=status,
                    retry_at=retry_at,
                )
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > self._max_bytes:
                    raise FetchFailure(
                        "retrieval_failed",
                        f"{safe_url(url)} is larger than the {self._max_bytes} byte limit",
                        status_code=200,
                    )
                chunks.append(chunk)
            content_type = response.headers.get("Content-Type")
            validators = Validators(
                response.headers.get("ETag"), response.headers.get("Last-Modified")
            )
        content = b"".join(chunks)
        digest = hashlib.sha256(content).hexdigest()
        archive = None
        if self._store is not None:
            archive = evidence_path(digest, extension_for(content_type, url))
            await self._store.put(archive, content, content_type or "application/octet-stream")
        await self._ok(provider, endpoint, status, total)
        return FetchOutcome(
            safe_url(url), status, True, content, digest, content_type, validators, archive, started
        )

    async def _ok(self, provider: str, endpoint: str, status: int, size: int) -> None:
        await record_access(
            self._pool,
            provider,
            endpoint,
            AccessResult(AccessState.AVAILABLE, "free endpoint returned a response"),
            status_code=status,
            response_bytes=size,
            credits=1,
        )
