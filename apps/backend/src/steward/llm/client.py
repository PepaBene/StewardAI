"""`LLM.complete`: one call that any provider answers, under the Budget config's caps.

Anthropic requests go through `BudgetedAnthropic`, which guards and records them itself. For
OpenAI and Google this module does the same, so every provider is capped, counted per step
(`record_response`, so a failed or killed step keeps its cost) and priced from the Budget price
table. At the monthly cap a request may be sent to `downgrade_model`, which can belong to
another provider: the provider is chosen after the guard has had its say.
"""

import asyncio
import dataclasses
import json
import logging
from collections.abc import Sequence
from typing import Any, cast

import anthropic
import httpx

from steward import budget_guard
from steward.config.jobs import reasoning_kwargs
from steward.jobs.runner import current_job, current_step, record_response
from steward.llm import wire
from steward.llm.types import (
    Citation,
    Completion,
    LLMError,
    Message,
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
GOOGLE_MODELS = "https://generativelanguage.googleapis.com/v1beta/models"
GOOGLE_BATCHES = "https://generativelanguage.googleapis.com/v1beta/batches"
OPENAI_FILES = "https://api.openai.com/v1/files"
OPENAI_BATCHES = "https://api.openai.com/v1/batches"
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}
DEADLINE_MARGIN = 30.0
MAX_PAUSES = 4
# Search calls a batch request makes directly (Gemini batch mode does not ground) at once.
DIRECT_SEARCH_CONCURRENCY = 4


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
        if req.tools or any(m.role == "tool" for m in req.messages):
            # Claude's tool loop (tool_use blocks) is kept by its call sites, not translated.
            raise LLMError(
                "app tools are not supported through LLM for Claude", provider="anthropic"
            )
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
        if not _search_with_schema(req):
            return await self._google_once(req)
        found = await self._google_once(dataclasses.replace(req, schema=None))
        return _merge_search(found, await self._google_once(_finish_request(req, found)))

    async def _google_once(self, req: Request) -> Completion:
        data = await self._post(
            "google",
            GOOGLE_URL.format(model=req.model),
            {"x-goog-api-key": self._key("google")},
            wire.google_body(req),
            req,
        )
        return await self._resolve_google(req, wire.google_completion(req, data))

    async def _resolve_google(self, req: Request, done: Completion) -> Completion:
        """Google's grounding citations are redirect links on vertexaisearch.cloud.google.com,
        whose page title is the real site's domain. Follow each redirect to the real page (or
        fall back to the domain in the title), then split kept from off-list citations again
        by the request's allowed domains, which could not be done on the redirect host."""
        every = [*done.citations, *done.off_list_citations]
        # Gemini sometimes searches and quotes the redirect links in its answer but returns no
        # grounding metadata: those links are the sources, so they are resolved the same way.
        listed = {c.url for c in every}
        quoted = [
            Citation(url, "")
            for url in dict.fromkeys(wire.grounding_redirects_in(done.text))
            if url not in listed
        ]
        every += quoted
        if not any(wire.is_grounding_redirect(c.url) for c in every):
            return done
        resolved = [await self._real_url(c) for c in every]
        # The model quotes the redirect links it was shown in its answer: write the real
        # URLs there too, so a job that checks its answer against the citations matches them
        # (and a follow-up call that is handed this answer sees real addresses).
        swaps = {
            old.url: new.url for old, new in zip(every, resolved, strict=True) if old.url != new.url
        }
        text = done.text
        for old_url, new_url in swaps.items():
            text = text.replace(old_url, new_url)
        if text != done.text:
            done.text = text
            if done.parsed is not None:
                done.parsed = type(done.parsed).model_validate_json(text)
        # A quoted link that could not be resolved names no page: drop it.
        resolved = [c for c in resolved if not wire.is_grounding_redirect(c.url)] + [
            c for c in resolved if wire.is_grounding_redirect(c.url) and c.title
        ]
        domains = req.search.allowed_domains if req.search else ()
        done.citations = wire.dedupe([c for c in resolved if wire.allowed(c.url, domains)])
        done.off_list_citations = wire.dedupe(
            [c for c in resolved if not wire.allowed(c.url, domains)]
        )
        return done

    async def _real_url(self, citation: Citation) -> Citation:
        if not wire.is_grounding_redirect(citation.url):
            return citation
        try:
            response = await self._http.get(citation.url, follow_redirects=False, timeout=10.0)
            target = response.headers.get("location")
            if target and response.is_redirect:
                return Citation(target, citation.title)
        except httpx.HTTPError:
            pass
        title = citation.title.strip().lower()
        if "." in title and " " not in title:
            return Citation(f"https://{title}/", citation.title)
        return citation

    def _key(self, provider: Provider) -> str:
        key = self._keys[provider]
        if key is None:
            raise LLMError(f"the {provider} API key is not set", provider=provider)
        return key

    async def _post(
        self, provider: str, url: str, headers: dict[str, str], body: dict[str, Any], req: Request
    ) -> dict[str, Any]:
        response = await self._call("POST", provider, url, headers, req.timeout, json=body)
        result: dict[str, Any] = response.json()
        return result

    async def _call(
        self,
        method: str,
        provider: str,
        url: str,
        headers: dict[str, str],
        timeout: float,  # noqa: ASYNC109 - an httpx request timeout, not a cancellation scope
        **kwargs: Any,
    ) -> httpx.Response:
        """One HTTP call with retries on rate limits, server errors and timeouts, and a hard
        deadline so a stalled connection cannot hang the job."""
        last = ""
        deadline = timeout * (self._retries + 1) + DEADLINE_MARGIN
        try:
            async with asyncio.timeout(deadline):
                for attempt in range(self._retries + 1):
                    try:
                        response = await self._http.request(
                            method, url, headers=headers, timeout=timeout, **kwargs
                        )
                    except httpx.HTTPError as exc:
                        last = f"{type(exc).__name__}: {exc}"
                    else:
                        if response.status_code == 200:
                            return response
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

    # ------------------------------------------------------------------ Batch

    async def complete_batch(
        self,
        requests: Sequence[Request],
        *,
        poll_seconds: float = 30.0,
        wait_minutes: float = 60.0,
    ) -> list[Completion | None]:
        """The answers to `requests` (one model) through the provider's batch API at half
        price, in request order; None for a request that failed. All None when the batch
        was not finished within `wait_minutes` (it is cancelled) or could not run, so the caller
        asks again directly. The cost is counted by the caller's step (`Usage(batch=True)`)
        and by the budget guard; the batch id is saved on the step when it is submitted."""
        split = {i for i, r in enumerate(requests) if _search_with_schema(r)}
        if not split:
            return await self._batch_once(requests, poll_seconds, wait_minutes)
        # Gemini returns no grounding citations when a search and a schema share a call, and none
        # at all for a search run in batch mode: the searches run as direct calls (full price),
        # then the structured answers as one batch.
        rest = [i for i in range(len(requests)) if i not in split]
        searched = sorted(split)
        answers: list[Completion | None] = [None] * len(requests)
        batched = await self._batch_once([requests[i] for i in rest], poll_seconds, wait_minutes)
        found = await self._search_directly(
            [dataclasses.replace(requests[i], schema=None) for i in searched]
        )
        for i, done in [*zip(rest, batched, strict=True), *zip(searched, found, strict=True)]:
            answers[i] = done
        pending = [i for i in searched if answers[i] is not None]
        finished = await self._batch_once(
            [_finish_request(requests[i], cast(Completion, answers[i])) for i in pending],
            poll_seconds,
            wait_minutes,
        )
        done_final = dict(zip(pending, finished, strict=True))
        for i in split:
            final = done_final.get(i)
            answers[i] = (
                _merge_search(cast(Completion, answers[i]), final) if final is not None else None
            )
        return answers

    async def _search_directly(self, requests: Sequence[Request]) -> list[Completion | None]:
        """Google search calls made one by one (a few at a time); None for one that failed or
        when the budget guard would change the model (the caller then asks directly)."""
        if not requests:
            return []
        ctx = current_job.get()
        model = requests[0].model
        if ctx is not None:
            if await budget_guard.before_request(ctx, model, can_switch_provider=True) != model:
                return [None] * len(requests)
        gate = asyncio.Semaphore(DIRECT_SEARCH_CONCURRENCY)

        async def one(req: Request) -> Completion | None:
            async with gate:
                try:
                    done = await self._google_once(req)
                except LLMError as exc:
                    logger.warning("google search call failed: %s", exc)
                    return None
            if ctx is not None:
                budget_guard.after_response(ctx, done.model, done.usage)
            return done

        return list(await asyncio.gather(*(one(r) for r in requests)))

    async def _batch_once(
        self, requests: Sequence[Request], poll_seconds: float, wait_minutes: float
    ) -> list[Completion | None]:
        if not requests:
            return []
        none: list[Completion | None] = [None] * len(requests)
        model = requests[0].model
        provider = provider_of(model)
        if provider == "anthropic" or any(r.model != model for r in requests):
            return none
        ctx = current_job.get()
        if ctx is not None:
            guarded = await budget_guard.before_request(ctx, model, can_switch_provider=True)
            if guarded != model:
                return none  # at the cap: the direct path applies the downgrade
        try:
            run = self._submit_openai if provider == "openai" else self._submit_google
            batch_id, poll, collect = await run(requests)
        except LLMError as exc:
            logger.warning("%s batch could not start: %s", provider, exc)
            return none
        current = current_step.get()
        if current is not None:
            current[1].detail.setdefault("batch_ids", []).append(batch_id)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_minutes * 60
        try:
            while True:
                state = await poll()
                if state != "running":
                    break
                if loop.time() > deadline:
                    await self._cancel(provider, batch_id)
                    logger.warning("%s batch %s not done in time", provider, batch_id)
                    return none
                await asyncio.sleep(poll_seconds)
            if state == "failed":
                logger.warning("%s batch %s failed", provider, batch_id)
                return none
            done = await collect()
        except LLMError as exc:
            logger.warning("%s batch %s: %s", provider, batch_id, exc)
            return none
        if ctx is not None:
            for item in done:
                if item is not None:
                    budget_guard.after_response(ctx, item.model, item.usage, batch=True)
        return list(done)

    async def _cancel(self, provider: Provider, batch_id: str) -> None:
        try:
            if provider == "openai":
                await self._call(
                    "POST",
                    "openai",
                    f"{OPENAI_BATCHES}/{batch_id}/cancel",
                    {"authorization": f"Bearer {self._key('openai')}"},
                    60.0,
                )
            else:
                await self._call(
                    "POST",
                    "google",
                    f"{GOOGLE_BATCHES}/{batch_id}:cancel",
                    {"x-goog-api-key": self._key("google")},
                    60.0,
                )
        except LLMError:
            logger.warning("could not cancel %s batch %s", provider, batch_id)

    async def _submit_openai(self, requests: Sequence[Request]) -> tuple[str, Any, Any]:
        headers = {"authorization": f"Bearer {self._key('openai')}"}
        uploaded = await self._call(
            "POST",
            "openai",
            OPENAI_FILES,
            headers,
            120.0,
            data={"purpose": "batch"},
            files={
                "file": (
                    "batch.jsonl",
                    wire.openai_batch_lines(requests).encode(),
                    "application/jsonl",
                )
            },
        )
        created = await self._call(
            "POST",
            "openai",
            OPENAI_BATCHES,
            headers,
            60.0,
            json={
                "input_file_id": uploaded.json()["id"],
                "endpoint": "/v1/responses",
                "completion_window": "24h",
            },
        )
        batch_id = str(created.json()["id"])
        last: dict[str, Any] = {}

        async def poll() -> str:
            got = await self._call("GET", "openai", f"{OPENAI_BATCHES}/{batch_id}", headers, 60.0)
            last.update(got.json())
            status = last.get("status")
            if status == "completed":
                return "done"
            if status in ("failed", "expired", "cancelled", "cancelling"):
                return "failed"
            return "running"

        async def collect() -> list[Completion | None]:
            file_id = last.get("output_file_id")
            if not file_id:
                return [None] * len(requests)
            body = await self._call(
                "GET", "openai", f"{OPENAI_FILES}/{file_id}/content", headers, 120.0
            )
            return wire.openai_batch_results(body.text, requests)

        return batch_id, poll, collect

    async def _submit_google(self, requests: Sequence[Request]) -> tuple[str, Any, Any]:
        headers = {"x-goog-api-key": self._key("google")}
        created = await self._call(
            "POST",
            "google",
            f"{GOOGLE_MODELS}/{requests[0].model}:batchGenerateContent",
            headers,
            120.0,
            json=wire.google_batch_body(requests, "stewardai"),
        )
        batch_id = str(created.json()["name"]).removeprefix("batches/")
        last: dict[str, Any] = {}

        async def poll() -> str:
            got = await self._call("GET", "google", f"{GOOGLE_BATCHES}/{batch_id}", headers, 60.0)
            last.clear()
            last.update(got.json())
            return wire.google_batch_state(last)

        async def collect() -> list[Completion | None]:
            results = wire.google_batch_results(last, requests)
            return [
                await self._resolve_google(r, done) if done is not None else None
                for r, done in zip(requests, results, strict=True)
            ]

        return batch_id, poll, collect


