"""`LLM.complete`: one call that any provider answers, under the Budget config's caps.

Anthropic requests go through `BudgetedAnthropic`, which guards and records them itself. For
OpenAI and Google this module does the same, so every provider is capped, counted per step
(`record_response`, so a failed or killed step keeps its cost) and priced from the Budget price
table. At the monthly cap a request may be sent to `downgrade_model`, which can belong to
another provider: the provider is chosen after the guard has had its say.
"""

import asyncio
import logging
from typing import Any, cast

import anthropic
import httpx

from steward import budget_guard
from steward.config.jobs import reasoning_kwargs
from steward.jobs.runner import current_job, record_response
from steward.llm import wire
from steward.llm.types import (
    Citation,
    Completion,
    LLMError,
    Provider,
    Request,
    StopReason,
    normalized_usage,
    provider_of,
)
from steward.settings import Settings

logger = logging.getLogger(__name__)

OPENAI_URL = "https://api.openai.com/v1/responses"
GOOGLE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}
DEADLINE_MARGIN = 30.0
MAX_PAUSES = 4


class LLM:
    """The client call sites use. Build one with `create_llm`."""

    def __init__(
        self,
        *,
        anthropic_client: anthropic.AsyncAnthropic | None = None,
        openai_key: str | None = None,
        google_key: str | None = None,
        http: httpx.AsyncClient | None = None,
        retries: int = 2,
    ) -> None:
        self._anthropic = anthropic_client
        self._keys: dict[Provider, str | None] = {
            "anthropic": None,
            "openai": openai_key,
            "google": google_key,
        }
        self._http = http or httpx.AsyncClient()
        self._retries = retries

    def configured(self, model: str) -> bool:
        provider = provider_of(model)
        if provider == "anthropic":
            return self._anthropic is not None
        return self._keys[provider] is not None

    async def complete(self, req: Request) -> Completion:
        ctx = current_job.get()
        if ctx is not None and provider_of(req.model) != "anthropic":
            model = await budget_guard.before_request(ctx, req.model, can_switch_provider=True)
            if model != req.model:
                req = _downgraded(req, model)
        provider = provider_of(req.model)
        if provider == "anthropic":
            # BudgetedAnthropic has already guarded and recorded this request.
            return await self._anthropic_complete(req)
        done = await (self._openai if provider == "openai" else self._google)(req)
        if ctx is not None:
            budget_guard.after_response(ctx, done.model, done.usage)
        await record_response(done.model, done.usage)
        return done

    # ------------------------------------------------------------------ Anthropic

    async def _anthropic_complete(self, req: Request) -> Completion:
        if self._anthropic is None:
            raise LLMError("ANTHROPIC_API_KEY is not set", provider="anthropic")
        system: list[dict[str, Any]] = [{"type": "text", "text": req.system}]
        if req.cache_system:
            system[0]["cache_control"] = {"type": "ephemeral"}
        messages: list[dict[str, Any]] = [{"role": m.role, "content": m.text} for m in req.messages]
        kwargs: dict[str, Any] = reasoning_kwargs(req.model, req.effort, req.max_tokens)
        if req.search is not None:
            kind = (
                "web_search_20250305"
                if req.model.startswith("claude-haiku-4")
                else "web_search_20260209"
            )
            tool: dict[str, Any] = {
                "type": kind,
                "name": "web_search",
                "max_uses": req.search.max_uses,
            }
            if req.search.allowed_domains:
                tool["allowed_domains"] = list(req.search.allowed_domains)
            kwargs["tools"] = [tool]
        totals = normalized_usage()
        for _ in range(MAX_PAUSES + 1):
            try:
                response = await self._anthropic.messages.parse(
                    model=req.model,
                    system=cast(Any, system),
                    messages=cast(Any, messages),
                    output_format=req.schema,
                    timeout=req.timeout,
                    **kwargs,
                )
            except anthropic.APIError as exc:
                raise LLMError(f"anthropic call failed: {exc}", provider="anthropic") from exc
            _accumulate(totals, response.usage)
            if response.stop_reason != "pause_turn":
                break
            messages.append({"role": "assistant", "content": response.content})
        stops: dict[str, StopReason] = {
            "end_turn": "end",
            "stop_sequence": "end",
            "max_tokens": "max_tokens",
            "refusal": "refusal",
        }
        stop = stops.get(str(response.stop_reason), "other")
        text = "".join(getattr(b, "text", "") for b in response.content if b.type == "text")
        return Completion(
            model=req.model,
            provider="anthropic",
            text=text,
            parsed=getattr(response, "parsed_output", None),
            stop_reason=stop,
            usage=totals,
            citations=_anthropic_citations(response.content),
        )

    # ------------------------------------------------------------------ OpenAI, Google

    async def _openai(self, req: Request) -> Completion:
        data = await self._post(
            "openai",
            OPENAI_URL,
            {"authorization": f"Bearer {self._key('openai')}"},
            wire.openai_body(req),
            req,
        )
        return wire.openai_completion(req, data)

    async def _google(self, req: Request) -> Completion:
        data = await self._post(
            "google",
            GOOGLE_URL.format(model=req.model),
            {"x-goog-api-key": self._key("google")},
            wire.google_body(req),
            req,
        )
        return wire.google_completion(req, data)

    def _key(self, provider: Provider) -> str:
        key = self._keys[provider]
        if key is None:
            raise LLMError(f"the {provider} API key is not set", provider=provider)
        return key

    async def _post(
        self, provider: str, url: str, headers: dict[str, str], body: dict[str, Any], req: Request
    ) -> dict[str, Any]:
        """POST with retries on rate limits, server errors and timeouts, and a hard deadline
        so a stalled connection cannot hang the job."""
        last = ""
        deadline = req.timeout * (self._retries + 1) + DEADLINE_MARGIN
        try:
            async with asyncio.timeout(deadline):
                for attempt in range(self._retries + 1):
                    try:
                        response = await self._http.post(
                            url, headers=headers, json=body, timeout=req.timeout
                        )
                    except httpx.HTTPError as exc:
                        last = f"{type(exc).__name__}: {exc}"
                    else:
                        if response.status_code == 200:
                            result: dict[str, Any] = response.json()
                            return result
                        last = f"HTTP {response.status_code}: {wire.error_message(response.text)}"
                        if response.status_code not in RETRY_STATUS:
                            raise LLMError(
                                f"{provider} call failed: {last}",
                                provider=provider,
                                status=response.status_code,
                            )
                    await asyncio.sleep(min(2.0**attempt, 8.0))
        except TimeoutError:
            last = f"no answer within {deadline:.0f}s"
        raise LLMError(f"{provider} call failed: {last}", provider=provider)


