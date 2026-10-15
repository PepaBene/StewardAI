-- The owner can say why after a save: the reason of an override that has none yet may be filled in.
-- Nothing else of a row can change, and a reason already given cannot be replaced.
grant update (reason_code) on public.owner_override to app_backend;
create policy backend_explain on public.owner_override
  for update to app_backend using (reason_code is null) with check (reason_code is not null);
