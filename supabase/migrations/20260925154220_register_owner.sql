-- Register the single Supabase Auth user as the app owner (README setup step 5), so RLS
-- lets that user read app data. Runs only when there is exactly one auth user and no
-- owner yet; anywhere else it does nothing and the owner is registered by hand.

insert into private.app_owner (user_id)
select id from auth.users
where (select count(*) from auth.users) = 1
  and not exists (select 1 from private.app_owner);
