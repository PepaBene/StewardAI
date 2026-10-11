-- News coverage plan, phase N3: language, translation and deduplication of the wire.
--
-- - news_article.summary_en / translation_state: the English text of a non-English item (the
--   original stays in headline / summary; title_en was added with the table).
-- - news_article.group_id / group_method: items that are copies of one text (SimHash) or the same
--   event in other words or another language (embeddings) share a group; the group id is the id
--   of its earliest item. Used for the duplicate-burden measure and "N outlets carry this".
-- - news_embedding: the multilingual embedding of headline + summary lead. Stored as real[]
--   rather than a pgvector column so it works on any Postgres; the wire window is a few thousand
--   rows, which the pipeline compares in memory.

alter table public.news_article
  add column summary_en text,
  add column translation_state text
    check (translation_state is null or translation_state in ('done', 'failed', 'not_needed')),
  add column group_id bigint references public.news_article (id),
  add column group_method text check (group_method is null or group_method in ('simhash', 'embedding'));

comment on column public.news_article.group_id is
  'Id of the earliest item of this item''s duplicate group; equal to its own id (or null) when it stands alone.';

create index news_article_group on public.news_article (group_id) where group_id is not null;
create index news_article_untranslated on public.news_article (first_seen_at)
  where translation_state is null;

create table public.news_embedding (
  article_id bigint      primary key references public.news_article (id) on delete cascade,
  model      text        not null,
  dim        integer     not null check (dim > 0),
  vector     real[]      not null,
  created_at timestamptz not null default now(),
  check (cardinality(vector) = dim)
);

comment on table public.news_embedding is
  'Embedding of a wire item''s headline and summary lead, L2-normalised, for cross-language duplicate grouping and search.';

-- Merging a new one-item story into the older story it duplicates removes the empty cluster
-- (only ever one with no alert and no other item; see steward.news.dedupe).
grant delete on public.story_cluster to app_backend;

alter table public.news_embedding enable row level security;
revoke all on public.news_embedding from anon, authenticated;
grant select on public.news_embedding to authenticated;
create policy owner_read on public.news_embedding
  for select to authenticated using ((select private.is_owner()));
grant select, insert, update on public.news_embedding to app_backend;
create policy backend_all on public.news_embedding
  for all to app_backend using (true) with check (true);
