"""The client call sites use: `client.messages.parse(...)` routed to OpenAI or Google by model id.

A call site passes `model=..., system=..., messages=..., output_format=..., **effort_kwargs(...)`
and gets back a response object with `parsed_output`, `usage`, `stop_reason` and `content` (text
blocks, plus a `web_search_tool_result` block with the pages a search cited). The request goes
through `LLM.complete`, so every call is capped, counted and priced by the Budget config.

Only plain structured-output calls and calls with a `web_search*` tool are translated. A model
id that is not a `gpt-` or `gemini-` model is refused (`provider_of`).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

from steward.llm.batching import BatchCollector
from steward.llm.cache import ResultCache, request_key
from steward.llm.client import LLM
from steward.llm.types import (
    Message,
    Request,
    SearchSpec,
    normalized_usage,
    provider_of,
)

_STOP = {"end": "end_turn", "max_tokens": "max_tokens", "refusal": "refusal", "other": "end_turn"}


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for b in content:
        if isinstance(b, dict) and b.get("type") == "document":
            # A plain-text document block becomes a titled text section.
            source = b.get("source") or {}
            if source.get("type") != "text":
                # A PDF or image sent as bytes is not translated; the call sites that send
                # one also send the same document as text, which is.
                continue
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


def _search_blocks(urls: list[Any]) -> list[Any]:
    """The search-result block call sites read, rebuilt from cached (url, title) pairs."""
    if not urls:
        return []
    return [
        SimpleNamespace(
            type="web_search_tool_result",
            content=[SimpleNamespace(url=u[0], title=u[1]) for u in urls],
        )
    ]


def _result_urls(content: Any) -> list[list[str]]:
    """(url, title) of every page in the response's search-result blocks."""
    out: list[list[str]] = []
    for block in content or []:
        if getattr(block, "type", "") != "web_search_tool_result":
            continue
        results = getattr(block, "content", None)
        if not isinstance(results, list):
            continue
        for r in results:
            url = getattr(r, "url", None)
            if url:
                out.append([str(url), str(getattr(r, "title", "") or "")])
    return out


def _effort(kwargs: dict[str, Any]) -> str | None:
    effort = kwargs.get("effort")
    return str(effort) if effort else None


class _Messages:
    def __init__(self, owner: "RoutingClient") -> None:
        self._owner = owner

    async def parse(self, **kwargs: Any) -> Any:
        cache = self._owner.cache
        schema = kwargs.get("output_format")
        tools = kwargs.get("tools")
        searching = bool(tools)
        if cache is None or schema is None:
            return await self._send(kwargs)
        if searching and (
            cache.search_ttl_days is None
            or _search(tools) is None
            # A continuation turn is not a stable key.
            or any(not isinstance(m.get("content"), str) for m in kwargs.get("messages", []))
        ):
            return await self._send(kwargs)
        key = request_key(
            str(kwargs.get("model", "")),
            kwargs.get("system"),
            kwargs.get("messages"),
            schema,
            _effort(kwargs),
            tools,
        )
        hit = await cache.get(key, cache.search_ttl_days if searching else None)
        if hit is not None:
            try:
                urls = hit.get("urls") if searching else None
                body = hit.get("parsed") if searching else hit
                return SimpleNamespace(
                    type="message",
                    parsed_output=schema.model_validate(body),
                    usage=normalized_usage(),
                    stop_reason="end_turn",
                    content=_search_blocks(urls or []),
                    cached=True,
                )
            except ValueError:
                pass  # the schema changed shape: ask again
        response = await self._send(kwargs)
        parsed = getattr(response, "parsed_output", None)
        if parsed is not None and getattr(response, "stop_reason", None) == "end_turn":
            result = parsed.model_dump(mode="json")
            urls = _result_urls(response.content) if searching else []
            if searching:
                result = {"parsed": result, "urls": urls}
            # A search that returned no sources failed (or answered from memory): asking again
            # later may find them, so it is not kept.
            if urls or not searching:
                await cache.put(key, str(kwargs.get("model", "")), result)
        return response

    async def _send(self, kwargs: dict[str, Any]) -> Any:
        model = str(kwargs.get("model", ""))
        # A model id that is neither gpt-* nor gemini-* fails here, loudly, instead of
        # quietly spending on a provider the app does not use.
        provider_of(model)
        system = kwargs.get("system") or ""
        request = Request(
            model=model,
            system=system if isinstance(system, str) else _text(system),
            messages=tuple(
                Message(m["role"], _text(m["content"])) for m in kwargs.get("messages", [])
            ),
            schema=kwargs.get("output_format"),
            effort=_effort(kwargs),
            cache_key=str(kwargs["cache_key"]) if kwargs.get("cache_key") else None,
            priority="flex" if kwargs.get("priority") == "flex" else "standard",
            # OpenAI and Google count their reasoning tokens against the output limit, which
            # call sites sized for an answer alone; the limit is a ceiling, not a cost.
            max_tokens=max(int(kwargs.get("max_tokens") or 4096) * 3, 8000),
            search=_search(kwargs.get("tools")),
            timeout=float(kwargs["timeout"])
            if isinstance(kwargs.get("timeout"), int | float)
            else 120.0,
        )
        collector = self._owner.collector
        # An `LLMError` is the call sites' "API error": they skip or retry on it, and
        # `BudgetExceeded` (not an `LLMError`) passes through.
        if collector is not None:
            done = await collector.submit(request)
        else:
            done = await self._owner.llm.complete(request)
        content: list[Any] = [SimpleNamespace(type="text", text=done.text, citations=None)]
        if done.citations:
            # The pages the answer cites, as the search-result block the search call sites
            # read to check every source against the allowed tiers.
            content.append(
                SimpleNamespace(
                    type="web_search_tool_result",
                    content=[SimpleNamespace(url=c.url, title=c.title) for c in done.citations],
                )
            )
        return SimpleNamespace(
            type="message",
            parsed_output=done.parsed,
            usage=done.usage,
            stop_reason=_STOP[done.stop_reason],
            content=content,
        )


