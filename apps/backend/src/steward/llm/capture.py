"""Training-data capture (fine-tuning data plan, phase 1).

After each AI call made inside a job, a redacted copy of the prompt, the answer and the usage
is appended to `llm_call`. It is the raw material for fine-tuning later; nothing here trains or
sends anything. Capture must never break or slow a call: every failure is logged and ignored.

Redaction runs before anything is stored. It removes what must not leave the app in a training
file: e-mail addresses, long digit runs (account and card numbers), IBANs, and API-key and
bearer-token shapes. Holding sizes and balances are not yet masked; `REDACTION_VERSION` is
stored with each row so a stronger pass can be applied to older rows before any export.
"""

import json
import logging
import re
import time
from typing import Any

import asyncpg

from steward.llm.types import Completion, Request

logger = logging.getLogger("steward.llm.capture")

REDACTION_VERSION = 1
SETTINGS_TTL = 300.0  # seconds a read of the config switch is reused

PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<EMAIL>"),
    (re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,4})?\b"), "<IBAN>"),
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b"), "<KEY>"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"), "<KEY>"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"), "Bearer <TOKEN>"),
    (re.compile(r"\b\d(?:[ -]?\d){11,}\b"), "<NUMBER>"),
)


def redact(text: str) -> str:
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…[cut]"


def clean(text: str, limit: int) -> str:
    return clip(redact(text), limit)


def request_payload(req: Request, limit: int) -> dict[str, Any]:
    return {
        "system": clean(req.system, limit),
        "messages": [
            {
                "role": m.role,
                "text": clean(m.text, limit),
                "tool_calls": [
                    {
                        "name": c.name,
                        "arguments": clean(json.dumps(c.arguments, default=str), limit),
                    }
                    for c in m.tool_calls
                ],
                **({"tool_name": m.tool_name} if m.tool_name else {}),
            }
            for m in req.messages
        ],
        "schema": req.schema.__name__ if req.schema is not None else None,
        "tools": [t.name for t in req.tools],
        "search": req.search is not None,
        "max_tokens": req.max_tokens,
    }


def response_payload(done: Completion, limit: int) -> dict[str, Any]:
    return {
        "text": clean(done.text, limit),
        "parsed": clean(done.parsed.model_dump_json(), limit) if done.parsed is not None else None,
        "tool_calls": [{"name": c.name} for c in done.tool_calls],
        "citations": [c.url for c in done.citations],
    }


_cache: dict[str, Any] = {"at": 0.0, "value": None}


async def settings(pool: asyncpg.Pool) -> Any:
    """The capture switch from the jobs config, reused for `SETTINGS_TTL` seconds."""
    from steward.config.jobs import JobsConfig, TrainingCaptureSettings
    from steward.config.store import load_active

    now = time.monotonic()
    if _cache["value"] is not None and now - _cache["at"] < SETTINGS_TTL:
        return _cache["value"]
    stored = await load_active(pool, "jobs", JobsConfig)
    value = stored.body.training_capture if stored else TrainingCaptureSettings()
    _cache.update(at=now, value=value)
    return value


def reset_cache() -> None:
    _cache.update(at=0.0, value=None)


async def record(
    pool: asyncpg.Pool, job_id: int | None, job_type: str, req: Request, done: Completion
) -> None:
    """Append one redacted call. Never raises."""
    try:
        conf = await settings(pool)
        if not conf.enabled or job_type in conf.exclude_job_types:
            return
        usage = done.usage
        await pool.execute(
            "insert into public.llm_call (job_id, job_type, provider, model, effort, request,"
            " response, input_tokens, output_tokens, cached_tokens, stop_reason, redaction)"
            " values ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8, $9, $10, $11, $12)",
            job_id,
            job_type,
            done.provider,
            done.model,
            req.effort,
            json.dumps(request_payload(req, conf.max_chars)),
            json.dumps(response_payload(done, conf.max_chars)),
            int(getattr(usage, "input_tokens", 0) or 0),
            int(getattr(usage, "output_tokens", 0) or 0),
            int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            done.stop_reason,
            REDACTION_VERSION,
        )
    except Exception:
        logger.warning("could not record an AI call for training data", exc_info=True)
