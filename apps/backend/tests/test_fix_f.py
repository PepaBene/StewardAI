"""Regression tests for the group F audit fixes: evals gate fingerprint, claim numbers,
review compliance, 13F aggregation and values, research facts, chat search budget and
redelivery."""

import datetime as dt
import io
import json
import zipfile
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest
from anthropic.types import ToolUseBlock

from steward.ai import Usage
from steward.chat.assistant import (
    AnswerSentence,
    AssistantResult,
    ChatAnswerDraft,
    ChatDeps,
    ClaudeChatAssistant,
    run_chat,
)
from steward.chat.tools import ChatTools
from steward.config.jobs import ChatSettings
from steward.config.strategy import StrategyConfig
from steward.jobs.runner import JobContext, create_job, run_job
from steward.ownership.read import holding_ownership
from steward.portfolio.drift import (
    BucketDrift,
    CashDrift,
    DimensionAllocation,
    DriftReport,
    PositionDrift,
)
from steward.research.gather import financial_trend
from steward.research.sources import Registry
from steward.review.monthly import ReviewDeps, _not_checked, run_review
from steward.sources.form13f import parse_dataset
from steward.verify import evals_gate
from steward.verify.claims import missing_numbers, numbers_in
from steward.verify.evals_gate import COMPONENTS, Component, fingerprint, run_gate

# ---------------------------------------------------------------------------
# Evals gate
# ---------------------------------------------------------------------------


def test_the_earnings_fingerprint_covers_the_golden_documents_and_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = evals_gate._golden_files()
    assert any(p.name == "case.json" for p in files)
    assert any(p.name.startswith("document") for p in files)

    case = tmp_path / "golden" / "c1"
    case.mkdir(parents=True)
    (case / "case.json").write_text(json.dumps({"document": "document.html.gz"}))
    doc = case / "document.html.gz"
    doc.write_bytes(b"one")
    monkeypatch.setattr(evals_gate, "BACKEND", tmp_path)
    monkeypatch.setattr(
        evals_gate, "_golden_files", lambda: (case / "case.json", doc), raising=True
    )
    effort = {"value": "high"}
    component = Component("earnings", (), (), lambda: "m", 0.98, lambda: effort["value"])
    before = fingerprint(component)
    doc.write_bytes(b"two")
    assert fingerprint(component) != before
    after_doc = fingerprint(component)
    effort["value"] = "low"
    assert fingerprint(component) != after_doc


def test_the_classification_fingerprint_covers_the_event_descriptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from steward.config import event_rules

    component = next(c for c in COMPONENTS if c.name == "classification")
    before = fingerprint(component)
    changed = dict(event_rules.EVENT_DESCRIPTIONS, other="Something else entirely")
    monkeypatch.setattr(event_rules, "EVENT_DESCRIPTIONS", changed)
    assert fingerprint(component) != before
    earnings = next(c for c in COMPONENTS if c.name == "earnings")
    assert "effort=" in earnings.extra()


async def test_no_baseline_and_no_key_says_no_evals_ran(tmp_path: Path) -> None:
    code = tmp_path / "p.py"
    code.write_text("x")
    component = Component("claims", (code,), (code,), lambda: "m", 1.0)
    result = await run_gate(None, False, components=(component,))
    assert result.ok  # the documented design: blocking only once a key is configured
    assert "NO EVALS RAN" in result.lines[0] and "unverified" in result.lines[0]


# ---------------------------------------------------------------------------
# Claim numbers
# ---------------------------------------------------------------------------


def test_numbers_keep_their_decimals_and_magnitudes() -> None:
    [bn] = numbers_in("Revenue was $1.5bn.")
    assert bn.value == Decimal("1.5e9")
    [x] = numbers_in("Net debt is 2.3x EBITDA")
    assert x.value == Decimal("2.3")
    assert numbers_in("It filed its 10-K late") == []
    [m] = numbers_in("sales of 1,512 million")
    assert m.value == Decimal("1512e6")


def test_a_rounded_magnitude_matches_and_a_wrong_one_is_flagged() -> None:
    assert missing_numbers("Revenue was $1.5bn.", ["Revenue of 1,512 million"]) == []
    assert missing_numbers("Revenue was $1.9bn.", ["Revenue of 1,400 million"]) == ["1.9"]
    assert missing_numbers("Net debt is 2.3x EBITDA", ["leverage 2.3 times"]) == []
    assert missing_numbers("Net debt is 2.8x EBITDA", ["leverage 2.3 times"]) == ["2.8"]
    assert missing_numbers("Its 10-K shows revenue of 94.0 billion.", ["94,036"]) == []


# ---------------------------------------------------------------------------
# Review compliance: rules evaluate() ran no check for
# ---------------------------------------------------------------------------


