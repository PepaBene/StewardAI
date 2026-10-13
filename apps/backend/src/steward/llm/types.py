"""Provider-neutral request and result of one AI call.

See docs/plans/multi_provider_model_routing_plan.md.

A call site describes what it wants (system prompt, messages, an output schema, an effort level,
an optional web search) and gets back the parsed object, the text, the citations and a
normalised usage. Which provider answers is decided by the model id alone.
"""

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Literal

from pydantic import BaseModel

Provider = Literal["openai", "google"]
StopReason = Literal["end", "max_tokens", "refusal", "tool_use", "other"]


def provider_of(model: str) -> Provider:
    """The provider that serves `model`, from its id prefix."""
    if model.startswith("gpt-"):
        return "openai"
    if model.startswith("gemini-"):
        return "google"
    raise ValueError(f"no provider is known for model {model!r}: use a gpt-* or gemini-* model")


class AINotConfigured(Exception):
    """Neither an OpenAI nor a Google key is set, so no AI call can be made."""


class LLMError(Exception):
    """A provider call failed (network, HTTP status, an unreadable answer). Call sites that
    skip or retry on an API error catch this; `BudgetExceeded` is deliberately not a subclass."""

    def __init__(self, message: str, *, provider: str = "", status: int | None = None) -> None:
        super().__init__(message)
        self.provider = provider
        self.status = status


@dataclass(frozen=True)
class ToolSpec:
    """A function the model may call (the app's own read tools, not a web search)."""

    name: str
    description: str
    schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Message:
    """One turn. `role="tool"` carries the result of the call `tool_call_id` (named
    `tool_name`). An assistant turn that called tools keeps the provider's own output items in
    `raw` and sends them back as they came: reasoning items (OpenAI) and thought signatures
    (Google) are needed to continue the turn."""

    role: Literal["user", "assistant", "tool"]
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    tool_name: str | None = None
    raw: Any = None


@dataclass(frozen=True)
class SearchSpec:
    """A web search the model may run. `allowed_domains` is enforced by the provider where it
    can (OpenAI) and checked on the citations afterwards where it cannot (Google)."""

    max_uses: int = 5
    allowed_domains: tuple[str, ...] = ()


@dataclass(frozen=True)
class Request:
    model: str
    system: str
    messages: tuple[Message, ...]
    schema: type[BaseModel] | None = None
    tools: tuple[ToolSpec, ...] = ()
    effort: str | None = None
    max_tokens: int = 4096
    search: SearchSpec | None = None
    timeout: float = 120.0
    # Static first, varying last; a stable key per job type lets OpenAI land related requests
    # on the same prefix cache. Ignored by the other providers.
    cache_key: str | None = None
    # "flex" asks OpenAI for its discounted, slower tier (answers may take minutes and are
    # refused when capacity is short; the client then repeats the call at standard). Ignored
    # by Google, which has no tier this app uses.
    priority: Literal["standard", "flex"] = "standard"
    # "24h" asks OpenAI to keep the prompt prefix cached for a day (`prompt_cache_retention`).
    # Ignored by Google.
    cache_retention: Literal["24h"] | None = None


@dataclass(frozen=True)
class Citation:
    url: str
    title: str = ""


def normalized_usage(
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
    searches: int = 0,
) -> Any:
    """One usage block for every provider (`input_tokens` excludes cache reads and writes),
    which `Usage.add`, `record_response` and the budget guard take. Providers report cached
    tokens as a subset of the prompt (OpenAI, Google); the adapter converts them, because
    adding them as they come would bill the cached tokens twice."""
    return SimpleNamespace(
        input_tokens=max(input_tokens, 0),
        output_tokens=max(output_tokens, 0),
        cache_creation_input_tokens=cache_write_tokens,
        cache_read_input_tokens=cache_read_tokens,
        server_tool_use=SimpleNamespace(web_search_requests=searches, web_fetch_requests=0),
    )


@dataclass
class Completion:
    model: str
    provider: Provider
    text: str
    parsed: BaseModel | None
    stop_reason: StopReason
    usage: Any
    citations: list[Citation] = field(default_factory=list)
    # Google cannot restrict a search to a domain list: citations outside it are removed here
    # and counted, so a job with source tiers can reject an answer that leaned on them.
    off_list_citations: list[Citation] = field(default_factory=list)
    # Set when the model asks for the app's tools (stop_reason "tool_use"); `raw` is what to
    # send back as the assistant turn.
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None
