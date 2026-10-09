-- D42: a note can say "Customer X 23 % of revenue" and "Customer X 18 % of receivables". The
-- unique key of a disclosure ignored the denominator, so the second overwrote the first's share.
-- The denominator now belongs to the key. Existing rows stay unique under the wider key.
drop index if exists public.relationship_disclosure_unique;
create unique index relationship_disclosure_unique on public.relationship_disclosure
  (ticker, relationship, direction, coalesce(counterparty_key_norm, 'anon:' || md5(passage)),
   coalesce(fiscal_period, ''), source_url, coalesce(denominator_kind, ''));
