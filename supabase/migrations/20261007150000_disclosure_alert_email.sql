-- Alert e-mail: remember which alerts were already mailed, so a digest never repeats one.
-- Set only after the mail provider accepted the message; a failed send leaves it null and the
-- next run sends it again.
alter table public.disclosure_alert add column emailed_at timestamptz;
create index disclosure_alert_unmailed on public.disclosure_alert (created_at)
  where emailed_at is null and acknowledged_at is null;
comment on column public.disclosure_alert.emailed_at is
  'When the alert was included in an accepted e-mail digest; null = not mailed yet.';
