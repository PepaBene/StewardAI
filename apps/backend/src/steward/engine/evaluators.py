"""How a prompt addition is tested in each area (see `engine/replay.py`).

Each evaluator re-runs the area's own code on inputs it already has, with the extra text it would
add to the prompt, and reports what code can check:

- news: the labelled headlines and recent stored items; labels that miss the labelled set.
- digest: the last stored windows; sentences the claim verifier rejects.
- theses: research reports that already led to a thesis; drafts the thesis builder refuses.
- chat: questions the owner asked before; sentences an answer cites nothing for.

Nothing is stored: the outputs only go to the judge and the counts.
"""

import dataclasses
import datetime as dt
import json
import logging
from typing import Any

import asyncpg

from steward.ai import Usage
from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.jobs import JobsConfig, effort_kwargs
from steward.config.prompts import load_prompts
from steward.config.store import load_active
from steward.config.strategy import StrategyConfig
from steward.config.thesis import ThesesConfig
from steward.config.verification import VerificationConfig
from steward.engine.replay import Evaluator, Outcome
from steward.llm.routing import RoutingClient

logger = logging.getLogger("steward.engine")

LIVE_ITEMS = 12  # recent stored items the judge compares
RECENT_WINDOWS = 3


async def _jobs(pool: asyncpg.Pool) -> JobsConfig:
    stored = await load_active(pool, "jobs", JobsConfig)
    return stored.body if stored else JobsConfig()


# --------------------------------------------------------------------------------------- news


class _Counting:
    """A classifier that adds what each batch used to `usage`."""

    def __init__(self, inner: Any, usage: Usage) -> None:
        self._inner = inner
        self._usage = usage

    async def classify(self, items: Any, holdings: Any, today: Any) -> tuple[Any, Any]:
        result, used = await self._inner.classify(items, holdings, today)
        self._usage.add(used)
        return result, used


@dataclasses.dataclass(frozen=True)
class NewsCase:
    kind: str  # "golden": the labelled set; "live": recent stored items
    items: tuple[Any, ...] = ()
    holdings: tuple[Any, ...] = ()
    labelled: tuple[Any, ...] = ()


def _label_line(item: Any, label: Any | None) -> str:
    got = (
        "no label"
        if label is None
        else (
            f"{label.event_type}, affects {label.affected_ticker or 'nobody'}, "
            f"{'relevant' if label.relevant else 'not relevant'}"
            f"{', rumour' if label.rumour else ''}"
        )
    )
    return f"- {item.headline[:140]} -> {got}"


class NewsEvaluator:
    area = "news"
    min_cases = 3
    max_cases = LIVE_ITEMS // 4 + 1
    calls_per_case = 3
    tolerance = 0.01

    async def cases(self, pool: asyncpg.Pool) -> list[Any]:
        from steward.news import eval as news_eval
        from steward.news.classify import Candidate, ClassifyItem, HoldingContext

        out: list[Any] = []
        if news_eval.SET_PATH.exists():
            labelled = news_eval.load_set()
            out.append(
                NewsCase(
                    "golden",
                    holdings=tuple(news_eval.load_holdings()),
                    labelled=tuple(labelled),
                )
            )
        rows = await pool.fetch(
            "select headline, summary, outlet, tier, published_at, links from public.news_item"
            " where classified_at is not null order by published_at desc limit $1",
            LIVE_ITEMS * 3,
        )
        items: list[ClassifyItem] = []
        holdings: dict[str, HoldingContext] = {}
        for r in rows:
            links = json.loads(r["links"]) if isinstance(r["links"], str) else r["links"]
            candidates = tuple(Candidate(c["ticker"], c["via"], c["term"]) for c in links or [])
            for c in candidates:
                holdings.setdefault(c.ticker, HoldingContext(c.ticker, c.term))
            if not candidates:
                continue
            items.append(
                ClassifyItem(
                    len(items),
                    r["headline"],
                    r["summary"],
                    r["outlet"],
                    r["tier"],
                    r["published_at"],
                    candidates,
                )
            )
            if len(items) >= LIVE_ITEMS:
                break
        # Four items a case keeps each judgement about a few labels the judge can read.
        for start in range(0, len(items), 4):
            chunk = items[start : start + 4]
            if len(chunk) == 4:
                out.append(NewsCase("live", tuple(chunk), tuple(holdings.values())))
        return out

    def describe(self, case: NewsCase) -> str:
        return "News items to label:\n" + "\n".join(f"- {i.headline[:140]}" for i in case.items)

    async def run(
        self,
        pool: asyncpg.Pool,
        client: RoutingClient,
        case: NewsCase,
        extra: str,
        usage: Usage,
        model: str | None = None,
    ) -> Outcome:
        from steward.news import eval as news_eval
        from steward.news.classify import SYSTEM_PROMPT, LLMClassifier

        jobs = await _jobs(pool)
        model = model or jobs.news.classification_model
        prompts = await load_prompts(pool)
        instructions = prompts.text("news_classification", SYSTEM_PROMPT, model)
        if extra:
            instructions = f"{instructions.rstrip()}\n\n{extra}"
        classifier = LLMClassifier(
            client.llm, model, instructions, effort=jobs.news.classification_effort
        )
        if case.kind == "golden":
            score = await news_eval.evaluate(
                _Counting(classifier, usage), list(case.labelled), holdings=list(case.holdings)
            )
            return Outcome(checked=score.total, bad=score.total - score.event_type, judged=False)
        today = dt.datetime.now(dt.UTC).date()
        result, used = await classifier.classify(case.items, case.holdings, today)
        usage.add(used)
        text = "\n".join(_label_line(i, result.labels.get(i.index)) for i in case.items)
        return Outcome(text=text)


