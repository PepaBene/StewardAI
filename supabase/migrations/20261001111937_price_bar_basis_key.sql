-- A source must not overwrite a raw bar with an adjusted bar (or vice versa).
alter table public.price_bar drop constraint if exists price_bar_pkey;
alter table public.price_bar add primary key (symbol,date,source,bar_type);
