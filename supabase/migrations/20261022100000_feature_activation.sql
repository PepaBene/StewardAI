-- M8.5: the owner's decision to switch a feature on or off. Append-only: the newest row per
-- feature stands, earlier rows stay as the history of the decision.

create table public.feature_activation (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  feature text not null check (length(trim(feature)) between 1 and 60),
  active boolean not null,
  note text check (note is null or length(note) <= 500),
  created_at timestamptz not null default now()
);
create index feature_activation_owner on public.feature_activation (owner_id, feature, id desc);

alter table public.feature_activation enable row level security;
revoke all on public.feature_activation from anon;
grant select, insert on public.feature_activation to authenticated, app_backend;

create policy feature_activation_owner_read on public.feature_activation for select to authenticated
  using (owner_id = (select auth.uid()));
create policy feature_activation_owner_insert on public.feature_activation for insert to authenticated
  with check (owner_id = (select auth.uid()));
create policy feature_activation_backend on public.feature_activation for all to app_backend
  using (true) with check (true);
