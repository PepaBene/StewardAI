-- Status history for entity_relationship, so its decision history is a real log like the other
-- graph tables (company_graph_edge, company_event, relationship_disclosure), not one synthesised
-- entry. entity_relationship keeps its reason in `exception_reason`, so it gets its own trigger
-- function instead of the generic graph_log_status (which reads `decision_reason`).
--
-- Rows that existed before this migration get one baseline entry stamped with created_at, so
-- as-of status reads have a starting point. Earlier status changes were never recorded and
-- cannot be reconstructed.

create or replace function public.entity_relationship_log_status() returns trigger
language plpgsql set search_path = '' as $$
begin
  if tg_op = 'INSERT' or old.status is distinct from new.status then
    insert into public.graph_status_log (table_name, row_id, from_status, to_status, reason)
    values ('entity_relationship', new.id,
            case when tg_op = 'INSERT' then null else old.status end,
            new.status, new.exception_reason);
  end if;
  return null;
end $$;

create trigger entity_relationship_status_log after insert or update of status
  on public.entity_relationship for each row execute function public.entity_relationship_log_status();

insert into public.graph_status_log (table_name, row_id, from_status, to_status, reason, changed_at)
select 'entity_relationship', id, null, status, exception_reason, created_at
  from public.entity_relationship;
