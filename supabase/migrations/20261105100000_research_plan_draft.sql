-- A research plan the owner reviews before the run: written by a model (or by code), edited,
-- approved, then started. The run stores the plan it used in the report; the draft only
-- carries it to the run.
create table public.research_plan_draft (
  id            bigint      generated always as identity primary key,
  ticker        text        not null,
  profile       text        not null,
  name          text,
  plan          jsonb       not null,
  status        text        not null default 'draft'
    check (status in ('draft', 'approved', 'started', 'discarded')),
  edited        boolean     not null default false,
  planner_note  text,
  planner_usage jsonb       not null default '{}'::jsonb,
  job_id        bigint      references public.job (id) on delete set null,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create index research_plan_draft_job on public.research_plan_draft (job_id);

comment on table public.research_plan_draft is
  'A research plan before its run: model-written or code-built, editable until started.';

alter table public.research_plan_draft enable row level security;
revoke all on public.research_plan_draft from anon, authenticated;
grant select on public.research_plan_draft to authenticated;
create policy owner_read on public.research_plan_draft
  for select to authenticated using ((select private.is_owner()));
grant select, insert, update on public.research_plan_draft to app_backend;
create policy backend_all on public.research_plan_draft
  for all to app_backend using (true) with check (true);
