-- Operational completion for Phase 0-2 consumers and existing domain records.

alter table public.trade_observation
  add column trade_currency text not null default 'USD'
  check (trade_currency ~ '^[A-Z]{3}$');

create table public.listing_lifecycle_snapshot (
  id bigint generated always as identity primary key,
  isin text not null,
  mic text not null,
  publication_date date,
  status text,
  terminated_at timestamptz,
  issuer_lei text,
  currency text,
  source_url text not null,
  content_hash text not null,
  known_at timestamptz not null,
  raw jsonb not null,
  unique(isin,mic,publication_date,content_hash)
);
create index listing_lifecycle_asof
  on public.listing_lifecycle_snapshot(isin,mic,known_at desc);

create table public.legal_entity_snapshot (
  id bigint generated always as identity primary key,
  lei text not null,
  known_at timestamptz not null,
  valid_from date,
  valid_to date,
  legal_name text,
  other_names jsonb not null default '[]',
  entity_status text,
  registration_status text,
  successor_lei text,
  source_url text not null,
  content_hash text not null,
  raw jsonb not null,
  unique(lei,content_hash)
);
create index legal_entity_snapshot_asof
  on public.legal_entity_snapshot(lei,known_at desc);

alter table public.source_observation add constraint source_observation_access_state_check
  check (access_state in ('available','not_covered','paid_only','access_unverified',
    'quota_exhausted','temporarily_failed','disputed'));

insert into public.provider_access(provider,endpoint,state,credential_env,detail,hard_blocked)
values
 ('fmp','sector_snapshot','access_unverified','FMP_API_KEY','owner-key probe required',false),
 ('finnhub','etf_holdings','access_unverified','FINNHUB_API_KEY','owner-key probe required',false),
 ('finnhub','index_constituents','access_unverified','FINNHUB_API_KEY','owner-key probe required',false),
 ('finnhub','forward_estimates','access_unverified','FINNHUB_API_KEY','owner-key probe required',false),
 ('twelve_data','splits','access_unverified','TWELVE_DATA_API_KEY','owner-key probe required',false)
on conflict(provider,endpoint) do nothing;
insert into public.provider_endpoint_manifest
  (provider,endpoint,path_template,response_unit,production_consumer)
values
 ('fmp','sector_snapshot','/stable/sector-performance-snapshot','sector fields','sector comparison'),
 ('finnhub','etf_holdings','/api/v1/etf/holdings','holding fields','coverage gap'),
 ('finnhub','index_constituents','/api/v1/index/constituents','constituent fields','coverage gap'),
 ('finnhub','forward_estimates','/api/v1/stock/revenue-estimate','estimate fields','coverage gap'),
 ('twelve_data','splits','/splits','action fields','corporate-action gap')
on conflict(provider,endpoint) do nothing;

create table public.source_record_evidence (
  id bigint generated always as identity primary key,
  domain_table text not null check(domain_table in
    ('news_item','fact','form13f_filing','institutional_holding','insider_filing','insider_transaction')),
  record_key text not null,
  document_version_id bigint not null references public.source_document_version(id),
  evidence_id bigint references public.source_evidence(id),
  known_at timestamptz not null,
  unique(domain_table,record_key,document_version_id)
);
create index source_record_evidence_asof
  on public.source_record_evidence(domain_table,record_key,known_at desc);

create table public.provider_benchmark_result (
  id bigint generated always as identity primary key,
  provider text not null, endpoint text not null, corpus_key text not null,
  tested_at timestamptz not null default now(), latency_ms integer not null check(latency_ms >= 0),
  state text not null, matched boolean, accurate boolean, incremental_coverage boolean,
  reference_source text, mismatch_reason text, response_fields jsonb not null default '[]',
  foreign key(provider,endpoint) references public.provider_access(provider,endpoint)
);
create index provider_benchmark_rank
  on public.provider_benchmark_result(provider,endpoint,incremental_coverage,accurate);

create or replace function public.check_exposure_total() returns trigger
language plpgsql security invoker set search_path='' as $$
declare exceeds_limit boolean;
begin
  if new.valid_to is not null and new.valid_to < new.valid_from then
    raise exception 'exposure valid_to precedes valid_from';
  end if;
  select exists (
    select 1
      from (
        select new.valid_from as day
        union
        select em.valid_from
          from public.exposure_mapping em
         where em.issuer_id=new.issuer_id
           and (tg_op <> 'UPDATE' or em.id <> new.id)
           and em.valid_from >= new.valid_from
           and (new.valid_to is null or em.valid_from <= new.valid_to)
           and (em.valid_to is null or em.valid_to >= new.valid_from)
      ) dates
     where (new.valid_to is null or dates.day <= new.valid_to)
       and new.weight + coalesce((
         select sum(em.weight)
           from public.exposure_mapping em
          where em.issuer_id=new.issuer_id
            and (tg_op <> 'UPDATE' or em.id <> new.id)
            and em.valid_from <= dates.day
            and (em.valid_to is null or em.valid_to >= dates.day)
       ),0) > 1
  ) into exceeds_limit;
  if exceeds_limit then raise exception 'mapped exposure exceeds 100%%'; end if;
  return new;
end $$;
create trigger exposure_total_guard before insert or update on public.exposure_mapping
  for each row execute function public.check_exposure_total();

create view public.provider_quota_status with (security_invoker=true) as
select pa.provider,pa.endpoint,pa.state,pa.hard_blocked,pa.quota,pa.quota_period,
       pa.last_success_at,pa.retry_at,
       coalesce(sum(q.credits) filter(where q.window_kind='day' and
         q.window_start=date_trunc('day',now())),0) day_credits,
       coalesce(sum(q.credits) filter(where q.window_kind='month' and
         q.window_start=date_trunc('month',now())),0) month_credits
from public.provider_access pa left join public.provider_quota_usage q
  on q.provider=pa.provider and q.endpoint=pa.endpoint
group by pa.provider,pa.endpoint;

alter table public.source_record_evidence enable row level security;
alter table public.provider_benchmark_result enable row level security;
alter table public.listing_lifecycle_snapshot enable row level security;
alter table public.legal_entity_snapshot enable row level security;
revoke all on public.source_record_evidence,public.provider_benchmark_result from anon,authenticated;
revoke all on public.listing_lifecycle_snapshot,public.legal_entity_snapshot from anon,authenticated;
grant select on public.source_record_evidence,public.provider_benchmark_result to authenticated;
grant select on public.listing_lifecycle_snapshot,public.legal_entity_snapshot to authenticated;
grant select,insert,update,delete on public.source_record_evidence,public.provider_benchmark_result to app_backend;
grant select,insert,update,delete on public.listing_lifecycle_snapshot,public.legal_entity_snapshot to app_backend;
grant select on public.provider_quota_status to authenticated,app_backend;
create policy owner_read on public.source_record_evidence for select to authenticated
  using ((select private.is_owner()));
create policy backend_all on public.source_record_evidence for all to app_backend
  using (true) with check (true);
create policy owner_read on public.provider_benchmark_result for select to authenticated
  using ((select private.is_owner()));
create policy backend_all on public.provider_benchmark_result for all to app_backend
  using (true) with check (true);
create policy owner_read on public.listing_lifecycle_snapshot for select to authenticated
  using ((select private.is_owner()));
create policy backend_all on public.listing_lifecycle_snapshot for all to app_backend
  using (true) with check (true);
create policy owner_read on public.legal_entity_snapshot for select to authenticated
  using ((select private.is_owner()));
create policy backend_all on public.legal_entity_snapshot for all to app_backend
  using (true) with check (true);
grant usage,select on all sequences in schema public to app_backend;
