"""Read models for the news feed, alerts and reports."""

import datetime as dt
import json
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.config.jobs import JobsConfig
from steward.news.digest import DigestBody
from steward.news.escalation import EscalationBody
from steward.news.evidence import Lifecycle
from steward.news.link import INDIRECT_VIA
from steward.news.normalize import host_of
from steward.portfolio.read import JobStatus, JobSummary

Confidence = Literal["verified", "corroborated", "unconfirmed", "disputed"]
StoryStatus = Literal["pending", "confirmed", "unconfirmed", "disputed", "sentiment"]
EscalationStatus = Literal["open", "acknowledged", "resolved"]


class ItemLink(BaseModel):
    ticker: str
    via: str
    term: str
    confidence: float | None = Field(
        default=None,
        description="How sure the link is: 1 for the source or an identifier, lower for a "
        "name, lowest for a competitor, supplier, customer or topic. Null before N5.",
    )


class StoryOut(BaseModel):
    id: int
    ticker: str | None
    event_type: str | None
    severity: int | None
    status: StoryStatus
    confidence: Confidence
    lifecycle: Lifecycle = Field(
        default="reported", description="Where the story is: reported to resolved."
    )
    status_reason: str | None
    rumour: bool
    recycled: bool
    best_tier: int
    source_count: int
    first_seen: dt.datetime
    last_seen: dt.datetime
    corroborating_urls: list[str] = Field(default_factory=list)
    contradicting_urls: list[str] = Field(default_factory=list)


class NewsItemOut(BaseModel):
    id: int
    url: str
    headline: str
    summary: str | None
    outlet: str | None
    outlet_name: str | None
    tier: int
    source_id: str
    published_at: dt.datetime
    tickers: list[str]
    links: list[ItemLink]
    event_type: str | None
    affected_ticker: str | None
    rumour: bool | None
    relevant: bool | None
    sentiment_score: float | None = Field(
        default=None, description="Provider sentiment signal; candidate ranking only."
    )
    sentiment_relevance: float | None = Field(
        default=None, description="Provider ticker relevance for the sentiment signal."
    )
    story: StoryOut


class FeedEventTypeCount(BaseModel):
    event_type: str | None = Field(description="Null for stories not classified yet.")
    count: int


class NewsFeed(BaseModel):
    items: list[NewsItemOut]
    next_before_id: int | None = Field(description="Pass as before_id for the next page.")
    hidden_stories: int = Field(
        default=0,
        description="First page only: stories matching the other filters that `direct` and "
        "`min_severity` leave out.",
    )
    event_types: list[FeedEventTypeCount] = Field(
        default_factory=list,
        description="First page only: stories per event type under the other filters, "
        "whatever `event_type` selects.",
    )
    last_ingest: JobSummary | None
    next_ingest_due_at: dt.datetime | None = Field(
        default=None,
        description="The scheduled news check after the last one: the feed is stale once "
        "this has passed (plus a margin) with no newer check. Null when the job is disabled.",
    )


class FeedFilters(BaseModel):
    ticker: str | None = None
    severities: list[int] = Field(default_factory=list)
    confidences: list[Confidence] = Field(default_factory=list)
    tiers: list[int] = Field(default_factory=list)
    story_id: int | None = None
    before_id: int | None = None
    direct: bool = False
    min_severity: int | None = None
    event_types: list[str] = Field(default_factory=list)
    limit: int = 50


