-- pgmq: the Postgres-native queue for event-driven jobs (report date reached, price
-- move, severity-3 cluster, on-demand research and chat requests from the API).
-- Supabase ships the extension; this only enables it. Queues are created by the
-- migrations of the phases that first use them.

create extension if not exists pgmq;

-- The backend sends to and reads from queues through pgmq's SQL functions.
grant usage on schema pgmq to app_backend;
grant select, insert, update, delete on all tables in schema pgmq to app_backend;
grant usage, select on all sequences in schema pgmq to app_backend;
grant execute on all functions in schema pgmq to app_backend;
-- Queues created later (pgmq.create) get tables in the pgmq schema: cover them too.
alter default privileges in schema pgmq
  grant select, insert, update, delete on tables to app_backend;
alter default privileges in schema pgmq
  grant usage, select on sequences to app_backend;
