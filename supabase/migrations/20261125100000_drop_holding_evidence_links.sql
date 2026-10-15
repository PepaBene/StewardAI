-- A 13F position needs no evidence link of its own: all positions of a filing were read from
-- the filing's one document, and `form13f_filing` already links that filing to the same document
-- version (checked for all 30,136 accessions: same version, known no later). The per-position
-- rows were 443K of 584K rows in source_record_evidence (about 120 MB with indexes).
--
-- Apply only after the backend that resolves a position's evidence through its filing
-- (`record_evidence_as_of` in sources/documents.py) is deployed, and run VACUUM FULL on
-- source_record_evidence afterwards to give the space back to the database.
--
-- source_record_evidence is append-only; this is a deliberate maintenance change, so lift the
-- guard for this transaction only (see 20261005100200_lifecycle_evidence_integrity.sql).
select set_config('steward.allow_append_only_changes', 'on', true);
delete from public.source_record_evidence where domain_table = 'institutional_holding';
