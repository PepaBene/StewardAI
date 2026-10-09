-- Rollback for supabase/migrations/20261026100000_relationship_denominator_key.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: where two disclosures differ only in their denominator, the older one is deleted
-- (a confirmed, rejected or disputed one is kept first), so the narrower key can be rebuilt.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
delete from public.relationship_disclosure d using (
  select id, row_number() over (
    partition by ticker, relationship, direction,
      coalesce(counterparty_key_norm, 'anon:' || md5(passage)), coalesce(fiscal_period, ''), source_url
    order by (status in ('confirmed','rejected','disputed')) desc, id desc) rn
  from public.relationship_disclosure) x
 where d.id = x.id and x.rn > 1;
drop index if exists public.relationship_disclosure_unique;
create unique index relationship_disclosure_unique on public.relationship_disclosure
  (ticker, relationship, direction, coalesce(counterparty_key_norm, 'anon:' || md5(passage)),
   coalesce(fiscal_period, ''), source_url);
