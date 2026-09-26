-- Phase 5: research, monthly review, chat and institutional ownership.
--
-- - job: a `chat` trigger for jobs the owner started from the chat panel.
-- - job_checkpoint: outputs of completed steps of a long job (research), so a run the
--   worker was killed in resumes from the last completed step when the queue redelivers it.
-- - report.search: full-text search over every report (digests, reviews, research).
-- - chat_conversation / chat_message / chat_action: the chat panel; an action is a job the
--   assistant proposed, started only when the owner confirms it.
-- - form13f_dataset / form13f_filing / institutional_holding: SEC Form 13F data sets,
--   stored for the portfolio's CUSIPs only.

alter table public.job drop constraint job_trigger_check;
alter table public.job add constraint job_trigger_check
  check (trigger in ('schedule', 'manual', 'event', 'chat'));

create table public.job_checkpoint (
  job_id      bigint      not null references public.job (id) on delete cascade,
  key         text        not null check (key ~ '^[a-z0-9_:.-]{1,120}$'),
  output      jsonb       not null,
  created_at  timestamptz not null default now(),
  primary key (job_id, key)
);

alter table public.report add column search tsvector generated always as (
  to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(ticker, '') || ' '
    || coalesce(profile, '') || ' ' || body_md)
) stored;
create index report_search on public.report using gin (search);

create table public.chat_conversation (
  id          bigint generated always as identity primary key,
  title       text        not null default 'New conversation' check (char_length(title) <= 200),
  created_by  uuid,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create table public.chat_message (
  id               bigint generated always as identity primary key,
  conversation_id  bigint      not null references public.chat_conversation (id) on delete cascade,
  role             text        not null check (role in ('user', 'assistant')),
  content          text        not null check (char_length(content) <= 20000),
  -- Assistant answers: sentences with their sources, the tools used, notes.
  body             jsonb       check (body is null or jsonb_typeof(body) = 'object'),
  -- The chat job answering a user message / that wrote an assistant message.
  job_id           bigint      references public.job (id),
  status           text        not null default 'done'
                               check (status in ('pending', 'done', 'failed')),
  created_at       timestamptz not null default now()
);

create index chat_message_conversation on public.chat_message (conversation_id, id);

create table public.chat_action (
  id               bigint generated always as identity primary key,
  conversation_id  bigint      not null references public.chat_conversation (id) on delete cascade,
  message_id       bigint      references public.chat_message (id) on delete cascade,
  kind             text        not null check (kind in ('earnings_check', 'research',
                                 'portfolio_sync')),
  params           jsonb       not null default '{}'::jsonb,
  reason           text,
  status           text        not null default 'proposed'
                               check (status in ('proposed', 'confirmed', 'cancelled')),
  job_id           bigint      references public.job (id),
  created_at       timestamptz not null default now(),
  decided_at       timestamptz,
  decided_by       uuid
);

create index chat_action_conversation on public.chat_action (conversation_id, id);

create table public.form13f_dataset (
  url          text        primary key,
  loaded_at    timestamptz not null default now(),
  filings      integer     not null default 0,
  rows_kept    integer     not null default 0,
  job_id       bigint      references public.job (id)
);

create table public.form13f_filing (
  accession        text        primary key check (accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'),
  cik              text        not null check (cik ~ '^[0-9]{10}$'),
  manager_name     text,
  period           date        not null,
  filed            date        not null,
  submission_type  text        not null check (submission_type in ('13F-HR', '13F-HR/A')),
  -- For amendments: RESTATEMENT replaces the filing, NEW HOLDINGS adds positions.
  amendment_type   text,
  dataset_url      text        references public.form13f_dataset (url)
);

create index form13f_filing_period on public.form13f_filing (period, cik);

-- One row per information-table entry for a portfolio CUSIP (the rest of the data set is
-- not stored). Values are US dollars (the SEC switched from thousands in 2023).
create table public.institutional_holding (
  accession      text        not null references public.form13f_filing (accession),
  infotable_sk   bigint      not null,
  cusip          text        not null check (cusip ~ '^[0-9A-Z]{9}$'),
  ticker         text,
  issuer_name    text,
  title_of_class text,
  value_usd      numeric,
  shares         numeric,
  shares_type    text,
  put_call       text,
  discretion     text,
  voting_sole    numeric,
  voting_shared  numeric,
  voting_none    numeric,
  primary key (accession, infotable_sk)
);

create index institutional_holding_cusip on public.institutional_holding (cusip);

-- ---------------------------------------------------------------------------
-- Privileges and row-level security
-- ---------------------------------------------------------------------------
alter table public.job_checkpoint enable row level security;
alter table public.chat_conversation enable row level security;
alter table public.chat_message enable row level security;
alter table public.chat_action enable row level security;
alter table public.form13f_dataset enable row level security;
alter table public.form13f_filing enable row level security;
alter table public.institutional_holding enable row level security;

revoke all on public.job_checkpoint, public.chat_conversation, public.chat_message,
  public.chat_action, public.form13f_dataset, public.form13f_filing,
  public.institutional_holding from anon, authenticated;
grant select on public.job_checkpoint, public.chat_conversation, public.chat_message,
  public.chat_action, public.form13f_dataset, public.form13f_filing,
  public.institutional_holding to authenticated;

create policy owner_read on public.job_checkpoint
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.chat_conversation
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.chat_message
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.chat_action
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.form13f_dataset
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.form13f_filing
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.institutional_holding
  for select to authenticated using ((select private.is_owner()));

grant select, insert, update, delete on public.job_checkpoint, public.chat_conversation,
  public.chat_message, public.chat_action, public.form13f_dataset, public.form13f_filing,
  public.institutional_holding to app_backend;

create policy backend_all on public.job_checkpoint
  for all to app_backend using (true) with check (true);
create policy backend_all on public.chat_conversation
  for all to app_backend using (true) with check (true);
create policy backend_all on public.chat_message
  for all to app_backend using (true) with check (true);
create policy backend_all on public.chat_action
  for all to app_backend using (true) with check (true);
create policy backend_all on public.form13f_dataset
  for all to app_backend using (true) with check (true);
create policy backend_all on public.form13f_filing
  for all to app_backend using (true) with check (true);
create policy backend_all on public.institutional_holding
  for all to app_backend using (true) with check (true);