def feed_where(filters: FeedFilters) -> tuple[str, list[Any]]:
    """The WHERE clause of the feed query and its arguments. Each filter narrows the set;
    an empty list means no filter on that field."""
    clauses: list[str] = []
    args: list[Any] = []

    def add(sql: str, value: Any) -> None:
        args.append(value)
        clauses.append(sql.replace("?", f"${len(args)}"))

    if filters.ticker:
        add("i.tickers @> array[?::text]", filters.ticker)
        ticker_arg = f"${len(args)}"
        # One holding's updates: an item the classifier judged irrelevant (another filer's 8-K
        # that merely mentions a watch topic) is not an update for it.
        clauses.append("i.relevant is not false")
        if filters.direct:
            # About the company itself, not reached through a rival, supplier, customer or topic.
            add(
                "exists (select 1 from jsonb_array_elements(i.links) l where l->>'ticker' = "  # noqa: S608 - a placeholder
                + ticker_arg
                + " and not (l->>'via' = any(?::text[])))",
                sorted(INDIRECT_VIA),
            )
    elif filters.direct:
        # Across holdings: items about at least one holding itself, not only reached through
        # a rival, supplier, customer or watch topic.
        clauses.append("i.relevant is not false")
        add(
            "exists (select 1 from jsonb_array_elements(i.links) l"
            " where not (l->>'via' = any(?::text[])))",
            sorted(INDIRECT_VIA),
        )
    if filters.severities:
        add("c.severity = any(?::smallint[])", filters.severities)
    if filters.min_severity is not None:
        # A story the classifier has not reached yet has no severity: it is news, not noise.
        add("(c.severity is null or c.severity >= ?)", filters.min_severity)
    if filters.event_types:
        add("coalesce(c.event_type, 'unclassified') = any(?::text[])", filters.event_types)
    if filters.confidences:
        add("c.confidence = any(?::text[])", list(filters.confidences))
    if filters.tiers:
        add("i.tier = any(?::smallint[])", filters.tiers)
    if filters.story_id is not None:
        add("i.cluster_id = ?", filters.story_id)
    if filters.before_id is not None:
        add("i.id < ?", filters.before_id)
    return (" where " + " and ".join(clauses)) if clauses else "", args


def _urls(corroboration: Any, key: str) -> list[str]:
    if not corroboration:
        return []
    doc = json.loads(corroboration) if isinstance(corroboration, str) else corroboration
    return [entry[0] for entry in doc.get(key, [])]


def _story(row: asyncpg.Record) -> StoryOut:
    return StoryOut(
        id=row["cluster_id"],
        ticker=row["c_ticker"],
        event_type=row["c_event_type"],
        severity=row["c_severity"],
        status=row["c_status"],
        confidence=row["c_confidence"],
        lifecycle=row["c_lifecycle"],
        status_reason=row["c_status_reason"],
        rumour=row["c_rumour"],
        recycled=row["c_recycled"],
        best_tier=row["c_best_tier"],
        source_count=row["c_source_count"],
        first_seen=row["c_first_seen"],
        last_seen=row["c_last_seen"],
        corroborating_urls=_urls(row["c_corroboration"], "supporting"),
        contradicting_urls=_urls(row["c_corroboration"], "contradicting"),
    )


FEED_SELECT = (
    # A story in another language reads in its stored English translation.
    "select i.id, i.url, coalesce(tr.title_en, i.headline) as headline,"
    " coalesce(tr.summary_en, i.summary) as summary, i.outlet, i.outlet_name, i.tier, i.source_id,"
    " i.published_at, i.tickers, i.links, i.event_type, i.affected_ticker, i.rumour,"
    " i.relevant, i.sentiment_score, i.sentiment_relevance, i.cluster_id,"
    " c.ticker as c_ticker, c.event_type as c_event_type,"
    " c.severity as c_severity, c.status as c_status, c.confidence as c_confidence,"
    " c.lifecycle as c_lifecycle,"
    " c.status_reason as c_status_reason, c.rumour as c_rumour, c.recycled as c_recycled,"
    " c.best_tier as c_best_tier, c.source_count as c_source_count,"
    " c.first_seen as c_first_seen, c.last_seen as c_last_seen,"
    " c.corroboration as c_corroboration"
    " from public.news_item i join public.story_cluster c on c.id = i.cluster_id"
    " left join lateral (select t.title_en, t.summary_en from public.news_article t"
    " where t.news_item_id = i.id and t.title_en is not null order by t.id limit 1) tr on true"
)


