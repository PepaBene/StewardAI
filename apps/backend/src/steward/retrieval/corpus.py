"""What gets indexed: the readers that turn stored records into `SourceDoc`s.

Tiers 1 to 3 of the corpus decision: archived filings and results releases, the owner's own
reports, theses and notes, and news. Every reader skips records that are already indexed
(immutable ones by a cheap existence check, editable ones by the text hash in `index_document`)
and takes at most a stated number per run, so one run's cost is bounded.
"""

import datetime as dt
from collections.abc import Sequence

import asyncpg

from steward.chat.tools import REPORT_PATHS
from steward.config.store import load_active
from steward.config.thesis import ThesesConfig, ThesisConfig
from steward.fundamentals.ir import IrError, document_lines
from steward.retrieval.index import SourceDoc
from steward.storage import EvidenceStore, StorageError

MIN_FILING_CHARS = 1500  # a shorter extract is a stub, not a document worth passages


def thesis_text(t: ThesisConfig) -> str:
    """One thesis as prose a passage can quote: why it is owned, the checks, the exits."""
    lines = [f"Thesis for {t.ticker}.", f"Why owned: {t.why_owned}"]
    kpis = [f"{k.metric} {k.operator} {k.threshold:g} {k.unit}" for k in t.kpis]
    if kpis:
        lines.append("KPIs the thesis is checked against: " + "; ".join(kpis) + ".")
    if t.exit_conditions:
        lines.append("Exit conditions: " + "; ".join(t.exit_conditions) + ".")
    if t.pre_mortem:
        lines.append("Pre-mortem: " + "; ".join(t.pre_mortem) + ".")
    vp = t.variant_perception
    if vp is not None:
        lines.append(f"Variant perception: {vp.belief} It would be wrong if: {vp.disproof}")
    return "\n\n".join(lines)


async def thesis_docs(pool: asyncpg.Pool) -> list[SourceDoc]:
    stored = await load_active(pool, "theses", ThesesConfig)
    if stored is None:
        return []
    today = dt.datetime.now(dt.UTC).date()
    return [
        SourceDoc(
            kind="thesis",
            ref=t.ticker,
            title=f"Thesis: {t.ticker}",
            text=thesis_text(t),
            tickers=(t.ticker,),
            as_of=today,
            app_path=f"/holdings/{t.ticker}",
            tier=1,
            structured=False,
        )
        for t in stored.body.theses
    ]


async def note_docs(pool: asyncpg.Pool) -> list[SourceDoc]:
    """The owner's case items (moat, bull, bear, risk, ...) that are not archived."""
    rows = await pool.fetch(
        "select id, ticker, kind, moat_type, statement, status, updated_at"
        " from public.company_case_item where not archived order by id"
    )
    return [
        SourceDoc(
            kind="note",
            ref=str(r["id"]),
            title=f"Owner note on {r['ticker']}: {r['kind']}",
            text=f"{r['kind']}{' (' + r['moat_type'] + ')' if r['moat_type'] else ''}"
            f" [{r['status']}]: {r['statement']}",
            tickers=(r["ticker"],),
            as_of=r["updated_at"].date(),
            app_path=f"/companies/{r['ticker']}",
            tier=1,
            structured=False,
        )
        for r in rows
    ]


async def report_docs(pool: asyncpg.Pool, limit: int) -> list[SourceDoc]:
    """Reports not indexed yet (a report never changes once written), newest first."""
    rows = await pool.fetch(
        "select r.id, r.type, r.ticker, r.title, r.body_md, r.created_at from public.report r"
        " where not exists (select 1 from public.document_chunk c"
        " where c.kind = 'report' and c.ref = r.id::text) order by r.created_at desc limit $1",
        limit,
    )
    return [
        SourceDoc(
            kind="report",
            ref=str(r["id"]),
            title=r["title"],
            text=r["body_md"],
            tickers=(r["ticker"],) if r["ticker"] else (),
            as_of=r["created_at"].date(),
            app_path=REPORT_PATHS.get(r["type"], "/reports/{}").format(r["id"]),
            tier=1,
        )
        for r in rows
    ]


async def news_docs(pool: asyncpg.Pool, days: int, limit: int) -> list[SourceDoc]:
    """News items of the last `days` not indexed yet, newest first: one passage each, the
    headline and summary (the article body is not stored for long)."""
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    rows = await pool.fetch(
        "select i.id, i.url, i.headline, i.summary, i.tier, i.tickers, i.published_at"
        " from public.news_item i where i.published_at >= $1 and not exists"
        " (select 1 from public.document_chunk c where c.kind = 'news' and c.ref = i.id::text)"
        " order by i.published_at desc limit $2",
        since,
        limit,
    )
    return [
        SourceDoc(
            kind="news",
            ref=str(r["id"]),
            title=r["headline"],
            text=r["summary"] or r["headline"],
            tickers=tuple(r["tickers"]),
            as_of=r["published_at"].date(),
            source_url=r["url"],
            tier=r["tier"],
            structured=False,
        )
        for r in rows
    ]


async def filing_docs(
    pool: asyncpg.Pool, storage: EvidenceStore, limit: int
) -> tuple[list[SourceDoc], list[str]]:
    """Archived documents (results releases, filings) that a stored fact was read from and that
    are not indexed yet, newest period first. Returns the documents and the reasons any could
    not be read."""
    rows = await pool.fetch(
        "select e.sha256, e.storage_path, e.source_url, e.content_type, e.title,"
        " array_agg(distinct f.ticker) as tickers, max(f.period_end) as period_end"
        " from public.evidence e join public.fact f on f.evidence_sha256 = e.sha256"
        " where not exists (select 1 from public.document_chunk c"
        " where c.kind = 'filing' and c.ref = e.sha256)"
        " group by e.sha256, e.storage_path, e.source_url, e.content_type, e.title"
        " order by max(f.period_end) desc nulls last limit $1",
        limit,
    )
    docs: list[SourceDoc] = []
    problems: list[str] = []
    for r in rows:
        try:
            data = await storage.get(r["storage_path"])
            text = "\n".join(document_lines(data, r["content_type"] or ""))
        except (StorageError, IrError) as exc:
            problems.append(f"{r['sha256'][:12]}: {str(exc)[:80]}")
            continue
        if len(text) < MIN_FILING_CHARS:
            problems.append(f"{r['sha256'][:12]}: text too short ({len(text)} characters)")
            continue
        tickers = tuple(sorted(t for t in r["tickers"] if t))
        docs.append(
            SourceDoc(
                kind="filing",
                ref=r["sha256"],
                title=r["title"] or f"{', '.join(tickers) or 'Filing'} document",
                text=text,
                tickers=tickers,
                as_of=r["period_end"],
                source_url=r["source_url"],
                tier=1,
            )
        )
    return docs, problems


def refs(docs: Sequence[SourceDoc]) -> list[str]:
    return [d.ref for d in docs]
