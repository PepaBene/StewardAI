-- Audit items A6, B7, B9 (core stream).
--
-- A6: record_provider_attempt overwrote provider_access.state on EVERY attempt, so one 429, 5xx,
--     timeout or empty 2xx closed the gate that collectors read (`state = 'available'`) until a
--     monthly, off-by-default audit re-probed.  `state` now holds only the durable access
--     verdict (available / paid_only / access_unverified / disputed).  Transient outcomes are
--     kept beside it (last_attempt_state, consecutive_failures, retry_at) and the gate is
--     `public.provider_gate_open`: not hard_blocked, state in (available, access_unverified)
--     and no provider Retry-After window pending.  reserve_provider_credits enforces the same.
-- B7: rows seeded `available` without any probe become `access_unverified`; they turn
--     `available` on the first recorded success (a collector run or an audit probe).
-- B9: usage ledger job ids, honored reset_timezone for day/month windows.

alter table public.provider_access
  add column if not exists last_attempt_state text,
  add column if not exists last_attempt_at timestamptz,
  add column if not exists consecutive_failures integer not null default 0
    check (consecutive_failures >= 0);
alter table public.provider_access
  add constraint provider_access_last_attempt_state_check
  check (last_attempt_state is null or last_attempt_state in ('available','not_covered',
    'paid_only','access_unverified','quota_exhausted','temporarily_failed','disputed'));

alter table public.provider_fetch_audit add column if not exists job_id bigint;
alter table public.provider_quota_usage add column if not exists last_job_id bigint;
comment on column public.provider_fetch_audit.job_id is
  'job.id of the run that made the attempt (no FK: the audit trail outlives pruned job rows)';
comment on column public.provider_quota_usage.last_job_id is
  'job.id that last reserved credits in this window (no FK: window rows outlive job rows)';
comment on column public.provider_quota_usage.reserved is
  'unused: the reserve held back is computed per reservation (Budget.reserve x priority)';
comment on column public.provider_access.reset_timezone is
  'IANA zone in which the provider resets day/month allowances; UTC when null';

-- Keep what the two data corrections below overwrite, so the rollback can restore it and the
-- owner can see which seeded claims were never evidence.
create table public.provider_access_state_backup (
  provider text not null,
  endpoint text not null,
  previous_state text not null,
  previous_detail text,
  reason text not null check (reason in ('seeded_without_probe','transient_state_latched')),
  recorded_at timestamptz not null default now(),
  primary key (provider, endpoint, reason),
  foreign key (provider, endpoint) references public.provider_access(provider, endpoint)
    on update cascade on delete cascade
);
alter table public.provider_access_state_backup enable row level security;
revoke all on public.provider_access_state_backup from anon, authenticated;
grant select on public.provider_access_state_backup to authenticated;
grant select, insert, update, delete on public.provider_access_state_backup to app_backend;
create policy owner_read on public.provider_access_state_backup for select to authenticated
  using ((select private.is_owner()));
create policy backend_all on public.provider_access_state_backup for all to app_backend
  using (true) with check (true);

-- Heal rows the old function latched.
insert into public.provider_access_state_backup (provider, endpoint, previous_state, previous_detail, reason)
select provider, endpoint, state, detail, 'transient_state_latched' from public.provider_access
 where state in ('quota_exhausted','temporarily_failed','not_covered');
update public.provider_access set
  state = case when last_success_at is not null and not hard_blocked then 'available'
               else 'access_unverified' end
 where state in ('quota_exhausted','temporarily_failed','not_covered');

-- B7: no probe, no `available`.
update public.provider_access pa set last_success_at = a.latest
  from (select provider, endpoint, max(attempted_at) latest from public.provider_fetch_audit
         where state = 'available' group by provider, endpoint) a
 where pa.provider = a.provider and pa.endpoint = a.endpoint and pa.last_success_at is null;
