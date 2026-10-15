"""Read side of the N6 analysis and the N7 pages: story analysis, threads, the wire, briefs,
themes and feedback."""

import datetime as dt
import json
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.engine import overrides
from steward.news.brief import BriefBody
from steward.news.claims import CheckedClaim
from steward.news.extract import StoryExtraction
from steward.news.framing import FramingComparison
from steward.news.impact import ImpactResult
from steward.news.market import MarketContext, StudyRow, event_study
from steward.news.sentiment import SentimentResult
from steward.news.thesis_link import LinkedAssumption
from steward.training import labels


def _doc(raw: Any) -> Any:
    return json.loads(raw) if isinstance(raw, str) else raw


class NoveltyOut(BaseModel):
    novelty: Literal["new", "update", "repeat", "recycled"]
    surprise: Literal["none", "in_line", "above", "below", "unknown"]
    similarity: float | None = None
    similar_story_id: int | None = None
    basis: str


class ThreadDiffOut(BaseModel):
    new: list[str] = Field(default_factory=list)
    changed: list[dict[str, str]] = Field(default_factory=list)
    retracted: list[str] = Field(default_factory=list)
    unknown: list[str] = Field(default_factory=list)


class ThreadStepOut(BaseModel):
    thread_id: int
    opened: bool
    diff: ThreadDiffOut


class ClaimsOut(BaseModel):
    claims: list[CheckedClaim]


class ThesisOut(BaseModel):
    links: list[LinkedAssumption]


class StoryAnalysis(BaseModel):
    story_id: int
    extraction: StoryExtraction | None = None
    novelty: NoveltyOut | None = None
    impact: ImpactResult | None = None
    thread: ThreadStepOut | None = None
    framing: FramingComparison | None = None
    claims: ClaimsOut | None = None
    market: MarketContext | None = None
    thesis: ThesisOut | None = None
    sentiment: SentimentResult | None = None
    models: dict[str, str] = Field(
        default_factory=dict, description="The model behind each technique that used one."
    )


_KINDS: dict[str, type[BaseModel]] = {
    "extraction": StoryExtraction,
    "novelty": NoveltyOut,
    "impact": ImpactResult,
    "thread": ThreadStepOut,
    "framing": FramingComparison,
    "claims": ClaimsOut,
    "market": MarketContext,
    "thesis": ThesisOut,
    "sentiment": SentimentResult,
}


async def story_analysis(pool: asyncpg.Pool, story_id: int) -> StoryAnalysis | None:
    if not await pool.fetchval(
        "select exists (select 1 from public.story_cluster where id = $1)", story_id
    ):
        return None
    rows = await pool.fetch(
        "select distinct on (kind) kind, model, payload from public.story_analysis"
        " where cluster_id = $1 order by kind, created_at desc, id desc",
        story_id,
    )
    out: dict[str, Any] = {"story_id": story_id}
    models: dict[str, str] = {}
    for r in rows:
        schema = _KINDS.get(r["kind"])
        if schema is None:
            continue
        try:
            out[r["kind"]] = schema.model_validate(_doc(r["payload"]))
        except ValueError:
            continue
        if r["model"]:
            models[r["kind"]] = r["model"]
    return StoryAnalysis(**out, models=models)


class ThreadOut(BaseModel):
    id: int
    ticker: str | None
    title: str
    opened_at: dt.datetime
    last_update_at: dt.datetime
    state: Literal["open", "quiet", "closed"]
    stories: int


class ThreadStep(BaseModel):
    story_id: int
    headline: str
    at: dt.datetime
    severity: int | None
    lifecycle: str
    diff: ThreadDiffOut


class ThreadDetail(BaseModel):
    thread: ThreadOut
    timeline: list[ThreadStep]


_THREAD_SELECT = (
    "select t.id, t.ticker, t.title, t.opened_at, t.last_update_at, t.state,"
    " (select count(*) from public.story_cluster c where c.thread_id = t.id) as stories"
    " from public.story_thread t"
)


async def list_threads(
    pool: asyncpg.Pool, ticker: str | None, state: str | None, limit: int
) -> list[ThreadOut]:
    rows = await pool.fetch(
        f"{_THREAD_SELECT} where ($1::text is null or t.ticker = $1)"  # noqa: S608
        " and ($2::text is null or t.state = $2)"
        " and exists (select 1 from public.story_cluster c where c.thread_id = t.id)"
        " order by t.last_update_at desc limit $3",
        ticker,
        state,
        limit,
    )
    return [ThreadOut(**dict(r)) for r in rows]


