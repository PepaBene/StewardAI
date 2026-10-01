-- D6.6 Schedule 13D/G beneficial-ownership / control channel (separate from Form 13F) and
-- D6.7 enrichment of the Forms 3/4/5 pipeline (footnotes, derivative terms, amendments,
-- reported Rule 10b5-1 plans, deterministic economic classification).

-- ---------------------------------------------------------------------------------------
-- D6.6 Schedule 13D/G
-- ---------------------------------------------------------------------------------------
-- One row per filing (initial or amendment) as filed. Percentages are the filer's own,
-- in percent points, and are never added to Form 13F or fund figures.
create table public.control_stake_filing (
  accession text primary key check (accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'),
  form text not null check (form in (
    'SC 13D', 'SC 13D/A', 'SC 13G', 'SC 13G/A',
    'SCHEDULE 13D', 'SCHEDULE 13D/A', 'SCHEDULE 13G', 'SCHEDULE 13G/A')),
  schedule text not null check (schedule in ('13D', '13G')),
  is_amendment boolean not null default false,
  amendment_no integer check (amendment_no is null or amendment_no >= 0),
  filed date not null,
  event_date date,
  issuer_cik text check (issuer_cik is null or issuer_cik ~ '^[0-9]{10}$'),
  issuer_cusip text check (issuer_cusip is null or issuer_cusip ~ '^[0-9A-Z]{9}$'),
  issuer_name text,
  class_title text,
  filer_cik text check (filer_cik is null or filer_cik ~ '^[0-9]{10}$'),
  -- Item 4 of a 13D (purpose / intent); null for a 13G, which is passive by definition.
  intent_text text,
  intent_hash text,
  -- Filing-level stake: the largest single reporting-person figure (a lower bound for a
  -- group, whose members report overlapping shares); never a sum.
  stake_shares numeric check (stake_shares is null or stake_shares >= 0),
  stake_percent numeric check (stake_percent is null or (stake_percent >= 0 and stake_percent <= 100)),
  stake_basis text not null default 'largest_reporting_person',
  -- The earlier filing of the same issuer class and overlapping reporting persons.
  prior_accession text references public.control_stake_filing (accession) on delete set null,
  source_url text not null,
  loaded_at timestamptz not null default now()
);
create index control_stake_filing_issuer on public.control_stake_filing (issuer_cusip, filed desc);
create index control_stake_filing_cik on public.control_stake_filing (issuer_cik, filed desc);
create index control_stake_filing_prior on public.control_stake_filing (prior_accession);

create table public.control_stake_holder (
  accession text not null references public.control_stake_filing (accession) on delete cascade,
  row_index integer not null check (row_index >= 0),
  person_key text not null,
  person_cik text check (person_cik is null or person_cik ~ '^[0-9]{10}$'),
  person_name text not null,
  member_of_group boolean,
  citizenship text,
  sole_voting numeric,
  shared_voting numeric,
  sole_dispositive numeric,
  shared_dispositive numeric,
  aggregate_shares numeric,
  percent_of_class numeric check (percent_of_class is null or percent_of_class between 0 and 100),
  person_type text,
  primary key (accession, row_index)
);
create index control_stake_holder_person on public.control_stake_holder (person_key);

-- Reconciliation of a filing to a held security's share class.
create table public.control_stake_issuer_link (
  accession text not null references public.control_stake_filing (accession) on delete cascade,
  ticker text not null,
  match_basis text not null check (match_basis in ('cusip', 'issuer_cik')),
  class_status text not null check (class_status in (
    'cusip_match', 'class_unreconciled')),
  class_label text,
  class_shares_outstanding numeric,
  class_shares_as_of date,
  computed_percent numeric,
  percent_check text not null check (percent_check in (
    'consistent', 'inconsistent', 'not_checkable')),
  note text,
  primary key (accession, ticker)
);
create index control_stake_issuer_link_ticker on public.control_stake_issuer_link (ticker);

-- Differences between a filing and its prior filing of the same chain.
create table public.control_stake_change (
  id bigint generated always as identity primary key,
  accession text not null references public.control_stake_filing (accession) on delete cascade,
  prior_accession text not null references public.control_stake_filing (accession) on delete cascade,
  change_type text not null check (change_type in (
    'holder_added', 'holder_removed', 'holder_shares_changed', 'holder_percent_changed',
    'holder_power_changed', 'group_status_changed', 'stake_shares_changed',
    'stake_percent_changed', 'intent_changed', 'schedule_type_changed',
    'fell_below_5_percent')),
  person_key text not null default '',
  person_name text,
  old_value text,
  new_value text,
  detail text,
  unique (accession, change_type, person_key)
);
create index control_stake_change_prior on public.control_stake_change (prior_accession);

alter table public.control_stake_filing enable row level security;
alter table public.control_stake_holder enable row level security;
alter table public.control_stake_issuer_link enable row level security;
alter table public.control_stake_change enable row level security;
revoke all on public.control_stake_filing, public.control_stake_holder,
  public.control_stake_issuer_link, public.control_stake_change from anon, authenticated;
grant select on public.control_stake_filing, public.control_stake_holder,
  public.control_stake_issuer_link, public.control_stake_change to authenticated;
create policy owner_read on public.control_stake_filing
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.control_stake_holder
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.control_stake_issuer_link
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.control_stake_change
  for select to authenticated using ((select private.is_owner()));
grant select, insert, update, delete on public.control_stake_filing, public.control_stake_holder,
  public.control_stake_issuer_link, public.control_stake_change to app_backend;
create policy backend_all on public.control_stake_filing
  for all to app_backend using (true) with check (true);
create policy backend_all on public.control_stake_holder
  for all to app_backend using (true) with check (true);
create policy backend_all on public.control_stake_issuer_link
  for all to app_backend using (true) with check (true);
create policy backend_all on public.control_stake_change
  for all to app_backend using (true) with check (true);

-- ---------------------------------------------------------------------------------------
-- D6.7 Forms 3/4/5 enrichment
-- ---------------------------------------------------------------------------------------
alter table public.insider_filing
  add column reporting_owner_cik text check (reporting_owner_cik is null or reporting_owner_cik ~ '^[0-9]{10}$'),
  add column joint_owner_count integer not null default 1,
  add column is_amendment boolean not null default false,
  add column original_submission_date date,
  -- The original this /A restates, and the amendment that restates this filing.
  add column amends_accession text references public.insider_filing (accession) on delete set null,
  add column superseded_by_accession text references public.insider_filing (accession) on delete set null,
  add column amendment_link_status text not null default 'not_amendment' check (
    amendment_link_status in ('not_amendment', 'linked', 'ambiguous', 'original_not_found')),
  -- The form's Rule 10b5-1(c) check box; null when the filing predates the box or omits it.
  add column aff10b5_one boolean,
  add column footnotes jsonb not null default '{}'::jsonb,
  add column remarks text,
  -- Set once the filing was parsed with the enriched parser.
  add column enriched_at timestamptz;
create index insider_filing_amends on public.insider_filing (amends_accession);
create index insider_filing_superseded_by on public.insider_filing (superseded_by_accession);
create index insider_filing_owner on public.insider_filing (issuer_cik, reporting_owner_cik, filed);

alter table public.insider_transaction
  add column footnote_ids text[] not null default '{}',
  add column footnote_text text,
  add column exercise_price numeric,
  add column exercisable_date date,
  add column expiration_date date,
  add column underlying_security_title text,
  add column underlying_shares numeric,
  add column deemed_execution_date date,
  add column equity_swap_involved boolean,
  -- Deterministic classification from the transaction code and footnotes (see
  -- steward.ownership.insider_classify); the raw code alone is not a signal.
  add column economic_class text check (economic_class is null or economic_class in (
    'open_market_buy', 'open_market_sell', 'option_exercise', 'grant_award', 'gift',
    'tax_withholding', 'conversion', 'other', 'holding_snapshot')),
  add column classification_basis text,
  -- Reported Rule 10b5-1 plan: true / false as the form states it, null when it does not.
  add column rule_10b5_1_plan boolean,
  add column rule_10b5_1_basis text check (rule_10b5_1_basis is null or rule_10b5_1_basis in (
    'form_checkbox', 'footnote')),
  add column rule_10b5_1_adopted date;
create index insider_transaction_class on public.insider_transaction (economic_class);

-- Admit 13D/G evidence links. Rebuilt from the live constraint plus the new name so other
-- migrations that extend the allowed list are not clobbered.
do $$
declare existing text[]; wanted text[] := array['control_stake_filing'];
begin
  select array(select m[1] from regexp_matches(pg_get_constraintdef(oid), '''([a-z0-9_]+)''', 'g') as m)
    into existing
    from pg_constraint
   where conrelid = 'public.source_record_evidence'::regclass
     and conname = 'source_record_evidence_domain_table_check';
  alter table public.source_record_evidence drop constraint source_record_evidence_domain_table_check;
  execute format(
    'alter table public.source_record_evidence add constraint source_record_evidence_domain_table_check check (domain_table in (%s))',
    (select string_agg(quote_literal(t), ',' order by t)
       from (select distinct unnest(coalesce(existing, '{}') || wanted) as t) u));
end $$;
