-- The owner has cleared news feedback and chat history for use as training data: the table now
-- also feeds `training_label` (written by the backend when a mark is saved).
comment on table public.news_feedback is
  'Owner feedback on news items, stories, claims and brief lines. Input to the benchmark and a source of training labels.';