async def thread_detail(pool: asyncpg.Pool, thread_id: int) -> ThreadDetail | None:
    row = await pool.fetchrow(f"{_THREAD_SELECT} where t.id = $1", thread_id)
    if row is None:
        return None
    steps = await pool.fetch(
        "select c.id as story_id, c.headline, coalesce(u.at, c.first_seen) as at, c.severity,"
        " c.lifecycle, u.diff from public.story_cluster c"
        " left join public.story_thread_update u on u.cluster_id = c.id"
        " and u.thread_id = c.thread_id"
        " where c.thread_id = $1 order by c.first_seen",
        thread_id,
    )
    return ThreadDetail(
        thread=ThreadOut(**dict(row)),
        timeline=[
            ThreadStep(
                story_id=s["story_id"],
                headline=s["headline"],
                at=s["at"],
                severity=s["severity"],
                lifecycle=s["lifecycle"],
                diff=ThreadDiffOut(**(_doc(s["diff"]) or {})),
            )
            for s in steps
        ],
    )


class WireItem(BaseModel):
    id: int
    url: str
    headline: str
    title_en: str | None
    summary: str | None
    language: str | None
    region: str | None
    outlet: str | None
    outlet_name: str | None
    tier: int
    published_at: dt.datetime
    linked: bool
    topics: list[str]
    fetch_state: str
    paywall: str
    state_media: bool
    group_id: int | None
    story_id: int | None
    story_severity: int | None
    story_lifecycle: str | None
    gloss: str | None = Field(
        default=None, description="One plain sentence from triage, when triage has read the item."
    )
    relevance: int | None = Field(
        default=None,
        description="Triage relevance: 0 noise, 1 background, 2 may matter, 3 material.",
    )


class WireFilters(BaseModel):
    region: str | None = None
    language: str | None = None
    outlet: str | None = None
    tier: int | None = None
    topic: str | None = None
    linked: bool | None = None
    paywall: str | None = None
    q: str | None = None
    before_id: int | None = None
    limit: int = 50


class WirePage(BaseModel):
    items: list[WireItem]
    next_before_id: int | None


TITLE_SEARCH_DAYS = 90


