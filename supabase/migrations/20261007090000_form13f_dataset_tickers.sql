-- form13f_dataset.tickers: the holdings a data set was searched for. A holding bought (or whose
-- CUSIP was found) after a data set was loaded makes the 13F job read that data set again.
--
-- Existing data sets are marked with the holdings they actually kept rows for, so a holding
-- with no rows in one (e.g. an ADR with no CUSIP until now) is looked for once more.

alter table public.form13f_dataset add column if not exists tickers text[];

update public.form13f_dataset d
   set tickers = coalesce(
         (select array_agg(distinct h.ticker order by h.ticker)
            from public.form13f_filing f
            join public.institutional_holding h on h.accession = f.accession
           where f.dataset_url = d.url and h.ticker is not null),
         '{}');
