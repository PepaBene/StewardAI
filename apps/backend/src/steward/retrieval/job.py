"""The `document_index` job: split new documents into passages, then embed what is missing.

Text indexing comes first and never needs a key, so keyword search over new documents works
even when embedding is off or over budget. Embedding is capped per run and degrades to "not
embedded yet" on a provider error or a spent budget; the next run picks up where this stopped.
"""

import httpx

from steward import __version__, budget_guard
from steward.ai import Usage
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.jobs.runner import JobContext
from steward.news.embeddings import EmbeddingError, GeminiEmbedder
from steward.retrieval import corpus, index
from steward.settings import get_settings
from steward.storage import SupabaseStorage


async def document_index_handler(ctx: JobContext) -> None:
    settings = get_settings()
    stored = await load_active(ctx.pool, "jobs", JobsConfig)
    await ctx.record_config_version(stored.id if stored else None)
    cfg = (stored.body if stored else JobsConfig()).document_index_settings

    async with ctx.step("index") as step:
        totals: dict[str, object] = {}

        async def add(kind: str, docs: list[index.SourceDoc]) -> None:
            stats = await index.index_documents(ctx.pool, docs)
            totals[kind] = {
                "new": stats.documents,
                "unchanged": stats.skipped,
                "passages": stats.children,
                "vectors_kept": stats.reused,
            }

        # Owner-edited records: every run re-reads them, the text hash decides what changes.
        theses = await corpus.thesis_docs(ctx.pool)
        await add("thesis", theses)
        await index.prune(ctx.pool, "thesis", corpus.refs(theses))
        notes = await corpus.note_docs(ctx.pool)
        await add("note", notes)
        await index.prune(ctx.pool, "note", corpus.refs(notes))
        if cfg.index_agent_notes:
            agent_notes = await corpus.agent_note_docs(ctx.pool)
            await add("agent_note", agent_notes)
            await index.prune(ctx.pool, "agent_note", corpus.refs(agent_notes))
        else:
            await index.prune(ctx.pool, "agent_note", [])
        if cfg.index_disclosures:
            disclosures = await corpus.disclosure_docs(ctx.pool, cfg.disclosure_alert_days)
            await add("disclosure", disclosures)
            await index.prune(ctx.pool, "disclosure", corpus.refs(disclosures))
        else:
            await index.prune(ctx.pool, "disclosure", [])
        if cfg.reports_per_run:
            await add("report", await corpus.report_docs(ctx.pool, cfg.reports_per_run))
        if cfg.news_per_run:
            await add("news", await corpus.news_docs(ctx.pool, cfg.news_days, cfg.news_per_run))
        problems: list[str] = []
        if cfg.filings_per_run:
            if settings.supabase_url and settings.supabase_secret_key:
                async with httpx.AsyncClient(
                    timeout=60.0, headers={"User-Agent": f"StewardAI/{__version__}"}
                ) as http:
                    storage = SupabaseStorage(
                        http, settings.supabase_url, settings.supabase_secret_key.get_secret_value()
                    )
                    docs, problems = await corpus.filing_docs(
                        ctx.pool, storage, cfg.filings_per_run
                    )
                    await add("filing", docs)
            else:
                problems = ["document storage is not configured: archived filings were skipped"]
        step.detail["indexed"] = totals
        if problems:
            step.detail["problems"] = problems[:20]

    async with ctx.step("embed") as step:
        if not cfg.embed_per_run:
            step.detail["skipped"] = "embed_per_run is 0"
            return
        if not settings.google_api_key:
            step.detail["skipped"] = "no embedding key configured: passages are keyword-searchable"
            return
        async with httpx.AsyncClient(
            timeout=60.0, headers={"User-Agent": f"StewardAI/{__version__}"}
        ) as http:
            embedder = GeminiEmbedder(
                http,
                settings.google_api_key.get_secret_value(),
                cfg.embedding_model,
                cfg.embedding_dimensions,
            )
            try:
                await budget_guard.before_request(ctx, embedder.model, can_switch_provider=True)
                done = await index.embed_pending(ctx.pool, embedder, cfg.embed_per_run)
            except (EmbeddingError, budget_guard.BudgetExceeded) as exc:
                step.detail["skipped"] = f"{type(exc).__name__}: {exc}"[:300]
                return
        step.detail["embedded"] = done.embedded
        step.detail["embedding_tokens_estimated"] = done.tokens_estimated
        if done.tokens_estimated:
            step.usage = Usage(
                model=embedder.model, input_tokens=done.tokens_estimated
            ).step_usage()
