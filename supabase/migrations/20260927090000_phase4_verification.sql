-- Phase 4: verification hardening.
--
-- - feedback: the owner's wrong / irrelevant / useful marks on facts, verdicts, alerts,
--   news items, stories and report sentences.
-- - source_trust: a trust score per source (outlet host) that owner feedback moves; a
--   source below the Verification config's threshold counts one tier lower.
-- - eval_candidate: items marked wrong that the owner added to an evaluation set with the
--   corrected value, until they are exported into the repository's eval files.
-- - entity_relationship: a relationship two sources disagree on is `disputed`, with the
--   conflicting source kept beside it.

create table public.feedback (
  id             bigint generated always as identity primary key,
  target_kind    text        not null check (target_kind in ('fact', 'verdict', 'alert',
                               'news_item', 'story', 'report_sentence')),
  target_id      bigint      not null,
  -- For report sentences: where the sentence sits in the report body, e.g. "overview/0"
  -- or "sections/2/summary/1" (digests), "sentences/3" (impact notes).
  sentence_path  text        check (sentence_path ~ '^[a-z_]+(/[0-9a-z_]+)*$'),
  rating         text        not null check (rating in ('wrong', 'irrelevant', 'useful')),
  note           text        check (char_length(note) <= 1000),
  -- The trust keys (outlet hosts) this feedback moved, and what else it did.
  source_hosts   text[]      not null default '{}',
  effects        jsonb       not null default '{}'::jsonb,
  created_by     uuid,
  created_at     timestamptz not null default now(),
  check ((target_kind = 'report_sentence') = (sentence_path is not null))
);

create index feedback_target on public.feedback (target_kind, target_id, created_at desc);
create index feedback_created_at on public.feedback (created_at desc);

create table public.source_trust (
  host          text        primary key check (host ~ '^[a-z0-9.-]{1,253}$'),
  score         numeric     not null default 1 check (score >= 0 and score <= 1),
  wrong_count   integer     not null default 0,
  useful_count  integer     not null default 0,
  updated_at    timestamptz not null default now()
);

comment on table public.source_trust is
  'Trust per source host, 1 when never judged. Owner feedback lowers it (wrong) or raises it '
  '(useful) by the Verification config''s steps; below its threshold the source counts one '
  'tier lower in corroboration.';

-- Trust is kept per outlet host (the unit tiers are assigned to, and what aggregated feeds
-- carry many of), not per configured feed, so the Phase 3 placeholder column goes.
alter table public.source drop column trust_score;

create table public.eval_candidate (
  id            bigint generated always as identity primary key,
  kind          text        not null check (kind in ('earnings_figure', 'news_label')),
  feedback_id   bigint      references public.feedback (id),
  fact_id       bigint      references public.fact (id),
  news_item_id  bigint      references public.news_item (id),
  -- earnings_figure: {"value", "value_as_written"?, "scale"?}; news_label: the label fields.
  corrected     jsonb       not null check (jsonb_typeof(corrected) = 'object'),
  note          text,
  created_at    timestamptz not null default now(),
  exported_at   timestamptz,
  exported_to   text,
  check ((kind = 'earnings_figure') = (fact_id is not null)),
  check ((kind = 'news_label') = (news_item_id is not null))
);

create unique index eval_candidate_once_per_fact on public.eval_candidate (fact_id)
  where fact_id is not null;
create unique index eval_candidate_once_per_item on public.eval_candidate (news_item_id)
  where news_item_id is not null;

alter table public.entity_relationship drop constraint entity_relationship_status_check;
alter table public.entity_relationship add constraint entity_relationship_status_check
  check (status in ('confirmed', 'suggested', 'rejected', 'disputed'));
alter table public.entity_relationship
  add column conflict jsonb check (conflict is null or jsonb_typeof(conflict) = 'object');

comment on column public.entity_relationship.conflict is
  'For a disputed relationship: the other source''s claim, {"source", "object_key", '
  '"object_name", "source_ref", "tier"}.';

-- ---------------------------------------------------------------------------
-- Privileges and row-level security
-- ---------------------------------------------------------------------------
alter table public.feedback enable row level security;
alter table public.source_trust enable row level security;
alter table public.eval_candidate enable row level security;

revoke all on public.feedback, public.source_trust, public.eval_candidate
  from anon, authenticated;
grant select on public.feedback, public.source_trust, public.eval_candidate
  to authenticated;

create policy owner_read on public.feedback
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.source_trust
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.eval_candidate
  for select to authenticated using ((select private.is_owner()));

grant select, insert, update on public.feedback, public.source_trust, public.eval_candidate
  to app_backend;

create policy backend_all on public.feedback
  for all to app_backend using (true) with check (true);
create policy backend_all on public.source_trust
  for all to app_backend using (true) with check (true);
create policy backend_all on public.eval_candidate
  for all to app_backend using (true) with check (true);
