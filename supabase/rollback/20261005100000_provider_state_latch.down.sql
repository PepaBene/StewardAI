-- Rollback for supabase/migrations/20261005100000_provider_state_latch.sql (core stream, audit items listed there; proven by the test below).
-- Restoration proven by apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is run,
-- and the catalog fingerprint (tables, columns, constraints, indexes, functions, triggers,
-- policies, grants, views, seed rows) must equal the one taken before the migration. Run it in a
-- transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: drops provider_access_state_backup after restoring the seeded states it holds.
-- Not restorable: provider_access.last_success_at values back-filled from provider_fetch_audit,
-- the last_attempt_* / consecutive_failures bookkeeping and provider_fetch_audit.job_id /
-- provider_quota_usage.last_job_id values written since.


update public.provider_access pa set state = b.previous_state, detail = b.previous_detail
  from public.provider_access_state_backup b
 where pa.provider = b.provider and pa.endpoint = b.endpoint;
drop table public.provider_access_state_backup;

drop function public.provider_gate_open(text,text,timestamptz);
drop function public.record_provider_attempt(text,text,text,text,timestamptz,integer,text,
  integer,bigint,numeric,bigint);
drop function public.reserve_provider_credits(text,text,numeric,numeric,numeric,numeric,
  numeric,timestamptz,bigint);

create function public.record_provider_attempt(
  p_provider text, p_endpoint text, p_state text, p_detail text, p_retry_at timestamptz,
  p_status_code integer, p_request_id text, p_elapsed_ms integer,
  p_response_bytes bigint, p_credits numeric
) returns void language plpgsql security invoker set search_path = '' as $$
begin
  insert into public.provider_fetch_audit
    (provider,endpoint,state,status_code,request_id,elapsed_ms,response_bytes,credits,retry_at,error_redacted)
  values (p_provider,p_endpoint,p_state,p_status_code,p_request_id,p_elapsed_ms,
          p_response_bytes,p_credits,p_retry_at,p_detail);
  update public.provider_access set state=p_state, status_code=p_status_code, detail=p_detail,
    retry_at=p_retry_at, tested_at=now(), updated_at=now(),
    hard_blocked=case when p_state='paid_only'
                           or (p_state='access_unverified' and p_status_code in (401,403)) then true
                      when p_state='available' then false else hard_blocked end,
    last_success_at=case when p_state='available' then now() else last_success_at end
  where provider=p_provider and endpoint=p_endpoint;
end $$;
create function public.reserve_provider_credits(
  p_provider text, p_endpoint text, p_cost numeric, p_per_minute numeric, p_daily numeric,
  p_monthly numeric, p_reserve numeric, p_now timestamptz
) returns boolean language plpgsql security invoker set search_path = '' as $$
declare n_start timestamptz := date_trunc('minute', p_now);
declare d_start timestamptz := date_trunc('day', p_now);
declare m_start timestamptz := date_trunc('month', p_now);
declare n_used numeric; declare d_used numeric; declare m_used numeric; declare blocked boolean;
begin
  if p_cost <= 0 or p_reserve < 0 then raise exception 'invalid provider credit request'; end if;
  perform pg_advisory_xact_lock(hashtext(p_provider), hashtext(p_endpoint));
  select hard_blocked into blocked from public.provider_access
   where provider=p_provider and endpoint=p_endpoint for update;
  if blocked is null or blocked then return false; end if;
  select coalesce(max(credits),0) into n_used from public.provider_quota_usage
   where provider=p_provider and endpoint=p_endpoint and window_kind='minute'
     and window_start=n_start;
  select coalesce(sum(credits),0) into d_used from public.provider_quota_usage
   where provider=p_provider and endpoint=p_endpoint and window_kind='day'
     and window_start=d_start;
  select coalesce(sum(credits),0) into m_used from public.provider_quota_usage
   where provider=p_provider and endpoint=p_endpoint and window_kind='month'
     and window_start=m_start;
  if (p_per_minute is not null and n_used+p_cost > p_per_minute)
     or (p_daily is not null and d_used+p_cost > p_daily-p_reserve)
     or (p_monthly is not null and m_used+p_cost > p_monthly-p_reserve) then return false; end if;
  insert into public.provider_quota_usage(provider,endpoint,window_kind,window_start,window_end,credits,requests)
    values (p_provider,p_endpoint,'minute',n_start,n_start+interval '1 minute',p_cost,1)
    on conflict (provider,endpoint,window_kind,window_start) do update
      set credits=provider_quota_usage.credits+excluded.credits,
          requests=provider_quota_usage.requests+1;
  insert into public.provider_quota_usage(provider,endpoint,window_kind,window_start,window_end,credits,requests)
    values (p_provider,p_endpoint,'day',d_start,d_start+interval '1 day',p_cost,1)
    on conflict (provider,endpoint,window_kind,window_start) do update
      set credits=provider_quota_usage.credits+excluded.credits,
          requests=provider_quota_usage.requests+1;
  insert into public.provider_quota_usage(provider,endpoint,window_kind,window_start,window_end,credits,requests)
    values (p_provider,p_endpoint,'month',m_start,m_start+interval '1 month',p_cost,1)
    on conflict (provider,endpoint,window_kind,window_start) do update
      set credits=provider_quota_usage.credits+excluded.credits,
          requests=provider_quota_usage.requests+1;
  return true;
end $$;
grant execute on function public.record_provider_attempt(text,text,text,text,timestamptz,integer,text,integer,bigint,numeric) to app_backend;
grant execute on function public.reserve_provider_credits(text,text,numeric,numeric,numeric,numeric,numeric,timestamptz) to app_backend;

create or replace view public.provider_quota_status with (security_invoker=true) as
select pa.provider,pa.endpoint,pa.state,pa.hard_blocked,pa.quota,pa.quota_period,
       pa.last_success_at,pa.retry_at,
       coalesce(sum(q.credits) filter(where q.window_kind='day' and
         q.window_start=date_trunc('day',now())),0) day_credits,
       coalesce(sum(q.credits) filter(where q.window_kind='month' and
         q.window_start=date_trunc('month',now())),0) month_credits
from public.provider_access pa left join public.provider_quota_usage q
  on q.provider=pa.provider and q.endpoint=pa.endpoint
group by pa.provider,pa.endpoint;

comment on column public.provider_fetch_audit.job_id is null;
comment on column public.provider_quota_usage.last_job_id is null;
comment on column public.provider_quota_usage.reserved is null;
comment on column public.provider_access.reset_timezone is null;
alter table public.provider_fetch_audit drop column job_id;
alter table public.provider_quota_usage drop column last_job_id;
alter table public.provider_access drop constraint provider_access_last_attempt_state_check;
alter table public.provider_access
  drop column last_attempt_state, drop column last_attempt_at, drop column consecutive_failures;
