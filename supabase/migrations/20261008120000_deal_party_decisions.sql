-- A deal party is matched to an issuer only when the owner confirms; every decision is kept.
create table public.deal_participant_decision (
  id bigint generated always as identity primary key,
  participant_id bigint not null references public.deal_participant(id) on delete cascade,
  issuer_id bigint not null references public.issuer(id),
  decision text not null check (decision in ('confirmed', 'rejected')),
  decided_by text not null check (length(btrim(decided_by)) > 0),
  decided_at timestamptz not null default now(),
  unique (participant_id, issuer_id)
);
create index fk_deal_participant_decision_issuer_id on public.deal_participant_decision (issuer_id);

alter table public.deal_participant_decision enable row level security;
revoke all on public.deal_participant_decision from anon, authenticated;
grant select on public.deal_participant_decision to authenticated;
grant select, insert, update, delete on public.deal_participant_decision to app_backend;
create policy owner_read on public.deal_participant_decision for select to authenticated
  using ((select private.is_owner()));
create policy backend_all on public.deal_participant_decision for all to app_backend
  using (true) with check (true);