def _drift(cash_weight: float | None = 0.05) -> DriftReport:
    return DriftReport(
        weight_basis="total",
        basis_value_base=1000,
        positions=[
            PositionDrift(ticker="AAPL_US_EQ", weight=0.95, target_weight=None, band=None,
                          lower=None, upper=None, hard_limit=None, drift=None,
                          status="no_target", held=True),
        ],
        categories=[
            DimensionAllocation(dimension="sector", buckets=[
                BucketDrift(bucket="Tech", value_base=950, weight=0.95, target_weight=None,
                            band=None, lower=None, upper=None, drift=None, status="no_target"),
            ]),
        ],
        cash=CashDrift(value_base=50, weight=cash_weight, target_weight=None, band=None,
                       lower=None, upper=None, drift=None, status="no_target"),
        out_of_band=0,
    )  # fmt: skip


def _rules(*rules: dict[str, Any]) -> StrategyConfig:
    return StrategyConfig.model_validate(
        {"base_currency": "USD", "categories": ["sector"], "rules": list(rules)}
    )


def test_rules_on_unheld_targets_and_unknown_cash_are_not_passed() -> None:
    strategy = _rules(
        {"id": "min-health", "type": "category_weight", "dimension": "sector",
         "bucket": "Health", "operator": ">=", "value": 0.1},
        {"id": "max-health", "type": "category_weight", "dimension": "sector",
         "bucket": "Health", "operator": "<=", "value": 0.1},
        {"id": "min-msft", "type": "position_weight", "ticker": "MSFT_US_EQ",
         "operator": ">=", "value": 0.05},
        {"id": "min-cash", "type": "cash_weight", "operator": ">=", "value": 0.02},
        {"id": "max-aapl", "type": "position_weight", "ticker": "AAPL_US_EQ",
         "operator": "<=", "value": 1.0},
    )  # fmt: skip
    rules = {r.id: r for r in strategy.rules}
    drift = _drift()
    status, detail = _not_checked(rules["min-health"], drift) or ("", "")
    assert status == "breach" and "Health" in detail
    assert (_not_checked(rules["max-health"], drift) or ("",))[0] == "pass"
    assert (_not_checked(rules["min-msft"], drift) or ("",))[0] == "breach"
    assert _not_checked(rules["min-cash"], drift) is None  # a check ran
    assert _not_checked(rules["max-aapl"], drift) is None
    status, detail = _not_checked(rules["min-cash"], _drift(None)) or ("", "")
    assert status == "unknown" and "cash" in detail


# ---------------------------------------------------------------------------
# 13F
# ---------------------------------------------------------------------------


def _tsv(rows: list[dict[str, str]]) -> str:
    header = list(rows[0])
    return "\n".join(["\t".join(header), *("\t".join(r[h] for h in header) for r in rows)])


def test_13f_values_before_2023_are_scaled_from_thousands() -> None:
    buffer = io.BytesIO()
    subs = [
        {"ACCESSION_NUMBER": "0000000001-22-000001", "FILING_DATE": "14-NOV-2022",
         "SUBMISSIONTYPE": "13F-HR", "CIK": "1", "PERIODOFREPORT": "30-SEP-2022"},
        {"ACCESSION_NUMBER": "0000000001-23-000001", "FILING_DATE": "03-JAN-2023",
         "SUBMISSIONTYPE": "13F-HR/A", "CIK": "1", "PERIODOFREPORT": "30-SEP-2022"},
    ]  # fmt: skip
    info = [
        {"ACCESSION_NUMBER": s["ACCESSION_NUMBER"], "INFOTABLE_SK": str(i), "CUSIP": "037833100",
         "VALUE": "150", "SSHPRNAMT": "10", "SSHPRNAMTTYPE": "SH"}
        for i, s in enumerate(subs)
    ]  # fmt: skip
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("SUBMISSION.tsv", _tsv(subs))
        z.writestr("COVERPAGE.tsv", "ACCESSION_NUMBER\tAMENDMENTTYPE\n")
        z.writestr("INFOTABLE.tsv", _tsv(info))
    buffer.seek(0)
    data = parse_dataset(buffer, {"037833100"})
    values = {p.accession: p.value_usd for p in data.positions}
    assert values == {
        "0000000001-22-000001": Decimal(150000),
        "0000000001-23-000001": Decimal(150),
    }


