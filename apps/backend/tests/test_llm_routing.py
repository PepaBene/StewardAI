from types import SimpleNamespace
from typing import Any

import httpx
from pydantic import BaseModel

from steward.llm.client import LLM
from steward.llm.routing import RoutingClient


class Out(BaseModel):
    label: str


class FakeAnthropic:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(parse=self.parse)

    async def parse(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return "anthropic answer"


ANSWER = {
    "status": "completed",
    "output": [
        {"type": "message", "content": [{"type": "output_text", "text": '{"label": "ok"}'}]}
    ],
    "usage": {"input_tokens": 10, "output_tokens": 2},
}


def routed(seen: list[httpx.Request]) -> tuple[RoutingClient, FakeAnthropic]:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=ANSWER)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fake = FakeAnthropic()
    llm = LLM(openai_key="k", http=http)
    return RoutingClient(fake, llm), fake  # type: ignore[arg-type]


async def test_a_claude_model_goes_to_the_anthropic_client_unchanged() -> None:
    seen: list[httpx.Request] = []
    client, fake = routed(seen)
    out = await client.messages.parse(model="claude-sonnet-5", system="s", messages=[])
    assert out == "anthropic answer" and fake.calls and not seen


async def test_another_providers_model_answers_in_the_anthropic_shape() -> None:
    seen: list[httpx.Request] = []
    client, fake = routed(seen)
    out = await client.messages.parse(
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
        output_config={"effort": "low"},
    )
    assert out.parsed_output == Out(label="ok") and out.stop_reason == "end_turn"
    assert out.usage.input_tokens == 10 and not fake.calls
    body = seen[0].read().decode()
    assert "page text" in body and '"effort":"low"' in body.replace(" ", "")
