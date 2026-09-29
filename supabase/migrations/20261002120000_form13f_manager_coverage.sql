-- Track which configured managers have been fully extracted from each quarterly dataset.
-- This lets a manager added later backfill existing SEC archives exactly once.
create table if not exists public.form13f_manager_dataset (
  cik text not null check (cik ~ '^[0-9]{10}$'),
  dataset_url text not null references public.form13f_dataset(url) on delete cascade,
  loaded_at timestamptz not null default now(),
  primary key (cik, dataset_url)
);

alter table public.form13f_manager_dataset enable row level security;
revoke all on public.form13f_manager_dataset from anon, authenticated;
grant select on public.form13f_manager_dataset to authenticated;
create policy owner_read on public.form13f_manager_dataset
  for select to authenticated using ((select private.is_owner()));
grant select, insert, update, delete on public.form13f_manager_dataset to app_backend;
create policy form13f_manager_dataset_owner_read on public.form13f_manager_dataset
  for all to app_backend using (true) with check (true);
