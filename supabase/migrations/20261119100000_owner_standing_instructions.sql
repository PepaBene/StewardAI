-- Standing instructions (autonomous engine plan 2b.4): the owner tells the engine a rule in words
-- ("never auto-accept a listing for a fund"). It is stored as an override row of action 'taught'
-- and handed to the model prompts as the owner's data; 'retracted' withdraws one. Append-only.
alter table public.owner_override drop constraint owner_override_action_check;
alter table public.owner_override add constraint owner_override_action_check
  check (action in ('reverted', 'edited', 'rejected', 'marked_wrong', 'taught', 'retracted'));
