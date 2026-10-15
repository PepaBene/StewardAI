-- The on-demand job queue as a plain table, so the database no longer needs the pgmq extension
-- (a stock managed Postgres does not have it). Same semantics as pgmq: a message is read by
-- hiding it for a visibility timeout, and removed when archived.
--
-- Cutover: deploy the backend that uses this table, then move anything still waiting in pgmq:
--   insert into public.job_queue (message, enqueued_at, vt, read_ct)
--   select message, enqueued_at, vt, read_ct from pgmq.q_jobs;
-- The pgmq queue and extension can be dropped afterwards.
create table public.job_queue (
  msg_id       bigint      generated always as identity primary key,
  message      jsonb       not null,
  read_ct      integer     not null default 0,
  enqueued_at  timestamptz not null default now(),
  vt           timestamptz not null default now()
);

create index job_queue_visible on public.job_queue (vt, msg_id);

alter table public.job_queue enable row level security;
revoke all on public.job_queue from anon, authenticated;
grant select, insert, update, delete on public.job_queue to app_backend;
create policy backend_all on public.job_queue
  for all to app_backend using (true) with check (true);