_STORIES_FROM = " from public.news_item i join public.story_cluster c on c.id = i.cluster_id"


async def _count_stories(pool: asyncpg.Pool, filters: FeedFilters) -> int:
    where, args = feed_where(filters)
    row = await pool.fetchrow(
        f"select count(distinct i.cluster_id) as n{_STORIES_FROM}{where}",
        *args,
    )
    return int(row["n"]) if row else 0


async def _first_page_facets(
    pool: asyncpg.Pool, filters: FeedFilters
) -> tuple[int, list[FeedEventTypeCount]]:
    """Stories the scope (`direct`, `min_severity`) leaves out, and the stories per event type
    under every filter but the event type, so its chips stay put once one is picked."""
    first = filters.model_copy(update={"before_id": None})
    hidden = 0
    if filters.story_id is None and (first.direct or first.min_severity is not None):
        relaxed = first.model_copy(update={"direct": False, "min_severity": None})
        hidden = max(0, await _count_stories(pool, relaxed) - await _count_stories(pool, first))
    where, args = feed_where(first.model_copy(update={"event_types": []}))
    rows = await pool.fetch(
        f"select c.event_type, count(distinct i.cluster_id) as n{_STORIES_FROM}{where}"
        " group by c.event_type order by n desc, c.event_type nulls last",
        *args,
    )
    return hidden, [FeedEventTypeCount(event_type=r["event_type"], count=r["n"]) for r in rows]


async def news_feed(
    pool: asyncpg.Pool, filters: FeedFilters, jobs: JobsConfig | None = None
) -> NewsFeed:
    where, args = feed_where(filters)
    args.append(filters.limit + 1)
    rows = await pool.fetch(
        f"{FEED_SELECT}{where} order by i.id desc limit ${len(args)}",
        *args,
    )
    more = len(rows) > filters.limit
    rows = rows[: filters.limit]
    items = [
        NewsItemOut(
            id=r["id"],
            url=r["url"],
            headline=r["headline"],
            summary=r["summary"],
            outlet=r["outlet"],
            outlet_name=r["outlet_name"],
            tier=r["tier"],
            source_id=r["source_id"],
            published_at=r["published_at"],
            tickers=list(r["tickers"]),
            links=[ItemLink(**link) for link in json.loads(r["links"])],
            event_type=r["event_type"],
            affected_ticker=r["affected_ticker"],
            rumour=r["rumour"],
            relevant=r["relevant"],
            sentiment_score=r["sentiment_score"],
            sentiment_relevance=r["sentiment_relevance"],
            story=_story(r),
        )
        for r in rows
    ]
    job = await pool.fetchrow(
        "select id, job_type, trigger, status, error, created_at, started_at, finished_at"
        " from public.job where job_type = 'news_ingest' order by id desc limit 1"
    )
    hidden, event_types = (
        await _first_page_facets(pool, filters) if filters.before_id is None else (0, [])
    )
    return NewsFeed(
        items=items,
        next_before_id=items[-1].id if more and items else None,
        hidden_stories=hidden,
        event_types=event_types,
        last_ingest=JobSummary(**dict(job)) if job else None,
        next_ingest_due_at=(jobs or JobsConfig()).next_run_after("news_ingest", job["created_at"])
        if job
        else None,
    )


class AlertOut(BaseModel):
    id: int
    kind: Literal["news", "thesis_breach", "test", "portfolio", "system"]
    severity: int
    ticker: str | None
    event_type: str | None
    cluster_id: int | None
    verdict_id: int | None
    channel: Literal["telegram", "email"]
    message: str
    status: Literal["pending", "sent", "suppressed", "failed"]
    send_after: dt.datetime
    sent_at: dt.datetime | None
    suppressed_reason: str | None
    error: str | None
    created_at: dt.datetime


