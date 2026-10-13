from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from steward.llm.client import LLM
from steward.llm.routing import RoutingClient


class Out(BaseModel):
    label: str


ANSWER = {
    "status": "completed",
    "output": [
        {"type": "message", "content": [{"type": "output_text", "text": '{"label": "ok"}'}]}
    ],
    "usage": {"input_tokens": 10, "output_tokens": 2},
}


def routed(seen: list[httpx.Request]) -> RoutingClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=ANSWER)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return RoutingClient(LLM(openai_key="k", http=http))


async def test_an_unknown_model_id_is_refused_and_nothing_is_sent() -> None:
    seen: list[httpx.Request] = []
    client = routed(seen)
    with pytest.raises(ValueError, match="gpt-\\* or gemini-\\*"):
        await client.messages.parse(model="other-model-1", system="s", messages=[])
    assert not seen


async def test_a_model_answers_in_the_response_shape_call_sites_read() -> None:
    seen: list[httpx.Request] = []
    client = routed(seen)
    out: Any = await client.messages.parse(
        model="gpt-6-luna",
        system=[{"type": "text", "text": "Be brief."}],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {"type": "text", "data": "page text"},
                        "title": "u",
                    },
                    {"type": "text", "text": "List."},
                ],
            }
        ],
        output_format=Out,
        max_tokens=500,
        effort="low",
    )
    assert out.parsed_output == Out(label="ok") and out.stop_reason == "end_turn"
    assert out.usage.input_tokens == 10
    body = seen[0].read().decode()
    assert "page text" in body and '"effort":"low"' in body.replace(" ", "")


async def test_a_cache_key_reaches_the_provider_as_the_prompt_cache_key() -> None:
    seen: list[httpx.Request] = []
    client = routed(seen)
    for key in ("research:7", None):
        await client.messages.parse(
            model="gpt-6-luna",
            system="s",
            messages=[{"role": "user", "content": "hi"}],
            output_format=Out,
            cache_key=key,
        )
    with_key, without = (r.read().decode().replace(" ", "") for r in seen)
    assert '"prompt_cache_key":"research:7"' in with_key
    assert "prompt_cache_key" not in without
