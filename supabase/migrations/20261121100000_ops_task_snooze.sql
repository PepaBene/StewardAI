-- Snooze a notice: set it aside until a date instead of for good. The watchdog does not reopen a
-- snoozed (kind, subject) before then; after it, a problem that is still there opens a new notice.
alter table public.ops_task add column snoozed_until timestamptz;