async def recent_alerts(
    pool: asyncpg.Pool, limit: int, kinds: list[str] | None = None
) -> list[AlertOut]:
    rows = await pool.fetch(
        "select id, kind, severity, ticker, event_type, cluster_id, verdict_id, channel, message,"
        " status, send_after, sent_at, suppressed_reason, error, created_at"
        " from public.alert where ($2::text[] is null or kind = any($2)) order by id desc limit $1",
        limit,
        kinds or None,
    )
    return [AlertOut(**dict(r)) for r in rows]


class ReportSummary(BaseModel):
    id: int
    type: Literal["digest", "research", "review", "escalation"]
    period: str | None
    title: str
    headline: str | None
    lead: str | None = Field(
        default=None,
        description="Digests: the first written sentence, the summary's or else the first "
        "holding's, to show what happened rather than how much. Null when none survived.",
    )
    nothing_material: bool | None
    created_at: dt.datetime
    delivery: dict[str, dict[str, str]]
    next_due_at: dt.datetime | None = Field(
        default=None,
        description="When the next report of this kind is scheduled: this one is current "
        "until then. Null for unscheduled kinds or a disabled schedule.",
    )
    story_count: int | None = Field(
        default=None,
        description="Digests: the stories across its sections (sections[].stories). Null for "
        "report kinds without stories.",
    )
    citation_count: int | None = Field(
        default=None,
        description="Digests: the distinct sources it cites, counted by URL across its news "
        "sources (sources[].url) and the filings of its cited facts "
        "(fact_sources[].source_url); several facts from one filing count once. Null for "
        "report kinds without a source list.",
    )


class ReportOut(BaseModel):
    id: int
    type: Literal["digest", "research", "review", "escalation"]
    period: str | None
    title: str
    created_at: dt.datetime
    period_start: dt.datetime | None
    period_end: dt.datetime | None
    body: DigestBody
    body_md: str
    delivery: dict[str, dict[str, str]]
    next_due_at: dt.datetime | None = Field(
        default=None, description="When the next report of this kind is scheduled."
    )


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _next_due(
    jobs: JobsConfig | None, report_type: str, period: str | None, created_at: dt.datetime
) -> dt.datetime | None:
    if report_type != "digest" or period not in ("daily", "weekly", "monthly", "quarterly"):
        return None
    return (jobs or JobsConfig()).next_run_after(f"{period}_digest", created_at)


# Counted in Postgres from the stored body, in the same query as the list (see ReportSummary).
_DIGEST_STORY_COUNT = (
    "case when type = 'digest' then (select coalesce(sum(jsonb_array_length("
    "coalesce(sec -> 'stories', '[]'::jsonb))), 0)::int"
    " from jsonb_array_elements(coalesce(body -> 'sections', '[]'::jsonb)) sec) end"
)
_DIGEST_CITATION_COUNT = (
    "case when type = 'digest' then (select count(distinct u)::int from ("
    "select src ->> 'url' as u from jsonb_array_elements(coalesce(body -> 'sources',"
    " '[]'::jsonb)) src union all select fs ->> 'source_url'"
    " from jsonb_array_elements(coalesce(body -> 'fact_sources', '[]'::jsonb)) fs) cited"
    " where u is not null) end"
)


async def list_reports(
    pool: asyncpg.Pool,
    report_type: str,
    period: str | None,
    limit: int,
    jobs: JobsConfig | None = None,
) -> list[ReportSummary]:
    rows = await pool.fetch(
        "select id, type, period, title, created_at, delivery, body ->> 'headline' as headline,"  # noqa: S608
        " case when type = 'digest' then coalesce(body -> 'overview' -> 0 ->> 'text',"
        " (select sec -> 'summary' -> 0 ->> 'text' from jsonb_array_elements("
        "coalesce(body -> 'sections', '[]'::jsonb)) with ordinality s(sec, n)"
        " where sec -> 'summary' -> 0 ->> 'text' is not null order by n limit 1)) end as lead,"
        " (body ->> 'nothing_material')::boolean as nothing_material,"
        f" {_DIGEST_STORY_COUNT} as story_count,"
        f" {_DIGEST_CITATION_COUNT} as citation_count"
        " from public.report"
        " where type = $1 and ($2::text is null or period = $2) order by id desc limit $3",
        report_type,
        period,
        limit,
    )
    return [
        ReportSummary(
            **{
                **dict(r),
                "delivery": _json(r["delivery"]),
                "next_due_at": _next_due(jobs, r["type"], r["period"], r["created_at"]),
            }
        )
        for r in rows
    ]