@pytest.mark.db
async def test_a_restatement_replaces_earlier_new_holdings_amendments(
    backend_pool: asyncpg.Pool,
) -> None:
    await backend_pool.execute(
        "insert into instrument (t212_ticker, isin) values ('AAPL_US_EQ', 'US0378331005')"
    )
    await backend_pool.execute("insert into form13f_dataset (url) values ('u')")
    filings = [
        ("0000000001-26-000001", "2026-02-10", "13F-HR", None, 100),
        ("0000000001-26-000002", "2026-02-12", "13F-HR/A", "NEW HOLDINGS", 50),
        ("0000000001-26-000003", "2026-02-14", "13F-HR/A", "RESTATEMENT", 150),
        ("0000000001-26-000004", "2026-02-16", "13F-HR/A", "NEW HOLDINGS", 5),
    ]
    for accession, filed, kind, amendment, shares in filings:
        await backend_pool.execute(
            "insert into form13f_filing (accession, cik, manager_name, period, filed,"
            " submission_type, amendment_type, dataset_url) values ($1, '0000999999', 'M',"
            " '2025-12-31', $2, $3, $4, 'u')",
            accession,
            dt.date.fromisoformat(filed),
            kind,
            amendment,
        )
        await backend_pool.execute(
            "insert into institutional_holding (accession, infotable_sk, cusip, ticker,"
            " shares, shares_type) values ($1, 1, '037833100', 'AAPL_US_EQ', $2, 'SH')",
            accession,
            shares,
        )
    ours = await holding_ownership(backend_pool, "AAPL_US_EQ", periods=1)
    # The restatement (150) plus the NEW HOLDINGS filed after it (5); not the earlier 50.
    assert ours.periods[0].shares == 155 and ours.periods[0].holders == 1


# ---------------------------------------------------------------------------
# Research facts
# ---------------------------------------------------------------------------


def _fact(fact_id: int, value: str) -> dict[str, Any]:
    return {
        "id": fact_id, "metric": "revenue", "fiscal_year": 2026, "fiscal_period": "Q1",
        "period": "FY2026 Q1", "value_units": Decimal(value), "value_as_written": value,
        "scale": "units", "unit": "currency", "currency": "USD", "snippet": value,
        "source_url": "https://sec.test", "extracted_at": None,
    }  # fmt: skip


def test_the_trend_uses_the_newest_fact_per_quarter() -> None:
    # Ordered as the query returns them: id desc, the newest first.
    section = financial_trend(Registry(), [_fact(2, "120"), _fact(1, "100")])  # type: ignore[list-item]
    assert "120" in section.sentences[0].text


# ---------------------------------------------------------------------------
# Chat: web search budget per answer
# ---------------------------------------------------------------------------


class _FakeMessages:
    def __init__(self, searches: list[int]) -> None:
        self.searches = searches
        self.tools: list[list[dict[str, Any]]] = []

    async def parse(self, **kwargs: Any) -> Any:
        self.tools.append([t for t in kwargs["tools"] if t.get("name") == "web_search"])
        n = len(self.tools)
        usage = SimpleNamespace(
            input_tokens=1, output_tokens=1,
            server_tool_use=SimpleNamespace(web_search_requests=self.searches[n - 1]),
        )  # fmt: skip
        if n < len(self.searches):
            block = ToolUseBlock(id=f"t{n}", name="portfolio", input={}, type="tool_use")
            return SimpleNamespace(
                usage=usage, content=[block], stop_reason="tool_use", parsed_output=None
            )
        draft = ChatAnswerDraft(sentences=[AnswerSentence(text="Done.")])
        return SimpleNamespace(usage=usage, content=[], stop_reason="end_turn", parsed_output=draft)


class _FakeTools:
    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}


async def test_the_search_budget_is_per_answer_not_per_request() -> None:
    messages = _FakeMessages([2, 1, 0])
    client = SimpleNamespace(messages=messages)
    assistant = ClaudeChatAssistant(
        client,  # type: ignore[arg-type]
        ChatSettings(max_searches=3),
        ("reuters.com",),
    )
    result = await assistant.answer([], "Q?", _FakeTools(), dt.date(2026, 9, 1))  # type: ignore[arg-type]
    assert result.draft is not None
    assert [[t["max_uses"] for t in call] for call in messages.tools] == [[3], [1], []]


# ---------------------------------------------------------------------------
# Redelivery: the monthly review and chat
# ---------------------------------------------------------------------------


class KillWorker(BaseException):
    """Stands in for the worker dying after the work was stored."""


@pytest.mark.db
async def test_a_redelivered_review_keeps_its_first_report(backend_pool: asyncpg.Pool) -> None:
    job_id = await create_job(backend_pool, "monthly_review", "manual")
    ids: list[int | None] = []

    async def dying(ctx: JobContext) -> None:
        ids.append(await run_review(ctx, ReviewDeps(writer=None, now=dt.datetime.now(dt.UTC))))
        raise KillWorker()

    with pytest.raises(KillWorker):
        await run_job(backend_pool, "monthly_review", dying, trigger="manual", job_id=job_id)

    async def again(ctx: JobContext) -> None:
        ids.append(await run_review(ctx, ReviewDeps(writer=None, now=dt.datetime.now(dt.UTC))))

    assert await run_job(backend_pool, "monthly_review", again, trigger="manual",
                         job_id=job_id) == "succeeded"  # fmt: skip
    assert ids[0] == ids[1]
    assert await backend_pool.fetchval("select count(*) from report where type = 'review'") == 1


