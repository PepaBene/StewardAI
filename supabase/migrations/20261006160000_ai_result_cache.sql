-- Answers of AI calls whose request repeats exactly, so the same question is not paid for twice.
-- The key is a hash of the model, the system prompt, the messages, the output schema and the
-- effort: a changed prompt or model is a different key, so nothing stale is ever served.

create table public.ai_result_cache (
  key text primary key,
  job_type text not null,
  model text not null,
  result jsonb not null,
  created_at timestamptz not null default now()
);

create index ai_result_cache_created_idx on public.ai_result_cache (created_at);

alter table public.ai_result_cache enable row level security;
revoke all on public.ai_result_cache from anon, authenticated;
grant select on public.ai_result_cache to authenticated;
grant select, insert, update, delete on public.ai_result_cache to app_backend;
create policy owner_read on public.ai_result_cache for select to authenticated
  using ((select private.is_owner()));
create policy backend_all on public.ai_result_cache for all to app_backend
  using (true) with check (true);
