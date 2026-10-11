-- Covering indexes for two foreign keys the news migrations added: the integrity tests require
-- a plain (non-partial) index on every foreign key's columns.
--
-- story_cluster.thread_id had only a partial index (where thread_id is not null), which the
-- planner cannot use for the key's own "on delete set null" lookup; a plain index replaces it.
create index if not exists story_cluster_thread_id on public.story_cluster (thread_id);
drop index if exists public.story_cluster_thread;

-- news_incident_review is keyed (kind, story_id); deleting a story looks rows up by story_id alone.
create index if not exists news_incident_review_story on public.news_incident_review (story_id);
