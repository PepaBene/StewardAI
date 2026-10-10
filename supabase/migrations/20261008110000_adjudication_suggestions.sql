-- Hybrid accuracy check: a model pre-labels a sample, a person confirms.  The suggestion is kept
-- apart from disclosure_adjudication so the release gate can only ever read a person's labels.
create table public.disclosure_adjudication_suggestion (
  id bigint generated always as identity primary key,
  dataset text not null check (dataset in (
    'insider_class', 'relationship_claim', 'ownership_match', 'statement_note', 'document_sample')),
  sample_key text not null,
  item_ref text not null,
  machine_value text,
  suggested_value text not null,
  rationale text,
  model text not null check (length(btrim(model)) > 0),
  must_review boolean not null,
  review_reasons text[] not null default '{}',
  created_at timestamptz not null default now(),
  unique (dataset, sample_key, item_ref)
);

alter table public.disclosure_adjudication_suggestion enable row level security;
revoke all on public.disclosure_adjudication_suggestion from anon, authenticated;
grant select on public.disclosure_adjudication_suggestion to authenticated;
grant select, insert, update, delete on public.disclosure_adjudication_suggestion to app_backend;
create policy owner_read on public.disclosure_adjudication_suggestion for select to authenticated
  using ((select private.is_owner()));
create policy backend_all on public.disclosure_adjudication_suggestion for all to app_backend
  using (true) with check (true);
