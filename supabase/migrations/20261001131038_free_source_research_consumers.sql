-- Structured storage for provider research signals and Alpha Vantage sentiment.
alter table public.source drop constraint if exists source_type_check;
alter table public.source add constraint source_type_check
  check (type in ('rss','page','edgar','finnhub','google_news','alpha_vantage',
                  'macro','edgar_fts','rns','eqs'));

alter table public.news_item add column if not exists sentiment_score numeric
  check (sentiment_score between -1 and 1);
alter table public.news_item add column if not exists sentiment_relevance numeric
  check (sentiment_relevance between 0 and 1);

create table public.company_research_observation (
  id bigint generated always as identity primary key,
  ticker text not null,
  provider text not null check(provider in ('alpha_vantage','pdl','diffbot','epo_ops')),
  dataset text not null,
  metric text not null,
  period_end date,
  value numeric,
  text_value text,
  unit text,
  currency text,
  source_url text not null,
  published_at timestamptz,
  known_at timestamptz not null default now(),
  match_confidence numeric check(match_confidence between 0 and 1),
  raw jsonb not null default '{}',
  check(value is not null or text_value is not null)
);
create index company_research_observation_asof
  on public.company_research_observation(ticker,dataset,metric,known_at desc);
alter table public.company_research_observation enable row level security;
revoke all on public.company_research_observation from anon,authenticated;
grant select on public.company_research_observation to authenticated;
grant select,insert,update,delete on public.company_research_observation to app_backend;
create policy owner_read on public.company_research_observation for select to authenticated
  using ((select private.is_owner()));
create policy backend_all on public.company_research_observation for all to app_backend
  using (true) with check (true);
grant usage,select on sequence public.company_research_observation_id_seq to app_backend;

insert into public.provider_access(provider,endpoint,state,credential_env,quota,quota_period,detail)
values ('alpha_vantage','news_sentiment','access_unverified','ALPHA_VANTAGE_API_KEY',25,'day',
        'owner-key endpoint probe; articles remain sentiment-ranked candidates'),
       ('epo_ops','patent_search','access_unverified','EPO_OPS_CLIENT_ID',null,'week',
        'OAuth and OPS access probe')
on conflict(provider,endpoint) do nothing;
insert into public.provider_endpoint_manifest(provider,endpoint,path_template,response_unit,production_consumer)
values ('alpha_vantage','news_sentiment','/query?function=NEWS_SENTIMENT','articles and ticker relevance/sentiment','ranked news candidates'),
       ('epo_ops','patent_search','/3.2/rest-services/published-data/search','bibliographic records','patent family analysis')
on conflict(provider,endpoint) do nothing;
