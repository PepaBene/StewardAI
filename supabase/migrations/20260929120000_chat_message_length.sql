-- Chat messages up to 200,000 characters: long pasted questions and long answers fit
-- (the API accepts user messages up to 100,000 characters; answers are stored up to
-- 100,000).
alter table public.chat_message drop constraint if exists chat_message_content_check;
alter table public.chat_message
  add constraint chat_message_content_check check (char_length(content) <= 200000);