class _ProposingAssistant:
    def __init__(self, die: bool) -> None:
        self.die = die

    async def answer(
        self, history: list[dict[str, str]], question: str, tools: ChatTools, today: dt.date
    ) -> AssistantResult:
        await tools.call("propose_job", {"kind": "portfolio_sync", "reason": "Stale."})
        if self.die:
            raise KillWorker()
        draft = ChatAnswerDraft(sentences=[AnswerSentence(text="Proposed a sync.")])
        return AssistantResult(draft, Usage(model="fake"), {})


@pytest.mark.db
async def test_a_redelivered_chat_cancels_the_first_attempts_actions(
    backend_pool: asyncpg.Pool,
) -> None:
    conv = await backend_pool.fetchval("insert into chat_conversation default values returning id")
    message = await backend_pool.fetchval(
        "insert into chat_message (conversation_id, role, content, status) values"
        " ($1, 'user', 'Sync?', 'pending') returning id",
        conv,
    )
    params = {"conversation_id": conv, "message_id": message}
    job_id = await create_job(backend_pool, "chat", "chat", params=params)

    def handler(assistant: Any, die_after: bool = False) -> Any:
        async def run(ctx: JobContext) -> None:
            await run_chat(ctx, ChatDeps(assistant=assistant, now=dt.datetime.now(dt.UTC)))
            if die_after:
                raise KillWorker()

        return run

    with pytest.raises(KillWorker):
        await run_job(backend_pool, "chat", handler(_ProposingAssistant(die=True)),
                      trigger="chat", job_id=job_id)  # fmt: skip
    with pytest.raises(KillWorker):
        await run_job(backend_pool, "chat", handler(_ProposingAssistant(die=False), True),
                      trigger="chat", job_id=job_id)  # fmt: skip
    actions = await backend_pool.fetch(
        "select status, message_id from chat_action where conversation_id = $1 order by id", conv
    )
    reply = await backend_pool.fetchval(
        "select id from chat_message where role = 'assistant' and conversation_id = $1", conv
    )
    assert [(a["status"], a["message_id"]) for a in actions] == [
        ("cancelled", message),
        ("proposed", reply),
    ]
    # A third delivery after the answer was stored does not answer again.
    assert await run_job(backend_pool, "chat", handler(_ProposingAssistant(die=False)),
                         trigger="chat", job_id=job_id) == "succeeded"  # fmt: skip
    assert (
        await backend_pool.fetchval(
            "select count(*) from chat_message where role = 'assistant' and conversation_id = $1",
            conv,
        )
        == 1
    )
    assert await backend_pool.fetchval("select count(*) from chat_action") == 2


def test_13f_parses_a_dataset_that_nests_its_tsvs_in_a_folder() -> None:
    # Some SEC quarterly archives put the TSVs under "<name>/SUBMISSION.tsv" instead of at
    # the zip root (e.g. 01jun2025-31aug2025_form13f.zip); parse_dataset must find them
    # either way.
    buffer = io.BytesIO()
    subs = [
        {"ACCESSION_NUMBER": "0000000001-25-000001", "FILING_DATE": "14-AUG-2025",
         "SUBMISSIONTYPE": "13F-HR", "CIK": "1", "PERIODOFREPORT": "30-JUN-2025"},
    ]  # fmt: skip
    info = [
        {"ACCESSION_NUMBER": "0000000001-25-000001", "INFOTABLE_SK": "1", "CUSIP": "037833100",
         "VALUE": "150", "SSHPRNAMT": "10", "SSHPRNAMTTYPE": "SH"}
    ]  # fmt: skip
    prefix = "01JUN2025-31AUG2025_form13f/"
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr(prefix, "")
        z.writestr(prefix + "SUBMISSION.tsv", _tsv(subs))
        z.writestr(prefix + "COVERPAGE.tsv", "ACCESSION_NUMBER\tAMENDMENTTYPE\n")
        z.writestr(prefix + "INFOTABLE.tsv", _tsv(info))
    buffer.seek(0)
    data = parse_dataset(buffer, {"037833100"})
    assert [p.cusip for p in data.positions] == ["037833100"]
    assert [f.accession for f in data.filings] == ["0000000001-25-000001"]


def test_13f_missing_tsv_raises_a_clear_error() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("COVERPAGE.tsv", "ACCESSION_NUMBER\tAMENDMENTTYPE\n")
        z.writestr("INFOTABLE.tsv", "ACCESSION_NUMBER\n")
    buffer.seek(0)
    with pytest.raises(KeyError, match=r"SUBMISSION\.tsv"):
        parse_dataset(buffer, {"037833100"})
