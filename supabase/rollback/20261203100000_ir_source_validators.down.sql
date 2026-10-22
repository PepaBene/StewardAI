-- Rolls back 20261203100000_ir_source_validators.sql.
alter table public.issuer_ir_source
  drop column if exists last_etag,
  drop column if exists last_modified_header;
