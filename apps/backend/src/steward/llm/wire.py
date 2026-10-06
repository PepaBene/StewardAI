"""Request bodies and response parsing for the OpenAI Responses API and Google's
generateContent. Pure functions (no network), so they are tested against recorded shapes."""

import copy
import json
import logging
import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ValidationError

from steward.llm.types import (
    Citation,
    Completion,
    LLMError,
    Message,
    Request,
    StopReason,
    ToolCall,
    normalized_usage,
)

logger = logging.getLogger(__name__)


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """`schema` in the form OpenAI's strict structured output accepts: every object lists all
    its properties as required and forbids extra ones. Optional fields must be nullable in the
    model itself (`X | None`), which pydantic already writes as `anyOf` with null."""
    out = copy.deepcopy(schema)

    def walk(node: Any) -> None:
        """`node` is a schema; its `properties` and `$defs` are maps of schemas (a property
        may itself be called "title" or "default", so the maps are never stripped)."""
        if isinstance(node, list):
            for value in node:
                walk(value)
            return
        if not isinstance(node, dict):
            return
        for key in ("default", "title", "examples"):
            node.pop(key, None)
        if node.get("type") == "object" or "properties" in node:
            node["required"] = list(node.get("properties", {}))
            node["additionalProperties"] = False
        for key, value in node.items():
            if key in ("properties", "$defs") and isinstance(value, dict):
                for sub in value.values():
                    walk(sub)
            else:
                walk(value)

    walk(out)
    return out


def _parse(schema: type[BaseModel] | None, text: str, provider: str) -> BaseModel | None:
    if schema is None or not text.strip():
        return None
    try:
        return schema.model_validate_json(text)
    except ValidationError as exc:
        raise LLMError(
            f"{provider} answer does not match the schema: {exc}", provider=provider
        ) from exc


# --------------------------------------------------------------------------- OpenAI


def openai_body(req: Request) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": req.model,
        "instructions": req.system,
        "input": _openai_input(req.messages),
        "max_output_tokens": req.max_tokens,
        "store": False,
    }
    if req.effort:
        body["reasoning"] = {"effort": req.effort}
    if req.cache_key:
        body["prompt_cache_key"] = req.cache_key
    if req.schema is not None:
        body["text"] = {
            "format": {
                "type": "json_schema",
                "name": req.schema.__name__,
                "schema": strict_schema(req.schema.model_json_schema()),
                "strict": True,
            }
        }
    tools: list[dict[str, Any]] = [
        {
            "type": "function",
            "name": t.name,
            "description": t.description,
            "parameters": t.schema,
            "strict": False,
        }
        for t in req.tools
    ]
    if req.search is not None:
        tool: dict[str, Any] = {"type": "web_search"}
        if req.search.allowed_domains:
            tool["filters"] = {"allowed_domains": list(req.search.allowed_domains[:100])}
        tools.append(tool)
        body["max_tool_calls"] = req.search.max_uses
    if tools:
        body["tools"] = tools
    return body


def _openai_input(messages: Sequence[Message]) -> list[Any]:
    out: list[Any] = []
    for m in messages:
        if m.role == "tool":
            out.append(
                {"type": "function_call_output", "call_id": m.tool_call_id, "output": m.text}
            )
        elif m.role == "assistant" and m.raw is not None:
            out.extend(m.raw)  # the previous output items, reasoning included
        else:
            out.append({"role": m.role, "content": m.text})
    return out


def openai_completion(req: Request, data: dict[str, Any]) -> Completion:
    text_parts: list[str] = []
    citations: list[Citation] = []
    calls: list[ToolCall] = []
    searches = 0
    refused = False
    for item in data.get("output") or []:
        kind = item.get("type")
        if kind == "web_search_call":
            searches += 1
        elif kind == "function_call":
            try:
                args = json.loads(item.get("arguments") or "{}")
            except ValueError:
                args = {}
            calls.append(
                ToolCall(
                    str(item.get("call_id") or item.get("id")),
                    str(item.get("name")),
                    args if isinstance(args, dict) else {},
                )
            )
        elif kind == "message":
            for part in item.get("content") or []:
                if part.get("type") == "output_text":
                    text_parts.append(part.get("text") or "")
                    for note in part.get("annotations") or []:
                        if note.get("type") == "url_citation" and note.get("url"):
                            citations.append(Citation(note["url"], note.get("title") or ""))
                elif part.get("type") == "refusal":
                    refused = True
    usage = data.get("usage") or {}
    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens") or 0
    prompt = usage.get("input_tokens") or 0
    stop: StopReason = "end"
    if calls:
        stop = "tool_use"
    elif refused:
        stop = "refusal"
    elif data.get("status") == "incomplete":
        reason = (data.get("incomplete_details") or {}).get("reason")
        stop = "max_tokens" if reason == "max_output_tokens" else "other"
    elif data.get("status") not in (None, "completed"):
        raise LLMError(
            f"openai response {data.get('status')}: {data.get('error')}", provider="openai"
        )
    text = "".join(text_parts)
    parsed = _parse(req.schema, text, "openai") if stop == "end" else None
    return Completion(
        model=req.model,
        provider="openai",
        text=text,
        parsed=parsed,
        stop_reason=stop,
        # Cached tokens are a subset of the prompt tokens: bill them once, at the cache price.
        usage=normalized_usage(
            input_tokens=prompt - cached,
            output_tokens=usage.get("output_tokens") or 0,
            cache_read_tokens=cached,
            searches=searches,
        ),
        citations=_dedupe(citations),
        tool_calls=calls,
        raw=list(data.get("output") or []) if calls else None,
    )


