"""Conditional fetches for the scheduled jobs (plan P1.2, audit 6.4).

`ConditionalFetch` sends the ETag / Last-Modified last remembered for a URL, so an unchanged page
costs a 304 and nothing else. The new validators are remembered only when the caller says the
response was fully handled (`done`): a run that failed half-way fetches the page in full next
time instead of being told "not modified" and skipping what it never stored.

Validators live in the source's `disclosure_source.cursor`, under `{scope}:{url}`. The scope keeps
two jobs that read one URL apart (the ingest and deal jobs both read a SEC submissions listing).
"""

from __future__ import annotations

from typing import Any, Protocol

import asyncpg

from steward.disclosure.fetch import FetchOutcome, Validators
from steward.disclosure.registry import load_cursor, save_cursor


class _Fetcher(Protocol):
    async def fetch(
        self, source_key: str, url: str, *, validators: Validators | None = None
    ) -> FetchOutcome: ...


def cursor_key(scope: str, url: str) -> str:
    return f"{scope}:{url}"


class ConditionalFetch:
    def __init__(
        self,
        pool: asyncpg.Pool | asyncpg.Connection,
        fetcher: _Fetcher,
        source_key: str,
        scope: str,
    ) -> None:
        self._pool = pool
        self._fetcher = fetcher
        self._source = source_key
        self._scope = scope
        self._pending: dict[str, Validators] = {}

    async def __call__(self, url: str) -> Any:
        stored = await load_cursor(self._pool, self._source, cursor_key(self._scope, url))
        got = await self._fetcher.fetch(
            self._source,
            url,
            validators=Validators(stored.get("etag"), stored.get("last_modified")),
        )
        if got.modified and (got.validators.etag or got.validators.last_modified):
            self._pending[url] = got.validators
        return got

    async def done(self, url: str) -> None:
        """The response for `url` was fully handled: remember its validators."""
        v = self._pending.pop(url, None)
        if v is None:
            return
        value = {k: x for k, x in (("etag", v.etag), ("last_modified", v.last_modified)) if x}
        await save_cursor(self._pool, self._source, cursor_key(self._scope, url), value)