insert into public.provider_access_state_backup (provider, endpoint, previous_state, previous_detail, reason)
select provider, endpoint, state, detail, 'seeded_without_probe' from public.provider_access
 where state = 'available' and last_success_at is null and tested_at is null;
update public.provider_access set state = 'access_unverified',
  detail = coalesce(nullif(detail, ''), 'existing collector') ||
           ' (seeded without a probe; turns available on the first recorded success)'
 where state = 'available' and last_success_at is null and tested_at is null;

drop function public.record_provider_attempt(text,text,text,text,timestamptz,integer,text,
  integer,bigint,numeric);
create function public.record_provider_attempt(
  p_provider text, p_endpoint text, p_state text, p_detail text, p_retry_at timestamptz,
  p_status_code integer, p_request_id text, p_elapsed_ms integer,
  p_response_bytes bigint, p_credits numeric, p_job_id bigint default null
) returns void language plpgsql security invoker set search_path = '' as $$
begin
  insert into public.provider_fetch_audit
    (provider,endpoint,state,status_code,request_id,elapsed_ms,response_bytes,credits,retry_at,
     error_redacted,job_id)
  values (p_provider,p_endpoint,p_state,p_status_code,p_request_id,p_elapsed_ms,
          p_response_bytes,p_credits,p_retry_at,p_detail,p_job_id);
  update public.provider_access set
    last_attempt_state = p_state,
    last_attempt_at = now(),
    status_code = p_status_code,
    detail = p_detail,
    tested_at = now(),
    updated_at = now(),
    -- Only durable verdicts change `state`; a 429, 5xx, timeout or empty 2xx never does.
    state = case when p_state in ('available','paid_only','access_unverified','disputed')
                 then p_state else state end,
    hard_blocked = case when p_state = 'paid_only'
                             or (p_state = 'access_unverified' and p_status_code in (401,403))
                        then true
                        when p_state = 'available' then false else hard_blocked end,
    last_success_at = case when p_state = 'available' then now() else last_success_at end,
    -- A success clears the wait; a failure keeps the later of the old and the new window.
    retry_at = case when p_state = 'available' then null
                    when p_retry_at is null then retry_at
                    else greatest(coalesce(retry_at, p_retry_at), p_retry_at) end,
    consecutive_failures = case when p_state in ('available','not_covered') then 0
                                when p_state in ('quota_exhausted','temporarily_failed')
                                then consecutive_failures + 1 else consecutive_failures end
  where provider = p_provider and endpoint = p_endpoint;
end $$;

-- The one definition of "may a collector call this endpoint now".
create function public.provider_gate_open(
  p_provider text, p_endpoint text, p_now timestamptz default now()
) returns boolean language sql stable security invoker set search_path = '' as $$
  select coalesce((
    select not pa.hard_blocked
       and pa.state in ('available','access_unverified')
       and (pa.retry_at is null or pa.retry_at <= p_now)
      from public.provider_access pa
     where pa.provider = p_provider and pa.endpoint = p_endpoint), false)
$$;

drop function public.reserve_provider_credits(text,text,numeric,numeric,numeric,numeric,
  numeric,timestamptz);
