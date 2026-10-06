"""Run many sequential per-holding flows side by side and send their model calls as batches.

A flow such as "search, fetch, judge" for one holding makes its calls one after another. Run
for a hundred holdings at once, every flow reaches its first call at about the same time, and
that round of calls can go to the provider's batch API (half the price) as one batch; the
flows then continue to their second call, which is the second batch, and so on. The flows
themselves are written as ordinary code against the routing client; only the runner differs.

    collector = BatchCollector(llm, wait_minutes=60)
    client.collector = collector          # the routing client sends gpt-/gemini- calls here
    results = await collector.run([flow(h) for h in holdings])

A call to a Claude model is not collected (it goes straight to Anthropic). A call the batch
did not answer (timeout, failure, the budget cap) is asked directly, so a flow never sees a
batch failure. `run` returns each flow's result or the exception it raised, in order.
"""

import asyncio
from collections.abc import Coroutine, Sequence
from typing import Any

from steward.llm.client import LLM
from steward.llm.types import Completion, Request


class BatchCollector:
    def __init__(self, llm: LLM, wait_minutes: float = 60.0) -> None:
        self._llm = llm
        self._wait = wait_minutes
        self._pending: list[tuple[Request, asyncio.Future[Completion | None]]] = []
        self._active = 0
        self._wake = asyncio.Event()
        self.rounds = 0

    async def submit(self, request: Request) -> Completion:
        """Called by a flow: queue the request for the next batch and wait for its answer."""
        future: asyncio.Future[Completion | None] = asyncio.get_running_loop().create_future()
        self._pending.append((request, future))
        self._check()
        answer = await future
        # Not answered by the batch: ask directly.
        return answer if answer is not None else await self._llm.complete(request)

    def _check(self) -> None:
        # Every live flow is waiting for a batch (or none is left): time to send one.
        if self._active == 0 or len(self._pending) >= self._active:
            self._wake.set()

    async def _flow(self, coro: Coroutine[Any, Any, Any]) -> Any:
        try:
            return await coro
        except Exception as exc:  # reported to the caller, not raised: one holding is not all
            return exc
        finally:
            self._active -= 1
            self._check()

    async def run(self, flows: Sequence[Coroutine[Any, Any, Any]]) -> list[Any]:
        self._active = len(flows)
        tasks = [asyncio.ensure_future(self._flow(f)) for f in flows]
        try:
            while self._active > 0:
                await self._wake.wait()
                self._wake.clear()
                if self._pending and len(self._pending) >= self._active:
                    await self._flush()
            return list(await asyncio.gather(*tasks))
        finally:
            for task in tasks:
                task.cancel()

    async def _flush(self) -> None:
        batch, self._pending = self._pending, []
        self.rounds += 1
        by_model: dict[str, list[int]] = {}
        for i, (request, _) in enumerate(batch):
            by_model.setdefault(request.model, []).append(i)
        try:
            for indexes in by_model.values():
                answers = await self._llm.complete_batch(
                    [batch[i][0] for i in indexes], wait_minutes=self._wait
                )
                for i, answer in zip(indexes, answers, strict=True):
                    batch[i][1].set_result(answer)
        except BaseException as exc:  # e.g. BudgetExceeded: every waiting flow sees it
            for _, future in batch:
                if not future.done():
                    future.set_exception(exc)
            if not isinstance(exc, Exception):
                raise