def _downgraded(req: Request, model: str) -> Request:
    """`req` for the downgrade model: effort dropped (it may not support it), and the search
    kept only when the target is the same provider (the tool types differ between them)."""
    from dataclasses import replace

    same = provider_of(model) == provider_of(req.model)
    return replace(
        req, model=model, effort=None, search=req.search if same else None, cache_key=None
    )


def _search_with_schema(req: Request) -> bool:
    return provider_of(req.model) == "google" and req.schema is not None and req.search is not None


def _finish_request(req: Request, found: Completion) -> Request:
    """The structured half of a Google search request: the search answer and the sources it
    returned (real URLs), then the schema, with no search tool."""
    sources = "\n".join(
        f"- {c.url} ({c.title})" if c.title else f"- {c.url}" for c in found.citations
    )
    # The shape is stated here too: Gemini's batch mode does not always apply the schema.
    shape = json.dumps(req.schema.model_json_schema()) if req.schema is not None else "{}"
    if sources:
        # Same-site pages are allowed too: a search often cites a site's results page but
        # not its home page, which the answer above may still name.
        listed = (
            "Sources the search returned (any URL in your answer must be one of these, "
            "written exactly as here, or a page on the same website as one of them that "
            f"your answer above names):\n{sources}"
        )
    else:
        # Gemini answers well-known companies from memory without searching. Its URLs are then
        # unsourced; the caller still checks each one (fetch, domain, judgement) before use.
        listed = (
            "The search returned no source links. Use the URLs from your answer above, "
            "written exactly as there."
        )
    ask = (
        f"{listed}\n\nNow give your answer as one JSON object "
        f"that matches this JSON schema, with nothing else:\n{shape}"
    )
    return dataclasses.replace(
        req,
        search=None,
        messages=(*req.messages, Message("assistant", found.text), Message("user", ask)),
    )


def _merge_search(found: Completion, final: Completion) -> Completion:
    """`final` (the structured answer) carrying the search's citations and both calls' cost."""
    usage = normalized_usage()
    _accumulate(usage, found.usage)
    _accumulate(usage, final.usage)
    final.usage = usage
    final.citations = found.citations
    final.off_list_citations = found.off_list_citations
    return final


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
