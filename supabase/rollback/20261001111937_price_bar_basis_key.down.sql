-- Rollback for supabase/migrations/20261001111937_price_bar_basis_key.sql (plan D8.4).
-- Generated from a before/after diff of the public catalog and proven by
-- apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is run, and the
-- catalog fingerprint (tables, columns, constraints, indexes, functions, policies, grants, seed
-- rows) must equal the one taken before the migration. Run it in a transaction, newest migration
-- first. The migration file itself is never edited.
-- PRECONDITION / DATA LOSS RISK: the restored key is (symbol, date, source), so a source may keep
-- only ONE bar per symbol and day.  If a source holds both a raw and an adjusted bar for the same
-- symbol and day this rollback STOPS with an error and changes nothing.  To roll back anyway and
-- keep the adjusted bar (discarding the raw one, which cannot be restored by the forward migration)
-- run first, in the same session:  set steward.rollback_keep_adjusted = 'on';
-- Proven with data by apps/backend/tests/test_phase8_rollback_data.py.

do $$ begin
  if exists (
    select 1 from public.price_bar
     group by symbol, date, source having count(*) > 1
  ) then
    if coalesce(current_setting('steward.rollback_keep_adjusted', true), 'off') = 'on' then
      delete from public.price_bar r
       using public.price_bar a
       where r.symbol = a.symbol and r.date = a.date and r.source = a.source
         and r.bar_type = 'raw' and a.bar_type = 'adjusted';
    else
      raise exception 'rollback precondition failed: public.price_bar has raw and adjusted bars for the same (symbol, date, source); the old key allows one. Re-run after set steward.rollback_keep_adjusted = ''on'' to keep the adjusted bars and discard the raw ones, or dedupe by hand first';
    end if;
  end if;
end $$;
alter table public.price_bar drop constraint if exists price_bar_pkey;
alter table public.price_bar add constraint price_bar_pkey PRIMARY KEY (symbol, date, source);
