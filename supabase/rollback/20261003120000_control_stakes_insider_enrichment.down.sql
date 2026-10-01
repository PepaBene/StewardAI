-- Rollback for supabase/migrations/20261003120000_control_stakes_insider_enrichment.sql (plan D8.4).
-- Generated from a before/after diff of the public catalog and proven by
-- apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is run, and the
-- catalog fingerprint (tables, columns, constraints, indexes, functions, policies, grants, seed
-- rows) must equal the one taken before the migration. Run it in a transaction, newest migration
-- first. The migration file itself is never edited.
-- DATA LOSS: drops these tables and every row in them: control_stake_change, control_stake_filing, control_stake_holder, control_stake_issuer_link.
-- DATA LOSS: drops these columns and their values: insider_filing.aff10b5_one, insider_filing.amendment_link_status, insider_filing.amends_accession, insider_filing.enriched_at, insider_filing.footnotes, insider_filing.is_amendment, insider_filing.joint_owner_count, insider_filing.original_submission_date, insider_filing.remarks, insider_filing.reporting_owner_cik, insider_filing.superseded_by_accession, insider_transaction.classification_basis, insider_transaction.deemed_execution_date, insider_transaction.economic_class, insider_transaction.equity_swap_involved, insider_transaction.exercisable_date, insider_transaction.exercise_price, insider_transaction.expiration_date, insider_transaction.footnote_ids, insider_transaction.footnote_text, insider_transaction.rule_10b5_1_adopted, insider_transaction.rule_10b5_1_basis, insider_transaction.rule_10b5_1_plan, insider_transaction.underlying_security_title, insider_transaction.underlying_shares.
-- PRECONDITION: no row may use a value the restored CHECK constraint (source_record_evidence_domain_table_check) forbids; the rollback stops with a clear error before changing anything if one does.

do $$ begin
  if exists (select 1 from public.source_record_evidence where not ((domain_table = ANY (ARRAY['fact'::text, 'form13f_filing'::text, 'insider_filing'::text, 'insider_transaction'::text, 'institutional_holding'::text, 'news_item'::text, 'nport_filing'::text, 'nport_holding'::text])))) then
    raise exception 'rollback precondition failed: public.source_record_evidence holds rows that violate the restored constraint source_record_evidence_domain_table_check; delete or migrate those rows first (select * from public.source_record_evidence where not ((domain_table = ANY (ARRAY[''fact''::text, ''form13f_filing''::text, ''insider_filing''::text, ''insider_transaction''::text, ''institutional_holding''::text, ''news_item''::text, ''nport_filing''::text, ''nport_holding''::text]))))';
  end if;
end $$;
alter table public.insider_filing drop constraint if exists insider_filing_amendment_link_status_check;
alter table public.insider_filing drop constraint if exists insider_filing_amends_accession_fkey;
alter table public.insider_filing drop constraint if exists insider_filing_reporting_owner_cik_check;
alter table public.insider_filing drop constraint if exists insider_filing_superseded_by_accession_fkey;
alter table public.insider_transaction drop constraint if exists insider_transaction_economic_class_check;
alter table public.insider_transaction drop constraint if exists insider_transaction_rule_10b5_1_basis_check;
alter table public.source_record_evidence drop constraint if exists source_record_evidence_domain_table_check;
drop index if exists public.insider_filing_amends;
drop index if exists public.insider_filing_owner;
drop index if exists public.insider_filing_superseded_by;
drop index if exists public.insider_transaction_class;
alter table public.insider_filing drop column if exists aff10b5_one cascade;
alter table public.insider_filing drop column if exists amendment_link_status cascade;
alter table public.insider_filing drop column if exists amends_accession cascade;
alter table public.insider_filing drop column if exists enriched_at cascade;
alter table public.insider_filing drop column if exists footnotes cascade;
alter table public.insider_filing drop column if exists is_amendment cascade;
alter table public.insider_filing drop column if exists joint_owner_count cascade;
alter table public.insider_filing drop column if exists original_submission_date cascade;
alter table public.insider_filing drop column if exists remarks cascade;
alter table public.insider_filing drop column if exists reporting_owner_cik cascade;
alter table public.insider_filing drop column if exists superseded_by_accession cascade;
alter table public.insider_transaction drop column if exists classification_basis cascade;
alter table public.insider_transaction drop column if exists deemed_execution_date cascade;
alter table public.insider_transaction drop column if exists economic_class cascade;
alter table public.insider_transaction drop column if exists equity_swap_involved cascade;
alter table public.insider_transaction drop column if exists exercisable_date cascade;
alter table public.insider_transaction drop column if exists exercise_price cascade;
alter table public.insider_transaction drop column if exists expiration_date cascade;
alter table public.insider_transaction drop column if exists footnote_ids cascade;
alter table public.insider_transaction drop column if exists footnote_text cascade;
alter table public.insider_transaction drop column if exists rule_10b5_1_adopted cascade;
alter table public.insider_transaction drop column if exists rule_10b5_1_basis cascade;
alter table public.insider_transaction drop column if exists rule_10b5_1_plan cascade;
alter table public.insider_transaction drop column if exists underlying_security_title cascade;
alter table public.insider_transaction drop column if exists underlying_shares cascade;
alter table public.source_record_evidence add constraint source_record_evidence_domain_table_check CHECK ((domain_table = ANY (ARRAY['fact'::text, 'form13f_filing'::text, 'insider_filing'::text, 'insider_transaction'::text, 'institutional_holding'::text, 'news_item'::text, 'nport_filing'::text, 'nport_holding'::text])));
drop table if exists public.control_stake_change cascade;
drop table if exists public.control_stake_filing cascade;
drop table if exists public.control_stake_holder cascade;
drop table if exists public.control_stake_issuer_link cascade;
