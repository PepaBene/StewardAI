-- Overrides (autonomous engine plan, L3): every time the owner overrules something the engine
-- did, with the reason when they give one. The engine learns from the pattern (which rule, which
-- source, which kind keeps being overruled) and never decides the same item again. Append-only.
create table public.owner_override (
  id           bigint generated always as identity primary key,
  created_at   timestamptz not null default now(),
  area         text        not null check (length(area) between 1 and 80),
  subject_kind text        not null check (length(subject_kind) between 1 and 80),
  subject_ref  text        not null check (length(subject_ref) between 1 and 200),
  action       text        not null check (action in ('reverted', 'edited', 'rejected', 'marked_wrong')),
  reason_code  text        check (reason_code in
                 ('wrong_data', 'wrong_source', 'right_but_unimportant', 'too_early', 'too_late',
                  'prefer_my_judgement', 'other')),
  note         text        check (note is null or length(note) <= 1000),
  decided_by   text,
  decision_id  bigint,
  via          text        not null check (via in ('screen', 'chat')),
  before       jsonb,
  after        jsonb
);

create index owner_override_kind on public.owner_override (subject_kind, created_at desc);
create index owner_override_decided_by
  on public.owner_override (subject_kind, decided_by, created_at desc);

comment on table public.owner_override is
  'Append-only record of the owner overruling the engine, with the reason chip when given.';

alter table public.owner_override enable row level security;
revoke all on public.owner_override from anon, authenticated;
grant select on public.owner_override to authenticated;
create policy owner_read on public.owner_override
  for select to authenticated using ((select private.is_owner()));
grant select, insert on public.owner_override to app_backend;
create policy backend_read on public.owner_override for select to app_backend using (true);
create policy backend_insert on public.owner_override
  for insert to app_backend with check (true);
grant usage, select on sequence public.owner_override_id_seq to app_backend;