def _downgraded(req: Request, model: str) -> Request:
    """`req` for the downgrade model: effort dropped (it may not support it), and the search
    kept only when the target is the same provider (the tool types differ between them)."""
    from dataclasses import replace

    same = provider_of(model) == provider_of(req.model)
    return replace(
        req, model=model, effort=None, search=req.search if same else None, cache_key=None
    )


def _accumulate(total: Any, usage: Any) -> None:
    total.input_tokens += getattr(usage, "input_tokens", 0) or 0
    total.output_tokens += getattr(usage, "output_tokens", 0) or 0
    total.cache_creation_input_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
    total.cache_read_input_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
    server = getattr(usage, "server_tool_use", None)
    if server is not None:
        total.server_tool_use.web_search_requests += getattr(server, "web_search_requests", 0) or 0


def _anthropic_citations(content: Any) -> list[Citation]:
    out: list[Citation] = []
    for block in content:
        for c in getattr(block, "citations", None) or []:
            url = getattr(c, "url", None)
            if url:
                out.append(Citation(url, getattr(c, "title", "") or ""))
    return out


def create_llm(settings: Settings, anthropic_client: anthropic.AsyncAnthropic | None) -> LLM:
    """The multi-provider client: Anthropic through the budgeted client the app already makes
    (None when its key is unset), OpenAI and Google from their keys."""

    def secret(value: Any) -> str | None:
        return value.get_secret_value() if value is not None else None

    return LLM(
        anthropic_client=anthropic_client,
        openai_key=secret(settings.openai_api_key),
        google_key=secret(settings.google_api_key),
    )
