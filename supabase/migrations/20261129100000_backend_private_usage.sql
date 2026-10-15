-- The worker runs the nightly retention itself (a plain Postgres has no pg_cron), so app_backend
-- must be able to reach private.run_retention(). It had EXECUTE on the function but no USAGE on
-- the schema. Nothing else in `private` is granted to it.
grant usage on schema private to app_backend;
