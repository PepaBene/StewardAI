-- Plan P3.1 / P3.2 / P3.3: ownership filings become point-in-time.
--
-- 13F, 13D/G and N-PORT rows stored only a filing DATE. A replay needs the moment each filing
-- counts as known, so each table gains `known_at`. SEC acceptance time is not stored by the
-- collectors, so it is derived as the end of the filing day (UTC) and never earlier: a
-- filing is never treated as known before it could have been. A later collector that stores the
-- real acceptance time may set it explicitly; the trigger only fills a NULL.
--
-- `confidential_treatment` on 13F filings is "unknown" until the source says otherwise. It is
-- deliberately not defaulted to "none": an unflagged filing may still omit positions.
-- Additive only: no existing column, key or row is rewritten.

create or replace function public.ownership_known_at_default() returns trigger
language plpgsql as $$
begin
  if new.known_at is null then
    new.known_at := ((new.filed::timestamp + interval '1 day' - interval '1 second') at time zone 'UTC');
  end if;
  return new;
end $$;

alter table public.form13f_filing
  add column known_at timestamptz,
  add column confidential_treatment text not null default 'unknown'
    check (confidential_treatment in ('none_indicated', 'requested', 'unknown'));
alter table public.control_stake_filing add column known_at timestamptz;
alter table public.nport_filing add column known_at timestamptz;

update public.form13f_filing
   set known_at = ((filed::timestamp + interval '1 day' - interval '1 second') at time zone 'UTC')
 where known_at is null;
update public.control_stake_filing
   set known_at = ((filed::timestamp + interval '1 day' - interval '1 second') at time zone 'UTC')
 where known_at is null;
update public.nport_filing
   set known_at = ((filed::timestamp + interval '1 day' - interval '1 second') at time zone 'UTC')
 where known_at is null;

alter table public.form13f_filing alter column known_at set not null;
alter table public.control_stake_filing alter column known_at set not null;
alter table public.nport_filing alter column known_at set not null;

create trigger form13f_filing_known_at before insert on public.form13f_filing
  for each row execute function public.ownership_known_at_default();
create trigger control_stake_filing_known_at before insert on public.control_stake_filing
  for each row execute function public.ownership_known_at_default();
create trigger nport_filing_known_at before insert on public.nport_filing
  for each row execute function public.ownership_known_at_default();

create index form13f_filing_known on public.form13f_filing (cik, period, known_at);
create index control_stake_filing_known on public.control_stake_filing (issuer_cik, known_at);
create index nport_filing_known on public.nport_filing (series_id, report_period_end, known_at);
