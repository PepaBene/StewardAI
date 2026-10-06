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

Provider = Literal["anthropic", "openai", "google"]
StopReason = Literal["end", "max_tokens", "refusal", "other"]


def provider_of(model: str) -> Provider:
    """The provider that serves `model`, from its id prefix."""
    if model.startswith("claude-"):
        return "anthropic"
    if model.startswith("gpt-"):
        return "openai"
    if model.startswith("gemini-"):
        return "google"
    raise ValueError(f"no provider is known for model {model!r}")


class LLMError(Exception):
    """A provider call failed (network, HTTP status, an unreadable answer). Call sites that
    skip or retry on an API error catch this; `BudgetExceeded` is deliberately not a subclass."""

    def __init__(self, message: str, *, provider: str = "", status: int | None = None) -> None:
        super().__init__(message)
        self.provider = provider
        self.status = status


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant"]
    text: str


@dataclass(frozen=True)
class SearchSpec:
    """A web search the model may run. `allowed_domains` is enforced by the provider where it
    can (Anthropic, OpenAI) and checked on the citations afterwards where it cannot (Google)."""

    max_uses: int = 5
    allowed_domains: tuple[str, ...] = ()


@dataclass(frozen=True)
class Request:
    model: str
    system: str
    messages: tuple[Message, ...]
    schema: type[BaseModel] | None = None
    effort: str | None = None
    max_tokens: int = 4096
    search: SearchSpec | None = None
    timeout: float = 120.0
    # Static first, varying last; a stable key per job type lets OpenAI land related requests
    # on the same prefix cache. Ignored by the other providers.
    cache_key: str | None = None
    # Anthropic only: mark the system prompt as a cache breakpoint.
    cache_system: bool = False


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
    """A usage block shaped like Anthropic's (`input_tokens` excludes cache reads and writes),
    so `Usage.add`, `record_response` and the budget guard take it unchanged. Providers that
    report cached tokens as a subset of the prompt (OpenAI, Google) are converted by the
    adapter; adding them as they come would bill the cached tokens twice."""
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
