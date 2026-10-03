-- F5-06: a family whose source cannot supply forward citations (EPO OPS bibliographic data)
-- stores NULL ("unknown"), not 0 ("no one cites it"). Additive: the column loses NOT NULL and
-- its default; existing readers coalesce. Rows from the epo_ops source stored 0 only because
-- OPS cannot provide the figure (raw.forward_citations says so), so those zeros become NULL.
-- Rows from any other source keep their value.

alter table public.patent_family alter column citations_forward drop not null;
alter table public.patent_family alter column citations_forward drop default;
update public.patent_family set citations_forward = null
  where source = 'epo_ops' and citations_forward = 0;
