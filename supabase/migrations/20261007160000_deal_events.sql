-- Plan P6: public deal, financing and corporate-action terms.
--
-- A deal is a `deal_event` (what it is, who is in it, which transaction it clusters into). Its
-- lifecycle is an append-only ledger of `deal_state_transition` rows, each carrying the evidence it
-- rests on and the moment it became known (`known_at`), kept apart from the moment it took effect
-- (`effective_at`). An amendment never edits a term: it appends a `deal_term_version`. Each typed
-- term has an explicit state (`stated` or `not_stated`), so an absent term is a typed unknown, never
-- a zero. `terms_origin` separates announced terms from management projections. A closed deal links
-- to its successor and to three separate dates in `deal_structure_link`: the legal closing, the
-- first reporting period that includes the target, and the broker's corporate-action posting.
-- Additive only: no existing table or column is changed.

create table public.deal_event (
  id bigint generated always as identity primary key,
  transaction_key text not null unique,
  deal_type text not null check (deal_type in (
    'acquisition', 'merger', 'divestiture', 'ipo', 'follow_on', 'debt_issuance', 'spin_off',
    'joint_venture', 'strategic_investment', 'buyback', 'tender_offer', 'material_agreement')),
  title text not null check (length(btrim(title)) > 0),
  ticker text,                              -- the holding this deal is linked to, when one is held
  issuer_id bigint references public.issuer(id),
  announced_at timestamptz,
  event_effective_at timestamptz,           -- when it happened in the world; never known_at
  known_at timestamptz not null,
  created_at timestamptz not null default now()
);
create index deal_event_ticker_known on public.deal_event (ticker, known_at);

create table public.deal_participant (
  id bigint generated always as identity primary key,
  deal_id bigint not null references public.deal_event(id) on delete cascade,
  role text not null check (role in (
    'acquirer', 'target', 'seller', 'buyer', 'issuer', 'bidder', 'partner', 'lender',
    'underwriter', 'counterparty')),
  name text not null check (length(btrim(name)) > 0),
  issuer_id bigint references public.issuer(id),   -- null until a reviewer matches the name
  share_class text,
  ticker text,
  known_at timestamptz not null
);
create index deal_participant_deal on public.deal_participant (deal_id);

create table public.deal_state_transition (
  id bigint generated always as identity primary key,
  deal_id bigint not null references public.deal_event(id) on delete cascade,
  state text not null check (state in (
    'rumored', 'announced', 'definitive', 'approved', 'closed', 'terminated', 'contested')),
  effective_at timestamptz not null,
  known_at timestamptz not null,
  document_version_id bigint not null references public.source_document_version(id),
  evidence_id bigint not null references public.source_evidence(id),
  rumor_source text,                        -- required wording for a rumored state
  note text,
  created_at timestamptz not null default now(),
  check (state <> 'rumored' or length(btrim(coalesce(rumor_source, ''))) > 0)
);
create index deal_state_transition_deal on public.deal_state_transition (deal_id, known_at);