async def get_report(
    pool: asyncpg.Pool, report_id: int, jobs: JobsConfig | None = None
) -> ReportOut | None:
    row = await pool.fetchrow(
        "select id, type, period, title, created_at, period_start, period_end, body, body_md,"
        " delivery from public.report where id = $1",
        report_id,
    )
    if row is None:
        return None
    return ReportOut(
        **{
            **dict(row),
            "body": DigestBody.model_validate(_json(row["body"])),
            "delivery": _json(row["delivery"]),
            "next_due_at": _next_due(jobs, row["type"], row["period"], row["created_at"]),
        }
    )


# ---------------------------------------------------------------------------
# Disputed stories: both versions side by side
# ---------------------------------------------------------------------------


class ClaimSource(BaseModel):
    url: str
    outlet: str | None
    tier: int
    headline: str | None = None
    published_at: dt.datetime | None = None
    news_item_id: int | None = None


class DisputedStory(BaseModel):
    id: int
    ticker: str | None
    event_type: str | None
    severity: int | None
    headline: str
    status_reason: str | None
    first_seen: dt.datetime
    last_seen: dt.datetime
    claim: list[ClaimSource] = Field(description="The story's own sources (the claim).")
    contradicting: list[ClaimSource] = Field(description="Verified sources that dispute it.")
    supporting: list[ClaimSource] = Field(description="Verified sources found to support it.")
    note: str | None = Field(description="What the corroboration search found, in a sentence.")
    resolution: Literal["confirmed", "false"] | None
    resolution_note: str | None
    resolved_at: dt.datetime | None
    verdicts_frozen: bool = Field(
        description="The holding's verdicts do not change until this story is resolved."
    )


def _sources(doc: Any, key: str) -> list[ClaimSource]:
    if not doc:
        return []
    data = json.loads(doc) if isinstance(doc, str) else doc
    return [ClaimSource(url=u, outlet=host_of(u), tier=int(t)) for u, t in data.get(key, [])]


async def disputed_stories(pool: asyncpg.Pool, include_resolved: bool) -> list[DisputedStory]:
    rows = await pool.fetch(
        "select id, ticker, event_type, severity, headline, status_reason, first_seen,"
        " last_seen, corroboration, resolution, resolution_note, resolved_at"
        " from public.story_cluster where (status = 'disputed' or resolution is not null)"
        " and ($1 or resolved_at is null) order by last_seen desc limit 100",
        include_resolved,
    )
    out = []
    for r in rows:
        items = await pool.fetch(
            "select id, url, outlet, tier, headline, published_at from public.news_item"
            " where cluster_id = $1 order by tier, published_at, id",
            r["id"],
        )
        doc = json.loads(r["corroboration"]) if r["corroboration"] else {}
        out.append(
            DisputedStory(
                id=r["id"],
                ticker=r["ticker"],
                event_type=r["event_type"],
                severity=r["severity"],
                headline=r["headline"],
                status_reason=r["status_reason"],
                first_seen=r["first_seen"],
                last_seen=r["last_seen"],
                claim=[
                    ClaimSource(
                        url=i["url"],
                        outlet=i["outlet"],
                        tier=i["tier"],
                        headline=i["headline"],
                        published_at=i["published_at"],
                        news_item_id=i["id"],
                    )
                    for i in items
                ],
                contradicting=_sources(doc, "contradicting"),
                supporting=_sources(doc, "supporting"),
                note=doc.get("note"),
                resolution=r["resolution"],
                resolution_note=r["resolution_note"],
                resolved_at=r["resolved_at"],
                verdicts_frozen=r["resolved_at"] is None
                and r["ticker"] is not None
                and (r["severity"] or 0) >= 2,
            )
        )
    return out


