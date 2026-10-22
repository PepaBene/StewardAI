-- The HTTP validators of each IR page's last fetch, so `ir_releases` asks the server whether the
-- page changed (If-None-Match / If-Modified-Since) instead of downloading and hashing it daily.
alter table public.issuer_ir_source
  add column if not exists last_etag text,
  add column if not exists last_modified_header text;
