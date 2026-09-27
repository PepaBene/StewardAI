"""Regression tests for a second round of the "backfill looked frozen" investigation:

- an organization-level ANTHROPIC_API_KEY not scoped to a workspace makes every AI call fail
  with a 400 ("This API key is not scoped to a workspace"); ANTHROPIC_WORKSPACE_ID fixes it.
- every AI request now has a hard wall-clock deadline, so a connection stalled at a level
  httpx's own timeout does not catch (a VPN, a TLS-inspecting antivirus) still ends instead
  of running forever.
"""

import asyncio
import json
from typing import Any

import anthropic
import httpx2
import pytest

from steward.ai import BudgetedAnthropic, _seconds, create_client
from steward.settings import Settings


def _settings(**overrides: Any) -> Settings:
    return Settings(anthropic_api_key="sk-test", **overrides)  # type: ignore[arg-type]


def test_create_client_sends_the_workspace_header_only_when_set() -> None:
    with_id = create_client(_settings(anthropic_workspace_id="wrk_123"))
    assert with_id.default_headers["anthropic-workspace-id"] == "wrk_123"

    without_id = create_client(_settings())
    assert "anthropic-workspace-id" not in without_id.default_headers


def test_seconds_reads_a_plain_float_or_an_httpx_timeout() -> None:
    assert _seconds(90.0, 300.0) == 90.0
    assert _seconds(None, 300.0) == 300.0
    assert _seconds(httpx2.Timeout(45.0), 300.0) == 45.0
    assert _seconds(httpx2.Timeout(connect=5.0, read=None, write=None, pool=None), 300.0) == 5.0
    assert _seconds(httpx2.Timeout(None), 300.0) == 300.0


async def test_a_normal_response_is_unaffected_by_the_hard_deadline() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": json.loads(request.content)["model"],
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    client = BudgetedAnthropic(
        api_key="test", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )
    response = await client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=10,
        messages=[{"role": "user", "content": "hi"}],
        timeout=5.0,
    )
    assert response.content[0].text == "ok"


async def test_a_workspace_scoping_error_is_a_normal_api_error() -> None:
    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            400,
            json={
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "This API key is not scoped to a workspace, so this request "
                    "must include the anthropic-workspace-id header.",
                },
            },
        )

    client = BudgetedAnthropic(
        api_key="test", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )
    with pytest.raises(anthropic.BadRequestError, match="not scoped to a workspace"):
        await client.messages.create(
            model="claude-haiku-4-5", max_tokens=10, messages=[{"role": "user", "content": "hi"}]
        )


async def test_a_stalled_connection_is_cancelled_instead_of_hanging_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connection stalled at a level httpx's own `timeout=` does not catch (the class of
    hang this guards against) still ends, via the hard wall-clock deadline. Proved by
    replacing the superclass `post` itself with something that never times out on its own
    (no httpx timeout machinery involved at all), so only the hard deadline can end it."""
    import steward.ai as ai_module

    monkeypatch.setattr(ai_module, "_DEADLINE_MARGIN", 0.05)

    async def stuck_post(self: Any, path: str, **_kwargs: Any) -> Any:
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    monkeypatch.setattr(anthropic.AsyncAnthropic, "post", stuck_post)
    client = BudgetedAnthropic(api_key="test")
    async with asyncio.timeout(5):  # the test's own safety net
        with pytest.raises(anthropic.APIConnectionError, match="network stalled"):
            await client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=10,
                messages=[{"role": "user", "content": "hi"}],
                timeout=0.05,
            )