def like_pattern(text: str) -> str:
    """`text` as a `like` pattern that matches it anywhere: `%`, `_` and the escape character
    `\\` in it are literal (use with `escape '\\'`)."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def wire(pool: asyncpg.Pool, f: WireFilters) -> WirePage:
    clauses: list[str] = []
    args: list[Any] = []

    def add(sql: str, value: Any) -> None:
        args.append(value)
        clauses.append(sql.replace("?", f"${len(args)}"))

    if f.region:
        add("a.region = ?", f.region.upper())
    if f.language:
        add("a.language = ?", f.language.lower())
    if f.outlet and f.outlet.strip():
        add("a.outlet ilike ? escape '\\'", like_pattern(f.outlet.strip()))
    if f.tier:
        add("a.tier = ?", f.tier)
    if f.topic:
        add("? = any(a.topics)", f.topic)
    if f.linked is not None:
        add("a.linked = ?", f.linked)
    if f.paywall:
        add("s.paywall = ?", f.paywall)
    if f.q and f.q.strip():
        query = f.q.strip()
        args.append(query)
        words = len(args)
        args.append(like_pattern(query))
        title = len(args)
        # Two index-friendly queries combined (the full-text index, and a bounded date range for
        # the title substring): one OR across both would scan the whole table.
        clauses.append(
            "a.id in (select m.id from public.news_article m"  # noqa: S608
            f" where m.search @@ plainto_tsquery('simple', ${words})"
            " union select t.id from public.news_article t"
            f" where t.published_at >= now() - interval '{TITLE_SEARCH_DAYS} days'"
            f" and coalesce(t.title_en, '') ilike ${title} escape '\\')"
        )
    if f.before_id:
        add("a.id < ?", f.before_id)
    where = (" where " + " and ".join(clauses)) if clauses else ""
    args.append(f.limit + 1)
    rows = await pool.fetch(
        "select a.id, a.url, a.headline, a.title_en, a.summary, a.language, a.region, a.outlet,"  # noqa: S608
        " a.outlet_name, a.tier, a.published_at, a.linked, a.topics, a.fetch_state,"
        " s.paywall, s.state_media, a.group_id, c.id as story_id, c.severity as story_severity,"
        " c.lifecycle as story_lifecycle, a.triage ->> 'gloss' as gloss,"
        " (a.triage ->> 'relevance')::int as relevance"
        " from public.news_article a join public.source s on s.id = a.source_id"
        " left join public.news_item i on i.id = a.news_item_id"
        " left join public.story_cluster c on c.id = i.cluster_id"
        f"{where} order by a.id desc limit ${len(args)}",
        *args,
    )
    more = len(rows) > f.limit
    items = [WireItem(**{**dict(r), "topics": list(r["topics"])}) for r in rows[: f.limit]]
    return WirePage(items=items, next_before_id=items[-1].id if more and items else None)


class BriefOut(BaseModel):
    id: int
    kind: Literal["morning", "weekly", "holding", "region", "theme"]
    ticker: str
    period_start: dt.datetime
    period_end: dt.datetime
    body: BriefBody
    model: str | None
    created_at: dt.datetime


async def list_briefs(
    pool: asyncpg.Pool, kind: str | None, ticker: str | None, limit: int
) -> list[BriefOut]:
    rows = await pool.fetch(
        "select id, kind, ticker, period_start, period_end, body, model, created_at"
        " from public.news_brief where ($1::text is null or kind = $1)"
        " and ($2::text is null or ticker = $2) order by period_end desc, id desc limit $3",
        kind,
        ticker,
        limit,
    )
    return [
        BriefOut(**{**dict(r), "body": BriefBody.model_validate(_doc(r["body"]))}) for r in rows
    ]


class ThemeDay(BaseModel):
    theme: str
    day: dt.date
    wire_items: int
    stories: int
    regions: list[str]
    flag: Literal["spike", "spread"] | None
    tone: float | None = Field(
        default=None,
        description="GDELT's average tone of worldwide coverage of the theme that day "
        "(negative below zero); null when GDELT is off or has no data.",
    )
    attention: float | None = Field(
        default=None, description="GDELT's share of monitored coverage the theme took that day."
    )


async def theme_days(pool: asyncpg.Pool, days: int) -> list[ThemeDay]:
    rows = await pool.fetch(
        "select theme, day, sum(wire_items)::int as wire_items, sum(stories)::int as stories,"
        " array_agg(region order by region) filter (where region <> '' and wire_items > 0)"
        " as regions, max(flag) as flag, max(tone) as tone, max(attention) as attention"
        " from public.news_theme_day"
        " where day >= $1::date group by theme, day order by day desc, theme",
        # The days are UTC dates (`update_themes`); the database's `current_date` follows the
        # session time zone, which is not guaranteed to be UTC.
        dt.datetime.now(dt.UTC).date() - dt.timedelta(days=days),
    )
    return [ThemeDay(**{**dict(r), "regions": list(r["regions"] or [])}) for r in rows]


FeedbackTarget = Literal["item", "story", "claim", "brief_line"]
FeedbackKind = Literal[
    "wrong_entity", "duplicate", "misread", "missing_source", "useful", "not_useful", "confirmed"
]


class NewsFeedbackIn(BaseModel):
    target: FeedbackTarget
    target_id: str = Field(min_length=1, max_length=200)
    kind: FeedbackKind
    note: str | None = Field(default=None, max_length=1000)


class NewsFeedbackOut(NewsFeedbackIn):
    id: int
    created_at: dt.datetime


async def add_feedback(pool: asyncpg.Pool, body: NewsFeedbackIn) -> NewsFeedbackOut:
    row = await pool.fetchrow(
        "insert into public.news_feedback (target, target_id, kind, note)"
        " values ($1, $2, $3, $4) returning id, target, target_id, kind, note, created_at",
        body.target,
        body.target_id,
        body.kind,
        body.note,
    )
    assert row is not None
    reason = FEEDBACK_AS_REASON.get(body.kind)
    if reason is not None:
        # Marking something wrong is an override of the news engine; the digest and the stories
        # learn from it, and the engine does not redo the item.
        await overrides.record(
            pool,
            area="digest" if body.target == "brief_line" else "news",
            subject_kind=f"news_{body.target}",
            subject_ref=f"{body.target}:{body.target_id}",
            action="marked_wrong",
            via="screen",
            reason_code=reason,
            note=body.note,
            decided_by="engine",
        )
    # The owner's mark is also a training label on what the news engine produced.
    await labels.record(
        pool,
        source="owner_review",
        subject_kind=f"news_{body.target}",
        subject_ref=f"{body.target}:{body.target_id}",
        label="right" if body.kind in ("useful", "confirmed") else "wrong",
        by_owner=True,
        detail={"kind": body.kind, **({"note": body.note} if body.note else {})},
    )
    return NewsFeedbackOut(**dict(row))


# Negative feedback kinds and the override reason each one stands for; "useful" and "confirmed"
# are not overrides.
FEEDBACK_AS_REASON: dict[str, overrides.Reason] = {
    "wrong_entity": "wrong_data",
    "misread": "wrong_data",
    "duplicate": "other",
    "missing_source": "wrong_source",
    "not_useful": "right_but_unimportant",
}


async def list_feedback(
    pool: asyncpg.Pool, target: str | None, target_id: str | None, limit: int
) -> list[NewsFeedbackOut]:
    rows = await pool.fetch(
        "select id, target, target_id, kind, note, created_at from public.news_feedback"
        " where ($1::text is null or target = $1) and ($2::text is null or target_id = $2)"
        " order by id desc limit $3",
        target,
        target_id,
        limit,
    )
    return [NewsFeedbackOut(**dict(r)) for r in rows]


async def study(pool: asyncpg.Pool) -> list[StudyRow]:
    return await event_study(pool)