# --------------------------------------------------------------------------- Google

_GOOGLE_LEVEL = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}


def google_body(req: Request) -> dict[str, Any]:
    config: dict[str, Any] = {"maxOutputTokens": req.max_tokens}
    if req.schema is not None:
        config["responseMimeType"] = "application/json"
        config["responseJsonSchema"] = req.schema.model_json_schema()
    if req.effort:
        config["thinkingConfig"] = {"thinkingLevel": _GOOGLE_LEVEL.get(req.effort, "medium")}
    body: dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": req.system}]},
        "contents": _google_contents(req.messages),
        "generationConfig": config,
    }
    tools: list[dict[str, Any]] = []
    if req.tools:
        tools.append(
            {
                "functionDeclarations": [
                    {"name": t.name, "description": t.description, "parametersJsonSchema": t.schema}
                    for t in req.tools
                ]
            }
        )
    if req.search is not None:
        tools.append({"googleSearch": {}})
    if tools:
        body["tools"] = tools
    return body


def _google_contents(messages: Sequence[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            part = {"functionResponse": {"name": m.tool_name, "response": {"result": m.text}}}
            if out and out[-1].get("role") == "user" and out[-1].get("_tool"):
                out[-1]["parts"].append(part)  # all results of one turn share one content
            else:
                out.append({"role": "user", "parts": [part], "_tool": True})
        elif m.role == "assistant" and m.raw is not None:
            out.append(m.raw)  # the model's own content, thought signatures included
        else:
            out.append(
                {"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.text}]}
            )
    return [{k: v for k, v in c.items() if k != "_tool"} for c in out]


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def allowed(url: str, domains: tuple[str, ...]) -> bool:
    """Is `url` on one of `domains` (or a subdomain of one)? An empty list allows everything."""
    host = _host(url)
    return not domains or any(host == d or host.endswith("." + d) for d in domains)


def google_completion(req: Request, data: dict[str, Any]) -> Completion:
    candidates = data.get("candidates") or []
    if not candidates:
        block = (data.get("promptFeedback") or {}).get("blockReason")
        raise LLMError(f"google returned no candidate (blocked: {block})", provider="google")
    cand = candidates[0]
    text = "".join(
        p.get("text") or ""
        for p in (cand.get("content") or {}).get("parts") or []
        if not p.get("thought")
    )
    calls = [
        ToolCall(
            str(p["functionCall"].get("id") or f"call_{i}"),
            str(p["functionCall"].get("name")),
            dict(p["functionCall"].get("args") or {}),
        )
        for i, p in enumerate((cand.get("content") or {}).get("parts") or [])
        if isinstance(p.get("functionCall"), dict)
    ]
    finish = cand.get("finishReason")
    stops: dict[str, StopReason] = {"STOP": "end", "MAX_TOKENS": "max_tokens"}
    stop = "tool_use" if calls else stops.get(str(finish), "other")
    if finish in ("SAFETY", "PROHIBITED_CONTENT", "RECITATION", "BLOCKLIST"):
        stop = "refusal"
    grounding = cand.get("groundingMetadata") or {}
    found = [
        Citation((c.get("web") or {}).get("uri") or "", (c.get("web") or {}).get("title") or "")
        for c in grounding.get("groundingChunks") or []
    ]
    found = [c for c in found if c.url]
    domains = req.search.allowed_domains if req.search else ()
    kept = [c for c in found if allowed(c.url, domains)]
    off = [c for c in found if not allowed(c.url, domains)]
    meta = data.get("usageMetadata") or {}
    cached = meta.get("cachedContentTokenCount") or 0
    searches = len(grounding.get("webSearchQueries") or [])
    parsed = _parse(req.schema, text, "google") if stop == "end" else None
    return Completion(
        model=req.model,
        provider="google",
        text=text,
        parsed=parsed,
        stop_reason=stop,
        # promptTokenCount includes the cached tokens; thinking tokens are billed as output.
        usage=normalized_usage(
            input_tokens=(meta.get("promptTokenCount") or 0) - cached,
            output_tokens=(meta.get("candidatesTokenCount") or 0)
            + (meta.get("thoughtsTokenCount") or 0),
            cache_read_tokens=cached,
            searches=searches,
        ),
        citations=_dedupe(kept),
        off_list_citations=_dedupe(off),
        tool_calls=calls,
        raw=cand.get("content") if calls else None,
    )


# --------------------------------------------------------------------------- Batch
# Both providers take a set of requests, run them within a day at half price, and return one
# answer per request. A request that failed comes back as None, so the caller can ask again
# directly; one bad request never loses the others.


def custom_id(index: int) -> str:
    return f"r{index}"


def openai_batch_lines(requests: Sequence[Request]) -> str:
    """The JSONL file of an OpenAI batch: one /v1/responses request per line."""
    return "\n".join(
        json.dumps(
            {
                "custom_id": custom_id(i),
                "method": "POST",
                "url": "/v1/responses",
                "body": openai_body(r),
            }
        )
        for i, r in enumerate(requests)
    )


def _by_index(lines: Sequence[tuple[str, Any]], n: int) -> list[Any]:
    out: list[Any] = [None] * n
    for key, value in lines:
        if key.startswith("r") and key[1:].isdigit() and int(key[1:]) < n:
            out[int(key[1:])] = value
    return out


def openai_batch_results(text: str, requests: Sequence[Request]) -> list[Completion | None]:
    """The answers in an OpenAI batch's output file (JSONL), by request; None where a request
    failed, was refused or does not match its schema."""
    found: list[tuple[str, Completion | None]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        response = row.get("response") or {}
        key = str(row.get("custom_id"))
        index = int(key[1:]) if key[1:].isdigit() and key.startswith("r") else -1
        if row.get("error") or response.get("status_code") != 200 or not 0 <= index < len(requests):
            found.append((key, None))
            continue
        try:
            found.append((key, openai_completion(requests[index], response.get("body") or {})))
        except LLMError:
            found.append((key, None))
    return _by_index(found, len(requests))


def google_batch_body(requests: Sequence[Request], name: str) -> dict[str, Any]:
    """The `batchGenerateContent` body: the requests inline, each tagged with its key."""
    return {
        "batch": {
            "display_name": name,
            "input_config": {
                "requests": {
                    "requests": [
                        {"request": google_body(r), "metadata": {"key": custom_id(i)}}
                        for i, r in enumerate(requests)
                    ]
                }
            },
        }
    }


GOOGLE_DONE = {"BATCH_STATE_SUCCEEDED", "JOB_STATE_SUCCEEDED"}
GOOGLE_FAILED = {
    "BATCH_STATE_FAILED",
    "BATCH_STATE_CANCELLED",
    "BATCH_STATE_EXPIRED",
    "JOB_STATE_FAILED",
    "JOB_STATE_CANCELLED",
    "JOB_STATE_EXPIRED",
}


def google_batch_state(data: dict[str, Any]) -> str:
    """ "done", "failed" or "running" from a Google batch operation."""
    state = str((data.get("metadata") or {}).get("state") or data.get("state") or "")
    if state in GOOGLE_DONE or (data.get("done") and not data.get("error") and not state):
        return "done"
    if state in GOOGLE_FAILED or data.get("error"):
        return "failed"
    return "running"


def _google_inlined(data: dict[str, Any]) -> list[Any]:
    for holder in (data.get("response"), data.get("dest"), data.get("metadata"), data):
        if not isinstance(holder, dict):
            continue
        inline = holder.get("inlinedResponses")
        if isinstance(inline, dict):
            inline = inline.get("inlinedResponses")
        if isinstance(inline, list):
            return inline
    return []


def google_batch_results(
    data: dict[str, Any], requests: Sequence[Request]
) -> list[Completion | None]:
    found: list[tuple[str, Completion | None]] = []
    for position, item in enumerate(_google_inlined(data)):
        key = str((item.get("metadata") or {}).get("key") or custom_id(position))
        index = int(key[1:]) if key[1:].isdigit() and key.startswith("r") else -1
        if item.get("error") or not item.get("response") or not 0 <= index < len(requests):
            logger.warning(
                "google batch item %s has no answer: %s", key, str(item.get("error"))[:200]
            )
            found.append((key, None))
            continue
        try:
            found.append((key, google_completion(requests[index], item["response"])))
        except LLMError as exc:
            logger.warning("google batch item %s dropped: %s", key, str(exc)[:200])
            found.append((key, None))
    return _by_index(found, len(requests))


GROUNDING_HOST = "vertexaisearch.cloud.google.com"


def is_grounding_redirect(url: str) -> bool:
    """A Google Search grounding link (it redirects to the cited page)."""
    return _host(url) == GROUNDING_HOST


_REDIRECT_IN_TEXT = re.compile(
    r"https://vertexaisearch\.cloud\.google\.com/grounding-api-redirect/[A-Za-z0-9_\-=%]+"
)


def grounding_redirects_in(text: str) -> list[str]:
    """Grounding redirect links quoted in an answer's text, in order."""
    return _REDIRECT_IN_TEXT.findall(text)


def dedupe(citations: list[Citation]) -> list[Citation]:
    return _dedupe(citations)


def _dedupe(citations: list[Citation]) -> list[Citation]:
    seen: set[str] = set()
    out = []
    for c in citations:
        if c.url not in seen:
            seen.add(c.url)
            out.append(c)
    return out


def error_message(body: Any) -> str:
    """A short reason from a provider's error body."""
    try:
        data = body if isinstance(body, dict) else json.loads(body)
        err = data.get("error")
        return str(err.get("message") if isinstance(err, dict) else err)[:300]
    except (ValueError, AttributeError):
        return str(body)[:300]
