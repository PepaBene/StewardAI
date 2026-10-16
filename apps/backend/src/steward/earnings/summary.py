# ruff: noqa: E501
"""Earnings-release summary and questions to ask (insight I13.5): a cited AI note.

The source is the earnings release already stored as evidence for the holding's extracted facts
(the company's IR page or the 8-K exhibit the extractor read). One budgeted model call returns
short summary sentences and three to five questions, each with the verbatim passage it rests on.
Every quote is checked against the stored document text; a sentence or question whose quote is
not found is dropped and counted, never shown. Nothing is claimed about a call transcript: only
the release is read.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import urllib.parse
from typing import Any

import asyncpg
from pydantic import BaseModel, ConfigDict, Field

from steward.analytics.versions import version
from steward.news.analysis import input_hash, verify_span

logger = logging.getLogger(__name__)

TEXT_CAP = 60_000  # characters of release text sent to the model
MIN_QUESTIONS = 3
MAX_QUESTIONS = 5


class SummaryUnavailable(Exception):
    """No stored release, no model, or nothing survived verification."""


class SummaryBudgetRefused(Exception):
    """The Budget caps refused the model call."""


# ------------------------------------------------------------------------- the model's answer


class ModelLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(description="One plain sentence.")
    quote: str = Field(
        description="The exact passage from the release this rests on, copied verbatim (at least a dozen characters)."
    )


class ModelNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: list[ModelLine] = Field(
        description="Three to six sentences: results, guidance, notable changes."
    )
    questions: list[ModelLine] = Field(
        description="Three to five questions an owner should ask, each grounded in a passage."
    )


SYSTEM = (
    "You summarise a company's earnings release for a long-term private investor who owns the stock. "
    "Write three to six short, neutral sentences covering the reported results, any guidance and anything "
    "that changed, then three to five pointed questions the owner should ask management. Every sentence and "
    "every question must carry a quote copied character for character from the release that supports it. "
    "Do not invent figures, do not mention a conference call or transcript, do not give advice to buy or sell."
)


def prompt(ticker: str, period: str | None, text: str) -> str:
    return f"Holding: {ticker}\nReporting period: {period or 'unknown'}\n\nEarnings release text:\n{text[:TEXT_CAP]}"


# ------------------------------------------------------------------------------ the note


class CitedLine(BaseModel):
    text: str
    quote: str
    quote_url: str | None = Field(
        description="The source page scrolled to the quote (a text fragment link), when the source is a web page."
    )


class EarningsNote(BaseModel):
    id: int | None
    ticker: str
    period: str | None
    source_url: str | None
    evidence_sha256: str
    summary: list[CitedLine]
    questions: list[CitedLine]
    dropped: int = Field(
        description="Sentences and questions dropped because their quote is not in the release."
    )
    model: str | None
    created_at: dt.datetime | None
    method_version: str
    note: str = "AI summary of the stored earnings release; each line shows the passage it rests on. No call transcript is read."


class ReleaseDoc(BaseModel):
    evidence_sha256: str
    period: str | None
    source_url: str | None
    fetched_at: dt.datetime | None


class EarningsSummaryView(BaseModel):
    ticker: str
    latest_release: ReleaseDoc | None
    note: EarningsNote | None
    stale: bool = Field(
        description="A newer release is stored than the one the note was written from."
    )
    reason: str | None


def fragment_url(url: str | None, quote: str) -> str | None:
    if not url or not url.startswith(("http://", "https://")) or url.lower().endswith(".pdf"):
        return None
    words = quote.split()
    if not words:
        return None
    # A text fragment needs only the start and the end of a long passage.
    target = " ".join(words) if len(words) <= 8 else f"{' '.join(words[:4])},{' '.join(words[-4:])}"
    parts = [urllib.parse.quote(p, safe="") for p in target.split(",")]
    return f"{url.split('#', 1)[0]}#:~:text={','.join(parts)}"


def verify(
    raw: ModelNote, text: str, url: str | None
) -> tuple[list[CitedLine], list[CitedLine], int]:
    """Keep the lines whose quote is in the release; (summary, questions, dropped)."""
    dropped = 0

    def keep(lines: list[ModelLine], cap: int) -> list[CitedLine]:
        nonlocal dropped
        out: list[CitedLine] = []
        seen: set[str] = set()
        for line in lines:
            body = " ".join(line.text.split())
            quote = " ".join(line.quote.split())
            if not body or body.lower() in seen or not verify_span(quote, [text]):
                dropped += 1
                continue
            if len(out) >= cap:
                continue
            seen.add(body.lower())
            out.append(CitedLine(text=body, quote=quote, quote_url=fragment_url(url, quote)))
        return out

    return keep(raw.summary, 6), keep(raw.questions, MAX_QUESTIONS), dropped


# --------------------------------------------------------------------------------- reads


async def latest_release(pool: asyncpg.Pool, ticker: str) -> asyncpg.Record | None:
    """The stored release behind the holding's extracted earnings figures that reports the newest
    period. Ranked by the period its figures end on, not by when it was fetched, so an older
    document read later (an annual report, a presentation) does not displace the latest release."""
    return await pool.fetchrow(
        """select e.sha256, e.source_url, e.storage_path, e.content_type, e.fetched_at, f.period
           from public.evidence e
           join lateral (select f2.period, f2.period_end, f2.fiscal_year from public.fact f2
                         where f2.evidence_sha256 = e.sha256 and f2.ticker = $1
                         order by f2.period_end desc nulls last, f2.fiscal_year desc, f2.id desc limit 1) f on true
           where e.storage_path is not null
           order by f.period_end desc nulls last, f.fiscal_year desc, e.fetched_at desc nulls last limit 1""",
        ticker,
    )


def _note(row: asyncpg.Record) -> EarningsNote:
    def lines(raw: Any) -> list[CitedLine]:
        data = json.loads(raw) if isinstance(raw, str) else (raw or [])
        return [CitedLine(**d) for d in data]

    return EarningsNote(
        id=row["id"], ticker=row["ticker"], period=row["period"], source_url=row["source_url"],
        evidence_sha256=row["evidence_sha256"], summary=lines(row["summary"]), questions=lines(row["questions"]),
        dropped=row["dropped"], model=row["model"], created_at=row["created_at"], method_version=row["method_version"],
    )  # fmt: skip


async def summary_view(pool: asyncpg.Pool, owner_id: Any, ticker: str) -> EarningsSummaryView:
    doc = await latest_release(pool, ticker)
    row = await pool.fetchrow(
        "select * from public.earnings_note where owner_id = $1 and ticker = $2 order by created_at desc, id desc limit 1",
        owner_id,
        ticker,
    )
    release = (
        ReleaseDoc(
            evidence_sha256=doc["sha256"],
            period=doc["period"],
            source_url=doc["source_url"],
            fetched_at=doc["fetched_at"],
        )
        if doc
        else None
    )
    note = _note(row) if row else None
    return EarningsSummaryView(
        ticker=ticker, latest_release=release, note=note,
        stale=bool(note and release and note.evidence_sha256 != release.evidence_sha256),
        reason=None if release else "No earnings release is stored for this holding yet; it is read when results are extracted.",
    )  # fmt: skip


# --------------------------------------------------------------------------------- write


async def summarise(
    pool: asyncpg.Pool, storage: Any, owner_id: Any, ticker: str, *, llm: Any, model: str
) -> EarningsNote:
    """Summarise the latest stored release (cached per document and model), inside a short
    `earnings_summary` job so the Budget caps apply; the note is stored for the owner."""
    from steward.budget_guard import BudgetExceeded
    from steward.earnings.read import document_text
    from steward.jobs.runner import JobContext, run_job
    from steward.llm.cache import ResultCache, request_key
    from steward.llm.types import LLMError, Message, Request
    from steward.storage import StorageError

    doc = await latest_release(pool, ticker)
    if doc is None:
        raise SummaryUnavailable("No earnings release is stored for this holding yet.")
    try:
        data = await storage.get(doc["storage_path"])
    except StorageError as exc:
        raise SummaryUnavailable(f"The stored release could not be read: {exc}"[:300]) from exc
    text = document_text(data, doc["content_type"], doc["source_url"] or "")
    if len(text.strip()) < 200:
        raise SummaryUnavailable("The stored release has too little text to summarise.")
    request = Request(
        model=model, system=SYSTEM,
        messages=(Message(role="user", text=prompt(ticker, doc["period"], text)),),
        schema=ModelNote, max_tokens=2000, cache_key="earnings_summary",
    )  # fmt: skip
    cache = ResultCache(pool, "earnings_summary", ttl_days=90)
    key = request_key(
        request.model, request.system, [m.text for m in request.messages], ModelNote, None
    )
    raw: ModelNote | None = None
    used_model: str | None = model
    cached = await cache.get(key)
    if cached is not None:
        try:
            raw = ModelNote.model_validate(cached)
        except ValueError:
            raw = None
    if raw is None:
        if not llm.configured(model):
            raise SummaryUnavailable("The model for AI notes is not configured.")
        box: dict[str, Any] = {}

        async def handler(ctx: JobContext) -> None:
            async with ctx.step("summarise"):
                try:
                    box["done"] = await llm.complete(request)
                except Exception as exc:  # kept for the caller; re-raised so the job shows failed
                    box["error"] = exc
                    raise

        outcome = await run_job(
            pool,
            "earnings_summary",
            handler,
            trigger="manual",
            params={"ticker": ticker, "chars": len(text)},
            wait=False,
        )
        if outcome == "busy":
            raise SummaryUnavailable(
                "Another earnings summary is being written; try again in a moment."
            )
        err: BaseException | None = box.get("error")
        while err is not None:
            if isinstance(err, BudgetExceeded):
                raise SummaryBudgetRefused(str(err)) from err
            err = err.__cause__
        if isinstance(box.get("error"), LLMError):
            raise SummaryUnavailable(f"The model call failed: {box['error']}"[:300])
        if "error" in box:
            raise box["error"]
        done = box.get("done")
        if done is None or not isinstance(done.parsed, ModelNote):
            raise SummaryUnavailable("The model returned no usable note.")
        raw = done.parsed
        used_model = done.model
        await cache.put(key, done.model, raw.model_dump(mode="json"))
    summary, questions, dropped = verify(raw, text, doc["source_url"])
    if not summary:
        raise SummaryUnavailable(
            "No summary sentence could be matched to a passage in the release, so nothing is shown."
        )
    row = await pool.fetchrow(
        """insert into public.earnings_note (owner_id, ticker, evidence_sha256, source_url, period, summary, questions, dropped, model, method_version, input_hash)
           values ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8, $9, $10, $11) returning *""",
        owner_id, ticker, doc["sha256"], doc["source_url"], doc["period"],
        json.dumps([s.model_dump() for s in summary]), json.dumps([q.model_dump() for q in questions]),
        dropped, used_model, version("earnings_summary"), input_hash(doc["sha256"], model, version("earnings_summary")),
    )  # fmt: skip
    note = _note(row)
    if len(questions) < MIN_QUESTIONS:
        note.note += f" Only {len(questions)} question{'s' if len(questions) != 1 else ''} could be matched to a passage."
    return note
