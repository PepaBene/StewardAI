-- Rebuild the per-position links from the filing links (same document version and known_at).
insert into public.source_record_evidence (domain_table, record_key, document_version_id, known_at)
select 'institutional_holding', h.accession || ':' || h.infotable_sk, f.document_version_id, f.known_at
  from public.institutional_holding h
  join public.source_record_evidence f
    on f.domain_table = 'form13f_filing' and f.record_key = h.accession
on conflict do nothing;