async def resolve_story(
    pool: asyncpg.Pool, story_id: int, resolution: str, note: str | None
) -> bool:
    """The owner's resolution of a disputed story: confirmed (it happened) or false. The
    story's verdict freeze lifts; a confirmed story is routed on the next news run."""
    updated = await pool.fetchval(
        "update public.story_cluster set resolution = $2, resolution_note = $3,"
        " resolved_at = now(), status = case when $2 = 'confirmed' then 'confirmed'"
        " else status end, confidence = case when $2 = 'confirmed' then 'corroborated'"
        " else confidence end, status_reason = case when $2 = 'confirmed' then"
        " 'resolved by the owner' else 'marked false by the owner' end,"
        " last_seen = greatest(last_seen, now())"
        " where id = $1 and (status = 'disputed' or resolution is not null) returning id",
        story_id,
        resolution,
        note,
    )
    return updated is not None


# ---------------------------------------------------------------------------
# Evidence behind a story: why it is linked, confirmed, disputed or corrected
# ---------------------------------------------------------------------------


class EvidenceItem(BaseModel):
    id: int
    url: str
    headline: str
    outlet: str | None
    tier: int
    kind: str | None = Field(description="Provenance kind; null on items stored before N5.")
    independence_key: str | None = Field(
        description="Items sharing a key are one source (a wire and its republishers)."
    )
    is_correction: bool
    published_at: dt.datetime
    links: list[ItemLink] = Field(description="Why the item is tied to each holding.")


class LifecycleStep(BaseModel):
    at: dt.datetime
    from_state: str | None
    to_state: Lifecycle
    reason: str
    evidence: list[dict[str, Any]] = Field(description="The items and results behind it.")


class StoryEvidence(BaseModel):
    id: int
    headline: str
    lifecycle: Lifecycle
    lifecycle_reason: str | None
    status: StoryStatus
    confidence: Confidence
    status_reason: str | None
    closed_at: dt.datetime | None = Field(description="When the owner closed (resolved) it.")
    independent_sources: int = Field(description="Independence groups among the items.")
    primary_evidence: int = Field(description="Items that are primary evidence.")
    items: list[EvidenceItem]
    history: list[LifecycleStep] = Field(description="Every lifecycle change, oldest first.")


async def story_evidence(pool: asyncpg.Pool, story_id: int) -> StoryEvidence | None:
    story = await pool.fetchrow(
        "select id, headline, lifecycle, lifecycle_reason, status, confidence, status_reason,"
        " lifecycle_closed_at from public.story_cluster where id = $1",
        story_id,
    )
    if story is None:
        return None
    rows = await pool.fetch(
        "select id, url, headline, outlet, tier, evidence_kind, independence_key, is_correction,"
        " published_at, links from public.news_item where cluster_id = $1"
        " order by tier, published_at, id",
        story_id,
    )
    events = await pool.fetch(
        "select at, from_state, to_state, reason, evidence from public.story_lifecycle_event"
        " where cluster_id = $1 order by at, id",
        story_id,
    )
    from steward.news.evidence import PRIMARY_KINDS

    return StoryEvidence(
        id=story["id"],
        headline=story["headline"],
        lifecycle=story["lifecycle"],
        lifecycle_reason=story["lifecycle_reason"],
        status=story["status"],
        confidence=story["confidence"],
        status_reason=story["status_reason"],
        closed_at=story["lifecycle_closed_at"],
        independent_sources=len({r["independence_key"] or r["outlet"] or r["url"] for r in rows}),
        primary_evidence=sum(1 for r in rows if r["evidence_kind"] in PRIMARY_KINDS),
        items=[
            EvidenceItem(
                id=r["id"],
                url=r["url"],
                headline=r["headline"],
                outlet=r["outlet"],
                tier=r["tier"],
                kind=r["evidence_kind"],
                independence_key=r["independence_key"],
                is_correction=r["is_correction"],
                published_at=r["published_at"],
                links=[ItemLink(**link) for link in _json(r["links"])],
            )
            for r in rows
        ],
        history=[
            LifecycleStep(
                at=e["at"],
                from_state=e["from_state"],
                to_state=e["to_state"],
                reason=e["reason"],
                evidence=_json(e["evidence"]),
            )
            for e in events
        ],
    )


