-- Chat as a control surface (autonomous engine plan, L7): a chat action may now also be one of
-- the owner actions of the shared registry (dismiss a notice, undo a decision, ...), not only a
-- job to start. The same confirm and cancel buttons in the chat panel run it.
alter table public.chat_action drop constraint chat_action_kind_check;
alter table public.chat_action
  add constraint chat_action_kind_check check (kind ~ '^[a-z][a-z_]{1,39}$');
