-- UK TR-1 notifications of major holdings (P3.4): the national voting-rights lane's raw material.
-- One row per announcement. Every figure is nullable: a field the form did not state, or that the
-- parser could not read, is null (never zero), and `gap` says why a notice has no usable percent.
-- `known_at` is the announcement time. The national lane stays `not_covered` for an issuer until
-- 20 reviewed examples reach the enablement bar; storing a notice does not enable it.

create table public.uk_tr1_notice (
  source_url                 text primary key,
  issuer_ticker              text,
  issuer_name                text,
  holder                     text,
  holder_office              text,
  shareholder_if_different   text,
  percent_shares_after       numeric check (percent_shares_after is null or percent_shares_after between 0 and 100),
  percent_instruments_after  numeric check (percent_instruments_after is null or percent_instruments_after between 0 and 100),
  percent_total_after        numeric check (percent_total_after is null or percent_total_after between 0 and 100),
  voting_rights_after        numeric check (voting_rights_after is null or voting_rights_after >= 0),
  percent_total_before       numeric check (percent_total_before is null or percent_total_before between 0 and 100),
  thresholds_crossed         integer[],
  direction                  text check (direction is null or direction in ('up', 'down')),
  date_crossed               date,
  date_issuer_notified       date,
  known_at                   timestamptz not null,
  gap                        text check (gap is null or gap in ('no_percent_stated', 'position_row_unreadable')),
  review_status              text not null default 'unreviewed'
                               check (review_status in ('unreviewed', 'confirmed', 'rejected')),
  created_at                 timestamptz not null default now()
);
create index uk_tr1_notice_issuer on public.uk_tr1_notice (issuer_ticker, known_at desc);

alter table public.uk_tr1_notice enable row level security;
revoke all on public.uk_tr1_notice from anon, authenticated;
grant select on public.uk_tr1_notice to authenticated;
create policy owner_read on public.uk_tr1_notice
  for select to authenticated using ((select private.is_owner()));
grant select, insert, update, delete on public.uk_tr1_notice to app_backend;
create policy backend_all on public.uk_tr1_notice
  for all to app_backend using (true) with check (true);
