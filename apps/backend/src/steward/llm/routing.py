"""A drop-in for the Anthropic client that routes `messages.parse` by model id.

Call sites written against `client.messages.parse(model=..., system=..., messages=...,
output_format=..., **effort_kwargs(...))` keep working unchanged: a Claude model goes to the
Anthropic client as before, a `gpt-` or `gemini-` model goes through `LLM.complete` and its
answer comes back shaped like an Anthropic response (`parsed_output`, `usage`, `stop_reason`,
`content`). Everything else (`with_options`, batches, streaming) is the Anthropic client's own,
so a call that needs it stays on Claude models.

Only plain structured-output calls and web-search calls with a `web_search*` tool are
translated; a call with other tools, or a call that reads Anthropic content blocks such as
`pause_turn` or citation results, must not be pointed at another provider's model.
"""

from types import SimpleNamespace
from typing import Any, cast

import anthropic

from steward.llm.client import LLM
from steward.llm.types import Message, Request, SearchSpec, provider_of

_STOP = {"end": "end_turn", "max_tokens": "max_tokens", "refusal": "refusal", "other": "end_turn"}


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for b in content:
        if isinstance(b, dict) and b.get("type") == "document":
            # A plain-text document block becomes a titled text section.
            source = b.get("source") or {}
            parts.append(
                f'<document title="{b.get("title", "")}">\n{source.get("data", "")}\n</document>'
            )
        else:
            parts.append(b.get("text", "") if isinstance(b, dict) else getattr(b, "text", ""))
    return "\n\n".join(parts)


def _search(tools: Any) -> SearchSpec | None:
    for tool in tools or []:
        if isinstance(tool, dict) and str(tool.get("type", "")).startswith("web_search"):
            return SearchSpec(
                max_uses=int(tool.get("max_uses") or 5),
                allowed_domains=tuple(tool.get("allowed_domains") or ()),
            )
    return None


def _effort(kwargs: dict[str, Any]) -> str | None:
    config = kwargs.get("output_config")
    effort = config.get("effort") if isinstance(config, dict) else None
    if effort is None:
        thinking = kwargs.get("thinking")
        if isinstance(thinking, dict) and thinking.get("budget_tokens"):
            effort = "high"  # an Anthropic thinking budget: another provider has no such knob
    return str(effort) if effort else None


class _Messages:
    def __init__(self, owner: "RoutingClient") -> None:
        self._owner = owner

    async def parse(self, **kwargs: Any) -> Any:
        model = str(kwargs.get("model", ""))
        anthropic_client = self._owner.anthropic
        if provider_of(model) == "anthropic":
            assert anthropic_client is not None, "ANTHROPIC_API_KEY is not set"
            return await anthropic_client.messages.parse(**kwargs)
        system = kwargs.get("system") or ""
        request = Request(
            model=model,
            system=system if isinstance(system, str) else _text(system),
            messages=tuple(
                Message(m["role"], _text(m["content"])) for m in kwargs.get("messages", [])
            ),
            schema=kwargs.get("output_format"),
            effort=_effort(kwargs),
            max_tokens=int(kwargs.get("max_tokens") or 4096),
            search=_search(kwargs.get("tools")),
            timeout=float(kwargs["timeout"])
            if isinstance(kwargs.get("timeout"), int | float)
            else 120.0,
        )
        done = await self._owner.llm.complete(request)
        return SimpleNamespace(
            type="message",
            parsed_output=done.parsed,
            usage=done.usage,
            stop_reason=_STOP[done.stop_reason],
            content=[SimpleNamespace(type="text", text=done.text, citations=None)],
        )


class RoutingClient:
    def __init__(self, anthropic_client: anthropic.AsyncAnthropic | None, llm: LLM) -> None:
        self.anthropic = anthropic_client
        self.llm = llm
        self.messages = _Messages(self)

    def __getattr__(self, name: str) -> Any:
        if self.anthropic is None:
            raise AttributeError(name)
        return getattr(self.anthropic, name)


def routing_client(
    anthropic_client: anthropic.AsyncAnthropic | None, llm: LLM
) -> anthropic.AsyncAnthropic:
    """The routing client, typed as the Anthropic client the call sites expect."""
    return cast(anthropic.AsyncAnthropic, RoutingClient(anthropic_client, llm))
