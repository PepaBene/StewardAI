-- Checks a restored copy from the point of view of the backend. Run as the app_backend login.
\echo == who
select current_user;
\echo == vector extension and an index search
select extname from pg_extension where extname in ('vector', 'pgcrypto', 'uuid-ossp') order by 1;
select count(*) as chunks_with_vectors from public.document_chunk;
\echo == retention function runs
select count(*) as policies from public.retention_policy;
select * from private.run_retention();
\echo == queue round trip
insert into public.job_queue (message) values ('{"smoke":true}'::jsonb) returning msg_id \gset
with next as (select msg_id from public.job_queue where vt <= now() order by msg_id limit 1 for update skip locked)
update public.job_queue q set vt = now() + interval '30 seconds', read_ct = q.read_ct + 1
  from next where q.msg_id = next.msg_id returning q.msg_id, q.read_ct;
delete from public.job_queue where msg_id = :msg_id;
\echo == sequences continue past the data
select (select max(id) from public.job) as max_job_id, nextval(pg_get_serial_sequence('public.job', 'id')) as next_job_id;
\echo == append-only guard still refuses deletes
do $f$ begin
  delete from public.source_record_evidence where ctid = (select ctid from public.source_record_evidence limit 1);
  raise exception 'delete was allowed';
exception when restrict_violation then raise notice 'guard ok';
end $f$;
\echo == owner foreign key rejects a stranger
do $f$ begin
  insert into public.watchlist_item (owner_id, ticker) values ('00000000-0000-0000-0000-000000000001', 'X');
  raise exception 'insert was allowed';
exception when foreign_key_violation then raise notice 'fk ok';
          when not_null_violation or check_violation then raise notice 'other constraint rejected first';
end $f$;
\echo == done
