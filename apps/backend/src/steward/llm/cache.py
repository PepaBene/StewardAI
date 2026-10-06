"""Application-level cache of AI answers (`public.ai_result_cache`).

Provider prefix caching only discounts input tokens; skipping a call whose request repeats
exactly saves all of it. The key hashes everything that shapes the answer (model, system
prompt, messages, output schema, effort), so a changed prompt or model never reads an old
answer. A cache failure never fails the call: it is only a missed saving.
"""

import hashlib
import json
import logging
from typing import Any

import asyncpg
from pydantic import BaseModel

logger = logging.getLogger(__name__)


def request_key(
    model: str,
    system: Any,
    messages: Any,
    schema: type[BaseModel],
    effort: str | None,
    tools: Any = None,
) -> str:
    payload = json.dumps(
        [model, system, messages, schema.model_json_schema(), effort] + ([tools] if tools else []),
        sort_keys=True,
        default=str,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class ResultCache:
    def __init__(
        self,
        pool: asyncpg.Pool,
        job_type: str,
        ttl_days: int = 30,
        search_ttl_days: int | None = None,
    ) -> None:
        self._pool = pool
        self._job_type = job_type
        self._ttl_days = ttl_days
        # A web-search answer goes stale with the web: cached only when this is set, and for
        # this many days (a repeated search within it costs nothing).
        self.search_ttl_days = search_ttl_days
        self.hits = 0
        self.misses = 0

    async def get(self, key: str, ttl_days: int | None = None) -> dict[str, Any] | None:
        try:
            raw = await self._pool.fetchval(
                "select result from public.ai_result_cache where key = $1"
                " and created_at > now() - make_interval(days => $2)",
                key,
                ttl_days if ttl_days is not None else self._ttl_days,
            )
        except Exception:
            logger.exception("ai_result_cache read failed")
            return None
        if raw is None:
            self.misses += 1
            return None
        self.hits += 1
        result = json.loads(raw) if isinstance(raw, str) else raw
        return result if isinstance(result, dict) else None

    async def put(self, key: str, model: str, result: dict[str, Any]) -> None:
        try:
            await self._pool.execute(
                "insert into public.ai_result_cache (key, job_type, model, result)"
                " values ($1, $2, $3, $4::jsonb)"
                " on conflict (key) do update set result = excluded.result, created_at = now()",
                key,
                self._job_type,
                model,
                json.dumps(result),
            )
        except Exception:
            logger.exception("ai_result_cache write failed")
