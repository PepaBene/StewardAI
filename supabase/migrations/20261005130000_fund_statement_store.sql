-- Fundamentals stream fixes (audit A3, A12, A13, B29, B32, F17):
--
-- - fundamentals.source admits 'alphavantage' (the Company Overview fallback inserted it and
--   violated the CHECK, aborting the valuation job for later tickers).
-- - financial_statement_fact becomes a real statement store: text-valued facts (the CIK keeps
--   its leading zeros), canonical line item, filer CIK, acceptance time, amendment lineage.
-- - forward_observation gets a content key so a refresh with the same figures is a no-op (the
--   table duplicated a row on every refresh); existing exact duplicates keep their earliest row.
-- - peer_set records its status history so an owner can un-confirm a peer and an as-of read
--   still sees the status that was in force.
-- - issuer_ir_source (the IR pages to poll) and version columns on issuer_ir_release.
-- - covering indexes for the job_id foreign keys of xbrl_filing and shares_outstanding.

-- ---------------------------------------------------------------------------------------------
-- fundamentals.source
-- ---------------------------------------------------------------------------------------------
do $$ declare c text; begin
  for c in select conname from pg_constraint
           where conrelid = 'public.fundamentals'::regclass and contype = 'c'
             and pg_get_constraintdef(oid) like 'CHECK ((source = ANY%'
  loop
    execute format('alter table public.fundamentals drop constraint %I', c);
  end loop;
end $$;
alter table public.fundamentals add constraint fundamentals_source_check
  check (source in ('sec', 'esef', 'finnhub', 'alphavantage'));

-- ---------------------------------------------------------------------------------------------
-- financial_statement_fact
-- ---------------------------------------------------------------------------------------------
alter table public.financial_statement_fact
  add column cik text check (cik is null or cik ~ '^[0-9]{10}$'),
  add column value_text text,
  add column line_item text,
  add column accepted_at timestamptz,
  add column amends_accession text,
  add column is_amendment boolean not null default false;

do $$ declare c text; begin
  for c in select conname from pg_constraint
           where conrelid = 'public.financial_statement_fact'::regclass and contype = 'c'
             and pg_get_constraintdef(oid) like '%text_block IS NOT NULL%'
  loop
    execute format('alter table public.financial_statement_fact drop constraint %I', c);
  end loop;
end $$;
alter table public.financial_statement_fact add constraint financial_statement_fact_content_check
  check (value is not null or text_block is not null or value_text is not null);

create index financial_fact_filing on public.financial_statement_fact (ticker, accession, source);
create index financial_fact_line_item
  on public.financial_statement_fact (ticker, line_item, period_end desc, known_at desc);

comment on column public.financial_statement_fact.value_text is
  'Non-numeric fact value (dei:EntityCentralIndexKey keeps its leading zeros, document type).';
comment on column public.financial_statement_fact.line_item is
  'Canonical line item (revenue, long_term_debt, lease_liability, ...) of the concept.';
comment on column public.financial_statement_fact.known_at is
  'When the filing became public: the SEC acceptance time (ESEF: the date added). Set once.';

-- ---------------------------------------------------------------------------------------------
-- forward_observation: one row per distinct content
-- ---------------------------------------------------------------------------------------------
alter table public.forward_observation add column observation_key text;

update public.forward_observation set observation_key = md5(concat_ws('|',
  kind, coalesce(provider, ''), metric, fiscal_period, coalesce(basis, ''), coalesce(currency, ''),
  coalesce(vintage, ''), coalesce(period_end::text, ''), coalesce(trim_scale(value)::text, ''),
  coalesce(trim_scale(low)::text, ''), coalesce(trim_scale(high)::text, ''),
  coalesce(analyst_count::text, '')));

delete from public.forward_observation f using public.forward_observation g
  where f.ticker = g.ticker and f.observation_key = g.observation_key and f.id > g.id;

alter table public.forward_observation alter column observation_key set not null;
create unique index forward_observation_content
  on public.forward_observation (ticker, observation_key);

-- Any writer gets the key: a row inserted without one (the backend's own helper passes it
-- computed in Python with the same fields and number formatting) is keyed here.
create function public.forward_observation_set_key() returns trigger
language plpgsql set search_path = '' as $$
begin
  if new.observation_key is null then
    new.observation_key := md5(concat_ws('|',
      new.kind, coalesce(new.provider, ''), new.metric, new.fiscal_period,
      coalesce(new.basis, ''), coalesce(new.currency, ''), coalesce(new.vintage, ''),
      coalesce(new.period_end::text, ''), coalesce(trim_scale(new.value)::text, ''),
      coalesce(trim_scale(new.low)::text, ''), coalesce(trim_scale(new.high)::text, ''),
      coalesce(new.analyst_count::text, '')));
  end if;
  return new;
end $$;
create trigger forward_observation_set_key before insert on public.forward_observation
  for each row execute function public.forward_observation_set_key();

-- ---------------------------------------------------------------------------------------------
-- peer_set: status history
-- ---------------------------------------------------------------------------------------------
alter table public.peer_set add column status_history jsonb not null default '[]'::jsonb;
update public.peer_set
  set status_history = jsonb_build_array(jsonb_build_object('status', status, 'at', known_at));

-- ---------------------------------------------------------------------------------------------
-- IR releases: the pages to poll and the version chain
-- ---------------------------------------------------------------------------------------------
create table public.issuer_ir_source (
  id bigint generated always as identity primary key,
  ticker text not null,
  url text not null check (url ~ '^https://'),
  release_type text not null check (release_type in
    ('guidance', 'consensus', 'segment_consensus', 'capital_return')),
  basis text,
  label text,
  active boolean not null default true,
  last_checked_at timestamptz,
  last_changed_at timestamptz,
  last_hash text,
  last_status text,
  last_error text,
  created_at timestamptz not null default now(),
  unique (ticker, url)
);

alter table public.issuer_ir_release
  add column source_id bigint references public.issuer_ir_source (id) on delete set null,
  add column previous_release_id bigint references public.issuer_ir_release (id)
    on delete set null,
  add column content_type text,
  add column byte_size integer,
  add column fetched_at timestamptz;
create index issuer_ir_release_source on public.issuer_ir_release (source_id);
create index issuer_ir_release_previous on public.issuer_ir_release (previous_release_id);

alter table public.issuer_ir_source enable row level security;
revoke all on public.issuer_ir_source from anon, authenticated;
grant select on public.issuer_ir_source to authenticated;
grant select, insert, update, delete on public.issuer_ir_source to app_backend;
create policy owner_read on public.issuer_ir_source
  for select to authenticated using ((select private.is_owner()));
create policy backend_all on public.issuer_ir_source
  for all to app_backend using (true) with check (true);
grant usage, select on all sequences in schema public to app_backend;

-- ---------------------------------------------------------------------------------------------
-- job_id foreign keys of the SEC XBRL tables
-- ---------------------------------------------------------------------------------------------
create index if not exists xbrl_filing_job on public.xbrl_filing (job_id);
create index if not exists shares_outstanding_job on public.shares_outstanding (job_id);
