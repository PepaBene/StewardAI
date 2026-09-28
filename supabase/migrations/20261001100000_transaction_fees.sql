-- Trading212 per-fill fees and taxes, and where they stand in the cash flow.
--
-- Trading212's GET /equity/history/orders returns, per fill,
-- walletImpact.taxes[{name, quantity, currency, chargedAt}]: the FX conversion fee
-- (CURRENCY_CONVERSION_FEE) and pass-through taxes and levies (STAMP_DUTY,
-- STAMP_DUTY_RESERVE_TAX, FRENCH_TRANSACTION_TAX, FINRA_FEE, PTM_LEVY, COMMISSION_TURNOVER,
-- TRANSACTION_FEE). Until now the sync parsed only part of walletImpact and stored the
-- parsed model as `raw`, so these were lost.
--
-- Design: one normalised row per charge in public.transaction_fee, so period sums are a
-- plain indexed aggregate (no jsonb unnesting of every order on every request), with
-- `amount` = abs(quantity): Trading212's own example shows a positive quantity, live
-- responses have been reported negative, and a fee is a cost either way. The reported
-- quantity is kept as-is next to it. The original response item stays in transaction.raw.
--
-- public.transaction gains:
-- - fees_captured_at: when the sync last read this fill's taxes array. Null = the row was
--   written before fee capture existed, so "no fees stored" does not mean "no fees paid".
--   The sync re-reads the whole order history once (its sync_state carries a fee-capture
--   version) and upserts every fill, which fills this in.
-- - fee_basis: whether walletImpact.netValue already contains the fill's fees, judged per
--   fill from quantity x price x fxRate (excluded | included | unknown; null without fees).
--   Trading212's documented example has netValue EXCLUDING taxes, which is the default
--   reading for `unknown`.
--
-- Access: the same model as public.transaction (owner reads; backend inserts, updates and,
-- for re-reads of a fill, deletes its fee rows).

alter table public.transaction
  add column fees_captured_at timestamptz,
  add column fee_basis text check (fee_basis in ('excluded', 'included', 'unknown'));

comment on column public.transaction.fees_captured_at is
  'Orders: when the sync last read this fill''s walletImpact.taxes. Null = written before '
  'fee capture (2026-10); the one-time order-history re-read fills it.';
comment on column public.transaction.fee_basis is
  'Orders with fees: whether walletImpact.netValue (= amount) already includes them '
  '(excluded | included | unknown, judged from quantity x price x fxRate).';

create table public.transaction_fee (
  transaction_id text        not null references public.transaction (id) on delete cascade,
  -- Position in walletImpact.taxes (a fill can carry several charges of one name).
  seq            smallint    not null check (seq >= 0),
  -- Trading212's name, e.g. CURRENCY_CONVERSION_FEE or STAMP_DUTY.
  name           text        not null,
  -- fx: CURRENCY_CONVERSION_FEE. tax: every other name (taxes and levies).
  category       text        not null check (category in ('fx', 'tax')),
  -- abs(quantity), in `currency`.
  amount         numeric     not null check (amount >= 0),
  -- As Trading212 reported it (the sign is not consistent across sources).
  quantity       numeric     not null,
  currency       text,
  charged_at     timestamptz,
  -- The fill's time, copied so period sums need no join.
  occurred_at    timestamptz not null,
  primary key (transaction_id, seq)
);

create index transaction_fee_occurred_at on public.transaction_fee (occurred_at desc);

comment on table public.transaction_fee is
  'Per-fill fees and taxes from Trading212 walletImpact.taxes, one row per charge.';

alter table public.transaction_fee enable row level security;

revoke all on public.transaction_fee from anon, authenticated;
grant select on public.transaction_fee to authenticated;
create policy owner_read on public.transaction_fee
  for select to authenticated using ((select private.is_owner()));

grant select, insert, update, delete on public.transaction_fee to app_backend;
create policy backend_all on public.transaction_fee
  for all to app_backend using (true) with check (true);
