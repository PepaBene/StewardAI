-- Align supabase_migrations.schema_migrations with the repo's migration filenames.
-- Metadata only: no application table is touched. Run once in the Supabase SQL editor on the
-- live project (one transaction). After it, `supabase migration list` shows local = remote for
-- every repo migration; the only remote-only rows left are the production maintenance entries
-- (maintenance_*), which have no repo file by design (mark them with `supabase migration repair
-- --status reverted <version>` or add no-op local files if you ever use `supabase db push`).
begin;

-- 1. Same migration, different version: the live row carries the apply time (or an older
--    numbering); the repo file name carries the canonical version.
update supabase_migrations.schema_migrations set version = '20261002100000' where version = '20260930182132' and name = 'watchlist_items';
update supabase_migrations.schema_migrations set version = '20261002110000' where version = '20260930182150' and name = 'insider_filings';
update supabase_migrations.schema_migrations set version = '20261002120000' where version = '20260930182207' and name = 'form13f_manager_coverage';
update supabase_migrations.schema_migrations set version = '20261002130000' where version = '20261001205814' and name = 'source_type_reference';
update supabase_migrations.schema_migrations set version = '20261003100000' where version = '20261002060546' and name = 'fund_look_through_extension';
update supabase_migrations.schema_migrations set version = '20261003110000' where version = '20261002060801' and name = 'fund_probes_nport';
update supabase_migrations.schema_migrations set version = '20261003120000' where version = '20261002061050' and name = 'control_stakes_insider_enrichment';
update supabase_migrations.schema_migrations set version = '20261004110000' where version = '20261002061346' and name = 'free_source_alert_trail';
update supabase_migrations.schema_migrations set version = '20261004120000' where version = '20261002061421' and name = 'free_sources_verification';

-- 2. provider_state_latch was applied in four pieces (three through the tool, the rest by hand):
--    replace the pieces by the one repo migration.
delete from supabase_migrations.schema_migrations
 where name in ('provider_state_latch_columns','provider_state_latch_backup_table','provider_state_latch_backup_rows');

-- 3. Migrations applied by hand in the SQL editor (never recorded).
insert into supabase_migrations.schema_migrations (version, name) values
  ('20261005100000', 'provider_state_latch'),
  ('20261005100100', 'observation_identity_nulls'),
  ('20261005100200', 'lifecycle_evidence_integrity'),
  ('20261005110000', 'macro_trade_correctness'),
  ('20261005120000', 'prices_stream_fixes'),
  ('20261005130000', 'fund_statement_store'),
  ('20261005140000', 'graph_integrity'),
  ('20261005150000', 'funds_stream_hardening'),
  ('20261005160000', 'free_source_alert_gates'),
  ('20261005170000', 'verification_gate_hardening')
on conflict (version) do nothing;

-- 4. Check: every repo version below must now be present exactly once.
do $$
declare missing int;
begin
  select count(*) into missing from (values
    ('20260925141607','app_backend_role'),
    ('20260925141614','enable_pgmq'),
    ('20260925141643','foundation_tables'),
    ('20260925153708','portfolio_core'),
    ('20260925153718','pgmq_jobs_queue'),
    ('20260925153814','seed_placeholder_strategy'),
    ('20260925154220','register_owner'),
    ('20260925185233','phase2_extraction'),
    ('20260925200207','phase3_news'),
    ('20260926090000','phase1b_phase2_followups'),
    ('20260926100000','phase2b_reference_data'),
    ('20260926110000','phase3_completion'),
    ('20260927090000','phase4_verification'),
    ('20260928090000','phase5_research'),
    ('20260929090000','prompts_config'),
    ('20260929110000','news_search'),
    ('20260929120000','chat_message_length'),
    ('20260930120000','escalation_lifecycle'),
    ('20260930120100','job_attempts'),
    ('20260930182226','free_sources_phases_0_2'),
    ('20260930182242','free_sources_completion'),
    ('20260930182259','free_sources_operational'),
    ('20261001100000','transaction_fees'),
    ('20261001100100','macro_cnb_source'),
    ('20261001103217','free_sources_phases_3_5'),
    ('20261001110000','etf_look_through'),
    ('20261001111937','price_bar_basis_key'),
    ('20261001112030','alpha_vantage_shared_budget'),
    ('20261001115301','free_sources_fk_indexes'),
    ('20261001120000','valuation'),
    ('20261001130000','sec_xbrl'),
    ('20261001131038','free_source_research_consumers'),
    ('20261001132609','alpha_vantage_exposure_gates'),
    ('20261001140000','intraday_prices'),
    ('20261002100000','watchlist_items'),
    ('20261002110000','insider_filings'),
    ('20261002120000','form13f_manager_coverage'),
    ('20261002130000','source_type_reference'),
    ('20261003100000','fund_look_through_extension'),
    ('20261003110000','fund_probes_nport'),
    ('20261003120000','control_stakes_insider_enrichment'),
    ('20261004110000','free_source_alert_trail'),
    ('20261004120000','free_sources_verification'),
    ('20261005100000','provider_state_latch'),
    ('20261005100100','observation_identity_nulls'),
    ('20261005100200','lifecycle_evidence_integrity'),
    ('20261005110000','macro_trade_correctness'),
    ('20261005120000','prices_stream_fixes'),
    ('20261005130000','fund_statement_store'),
    ('20261005140000','graph_integrity'),
    ('20261005150000','funds_stream_hardening'),
    ('20261005160000','free_source_alert_gates'),
    ('20261005170000','verification_gate_hardening')
  ) r(version,name)
  where not exists (select 1 from supabase_migrations.schema_migrations s where s.version = r.version and s.name = r.name);
  if missing > 0 then raise exception 'migration history still differs from the repo (% missing)', missing; end if;
end $$;

commit;
