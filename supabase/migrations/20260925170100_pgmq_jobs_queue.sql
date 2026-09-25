-- The `jobs` queue: the API enqueues on-demand job runs (e.g. "sync now"), the worker
-- consumes them. Message body: {"job_id": <job.id>, "job_type": "<type>"}.
-- The pgmq migration's default privileges give app_backend access to the queue's tables.

select pgmq.create('jobs');
