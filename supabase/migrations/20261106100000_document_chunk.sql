-- Passage index over the prose the app holds (agentic workflows and RAG plan, AG2): archived
-- filings and results releases, the owner's reports, theses and notes, and news.
--
-- A document is split by section into parent chunks (the section, read for context) and child
-- chunks (small passages, matched by keyword and by embedding). Only child chunks carry an
-- embedding. A document version that changed is re-indexed whole: its old chunks are deleted in
-- the same transaction, so a ref has exactly one version here.
--
-- The embedding is stored as real[] so the table works on any Postgres; when the server has
-- the pgvector extension, a generated vector column and an HNSW index make the nearest-
-- neighbour search an index scan (the code detects the column and falls back to a bounded
-- scan in Python without it). The column is fixed at 768 dimensions, the dimension the
-- news wire already uses; another dimension is stored (real[], embedding_dim) but not indexed.
create table public.document_chunk (
  id               bigint generated always as identity primary key,
  kind             text        not null check (kind in ('filing', 'report', 'thesis', 'note', 'news')),
  -- The stored record the text came from: evidence sha256, report id, ticker, note id, news id.
  ref              text        not null,
  -- Hash of the indexed text; a different hash is a new version.
  version          text        not null,
  chunk_index      integer     not null check (chunk_index >= 0),
  is_parent        boolean     not null default false,
  parent_id        bigint      references public.document_chunk (id) on delete cascade,
  section_path     text,
  title            text        not null,
  text             text        not null check (length(text) > 0),
  tickers          text[]      not null default '{}',
  -- The date the document is about or was published, for date filters and recency.
  as_of            date,
  source_url       text,
  app_path         text,
  tier             smallint    check (tier between 1 and 3),
  search           tsvector generated always as
                   (to_tsvector('english', coalesce(section_path, '') || ' ' || text)) stored,
  embedding        real[],
  embedding_model  text,
  embedding_dim    integer,
  created_at       timestamptz not null default now(),
  unique (kind, ref, version, chunk_index, is_parent),
  check (is_parent = false or (embedding is null and parent_id is null)),
  check ((embedding is null) = (embedding_model is null)),
  check ((embedding is null) = (embedding_dim is null)),
  check (embedding is null or cardinality(embedding) = embedding_dim)
);

comment on table public.document_chunk is
  'Passages of prose documents for hybrid (keyword + embedding) search. Parents hold a whole section for context; children are matched and carry the embedding.';

create index document_chunk_parent on public.document_chunk (parent_id);
create index document_chunk_ref on public.document_chunk (kind, ref);
create index document_chunk_tickers on public.document_chunk using gin (tickers)
  where not is_parent;
create index document_chunk_search on public.document_chunk using gin (search)
  where not is_parent;
create index document_chunk_as_of on public.document_chunk (as_of desc) where not is_parent;

do $$
declare
  vector_schema text;
begin
  if exists (select 1 from pg_available_extensions where name = 'vector') then
    create extension if not exists vector;
    select n.nspname into vector_schema
      from pg_extension e join pg_namespace n on n.oid = e.extnamespace
     where e.extname = 'vector';
    execute format(
      'alter table public.document_chunk add column embedding_vec %1$I.vector(768)'
      ' generated always as (case when embedding_dim = 768'
      ' then embedding::%1$I.vector(768) end) stored',
      vector_schema);
    execute format(
      'create index document_chunk_embedding_hnsw on public.document_chunk'
      ' using hnsw (embedding_vec %1$I.vector_cosine_ops) where not is_parent',
      vector_schema);
  end if;
end $$;

alter table public.document_chunk enable row level security;
revoke all on public.document_chunk from anon, authenticated;
grant select on public.document_chunk to authenticated;
create policy owner_read on public.document_chunk
  for select to authenticated using ((select private.is_owner()));
grant select, insert, update, delete on public.document_chunk to app_backend;
create policy backend_all on public.document_chunk
  for all to app_backend using (true) with check (true);
grant usage, select on all sequences in schema public to app_backend;
