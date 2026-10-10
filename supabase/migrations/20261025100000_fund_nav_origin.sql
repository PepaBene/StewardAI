-- F8: who wrote a NAV point. The issuer sync revises only rows it wrote itself; an owner entry
-- that cites the issuer's page must not be superseded by the next sync. Rows that exist before
-- this migration have an unknown writer (null) and are protected like owner rows.
alter table public.fund_nav_point
  add column origin text check (origin in ('issuer', 'owner'));

comment on column public.fund_nav_point.origin is
  'Writer of the row: issuer (automatic sync), owner (entered by hand) or null (written before origin was recorded; treated as the owner''s).';
