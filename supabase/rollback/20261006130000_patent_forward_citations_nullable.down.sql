-- Rollback for supabase/migrations/20261006130000_patent_forward_citations_nullable.sql.
-- Unknown (NULL) forward citations become 0 again (the pre-migration representation); the
-- unknown/zero distinction is lost. Run in a transaction, newest migration first.
-- Proven by apps/backend/tests/test_graph_coverage_fixpass.py (unknown forward citations are stored
-- as NULL) and the up/down migration round trip in test_lifecycle_phase8.py.

update public.patent_family set citations_forward = 0 where citations_forward is null;
alter table public.patent_family alter column citations_forward set default 0;
alter table public.patent_family alter column citations_forward set not null;
