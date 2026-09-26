"""The owner's feedback (Defence layer 6): *wrong*, *irrelevant* and *useful* on every fact,
verdict, alert, news item, story and report sentence.

What each mark does, beyond being stored:

| Target          | wrong                                                   | useful          |
| --------------- | ------------------------------------------------------- | --------------- |
| fact            | quarantined (leaves every verdict); the holding's       | source trust up |
|                 | verdicts are re-judged by the worker; source trust down |                 |
| news item       | its outlet's trust down                                 | trust up        |
| story, alert    | trust down for every outlet in the story                | trust up        |
| verdict         | stored for the error report (a verdict is arithmetic on | -               |
|                 | facts: mark the fact that is wrong)                     |                 |
| report sentence | stored for the error report (a model-written sentence;  | -               |
|                 | the claim verifier is the defence)                      |                 |

*Irrelevant* never changes trust: it says nothing about accuracy. A wrong fact or news item
can also be added to an evaluation set with its corrected value (`eval_candidate`), which
`python -m steward.verify.export_evals` writes into the repository's eval files.
"""

import datetime as dt
import json
from decimal import Decimal
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field, model_validator

from steward.config.event_rules import EventType
from steward.config.verification import VerificationConfig
from steward.jobs import queue
from steward.jobs.runner import create_job
from steward.verify.trust import apply_rating, hosts_of

TargetKind = Literal["fact", "verdict", "alert", "news_item", "story", "report_sentence"]
Rating = Literal["wrong", "irrelevant", "useful"]


class NewsLabel(BaseModel):
    """The right classification of a news item, for the labelled set."""

    event_type: EventType
    affected_ticker: str | None
    relevant: bool
    rumour: bool


class FeedbackIn(BaseModel):
    target_kind: TargetKind
    target_id: int = Field(
        ge=1, le=2**63 - 1, description="The fact, verdict, alert, news item, story or report id."
    )
    sentence_path: str | None = Field(
        default=None,
        pattern=r"^[a-z_]+(/[0-9a-z_]+)*$",
        description="Report sentences only: where it sits in the report body, e.g. "
        "'overview/0', 'sections/2/summary/1' (digest) or 'sentences/3' (impact note).",
    )
    rating: Rating
    note: str | None = Field(default=None, max_length=1000)
    add_to_eval: bool = Field(
        default=False,
        description="With rating 'wrong' on a fact or news item: add it to the golden or "
        "labelled evaluation set with the corrected value or label.",
    )
    corrected_value: Decimal | None = Field(
        default=None, description="Facts: the right value, in the fact's printed scale."
    )
    corrected_value_as_written: str | None = Field(
        default=None,
        max_length=40,
        description="Facts added to the golden set: the right number exactly as printed, when "
        "it differs from the stored one (e.g. '94,036').",
    )
    corrected_snippet: str | None = Field(
        default=None,
        max_length=1000,
        description="Facts added to the golden set: the document passage that states the right "
        "number, when the stored snippet does not contain it.",
    )
    corrected_label: NewsLabel | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "FeedbackIn":
        if (self.target_kind == "report_sentence") != (self.sentence_path is not None):
            raise ValueError("sentence_path is required for, and only for, report sentences")
        if self.add_to_eval:
            if self.rating != "wrong":
                raise ValueError("only an item marked wrong can be added to an eval set")
            if self.target_kind == "fact" and self.corrected_value is None:
                raise ValueError("adding a fact to the golden set needs corrected_value")
            if self.target_kind == "news_item" and self.corrected_label is None:
                raise ValueError("adding a news item to the labelled set needs corrected_label")
            if self.target_kind not in ("fact", "news_item"):
                raise ValueError("only facts and news items can be added to an eval set")
        return self


class FeedbackOut(BaseModel):
    id: int
    target_kind: TargetKind
    target_id: int
    sentence_path: str | None
    rating: Rating
    note: str | None
    source_hosts: list[str]
    effects: dict[str, Any] = Field(
        description="What the feedback did: quarantined fact, verdict job, trust scores, "
        "eval candidate."
    )
    created_at: dt.datetime


class FeedbackError(Exception):
    def __init__(self, message: str, *, not_found: bool = False) -> None:
        super().__init__(message)
        self.not_found = not_found


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def sentence_at(body: dict[str, Any], path: str) -> dict[str, Any] | None:
    node: Any = body
    for part in path.split("/"):
        if isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        elif isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node if isinstance(node, dict) and isinstance(node.get("text"), str) else None


async def _story_urls(conn: asyncpg.Connection, cluster_id: int) -> list[str]:
    rows = await conn.fetch("select url from public.news_item where cluster_id = $1", cluster_id)
    return [r["url"] for r in rows]