# ------------------------------------------------------------------------------------ digest


@dataclasses.dataclass(frozen=True)
class DigestCase:
    end: dt.datetime


def _sentences(body: Any) -> list[str]:
    return [s.text for s in body.overview] + [s.text for sec in body.sections for s in sec.summary]


class DigestEvaluator:
    area = "digest"
    min_cases = 2
    max_cases = RECENT_WINDOWS
    calls_per_case = 12
    tolerance = 0.03

    async def cases(self, pool: asyncpg.Pool) -> list[Any]:
        now = dt.datetime.now(dt.UTC)
        has_news = await pool.fetchval(
            "select count(*) from public.story_cluster where last_seen >= $1",
            now - dt.timedelta(days=10),
        )
        if not has_news:
            return []
        return [DigestCase(now - dt.timedelta(days=d)) for d in range(RECENT_WINDOWS)]

    def describe(self, case: DigestCase) -> str:
        return f"The daily digest for the window ending {case.end:%Y-%m-%d}."

    async def run(
        self,
        pool: asyncpg.Pool,
        client: RoutingClient,
        case: DigestCase,
        extra: str,
        usage: Usage,
        model: str | None = None,
    ) -> Outcome:
        from steward.news.digest import (
            SYSTEM_PROMPT,
            LLMDigestWriter,
            build_digest,
            verify_digest_claims,
        )
        from steward.verify.claims import SYSTEM as CLAIM_SYSTEM
        from steward.verify.claims import LLMClaimVerifier

        jobs = await _jobs(pool)
        cfg = jobs.digest.model_copy(
            update={"use_batch": False, **({"model": model} if model else {})}
        )
        prompts = await load_prompts(pool)
        system = prompts.text("news_digest", SYSTEM_PROMPT, cfg.model)
        writer = LLMDigestWriter(client.llm, cfg, system=system + ("\n\n" + extra if extra else ""))
        profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
        theses = await load_active(pool, "theses", ThesesConfig)
        verification = await load_active(pool, "verification", VerificationConfig)
        claim_cfg = (verification.body if verification else VerificationConfig()).claim_verifier
        verifier = LLMClaimVerifier(
            client,
            claim_cfg.model,
            prompts.text("claim_verifier", CLAIM_SYSTEM, claim_cfg.model),
            effort=claim_cfg.effort,
        )
        body, used, _ = await build_digest(
            pool,
            "daily",
            case.end,
            profiles.body if profiles else HoldingProfilesConfig(),
            cfg,
            writer,
            theses.body if theses else None,
        )
        if used is not None:
            usage.input_tokens += used.input_tokens
            usage.output_tokens += used.output_tokens
        sentences = _sentences(body)
        checked = await verify_digest_claims(body, claim_cfg, verifier, case.end.date())
        unsupported = sum(1 for r in checked.results.values() if r.status == "unsupported")
        return Outcome(
            checked=len(sentences), bad=unsupported, text="\n".join(f"- {s}" for s in sentences)
        )


# ------------------------------------------------------------------------------------ theses


@dataclasses.dataclass(frozen=True)
class ThesisCase:
    report_id: int
    ticker: str
    mode: str


