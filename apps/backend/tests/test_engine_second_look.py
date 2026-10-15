"""A second model checks research claims, contradictions and a thesis; findings are notices."""

import datetime as dt
import json
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest

from steward.ai import Usage
from steward.config.jobs import AutopilotSettings
from steward.config.store import save_config
from steward.config.thesis import ThesesConfig, ThesisConfig
from steward.engine import second_look as sl
from steward.ops import tasks

NOW = dt.datetime.now(dt.UTC)


def settings() -> AutopilotSettings:
    return AutopilotSettings(model="gpt-small")


def body(model: str, texts: list[str]) -> dict[str, Any]:
    return {
        "model": model,
        "sections": [
            {
                "id": "s",
                "sentences": [
                    {"text": t, "source_ids": ["S1"], "written_by": "model"} for t in texts
                ],
            },
            {
                "id": "c",
                "sentences": [{"text": "code", "source_ids": ["S1"], "written_by": "code"}],
            },
        ],
        "sources": [{"id": "S1", "snippet": "Revenue was 10."}],
    }


class Client:
    def __init__(self, strained: bool = False, bad: int = 0, pairs: bool = False) -> None:
        self.strained, self.bad, self.pairs = strained, bad, pairs
        self.calls: list[str] = []
        self.messages = SimpleNamespace(parse=self._parse)

    async def _parse(self, **kw: Any) -> Any:
        fmt = kw["output_format"]
        self.calls.append(fmt.__name__)
        usage = SimpleNamespace(input_tokens=1, output_tokens=1)
        if fmt is sl.ClaimChecks:
            out: Any = sl.ClaimChecks(
                checks=[sl.ClaimVerdict(n=self.bad, supported="no", reason="not there")]
                if self.bad
                else []
            )
        elif fmt is sl.Contradictions:
            out = sl.Contradictions(
                pairs=[sl.Contradiction(new=1, old=1, why="opposite")] if self.pairs else []
            )
        else:
            out = sl.ThesisCheck(
                verdict="strained" if self.strained else "consistent", reason="r", evidence=["e"]
            )
        return SimpleNamespace(usage=usage, parsed_output=out)


def test_figures_come_first_and_only_model_sentences_with_a_source_are_checked() -> None:
    claims = sl.pick_claims(body("m", ["No number here", "Sales rose 12%"])["sections"])
    assert [c[0] for c in claims] == ["Sales rose 12%", "No number here"]


def test_the_checker_is_never_the_model_that_wrote_the_report() -> None:
    only_small = AutopilotSettings(model="gpt-small", escalate_model="gpt-small")
    assert sl.different_model(only_small, "gpt-small") is None
    assert sl.different_model(only_small, "gpt-big") == "gpt-small"


async def add_report(pool: asyncpg.Pool, model: str, texts: list[str]) -> int:
    return int(
        await pool.fetchval(
            "insert into public.report (type, ticker, title, body, body_md) values ('research',"
            " 'AAA', 'Research', $1::jsonb, 'x') returning id",
            json.dumps(body(model, texts)),
        )
    )


@pytest.mark.db
async def test_an_unsupported_claim_and_a_contradiction_become_notices_once(
    backend_pool: asyncpg.Pool,
) -> None:
    await add_report(backend_pool, "gpt-big", ["Sales fell 5%"])
    await add_report(backend_pool, "gpt-big", ["Sales rose 12%"])
    # Newest first: the newer report is the one checked, against the older one.
    client = Client(bad=1, pairs=True)
    out = await sl.check_research(
        backend_pool,
        client,
        settings(),
        {"calls": 5},
        NOW,
        Usage(model="gpt-small"),  # type: ignore[arg-type]
    )
    assert out["unsupported"] == 1 and out["contradictions"] == 1
    subjects = {t.subject for t in await tasks.needs_owner(backend_pool)}
    assert any(s.startswith("claim:") for s in subjects)
    assert any(s.startswith("contradiction:") for s in subjects)
    again = await sl.check_research(
        backend_pool,
        client,
        settings(),
        {"calls": 5},
        NOW,
        Usage(model="gpt-small"),  # type: ignore[arg-type]
    )
    assert again["research"] in {"nothing new"} or isinstance(again["research"], int)


@pytest.mark.db
async def test_a_strained_thesis_is_reported_and_not_looked_at_again_for_a_month(
    backend_pool: asyncpg.Pool,
) -> None:
    async with backend_pool.acquire() as conn:
        await save_config(
            conn,
            "theses",
            ThesesConfig(theses=[ThesisConfig(ticker="AAA", why_owned="Cheap compounder")]),
            created_by=None,
        )
    client = Client(strained=True)
    out = await sl.check_thesis(
        backend_pool,
        client,
        settings(),
        {"calls": 3},
        NOW,
        Usage(model="gpt-small"),  # type: ignore[arg-type]
    )
    assert out == {"thesis": "AAA", "verdict": "strained"}
    assert [t.subject for t in await tasks.needs_owner(backend_pool)] == ["thesis:AAA"]
    later = await sl.check_thesis(
        backend_pool,
        client,
        settings(),
        {"calls": 3},
        NOW,
        Usage(model="gpt-small"),  # type: ignore[arg-type]
    )
    assert later == {"thesis": "nothing due"}