async def record_feedback(
    pool: asyncpg.Pool,
    body: FeedbackIn,
    verification: VerificationConfig,
    owner_id: str | None,
) -> FeedbackOut:
    """Store one mark and apply its effects in one transaction."""
    effects: dict[str, Any] = {}
    async with pool.acquire() as conn, conn.transaction():
        urls: list[str] = []
        kind, target = body.target_kind, body.target_id
        if kind == "fact":
            fact = await conn.fetchrow(
                "select id, ticker, status, source_url from public.fact where id = $1 for update",
                target,
            )
            if fact is None:
                raise FeedbackError(f"No fact {target}", not_found=True)
            urls = [fact["source_url"]]
            if body.rating == "wrong" and fact["status"] in ("verified", "unverified"):
                reason = "owner marked it wrong" + (f": {body.note}" if body.note else "")
                await conn.execute(
                    "update public.fact set status = 'quarantined', status_reason = $2,"
                    " reviewed_at = now(), reviewed_by = $3::uuid where id = $1",
                    target,
                    reason[:500],
                    owner_id,
                )
                job_id = await create_job(
                    conn, "thesis_verdicts", "event", owner_id, {"ticker": fact["ticker"]}
                )
                await queue.enqueue(conn, job_id, "thesis_verdicts")
                effects.update(quarantined=True, verdict_job_id=job_id)
        elif kind == "news_item":
            item = await conn.fetchrow("select url from public.news_item where id = $1", target)
            if item is None:
                raise FeedbackError(f"No news item {target}", not_found=True)
            urls = [item["url"]]
        elif kind == "story":
            if not await conn.fetchval(
                "select exists (select 1 from public.story_cluster where id = $1)", target
            ):
                raise FeedbackError(f"No story {target}", not_found=True)
            urls = await _story_urls(conn, target)
        elif kind == "alert":
            alert = await conn.fetchrow("select cluster_id from public.alert where id = $1", target)
            if alert is None:
                raise FeedbackError(f"No alert {target}", not_found=True)
            if alert["cluster_id"] is not None:
                urls = await _story_urls(conn, alert["cluster_id"])
        elif kind == "verdict":
            if not await conn.fetchval(
                "select exists (select 1 from public.verdict where id = $1)", target
            ):
                raise FeedbackError(f"No verdict {target}", not_found=True)
        else:  # report_sentence
            report = await conn.fetchrow("select body from public.report where id = $1", target)
            if report is None:
                raise FeedbackError(f"No report {target}", not_found=True)
            assert body.sentence_path is not None
            if sentence_at(_json(report["body"]), body.sentence_path) is None:
                raise FeedbackError(
                    f"Report {target} has no sentence at {body.sentence_path}", not_found=True
                )

        hosts = hosts_of(urls) if body.rating in ("wrong", "useful") else []
        # One item moves trust once per rating: marking it wrong again changes nothing.
        repeat = await conn.fetchval(
            "select exists (select 1 from public.feedback where target_kind = $1"
            " and target_id = $2 and rating = $3 and sentence_path is not distinct from $4)",
            kind,
            target,
            body.rating,
            body.sentence_path,
        )
        if repeat:
            hosts = []
            effects["repeat"] = True
        if hosts:
            effects["trust"] = await apply_rating(conn, hosts, body.rating, verification.trust)
        row = await conn.fetchrow(
            "insert into public.feedback (target_kind, target_id, sentence_path, rating, note,"
            " source_hosts, effects, created_by) values ($1, $2, $3, $4, $5, $6, $7::jsonb,"
            " $8::uuid) returning id, created_at",
            kind,
            target,
            body.sentence_path,
            body.rating,
            body.note,
            hosts,
            json.dumps(effects, default=str),
            owner_id,
        )
        if body.add_to_eval:
            corrected: dict[str, Any]
            if kind == "fact":
                corrected = {
                    "value": str(body.corrected_value),
                    "value_as_written": body.corrected_value_as_written,
                    "snippet": body.corrected_snippet,
                }
            else:
                corrected = body.corrected_label.model_dump() if body.corrected_label else {}
            candidate = await conn.fetchval(
                "insert into public.eval_candidate (kind, feedback_id, fact_id, news_item_id,"
                " corrected, note) values ($1, $2, $3, $4, $5::jsonb, $6)"
                " on conflict do nothing returning id",
                "earnings_figure" if kind == "fact" else "news_label",
                row["id"],
                target if kind == "fact" else None,
                target if kind == "news_item" else None,
                json.dumps(corrected),
                body.note,
            )
            effects["eval_candidate_id"] = candidate
            await conn.execute(
                "update public.feedback set effects = $2::jsonb where id = $1",
                row["id"],
                json.dumps(effects, default=str),
            )
    return FeedbackOut(
        id=row["id"],
        target_kind=kind,
        target_id=target,
        sentence_path=body.sentence_path,
        rating=body.rating,
        note=body.note,
        source_hosts=hosts,
        effects=effects,
        created_at=row["created_at"],
    )


async def list_feedback(
    pool: asyncpg.Pool,
    target_kind: TargetKind | None,
    target_ids: list[int] | None,
    since: dt.datetime | None,
    limit: int,
) -> list[FeedbackOut]:
    rows = await pool.fetch(
        "select id, target_kind, target_id, sentence_path, rating, note, source_hosts, effects,"
        " created_at from public.feedback where ($1::text is null or target_kind = $1)"
        " and ($2::bigint[] is null or target_id = any($2)) and ($3::timestamptz is null"
        " or created_at >= $3) order by id desc limit $4",
        target_kind,
        target_ids,
        since,
        limit,
    )
    return [
        FeedbackOut(
            **{
                **dict(r),
                "source_hosts": list(r["source_hosts"]),
                "effects": _json(r["effects"]),
            }
        )
        for r in rows
    ]


class SourceTrustOut(BaseModel):
    host: str
    score: float
    wrong_count: int
    useful_count: int
    demoted: bool = Field(description="Below the threshold: counts one tier lower.")
    updated_at: dt.datetime


async def list_trust(pool: asyncpg.Pool, verification: VerificationConfig) -> list[SourceTrustOut]:
    rows = await pool.fetch(
        "select host, score, wrong_count, useful_count, updated_at from public.source_trust"
        " order by score, host"
    )
    return [
        SourceTrustOut(
            **{**dict(r), "score": float(r["score"])},
            demoted=float(r["score"]) < verification.trust.demote_below,
        )
        for r in rows
    ]
