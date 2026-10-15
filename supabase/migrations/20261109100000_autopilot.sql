-- Autopilot (autonomy plan, phase A): the ledger of every decision the app made on its own.
--
-- auto_decision: one row per automatic decision (or escalation). It records what was decided,
-- by which rule or model, with what confidence and evidence, whether it was applied or only
-- decided in shadow, and what is needed to revert it. A row with status 'escalated' is an item
-- the app could not settle with confidence: it is what the owner's "Needs you" list shows.
--
-- autopilot_kind_state: the mode a kind of decision runs in right now when calibration (not the
-- Jobs config) has changed it: shadow decides and logs but applies nothing; auto applies.
create table public.auto_decision (
  id              bigint generated always as identity primary key,
  kind            text        not null,
  -- What was decided about, e.g. 'fact:123'. Unique per kind among open escalations.
  subject_ref     text        not null,
  ticker          text,
  decision        text        not null,
  confidence      real        check (confidence between 0 and 1),
  -- 'rule:<id>', 'model:<id>', 'ttl' (an unanswered escalation expired) or 'owner-accepted'.
  decided_by      text        not null,
  summary         text        not null,
  evidence        jsonb       not null default '{}',
  status          text        not null
                  check (status in ('shadow', 'applied', 'escalated', 'reverted', 'closed')),
  -- What kind.revert needs to undo the decision (the prior state), when it was applied.
  revert_payload  jsonb,
  -- A spot-check sample the owner may look at (a small fraction of applied decisions).
  sampled         boolean     not null default false,
  -- Whether the decision was right: from the owner (a sample check or an override), or from
  -- a stronger model's independent audit. Calibration reads these.
  review          text        check (review in ('ok', 'wrong')),
  reviewed_by     text,
  reviewed_at     timestamptz,
  decided_at      timestamptz not null default now()
);

create index auto_decision_kind on public.auto_decision (kind, decided_at desc);
create index auto_decision_subject on public.auto_decision (kind, subject_ref);
create index auto_decision_status on public.auto_decision (status, decided_at desc);
create unique index auto_decision_one_escalation
  on public.auto_decision (kind, subject_ref) where status = 'escalated';

create table public.autopilot_kind_state (
  kind        text        primary key,
  mode        text        not null check (mode in ('off', 'shadow', 'auto')),
  reason      text        not null,
  updated_at  timestamptz not null default now()
);

comment on table public.auto_decision is
  'Every decision the app made on its own, with evidence, mode, review and how to revert it.';
comment on table public.autopilot_kind_state is
  'The mode calibration set for a kind of automatic decision; overrides the Jobs config.';

alter table public.auto_decision enable row level security;
revoke all on public.auto_decision from anon, authenticated;
grant select on public.auto_decision to authenticated;
create policy owner_read on public.auto_decision
  for select to authenticated using ((select private.is_owner()));
grant select, insert, update on public.auto_decision to app_backend;
create policy backend_all on public.auto_decision
  for all to app_backend using (true) with check (true);

alter table public.autopilot_kind_state enable row level security;
revoke all on public.autopilot_kind_state from anon, authenticated;
grant select on public.autopilot_kind_state to authenticated;
create policy owner_read on public.autopilot_kind_state
  for select to authenticated using ((select private.is_owner()));
grant select, insert, update on public.autopilot_kind_state to app_backend;
create policy backend_all on public.autopilot_kind_state
  for all to app_backend using (true) with check (true);
