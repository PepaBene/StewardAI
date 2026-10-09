-- Plan P4 / P5: insider filings become point-in-time, classification overrides are auditable,
-- and relationship disclosures say what shape of claim they are.
--
-- P4. `insider_filing` stored only a filing DATE. known_at is derived as the end of the filing
-- day (UTC), never earlier; the trigger fills only a NULL so a collector that stores the real
-- acceptance time can set it. A reviewer can override the deterministic classifier for one
-- transaction row; the override is a ledger (latest wins on read, none is ever edited), and
-- it carries the original class and a reason of at least 10 characters.
--
-- P5. A named customer, an anonymised "Customer A", a group ("three customers") and an
-- industry- or geography-only exposure are different claims. `claim_shape` records which,
-- `counterparty_count` how many parties a group stands for, and `denominator_kind` what the
-- reported share is a share OF (shares of different denominators are never combined).
-- Additive only: no existing column or row is rewritten; old rows are backfilled by the rules
-- they already satisfy (anonymous flag -> 'anonymous', otherwise 'named').

-- ----------------------------------------------------------------------------------- P4
alter table public.insider_filing add column known_at timestamptz;
update public.insider_filing
   set known_at = ((filed::timestamp + interval '1 day' - interval '1 second') at time zone 'UTC')
 where known_at is null;
alter table public.insider_filing alter column known_at set not null;
create trigger insider_filing_known_at before insert on public.insider_filing
  for each row execute function public.ownership_known_at_default();
create index insider_filing_known on public.insider_filing (ticker, known_at);

create table public.insider_classification_override (
  id bigint generated always as identity primary key,
  accession text not null,
  row_index integer not null,
  original_class text,
  new_class text not null check (new_class in (
    'open_market_buy', 'open_market_sell', 'option_exercise', 'grant_award', 'gift',
    'tax_withholding', 'conversion', 'other', 'holding_snapshot')),
  reason text not null check (length(btrim(reason)) >= 10),
  decided_by text not null check (length(btrim(decided_by)) > 0),
  decided_at timestamptz not null default now(),
  foreign key (accession, row_index)
    references public.insider_transaction (accession, row_index) on delete cascade
);
create index insider_classification_override_row
  on public.insider_classification_override (accession, row_index, id desc);
create trigger append_only before update or delete on public.insider_classification_override
  for each row execute function public.forbid_append_only_mutation();

alter table public.insider_classification_override enable row level security;
revoke all on public.insider_classification_override from anon, authenticated;
grant select on public.insider_classification_override to authenticated;
create policy owner_read on public.insider_classification_override
  for select to authenticated using ((select private.is_owner()));
grant select, insert, update, delete on public.insider_classification_override to app_backend;
create policy backend_all on public.insider_classification_override
  for all to app_backend using (true) with check (true);

-- ----------------------------------------------------------------------------------- P5
alter table public.relationship_disclosure
  add column claim_shape text check (claim_shape is null or claim_shape in (
    'named', 'anonymous', 'group', 'segment_only')),
  add column counterparty_count integer check (counterparty_count is null or counterparty_count >= 1),
  add column denominator_kind text check (denominator_kind is null or denominator_kind in (
    'total_revenue', 'segment_revenue', 'cost_of_sales', 'purchases', 'receivables', 'other')),
  add column period_end date;

update public.relationship_disclosure
   set claim_shape = case when anonymous_counterparty then 'anonymous' else 'named' end
 where claim_shape is null;

-- Enforced for new rows only: a named claim names its counterparty, an anonymous or group claim
-- never carries a name, and a group says how many parties it covers.
alter table public.relationship_disclosure
  add constraint relationship_disclosure_shape_ok check (
    claim_shape is null
    or (claim_shape = 'named' and counterparty_name is not null)
    or (claim_shape in ('anonymous', 'group', 'segment_only') and counterparty_name is null)
  ) not valid,
  add constraint relationship_disclosure_group_count check (
    claim_shape is distinct from 'group' or counterparty_count >= 2
  ) not valid;
