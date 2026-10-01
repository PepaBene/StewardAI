-- Rollback for supabase/migrations/20261005110000_macro_trade_correctness.sql (plan D8.4).
-- Restores the one-total exposure cap, the two-value flow check and drops the columns,
-- functions and table the migration added. Run it in a transaction, newest migration first.
-- DATA LOSS: deletes re_export / re_import trade rows; drops these columns and their values:
-- trade_observation.net_weight, reporter_iso, partner_iso, vintage, revision; drops
-- hs_concordance and every row in it. Revision labels stay in the canonical HS<year> form.

drop table if exists public.hs_concordance cascade;
create or replace function public.check_exposure_total() returns trigger
language plpgsql security invoker set search_path='' as $$
declare exceeds_limit boolean;
begin
  if new.valid_to is not null and new.valid_to < new.valid_from then
    raise exception 'exposure valid_to precedes valid_from';
  end if;
  select exists (
    select 1
      from (
        select new.valid_from as day
        union
        select em.valid_from
          from public.exposure_mapping em
         where em.issuer_id=new.issuer_id
           and (tg_op <> 'UPDATE' or em.id <> new.id)
           and em.valid_from >= new.valid_from
           and (new.valid_to is null or em.valid_from <= new.valid_to)
           and (em.valid_to is null or em.valid_to >= new.valid_from)
      ) dates
     where (new.valid_to is null or dates.day <= new.valid_to)
       and new.weight + coalesce((
         select sum(em.weight)
           from public.exposure_mapping em
          where em.issuer_id=new.issuer_id
            and (tg_op <> 'UPDATE' or em.id <> new.id)
            and em.valid_from <= dates.day
            and (em.valid_to is null or em.valid_to >= dates.day)
       ),0) > 1
  ) into exceeds_limit;
  if exceeds_limit then raise exception 'mapped exposure exceeds 100%%'; end if;
  return new;
end $$;
alter table public.exposure_mapping drop constraint if exists exposure_mapping_hs_revision_required;
drop function if exists public.exposure_dimension(text, text);
drop index if exists public.trade_observation_reporter_iso;
delete from public.trade_observation where flow in ('re_export','re_import');
alter table public.trade_observation drop constraint if exists trade_observation_flow_check;
alter table public.trade_observation add constraint trade_observation_flow_check
  check (flow in ('import','export'));
alter table public.trade_observation
  drop column if exists net_weight cascade,
  drop column if exists reporter_iso cascade,
  drop column if exists partner_iso cascade,
  drop column if exists vintage cascade,
  drop column if exists revision cascade;
drop function if exists public.normalize_hs_revision(text);
