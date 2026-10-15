-- Event-driven agents and memory (agentic workflows and RAG plan, AG4 and AG5).
--
-- inbox_item: what a bounded agent run left for the owner after a trigger (an escalation
-- report, a thesis-breach or news alert). It holds what happened, the evidence (registry
-- sources), the thesis KPI or rule it touches and, at most, one proposed action. A proposed
-- action starts nothing: the owner confirms it from the inbox, which then creates the job.
--
-- agent_note: short working notes the agent reads (per holding or general). The owner writes
-- and edits them; the agent never writes one.
create table public.inbox_item (
  id               bigint generated always as identity primary key,
  -- One item per trigger, e.g. 'report:123' or 'alert:45'; a rescan cannot add a second.
  trigger_key      text        not null unique,
  trigger_kind     text        not null check (trigger_kind in ('escalation', 'alert')),
  trigger_ref      bigint      not null,
  ticker           text,
  severity         smallint    check (severity between 0 and 3),
  headline         text        not null,
  body             jsonb       not null,
  status           text        not null default 'new'
                               check (status in ('new', 'read', 'actioned', 'dismissed')),
  -- The proposed action, if any: {kind, params, reason}. action_status tracks the owner's call.
  proposed_action  jsonb,
  action_status    text        check (action_status in ('proposed', 'confirmed', 'declined')),
  action_job_id    bigint      references public.job (id),
  -- The owner's verdict on the item, which feeds the evaluation set.
  feedback         text        check (feedback in ('useful', 'not_useful')),
  feedback_note    text,
  job_id           bigint      references public.job (id),
  created_at       timestamptz not null default now(),
  decided_at       timestamptz,
  check ((proposed_action is null) = (action_status is null))
);

create index inbox_item_status on public.inbox_item (status, created_at desc);
create index inbox_item_ticker on public.inbox_item (ticker, created_at desc);
create index inbox_item_job on public.inbox_item (job_id);
create index inbox_item_action_job on public.inbox_item (action_job_id);

create table public.agent_note (
  id          bigint generated always as identity primary key,
  -- null: a note for every run.
  ticker      text,
  text        text        not null check (length(trim(text)) > 0 and length(text) <= 2000),
  active      boolean     not null default true,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create index agent_note_ticker on public.agent_note (ticker) where active;

comment on table public.inbox_item is
  'What an event-triggered agent run left for the owner: evidence and at most one proposed action.';
comment on table public.agent_note is
  'Owner-written working notes the agent reads; the agent never writes them.';

alter table public.inbox_item enable row level security;
revoke all on public.inbox_item from anon, authenticated;
grant select on public.inbox_item to authenticated;
create policy owner_read on public.inbox_item
  for select to authenticated using ((select private.is_owner()));
grant select, insert, update on public.inbox_item to app_backend;
create policy backend_all on public.inbox_item
  for all to app_backend using (true) with check (true);

alter table public.agent_note enable row level security;
revoke all on public.agent_note from anon, authenticated;
grant select on public.agent_note to authenticated;
create policy owner_read on public.agent_note
  for select to authenticated using ((select private.is_owner()));
grant select, insert, update, delete on public.agent_note to app_backend;
create policy backend_all on public.agent_note
  for all to app_backend using (true) with check (true);