async def set_story_closed(pool: asyncpg.Pool, story_id: int, closed: bool) -> bool:
    """The owner closes a story (lifecycle `resolved`) or reopens it. Closing sets the state
    now and records the step; reopening clears the closing time and the story's next
    assessment recomputes the state from its evidence."""
    async with pool.acquire() as conn, conn.transaction():
        before = await conn.fetchrow(
            "select lifecycle from public.story_cluster where id = $1 for update", story_id
        )
        if before is None:
            return False
        await conn.execute(
            "update public.story_cluster set lifecycle_closed_at ="
            " case when $2 then coalesce(lifecycle_closed_at, now()) end,"
            " lifecycle = case when $2 then 'resolved' else lifecycle end,"
            " lifecycle_reason = case when $2 then 'closed by the owner' else lifecycle_reason"
            " end, lifecycle_at = now() where id = $1",
            story_id,
            closed,
        )
        if closed and before["lifecycle"] != "resolved":
            await conn.execute(
                "insert into public.story_lifecycle_event (cluster_id, from_state, to_state,"
                " reason) values ($1, $2, 'resolved', 'closed by the owner')",
                story_id,
                before["lifecycle"],
            )
    return True


# ---------------------------------------------------------------------------
# Escalations
# ---------------------------------------------------------------------------


class EscalationOut(BaseModel):
    id: int
    ticker: str | None
    title: str
    created_at: dt.datetime
    body: EscalationBody
    body_md: str
    job_id: int | None = Field(description="The event_escalation run that wrote the note.")
    job_status: JobStatus | None
    cost_usd: float | None = Field(
        description="Model and search cost of that run (sum of its steps); null when no "
        "step recorded a cost."
    )
    models: list[str] = Field(description="Models the run called.")
    status: EscalationStatus = Field(description="Where the owner is with it.")
    acknowledged_at: dt.datetime | None = Field(
        description="When the owner first acknowledged (or directly resolved) it; kept when "
        "reopened."
    )
    resolved_at: dt.datetime | None = Field(
        description="When the owner resolved it; null unless resolved."
    )


async def list_escalations(
    pool: asyncpg.Pool,
    ticker: str | None,
    limit: int,
    report_id: int | None = None,
    status: EscalationStatus | None = None,
) -> list[EscalationOut]:
    rows = await pool.fetch(
        "select r.id, r.ticker, r.title, r.created_at, r.body, r.body_md, r.job_id,"
        " r.escalation_status as status, r.acknowledged_at, r.resolved_at,"
        " j.status as job_status, s.cost_usd, coalesce(s.models, '{}') as models"
        " from public.report r left join public.job j on j.id = r.job_id"
        " left join lateral (select sum(cost_usd) as cost_usd,"
        " array_agg(distinct model) filter (where model is not null) as models"
        " from public.job_step where job_id = r.job_id) s on true"
        " where r.type = 'escalation' and ($1::text is null or r.ticker = $1)"
        " and ($3::bigint is null or r.id = $3)"
        " and ($4::text is null or r.escalation_status = $4)"
        " order by r.id desc limit $2",
        ticker,
        limit,
        report_id,
        status,
    )
    return [
        EscalationOut(
            **{
                **dict(r),
                "body": EscalationBody.model_validate(_json(r["body"])),
                "cost_usd": float(r["cost_usd"]) if r["cost_usd"] is not None else None,
                "models": list(r["models"]),
            }
        )
        for r in rows
    ]