class RoutingClient:
    def __init__(self, llm: LLM, cache: ResultCache | None = None) -> None:
        self.llm = llm
        self.cache = cache
        # Set to a BatchCollector to send calls through the provider's batch API.
        self.collector: BatchCollector | None = None
        self.messages = _Messages(self)


ModelClient = RoutingClient


def create_routing_client(settings: Any) -> tuple[RoutingClient, LLM]:
    """The client and its `LLM` from the settings, for scripts (the evals)."""
    from steward.llm.client import create_llm

    llm = create_llm(settings)
    return routing_client(llm), llm


@contextmanager
def batch_collector(
    client: RoutingClient, llm: LLM, wait_minutes: float
) -> Iterator[BatchCollector]:
    """A collector the `client` sends its calls to while the block runs; run the per-holding
    flows with `collector.run` inside it."""
    collector = BatchCollector(llm, wait_minutes)
    client.collector = collector
    try:
        yield collector
    finally:
        client.collector = None


async def parse_many(
    client: RoutingClient,
    calls: list[dict[str, Any]],
    *,
    batch: bool = False,
    wait_minutes: float = 60.0,
) -> list[Any]:
    """`client.messages.parse(**kwargs)` for each of `calls`, in order: the response, or the
    exception a call raised (`BudgetExceeded` is raised). With `batch` the calls go to the
    provider's batch API as one batch (about half the price)."""
    from steward.budget_guard import BudgetExceeded

    async def one(kwargs: dict[str, Any]) -> Any:
        return await client.messages.parse(**kwargs)

    models = {str(kw.get("model", "")) for kw in calls}
    if batch and len(models) == 1 and client.llm.configured(next(iter(models))):
        with batch_collector(client, client.llm, wait_minutes) as collector:
            out = await collector.run([one(kw) for kw in calls])
    else:
        out = []
        for kwargs in calls:
            try:
                out.append(await one(kwargs))
            except BudgetExceeded:
                raise
            except Exception as exc:  # the caller reports it per item
                out.append(exc)
    for item in out:
        if isinstance(item, BudgetExceeded):
            raise item
    return out


def routing_client(llm: LLM, cache: ResultCache | None = None) -> RoutingClient:
    """The client for `llm`. With a `cache`, a structured call (no tools) whose request repeats
    exactly is answered from it."""
    return RoutingClient(llm, cache)


__all__ = [
    "ModelClient",
    "RoutingClient",
    "batch_collector",
    "create_routing_client",
    "parse_many",
    "routing_client",
]
