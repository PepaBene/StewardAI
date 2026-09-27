-- job.attempts: how many times the runner started executing the job. The runner adds one
-- each time it takes the job's lock and starts it, including when the queue redelivers a
-- run the worker was killed in (a resumed run). A value above 1 means the run was retried.
--
-- Backfill for runs recorded before this column existed, which counted nothing. It is a
-- lower bound, never a guess above what the rows prove:
--   0  never started (started_at is null: still queued/waiting, or gave up on the lock);
--   2  started and later resumed: a step carries the marker written when an interrupted
--      run is closed ("interrupted: the worker stopped during this step") and a later step
--      follows it. Worker catch-up writes the same marker but fails the run and adds no
--      step, so only a resume leaves steps after the marker; the run ran at least twice;
--   1  every other started run.
--
-- The table-level grants and RLS policies of `job` cover the new column.

alter table public.job
  add column attempts integer not null default 0 check (attempts >= 0);

update public.job j set attempts = case
  when j.started_at is null then 0
  when exists (
    select 1 from public.job_step s
    where s.job_id = j.id
      and s.error = 'interrupted: the worker stopped during this step'
      and exists (
        select 1 from public.job_step later
        where later.job_id = j.id and later.step_index > s.step_index
      )
  ) then 2
  else 1
end;

comment on column public.job.attempts is
  'Times the runner started executing this job (resumed runs count again); > 1 = retried. '
  'Rows from before 2026-09-30 hold a lower bound (0 never started, 2 resumed, else 1).';
