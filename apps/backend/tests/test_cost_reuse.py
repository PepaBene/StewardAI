"""Cost lever C8: a claim whose sentence and snippets are unchanged is not verified again."""

import datetime as dt
from types import SimpleNamespace
from typing import Any

from steward.llm.types import normalized_usage
from steward.verify.claims import Claim, ClaimVerdict, ClaimVerdicts, LLMClaimVerifier

TODAY = dt.date(2026, 10, 11)


class MemoryCache:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def get(self, key: str, ttl_days: int | None = None) -> dict[str, Any] | None:
        return self.rows.get(key)

    async def put(self, key: str, model: str, result: dict[str, Any]) -> None:
        self.rows[key] = result


class FakeClient:
    """Answers `supported` for every claim it is asked about, and counts what it was asked."""

    def __init__(self) -> None:
        self.asked: list[str] = []
        self.messages = SimpleNamespace(parse=self.parse)

    async def parse(self, **kwargs: Any) -> Any:
        prompt = kwargs["messages"][0]["content"]
        self.asked.append(prompt)
        indexes = [int(p.split('"')[0]) for p in prompt.split('<claim index="')[1:]]
        return SimpleNamespace(
            usage=normalized_usage(input_tokens=10, output_tokens=5),
            parsed_output=ClaimVerdicts(
                verdicts=[ClaimVerdict(index=i, supported=i % 2 == 0, reason="r") for i in indexes]
            ),
        )


async def test_an_unchanged_claim_is_answered_from_the_cache() -> None:
    client = FakeClient()
    verifier = LLMClaimVerifier(client, "gpt-6-luna", cache=MemoryCache())  # type: ignore[arg-type]
    claims = [Claim(0, "Revenue grew 12%.", ("Revenue grew 12%.",)), Claim(1, "B.", ("b",))]
    first, _ = await verifier.check(claims, TODAY)
    again, usage = await verifier.check(claims, TODAY + dt.timedelta(days=1))
    assert len(client.asked) == 1  # the second run asked nothing
    assert {(v.index, v.supported) for v in again} == {(v.index, v.supported) for v in first}
    assert usage.input_tokens == 0


async def test_a_changed_sentence_or_snippet_or_model_is_asked_again() -> None:
    client = FakeClient()
    cache = MemoryCache()
    verifier = LLMClaimVerifier(client, "gpt-6-luna", cache=cache)  # type: ignore[arg-type]
    await verifier.check([Claim(0, "A.", ("snippet",))], TODAY)
    await verifier.check([Claim(0, "A changed.", ("snippet",))], TODAY)
    await verifier.check([Claim(0, "A.", ("snippet edited",))], TODAY)
    other = LLMClaimVerifier(client, "gpt-6.1-sol", cache=cache)  # type: ignore[arg-type]
    await other.check([Claim(0, "A.", ("snippet",))], TODAY)
    assert len(client.asked) == 4


async def test_only_the_new_claims_of_a_report_are_sent() -> None:
    client = FakeClient()
    verifier = LLMClaimVerifier(client, "gpt-6-luna", cache=MemoryCache())  # type: ignore[arg-type]
    await verifier.check([Claim(0, "A.", ("a",)), Claim(1, "B.", ("b",))], TODAY)
    verdicts, _ = await verifier.check(
        [Claim(0, "A.", ("a",)), Claim(1, "B.", ("b",)), Claim(2, "C.", ("c",))], TODAY
    )
    assert len(client.asked) == 2 and "<sentence>C.</sentence>" in client.asked[1]
    assert "<sentence>A.</sentence>" not in client.asked[1]
    assert sorted(v.index for v in verdicts) == [0, 1, 2]


async def test_without_a_cache_every_claim_is_asked_every_time() -> None:
    client = FakeClient()
    verifier = LLMClaimVerifier(client, "gpt-6-luna")  # type: ignore[arg-type]
    claims = [Claim(0, "A.", ("a",))]
    await verifier.check(claims, TODAY)
    await verifier.check(claims, TODAY)
    assert len(client.asked) == 2
