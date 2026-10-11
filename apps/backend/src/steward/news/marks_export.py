"""The owner's marks as candidate cases for the labelled benchmark.

A mark such as "wrong company" or "misread" is evidence that the pipeline got an item wrong,
but it does not say what the right answer was. This turns each such mark into a case in the
benchmark's shape with `expected` left empty: the owner fills it in, then moves the case into
`tests/news/news_benchmark.json`. Nothing here edits the benchmark itself, so an unreviewed
mark can never change the score.
"""

import datetime as dt
from typing import Any

import asyncpg
from pydantic import BaseModel, Field

# Marks that say the pipeline was wrong. "useful" and "confirmed" say nothing about a label.
ERROR_KINDS = ("wrong_entity", "duplicate", "misread", "missing_source", "not_useful")


class CandidateCase(BaseModel):
    id: str
    mark: str
    note: str | None
    marked_at: dt.datetime
    headline: str
    summary: str | None
    outlet: str | None
    published_at: dt.datetime
    candidates: list[str] = Field(default_factory=list)
    region: str | None = None
    language: str | None = None
    expected: dict[str, Any] | None = Field(
        default=None, description="Left empty on purpose: the owner supplies the right answer."
    )


async def marks_candidates(pool: asyncpg.Pool, limit: int = 200) -> list[CandidateCase]:
    """Story and wire-item marks of an error kind, newest first, one case per target."""
    marks = await pool.fetch(
        "select distinct on (target, target_id) target, target_id, kind, note, created_at"
        " from public.news_feedback where target in ('story', 'item') and kind = any($1::text[])"
        " and target_id ~ '^[0-9]+$' order by target, target_id, created_at desc",
        list(ERROR_KINDS),
    )
    out: list[CandidateCase] = []
    for m in marks:
        ident = int(m["target_id"])
        if m["target"] == "story":
            row = await pool.fetchrow(
                "select c.headline, i.summary, i.outlet, i.published_at, i.language,"
                " c.ticker, i.tickers from public.story_cluster c"
                " join public.news_item i on i.cluster_id = c.id where c.id = $1"
                " order by i.tier, i.published_at limit 1",
                ident,
            )
            tickers = sorted({*(row["tickers"] or []), row["ticker"]} - {None}) if row else []
            region = None
        else:
            row = await pool.fetchrow(
                "select coalesce(title_en, headline) as headline, summary, outlet,"
                " published_at, language, region from public.news_article where id = $1",
                ident,
            )
            tickers = []
            region = row["region"] if row else None
        if row is None:
            continue
        out.append(
            CandidateCase(
                id=f"{m['target']}-{ident}",
                mark=m["kind"],
                note=m["note"],
                marked_at=m["created_at"],
                headline=row["headline"],
                summary=row["summary"],
                outlet=row["outlet"],
                published_at=row["published_at"],
                candidates=[str(t) for t in tickers],
                region=region,
                language=row["language"],
            )
        )
    out.sort(key=lambda c: c.marked_at, reverse=True)
    return out[:limit]
