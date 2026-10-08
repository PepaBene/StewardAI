-- Phase 7 (M7.1): the thesis and decision journal. One row is one decision the owner took (or
-- chose not to take) with the reasons written at the time and a snapshot of the evidence and
-- method versions it rested on. Rows are never edited or deleted: a later review is a new row that
-- points at the one it revisits (`parent_id`), so outcomes can be audited against what was known.

create table public.decision_journal (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  scope text not null check (scope in ('security', 'portfolio')),
  ticker text check (ticker is null or length(trim(ticker)) between 1 and 40),
  decision text not null
    check (decision in ('buy', 'add', 'hold', 'trim', 'sell', 'pass', 'watch', 'review')),
  parent_id bigint references public.decision_journal(id),
  decided_on date not null default current_date,
  hypothesis text not null check (length(trim(hypothesis)) between 1 and 4000),
  valuation_assumptions text check (valuation_assumptions is null or length(valuation_assumptions) <= 4000),
  catalysts text[] not null default '{}',
  disconfirming text[] not null default '{}',
  review_date date,
  conviction smallint check (conviction is null or conviction between 1 and 5),
  main_reason text check (main_reason is null or main_reason in ('quality', 'value', 'catalyst', 'momentum', 'other')),
  price_at_decision numeric check (price_at_decision is null or price_at_decision > 0),
  price_currency text,
  snapshot jsonb not null default '{}' check (jsonb_typeof(snapshot) = 'object'),
  created_at timestamptz not null default now(),
  check ((scope = 'security') = (ticker is not null))
);
create index decision_journal_owner on public.decision_journal (owner_id, decided_on desc, id desc);
create index decision_journal_ticker on public.decision_journal (owner_id, ticker, decided_on desc);
create index decision_journal_parent on public.decision_journal (parent_id);

alter table public.decision_journal enable row level security;
revoke all on public.decision_journal from anon;
grant select, insert on public.decision_journal to authenticated, app_backend;
create policy decision_journal_owner_read on public.decision_journal for select to authenticated
  using (owner_id = (select auth.uid()));
create policy decision_journal_owner_insert on public.decision_journal for insert to authenticated
  with check (owner_id = (select auth.uid()));
create policy decision_journal_backend on public.decision_journal for all to app_backend
  using (true) with check (true);
grant usage, select on sequence public.decision_journal_id_seq to authenticated, app_backend;

comment on table public.decision_journal is
  'Owner decision journal: reasons written at decision time plus an evidence and method snapshot. Append-only; a review is a new row with parent_id.';
