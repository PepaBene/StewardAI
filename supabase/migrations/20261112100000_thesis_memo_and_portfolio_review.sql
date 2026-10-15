-- The detailed thesis the app writes on request (executive summary and memo), and the portfolio
-- review that says what to sell, trim and buy.

create table public.thesis_memo (
  id                 bigint generated always as identity primary key,
  ticker             text        not null,
  -- held: a thesis for a position the owner has; buy: for a stock the owner might buy.
  mode               text        not null check (mode in ('held', 'buy')),
  stance             text        not null check (stance in ('keep', 'add', 'trim', 'exit', 'buy', 'pass')),
  executive_summary  text        not null,
  -- The memo sections, the thesis as a config body, the pre-trade figures and the sources.
  body               jsonb       not null,
  research_report_id bigint      references public.report (id) on delete set null,
  job_id             bigint      references public.job (id) on delete set null,
  review_id          bigint,
  -- The theses config version the thesis was saved in (held), or null while a buy thesis waits.
  config_version_id  bigint      references public.config_version (id) on delete set null,
  status             text        not null default 'open' check (status in ('open', 'accepted', 'dismissed')),
  created_at         timestamptz not null default now()
);

create index thesis_memo_ticker on public.thesis_memo (ticker, created_at desc);

create table public.portfolio_review (
  id         bigint generated always as identity primary key,
  job_id     bigint      references public.job (id) on delete set null,
  status     text        not null default 'researching' check (status in ('researching', 'done')),
  summary    text        not null default '',
  -- Holdings flags, gaps, discovery notes, candidates and the final sell/trim/buy/pass lists.
  body       jsonb       not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  finished_at timestamptz
);

alter table public.thesis_memo
  add constraint thesis_memo_review_fk foreign key (review_id)
  references public.portfolio_review (id) on delete set null;

comment on table public.thesis_memo is
  'A detailed thesis written by the app on the owner''s request: stance, executive summary and memo.';
comment on table public.portfolio_review is
  'A review of what to sell, trim and buy, started by the owner. Nothing is traded.';

alter table public.thesis_memo enable row level security;
alter table public.portfolio_review enable row level security;
revoke all on public.thesis_memo, public.portfolio_review from anon, authenticated;
grant select on public.thesis_memo, public.portfolio_review to authenticated;
create policy owner_read on public.thesis_memo
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.portfolio_review
  for select to authenticated using ((select private.is_owner()));
grant select, insert, update on public.thesis_memo, public.portfolio_review to app_backend;
create policy backend_all on public.thesis_memo
  for all to app_backend using (true) with check (true);
create policy backend_all on public.portfolio_review
  for all to app_backend using (true) with check (true);
