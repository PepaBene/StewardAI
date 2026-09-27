"""The Anthropic client, and per-step usage counts for `job_step`.

The cost of a step is computed by the job runner from these counts and the Budget config's
price table (see `steward.costs`), so a price change needs no deploy.

Every client is a `BudgetedAnthropic`: inside a job, each Messages request (single, beta or
batch) is checked against the Budget config's caps before it is sent, and may be sent to a
cheaper model at the monthly cap (see `steward.budget_guard`). Every call site gets its
client here, so the caps hold without any call site knowing about them.
"""

from dataclasses import dataclass
from typing import Any

import anthropic

from steward import budget_guard
from steward.jobs.runner import StepUsage, current_job
from steward.settings import Settings


class AINotConfigured(Exception):
    pass


_MESSAGES = "/v1/messages"
_BATCHES = "/v1/messages/batches"


# Server tool versions Claude Haiku 4.5 accepts in place of the dynamic-filtering ones.
_HAIKU_TOOL_TYPES = {
    "web_search_20260209": "web_search_20250305",
    "web_fetch_20260209": "web_fetch_20250910",
}


def _downgrade(params: dict[str, Any], model: str) -> dict[str, Any]:
    """`params` sent to `model` instead. Effort is dropped: a cheaper model may not support
    it (the response format is kept). On Claude Haiku 4 the server tools fall back to the
    versions it supports and adaptive thinking is removed, so the request stays valid."""
    out = {**params, "model": model}
    config = out.get("output_config")
    if isinstance(config, dict) and "effort" in config:
        out["output_config"] = {k: v for k, v in config.items() if k != "effort"}
    if model.startswith("claude-haiku-4"):
        tools = out.get("tools")
        if isinstance(tools, list):
            out["tools"] = [
                {**t, "type": _HAIKU_TOOL_TYPES[t["type"]]}
                if isinstance(t, dict) and t.get("type") in _HAIKU_TOOL_TYPES
                else t
                for t in tools
            ]
        thinking = out.get("thinking")
        if isinstance(thinking, dict) and thinking.get("type") == "adaptive":
            out.pop("thinking")
    return out


class BudgetedAnthropic(anthropic.AsyncAnthropic):
    """The Anthropic client with the Budget config's caps applied to the requests of the job
    running in the current task (`current_job`). Outside a job it is the plain client."""

    async def post(self, path: str, *, body: Any = None, **kwargs: Any) -> Any:
        ctx = current_job.get()
        route = path.split("?", 1)[0]
        if ctx is None or not isinstance(body, dict) or route not in (_MESSAGES, _BATCHES):
            return await super().post(path, body=body, **kwargs)
        if route == _BATCHES:
            requests = body.get("requests") or []
            first = requests[0].get("params", {}).get("model") if requests else None
            if isinstance(first, str):
                model = await budget_guard.before_request(ctx, first)
                if model != first:
                    body = {
                        **body,
                        "requests": [
                            {**r, "params": _downgrade(r["params"], model)} for r in requests
                        ],
                    }
            # A batch's cost reaches the budget when its step records it.
            return await super().post(path, body=body, **kwargs)
        requested = body.get("model")
        if not isinstance(requested, str):
            return await super().post(path, body=body, **kwargs)
        model = await budget_guard.before_request(ctx, requested)
        if model != requested:
            body = _downgrade(body, model)
        result = await super().post(path, body=body, **kwargs)
        # A streamed response has no usage block yet; its cost counts when its step ends.
        if getattr(result, "type", None) == "message":
            budget_guard.after_response(ctx, model, getattr(result, "usage", None))
        return result


def create_client(settings: Settings) -> anthropic.AsyncAnthropic:
    if settings.anthropic_api_key is None:
        raise AINotConfigured("ANTHROPIC_API_KEY is not set")
    return BudgetedAnthropic(
        api_key=settings.anthropic_api_key.get_secret_value(), max_retries=3, timeout=300.0
    )


@dataclass
class Usage:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    searches: int = 0
    fetches: int = 0
    # Batch API requests are billed at half price.
    batch: bool = False

    def add(self, usage: Any) -> None:
        """Add a response's `usage` block."""
        self.input_tokens += usage.input_tokens or 0
        self.output_tokens += usage.output_tokens or 0
        self.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        server = getattr(usage, "server_tool_use", None)
        if server is not None:
            self.searches += getattr(server, "web_search_requests", 0) or 0
            self.fetches += getattr(server, "web_fetch_requests", 0) or 0

    def step_usage(self) -> StepUsage:
        return StepUsage(
            model=self.model,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cache_write_tokens=self.cache_write_tokens,
            cache_read_tokens=self.cache_read_tokens,
            searches=self.searches,
            batch=self.batch,
        )