class ThesisEvaluator:
    area = "theses"
    min_cases = 2
    max_cases = 3
    calls_per_case = 3
    tolerance = 0.0

    async def cases(self, pool: asyncpg.Pool) -> list[Any]:
        rows = await pool.fetch(
            "select distinct on (research_report_id) research_report_id, ticker, mode"
            " from public.thesis_memo where research_report_id is not null"
            " order by research_report_id desc, id desc limit 6"
        )
        return [ThesisCase(r["research_report_id"], r["ticker"], r["mode"]) for r in rows]

    def describe(self, case: ThesisCase) -> str:
        return f"A thesis for {case.ticker} written from stored research report {case.report_id}."

    async def run(
        self,
        pool: asyncpg.Pool,
        client: RoutingClient,
        case: ThesisCase,
        extra: str,
        usage: Usage,
        model: str | None = None,
    ) -> Outcome:
        from steward.thesis import author
        from steward.thesis.job import MAX_OUTPUT_TOKENS, _body, _position

        cfg = (await _jobs(pool)).thesis_settings
        if model:
            cfg = cfg.model_copy(update={"model": model})
        mode: Any = case.mode
        row = await pool.fetchrow("select body from public.report where id = $1", case.report_id)
        strategy = await load_active(pool, "strategy", StrategyConfig)
        if row is None or strategy is None:
            return Outcome(judged=False)
        try:
            pos = await _position(pool, case.ticker, mode)
        except Exception:  # the holding left the portfolio since: the case cannot be re-run
            return Outcome(judged=False)
        report = _body(row["body"])
        text, figures = author.report_text(report)
        prompt = author.author_prompt(
            ticker=case.ticker,
            name=pos["name"],
            currency=pos["currency"],
            price=pos["price"],
            weight=pos["weight"],
            report_text=text,
            valuation=figures,
            strategy_text=author.strategy_text(strategy.body, case.ticker),
        )
        response = await client.messages.parse(
            model=cfg.model,
            system=author.system_for(mode) + ("\n\n" + extra if extra else ""),
            messages=[{"role": "user", "content": prompt}],
            output_format=author.ThesisDraft,
            **effort_kwargs(cfg.model, cfg.effort, MAX_OUTPUT_TOKENS),
        )
        usage.add(response.usage)
        draft = response.parsed_output
        if draft is None:
            return Outcome(checked=1, bad=1, text="(no draft)")
        today = dt.datetime.now(dt.UTC).date()
        try:
            author.build_thesis(
                draft, case.ticker, pos["currency"], today, today_price=pos["price"], mode=mode
            )
            bad = 0
        except author.AuthorError:
            bad = 1
        return Outcome(
            checked=1,
            bad=bad,
            text=f"Stance: {draft.stance}. {draft.stance_reason}\n{draft.executive_summary}",
        )


# --------------------------------------------------------------------------------------- chat


@dataclasses.dataclass(frozen=True)
class ChatCase:
    question: str


class ChatEvaluator:
    area = "chat"
    min_cases = 3
    max_cases = 4
    calls_per_case = 10
    tolerance = 0.05

    async def cases(self, pool: asyncpg.Pool) -> list[Any]:
        rows = await pool.fetch(
            "select distinct on (content) content from public.chat_message where role = 'user'"
            " and status = 'done' and char_length(content) between 12 and 400"
            " order by content, id desc limit 40"
        )
        return [ChatCase(r["content"]) for r in rows][:8]

    def describe(self, case: ChatCase) -> str:
        return f"The owner asked: {case.question}"

    async def run(
        self,
        pool: asyncpg.Pool,
        client: RoutingClient,
        case: ChatCase,
        extra: str,
        usage: Usage,
        model: str | None = None,
    ) -> Outcome:
        from steward.chat.assistant import SYSTEM, LLMChatAssistant
        from steward.chat.tools import ChatTools
        from steward.research.sources import Registry

        settings = (await _jobs(pool)).chat
        if model:
            settings = settings.model_copy(update={"model": model})
        prompts = await load_prompts(pool)
        assistant = LLMChatAssistant(
            client.llm, settings, (), system=prompts.text("chat_assistant", SYSTEM, settings.model)
        )
        # No conversation, so nothing the assistant proposes can be stored or confirmed.
        tools = ChatTools(pool, Registry(), None, None)
        tools.corrections_override = extra
        result = await assistant.answer([], case.question, tools, dt.datetime.now(dt.UTC).date())
        usage.input_tokens += result.usage.input_tokens
        usage.output_tokens += result.usage.output_tokens
        draft = result.draft
        if draft is None:
            return Outcome(checked=1, bad=1, text="(no answer)")
        sentences = [s.text for s in draft.sentences]
        uncited = sum(1 for s in draft.sentences if not s.source_ids and not s.urls)
        text = "\n".join(sentences) or (draft.insufficient_evidence or "")
        return Outcome(checked=len(sentences), bad=uncited, text=text)


EVALUATORS: dict[str, Evaluator] = {
    e.area: e for e in (NewsEvaluator(), DigestEvaluator(), ThesisEvaluator(), ChatEvaluator())
}

__all__ = ["EVALUATORS"]
