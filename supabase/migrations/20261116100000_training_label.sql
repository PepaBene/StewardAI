-- Labels for fine-tuning data (fine-tuning data plan, phase 2): what the owner, an audit or a
-- later outcome said about something the engine produced. One row per judgement, append-only.
-- A label points at the thing judged (autopilot kind and subject), not at one model call: the
-- call is found later by the prompt the decision stored, so a label survives a re-run.
create table public.training_label (
  id           bigint generated always as identity primary key,
  created_at   timestamptz not null default now(),
  source       text        not null check (source in
                 ('owner_review', 'owner_revert', 'owner_accept', 'audit', 'outcome')),
  subject_kind text        not null check (length(subject_kind) between 1 and 80),
  subject_ref  text        not null check (length(subject_ref) between 1 and 200),
  decision_id  bigint,
  label        text        not null check (label in ('right', 'wrong', 'corrected')),
  by_owner     boolean     not null,
  detail       jsonb       not null default '{}'::jsonb
);

create index training_label_subject on public.training_label (subject_kind, subject_ref);
create index training_label_created on public.training_label (created_at desc);

comment on table public.training_label is
  'Append-only judgements on engine output (owner, audit, outcome) used as fine-tuning labels.';

alter table public.training_label enable row level security;
revoke all on public.training_label from anon, authenticated;
grant select on public.training_label to authenticated;
create policy owner_read on public.training_label
  for select to authenticated using ((select private.is_owner()));
grant select, insert on public.training_label to app_backend;
create policy backend_read on public.training_label for select to app_backend using (true);
create policy backend_insert on public.training_label
  for insert to app_backend with check (true);
grant usage, select on sequence public.training_label_id_seq to app_backend;
