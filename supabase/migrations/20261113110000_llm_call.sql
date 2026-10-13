-- Model-call capture (fine-tuning data plan, phase 1): one row per AI call, written by the
-- central client after redaction. It is the raw material for later fine-tuning and for judging
-- the engine's models against the owner's corrections. Append-only: the backend can insert and
-- read but never update or delete, so a captured call cannot be rewritten after the fact.
create table public.llm_call (
  id             bigint generated always as identity primary key,
  created_at     timestamptz not null default now(),
  job_id         bigint,
  job_type       text        not null,
  provider       text        not null,
  model          text        not null,
  effort         text,
  request        jsonb       not null,
  response       jsonb       not null,
  input_tokens   integer     not null default 0,
  output_tokens  integer     not null default 0,
  cached_tokens  integer     not null default 0,
  stop_reason    text,
  redaction      integer     not null
);

create index llm_call_created on public.llm_call (created_at desc);
create index llm_call_job_type on public.llm_call (job_type, created_at desc);
create index llm_call_job on public.llm_call (job_id);

comment on table public.llm_call is
  'Append-only, redacted record of each AI call (prompt, answer, usage) kept for later fine-tuning.';

alter table public.llm_call enable row level security;
revoke all on public.llm_call from anon, authenticated;
grant select on public.llm_call to authenticated;
create policy owner_read on public.llm_call
  for select to authenticated using ((select private.is_owner()));
grant select, insert on public.llm_call to app_backend;
create policy backend_read on public.llm_call for select to app_backend using (true);
create policy backend_insert on public.llm_call for insert to app_backend with check (true);
grant usage, select on sequence public.llm_call_id_seq to app_backend;
