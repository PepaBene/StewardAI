-- The foreign key web_fact.job_id had no covering index (deleting a job scans the table).
create index if not exists web_fact_job_id_idx on public.web_fact (job_id);
