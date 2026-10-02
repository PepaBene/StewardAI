-- Run in the Supabase SQL editor on the live project, ONE transaction (all or nothing).
-- Identical to supabase/migrations/20261005170000_verification_gate_hardening.sql, wrapped in begin/commit.
begin;

-- Phase 8 gate hardening (audit B47, B51).
--
-- reconciliation_sample: one sample per (domain, subject, metric, period) - 30 copies of one
--   value no longer satisfy min_samples - a sample cannot be dated in the future, and every
--   sample names the person who read it (reviewer) and where it was read (source and url).
-- signal_audit_result: an audit cannot be dated in the future and names its reviewer.
-- provider_access: the one credentialed fmp endpoint without a declared free quota gets the
--   same 250/day the other fmp endpoints carry, so the zero-cost audit can prove a hard stop.
--
-- The new CHECK constraints are NOT VALID: rows recorded before this migration are left as they
-- are (nothing is rewritten or invented), but the gates (thresholds.domain_gate and
-- signal_audit.publish_gate) ignore any row that would violate them, so legacy rows never count
-- as evidence.  Duplicate reconciliation samples are collapsed to the newest one.

delete from public.reconciliation_sample a
 using public.reconciliation_sample b
 where a.domain = b.domain and a.subject_key = b.subject_key and a.metric = b.metric
   and a.period_end is not distinct from b.period_end
   and (a.sampled_at, a.id) < (b.sampled_at, b.id);

create unique index reconciliation_sample_unique_key
  on public.reconciliation_sample (domain, subject_key, metric, (coalesce(period_end, date '0001-01-01')));

alter table public.reconciliation_sample
  add constraint reconciliation_sample_not_future
    check (sampled_at <= now() + interval '5 minutes') not valid,
  add constraint reconciliation_sample_reviewer_named
    check (reviewer is not null and btrim(reviewer) <> '') not valid,
  add constraint reconciliation_sample_source_named
    check (btrim(primary_source) <> '' and primary_url is not null and btrim(primary_url) <> '') not valid;

alter table public.signal_audit_result
  add constraint signal_audit_result_not_future
    check (audited_at <= now() + interval '5 minutes') not valid,
  add constraint signal_audit_result_reviewer_named
    check (reviewer is not null and btrim(reviewer) <> '') not valid;

update public.provider_access
   set quota = 250, quota_period = 'day'
 where provider = 'fmp' and endpoint = 'sector_snapshot' and quota is null;

commit;
