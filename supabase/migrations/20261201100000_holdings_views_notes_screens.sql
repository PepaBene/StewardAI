-- Insight plan I9 and I10.
--
-- 1. `holding_note` (I9.6): the owner's free-text notes per ticker, shown in the holdings drawer
--    and searchable from chat. Never rewritten by AI.
-- 2. Config domains `holdings_views` (I9.3: presets and the custom column view) and
--    `screen_templates` (I10.7: curated starter screens), versioned like every other domain.
-- 3. `screen_parse_log` (I10.2): each plain-English screen request with the draft it produced,
--    so a misparse can be traced; the LLM call itself is also budgeted and logged by steward/llm.

create table public.holding_note (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  ticker text not null check (length(trim(ticker)) between 1 and 40),
  body text not null check (length(trim(body)) between 1 and 20000),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index holding_note_owner_ticker on public.holding_note (owner_id, ticker, updated_at desc);
create index holding_note_search on public.holding_note using gin (to_tsvector('simple', body));
create trigger holding_note_set_updated_at
  before update on public.holding_note
  for each row execute function private.set_updated_at();

create table public.screen_parse_log (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  text text not null check (length(trim(text)) between 1 and 1000),
  draft jsonb not null,
  unmatched text[] not null default '{}',
  refused boolean not null default false,
  model text,
  method_version text not null,
  registry_hash text not null,
  created_at timestamptz not null default now()
);
create index screen_parse_log_owner on public.screen_parse_log (owner_id, created_at desc);

do $$
declare t text;
begin
  foreach t in array array['holding_note', 'screen_parse_log'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on public.%I from anon', t);
    execute format('grant select on public.%I to authenticated', t);
    execute format(
      'create policy %I on public.%I for select to authenticated using (owner_id = (select auth.uid()))',
      t || '_owner_read', t);
    execute format('create policy %I on public.%I for all to app_backend using (true) with check (true)',
      t || '_backend', t);
    execute format('grant usage, select on sequence public.%I to app_backend', t || '_id_seq');
  end loop;
end $$;
grant select, insert, update, delete on public.holding_note to app_backend;
grant select, insert on public.screen_parse_log to app_backend;

comment on table public.holding_note is
  'Owner notes per ticker (insight I9.6): free text, shown in the holdings drawer and searchable from chat; never rewritten by AI.';
comment on table public.screen_parse_log is
  'Plain-English screen requests and the draft filters they produced (insight I10.2); the screen is never run by the parse.';

alter table public.config_version drop constraint if exists config_version_domain_known;
alter table public.config_version add constraint config_version_domain_known check (domain in (
  'strategy', 'theses', 'holding_profiles', 'news_sources', 'event_rules',
  'notifications', 'research_profiles', 'verification', 'jobs', 'budget', 'prompts',
  'performance', 'checks', 'holdings_views', 'screen_templates'
));
