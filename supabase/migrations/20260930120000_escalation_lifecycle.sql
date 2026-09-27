-- Escalation lifecycle: the owner acknowledges an impact note, and later resolves it.
--
-- - report.escalation_status: open | acknowledged | resolved. Escalation reports only; null
--   for digests, research and reviews. A new escalation starts open: a BEFORE INSERT trigger
--   fills it in, so the writers need no change. Every existing escalation is open.
-- - report.acknowledged_at: when the owner first acknowledged it (also set when an
--   escalation is resolved without having been acknowledged). Kept when it is reopened, so
--   time-to-acknowledge stays the first acknowledgement.
-- - report.resolved_at: when it was resolved; cleared when it is reopened.
--
-- The table-level grants and RLS policies of `report` cover the new columns (the backend
-- already has update; the owner reads).

alter table public.report
  add column escalation_status text
    check (escalation_status in ('open', 'acknowledged', 'resolved')),
  add column acknowledged_at timestamptz,
  add column resolved_at timestamptz;

update public.report set escalation_status = 'open' where type = 'escalation';

create function private.report_escalation_status_default()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.type = 'escalation' and new.escalation_status is null then
    new.escalation_status := 'open';
  end if;
  return new;
end;
$$;

create trigger report_escalation_status_default
  before insert on public.report
  for each row execute function private.report_escalation_status_default();

alter table public.report
  add constraint report_escalation_status_only_escalations
    check ((type = 'escalation') = (escalation_status is not null)),
  add constraint report_escalation_timestamps check (
    (escalation_status is distinct from 'acknowledged' or acknowledged_at is not null)
    and (escalation_status is distinct from 'resolved'
         or (acknowledged_at is not null and resolved_at is not null))
    and (escalation_status = 'resolved' or resolved_at is null)
  );

comment on column public.report.escalation_status is
  'Escalations only: open, acknowledged or resolved by the owner. Null for other report types.';
comment on column public.report.acknowledged_at is
  'Escalations only: when the owner first acknowledged (or directly resolved) it.';
comment on column public.report.resolved_at is
  'Escalations only: when the owner resolved it; cleared when reopened.';

create index report_escalation_status on public.report (escalation_status, created_at desc)
  where type = 'escalation';
