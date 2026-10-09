-- Plan P0-P5 gap closure: stored alerts, an anonymous label, owner-assigned dependency weights,
-- structured procurement awards, filing-level 13F totals, 13D/G exhibits and the
-- sample/adjudication records the release thresholds ask for.
--
-- Additive only. No existing column or row is rewritten except the one backfill below, which fills
-- `denominator_kind` only where the old `share_basis` states it unambiguously (an issuer-basis
-- share of revenue or purchases); counterparty-basis shares stay null so they can never enter the
-- reporter's concentration.

-- ------------------------------------------------------------------------------------ alerts
-- One row per thing worth telling the owner about, deduplicated by `dedupe_key`. Insider trades,
-- verified major-holder filings and new/ended relationships all land here; reading is separate
-- from acknowledging.
create table public.disclosure_alert (
  id bigint generated always as identity primary key,
  kind text not null check (kind in ('insider_trade', 'major_holder', 'relationship_change')),
  ticker text not null,
  dedupe_key text not null unique,
  headline text not null check (length(btrim(headline)) > 0),
  detail jsonb not null default '{}',
  source_url text,
  known_at timestamptz not null,
  created_at timestamptz not null default now(),
  acknowledged_at timestamptz,
  acknowledged_by text
);
create index disclosure_alert_open on public.disclosure_alert (ticker, created_at desc)
  where acknowledged_at is null;

-- ------------------------------------------------------------------------- P5 claim columns
alter table public.relationship_disclosure add column claim_label text;
comment on column public.relationship_disclosure.claim_label is
  'The filing''s own wording for an anonymous or group claim, e.g. "Customer A". Never a real name.';

-- Fill the label only from the passage's own words ("Customer A"), never from anything inferred.
update public.relationship_disclosure
   set claim_label = substring(passage from '\m((Customer|Supplier|Distributor|Partner|Client) [A-Z0-9])\M')
 where anonymous_counterparty and claim_label is null
   and substring(passage from '\m((Customer|Supplier|Distributor|Partner|Client) [A-Z0-9])\M') is not null;

update public.relationship_disclosure
   set denominator_kind = case share_basis
         when 'issuer_revenue' then 'total_revenue'
         when 'issuer_purchases' then 'purchases'
       end
 where denominator_kind is null and share_basis in ('issuer_revenue', 'issuer_purchases');

-- A weight the owner assigned (not a reported figure). Always labelled as such wherever shown.
create table public.owner_dependency_weight (
  id bigint generated always as identity primary key,
  ticker text not null,
  relationship text not null check (relationship in ('customer', 'supplier')),
  counterparty_key text not null,
  counterparty_name text,
  share numeric not null check (share between 0 and 1),
  denominator_kind text not null check (denominator_kind in (
    'total_revenue', 'segment_revenue', 'cost_of_sales', 'purchases', 'receivables', 'other')),
  as_of date not null,
  note text not null check (length(btrim(note)) >= 10),
  assigned_by text not null check (length(btrim(assigned_by)) > 0),
  created_at timestamptz not null default now(),
  retired_at timestamptz
);
create index owner_dependency_weight_ticker on public.owner_dependency_weight (ticker, created_at);

-- Structured fields of a TED award notice. The claim row stays the unit of review; this keeps the
-- facts a contract signal is made of (lot, CPV, value, dates, status) and nothing about revenue.
create table public.procurement_award_detail (
  disclosure_id bigint not null references public.relationship_disclosure (id) on delete cascade,
  notice_number text not null,
  lot_id text not null default '',
  notice_type text,
  cpv_codes text[] not null default '{}',
  value numeric check (value is null or value >= 0),
  currency text check (currency is null or currency ~ '^[A-Z]{3}$'),
  start_date date,
  end_date date,
  status text not null default 'awarded' check (status in ('awarded', 'modified', 'cancelled')),
  published date,
  primary key (notice_number, lot_id),
  check (end_date is null or start_date is null or end_date >= start_date)
);
create index procurement_award_detail_claim on public.procurement_award_detail (disclosure_id);

-- ------------------------------------------------------------------------------------ P3
-- Filing-level totals so a 13F report can be reconciled and an exit is visible: the number of
-- lines and the reported table value, kept for every filing of a tracked manager even when none
-- of the held CUSIPs is listed.
alter table public.form13f_filing
  add column reported_line_count integer check (reported_line_count is null or reported_line_count >= 0),
  add column reported_total_value_usd numeric check (
    reported_total_value_usd is null or reported_total_value_usd >= 0),
  add column omitted_holdings_notice boolean;
comment on column public.form13f_filing.omitted_holdings_notice is
  'True when the filing states that holdings were omitted under a confidential treatment request.';

-- Reporting persons (group members, voting and dispositive power) and Item 4 are already stored in
-- `control_stake_holder` and `control_stake_filing.intent_text`. What was never stored is Item 7:
-- the agreements and other exhibits a filing lists, one row per exhibit as filed.
create table public.control_stake_exhibit (
  accession text not null references public.control_stake_filing (accession) on delete cascade,
  row_index integer not null,
  exhibit_label text not null,
  description text,
  document_url text,
  primary key (accession, row_index)
);

-- ------------------------------------------------------------------------------- adjudication
-- A person's label for one sampled item, per dataset. The release thresholds are measured from
-- these rows, never from the classifier's own output.
create table public.disclosure_adjudication (
  id bigint generated always as identity primary key,
  dataset text not null check (dataset in (
    'insider_class', 'relationship_claim', 'ownership_match', 'statement_note', 'document_sample')),
  sample_key text not null,
  item_ref text not null,
  machine_value text,
  human_value text not null,
  correct boolean not null,
  planted_false_positive boolean not null default false,
  note text,
  adjudicated_by text not null check (length(btrim(adjudicated_by)) > 0),
  adjudicated_at timestamptz not null default now(),
  unique (dataset, sample_key, item_ref, adjudicated_by)
);
create index disclosure_adjudication_sample on public.disclosure_adjudication (dataset, sample_key);

-- ------------------------------------------------------------------------------------ access
do $$
declare t text;
begin
  foreach t in array array[
    'disclosure_alert', 'owner_dependency_weight', 'procurement_award_detail',
    'control_stake_exhibit', 'disclosure_adjudication']
  loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on public.%I from anon, authenticated', t);
    execute format('grant select on public.%I to authenticated', t);
    execute format(
      'create policy owner_read on public.%I for select to authenticated using ((select private.is_owner()))', t);
    execute format('grant select, insert, update, delete on public.%I to app_backend', t);
    execute format(
      'create policy backend_all on public.%I for all to app_backend using (true) with check (true)', t);
  end loop;
end $$;