create table public.deal_term_version (
  id bigint generated always as identity primary key,
  deal_id bigint not null references public.deal_event(id) on delete cascade,
  version_no integer not null check (version_no >= 1),
  supersedes_id bigint references public.deal_term_version(id),
  terms_origin text not null default 'announced'
    check (terms_origin in ('announced', 'management_projection')),
  known_at timestamptz not null,
  event_effective_at timestamptz,
  document_version_id bigint not null references public.source_document_version(id),
  evidence_id bigint not null references public.source_evidence(id),
  currency text check (currency is null or currency ~ '^[A-Z]{3}$'),
  -- Each numeric term is a value plus a state; a value exists exactly when the state is 'stated'.
  -- Amounts are in whole units of `currency` (scale already applied), never thousands or millions.
  price_per_share numeric, price_per_share_state text not null default 'not_stated',
  cash_amount numeric, cash_amount_state text not null default 'not_stated',
  stock_amount numeric, stock_amount_state text not null default 'not_stated',
  debt_amount numeric, debt_amount_state text not null default 'not_stated',
  earnout_amount numeric, earnout_amount_state text not null default 'not_stated',
  cash_pct numeric, cash_pct_state text not null default 'not_stated',
  stock_pct numeric, stock_pct_state text not null default 'not_stated',
  exchange_ratio numeric, exchange_ratio_state text not null default 'not_stated',
  stake_pct numeric, stake_pct_state text not null default 'not_stated',
  financing_amount numeric, financing_amount_state text not null default 'not_stated',
  refinanced_amount numeric, refinanced_amount_state text not null default 'not_stated',
  termination_fee numeric, termination_fee_state text not null default 'not_stated',
  expected_synergies numeric, expected_synergies_state text not null default 'not_stated',
  authorized_amount numeric, authorized_amount_state text not null default 'not_stated',
  executed_amount numeric, executed_amount_state text not null default 'not_stated',
  unaffected_price numeric, unaffected_price_state text not null default 'not_stated',
  unaffected_date date,
  unaffected_source text,
  closing_conditions text, closing_conditions_state text not null default 'not_stated',
  created_at timestamptz not null default now(),
  unique (deal_id, version_no, terms_origin),
  check (price_per_share_state in ('stated', 'not_stated') and (price_per_share_state = 'stated') = (price_per_share is not null)),
  check (cash_amount_state in ('stated', 'not_stated') and (cash_amount_state = 'stated') = (cash_amount is not null)),
  check (stock_amount_state in ('stated', 'not_stated') and (stock_amount_state = 'stated') = (stock_amount is not null)),
  check (debt_amount_state in ('stated', 'not_stated') and (debt_amount_state = 'stated') = (debt_amount is not null)),
  check (earnout_amount_state in ('stated', 'not_stated') and (earnout_amount_state = 'stated') = (earnout_amount is not null)),
  check (cash_pct_state in ('stated', 'not_stated') and (cash_pct_state = 'stated') = (cash_pct is not null)),
  check (stock_pct_state in ('stated', 'not_stated') and (stock_pct_state = 'stated') = (stock_pct is not null)),
  check (exchange_ratio_state in ('stated', 'not_stated') and (exchange_ratio_state = 'stated') = (exchange_ratio is not null)),
  check (stake_pct_state in ('stated', 'not_stated') and (stake_pct_state = 'stated') = (stake_pct is not null)),
  check (financing_amount_state in ('stated', 'not_stated') and (financing_amount_state = 'stated') = (financing_amount is not null)),
  check (refinanced_amount_state in ('stated', 'not_stated') and (refinanced_amount_state = 'stated') = (refinanced_amount is not null)),
  check (termination_fee_state in ('stated', 'not_stated') and (termination_fee_state = 'stated') = (termination_fee is not null)),
  check (expected_synergies_state in ('stated', 'not_stated') and (expected_synergies_state = 'stated') = (expected_synergies is not null)),
  check (authorized_amount_state in ('stated', 'not_stated') and (authorized_amount_state = 'stated') = (authorized_amount is not null)),
  check (executed_amount_state in ('stated', 'not_stated') and (executed_amount_state = 'stated') = (executed_amount is not null)),
  check (unaffected_price_state in ('stated', 'not_stated') and (unaffected_price_state = 'stated') = (unaffected_price is not null)),
  check (closing_conditions_state in ('stated', 'not_stated') and (closing_conditions_state = 'stated') = (closing_conditions is not null))
);
create index deal_term_version_deal on public.deal_term_version (deal_id, known_at);

create table public.deal_document (
  id bigint generated always as identity primary key,
  deal_id bigint not null references public.deal_event(id) on delete cascade,
  document_version_id bigint not null references public.source_document_version(id),
  role text not null check (role in ('original', 'amendment', 'exhibit', 'proxy', 'notice', 'other')),
  known_at timestamptz not null,
  unique (deal_id, document_version_id)
);

create table public.deal_structure_link (
  id bigint generated always as identity primary key,
  deal_id bigint not null references public.deal_event(id) on delete cascade,
  legal_closing_date date,                  -- the three dates are different facts; none fills another
  first_reporting_period_end date,          -- first period whose statements include the target
  broker_posting_date date,                 -- when the portfolio broker posted the corporate action
  successor_name text,
  successor_ticker text,
  successor_issuer_id bigint references public.issuer(id),
  known_at timestamptz not null,
  evidence_id bigint references public.source_evidence(id),
  created_at timestamptz not null default now()
);
create index deal_structure_link_deal on public.deal_structure_link (deal_id, known_at);

-- Ledgers: nothing is edited or deleted after the fact (a correction is a new row).
create trigger append_only before update or delete on public.deal_state_transition
  for each row execute function public.forbid_append_only_mutation();
create trigger append_only before update or delete on public.deal_term_version
  for each row execute function public.forbid_append_only_mutation();
create trigger append_only before update or delete on public.deal_structure_link
  for each row execute function public.forbid_append_only_mutation();

do $$
declare t text;
begin
  foreach t in array array[
    'deal_event', 'deal_participant', 'deal_state_transition', 'deal_term_version',
    'deal_document', 'deal_structure_link']
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
