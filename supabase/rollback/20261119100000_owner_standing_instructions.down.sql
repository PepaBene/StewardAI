delete from public.owner_override where action in ('taught', 'retracted');
alter table public.owner_override drop constraint owner_override_action_check;
alter table public.owner_override add constraint owner_override_action_check
  check (action in ('reverted', 'edited', 'rejected', 'marked_wrong'));