create function public.reserve_provider_credits(
  p_provider text, p_endpoint text, p_cost numeric, p_per_minute numeric, p_daily numeric,
  p_monthly numeric, p_reserve numeric, p_now timestamptz, p_job_id bigint default null
) returns boolean language plpgsql security invoker set search_path = '' as $$
declare tz text;
declare local_now timestamp;
declare n_start timestamptz := date_trunc('minute', p_now);
declare d_start timestamptz; declare d_end timestamptz;
declare m_start timestamptz; declare m_end timestamptz;
declare n_used numeric; declare d_used numeric; declare m_used numeric;
declare blocked boolean; declare wait_until timestamptz;
begin
  if p_cost <= 0 or p_reserve < 0 then raise exception 'invalid provider credit request'; end if;
  perform pg_advisory_xact_lock(hashtext(p_provider), hashtext(p_endpoint));
  select hard_blocked, retry_at, coalesce(reset_timezone, 'UTC')
    into blocked, wait_until, tz from public.provider_access
   where provider=p_provider and endpoint=p_endpoint for update;
  if blocked is null or blocked then return false; end if;
  -- A provider-supplied Retry-After / backoff window is binding (A6).
  if wait_until is not null and wait_until > p_now then return false; end if;
  if not exists (select 1 from pg_catalog.pg_timezone_names where name = tz) then tz := 'UTC'; end if;
  local_now := p_now at time zone tz;
  d_start := date_trunc('day', local_now) at time zone tz;
  d_end := (date_trunc('day', local_now) + interval '1 day') at time zone tz;
  m_start := date_trunc('month', local_now) at time zone tz;
  m_end := (date_trunc('month', local_now) + interval '1 month') at time zone tz;
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
  insert into public.provider_quota_usage
    (provider,endpoint,window_kind,window_start,window_end,credits,requests,last_job_id)
    values (p_provider,p_endpoint,'minute',n_start,n_start+interval '1 minute',p_cost,1,p_job_id)
    on conflict (provider,endpoint,window_kind,window_start) do update
      set credits=provider_quota_usage.credits+excluded.credits,
          requests=provider_quota_usage.requests+1,
          last_job_id=coalesce(excluded.last_job_id, provider_quota_usage.last_job_id);
  insert into public.provider_quota_usage
    (provider,endpoint,window_kind,window_start,window_end,credits,requests,last_job_id)
    values (p_provider,p_endpoint,'day',d_start,d_end,p_cost,1,p_job_id)
    on conflict (provider,endpoint,window_kind,window_start) do update
      set credits=provider_quota_usage.credits+excluded.credits,
          requests=provider_quota_usage.requests+1,
          last_job_id=coalesce(excluded.last_job_id, provider_quota_usage.last_job_id);
  insert into public.provider_quota_usage
    (provider,endpoint,window_kind,window_start,window_end,credits,requests,last_job_id)
    values (p_provider,p_endpoint,'month',m_start,m_end,p_cost,1,p_job_id)
    on conflict (provider,endpoint,window_kind,window_start) do update
      set credits=provider_quota_usage.credits+excluded.credits,
          requests=provider_quota_usage.requests+1,
          last_job_id=coalesce(excluded.last_job_id, provider_quota_usage.last_job_id);
  return true;
end $$;

-- Quota status honors the provider's reset zone as well.
create or replace view public.provider_quota_status with (security_invoker=true) as
select pa.provider,pa.endpoint,pa.state,pa.hard_blocked,pa.quota,pa.quota_period,
       pa.last_success_at,pa.retry_at,
       coalesce(sum(q.credits) filter(where q.window_kind='day' and
         q.window_start=date_trunc('day', now() at time zone coalesce(pa.reset_timezone,'UTC'))
                        at time zone coalesce(pa.reset_timezone,'UTC')),0) day_credits,
       coalesce(sum(q.credits) filter(where q.window_kind='month' and
         q.window_start=date_trunc('month', now() at time zone coalesce(pa.reset_timezone,'UTC'))
                        at time zone coalesce(pa.reset_timezone,'UTC')),0) month_credits
from public.provider_access pa left join public.provider_quota_usage q
  on q.provider=pa.provider and q.endpoint=pa.endpoint
group by pa.provider,pa.endpoint;

grant execute on function public.record_provider_attempt(text,text,text,text,timestamptz,
  integer,text,integer,bigint,numeric,bigint) to app_backend;
grant execute on function public.reserve_provider_credits(text,text,numeric,numeric,numeric,
  numeric,numeric,timestamptz,bigint) to app_backend;
grant execute on function public.provider_gate_open(text,text,timestamptz) to app_backend;
