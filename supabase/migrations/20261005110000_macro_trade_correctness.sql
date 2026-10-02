-- Macro/trade data correctness (audit B10, B14, B16, A18, HS revisions).
--
-- * trade_observation: provider vintage + revision (a changed value is the next revision, an
--   unchanged one is not re-inserted), net weight apart from quantity, ISO alpha-2
--   `reporter_iso` / `partner_iso` populated at write time (Comtrade reports UN M49 numbers; the
--   exposure mappings and alerts use ISO), the re-export / re-import flows, and canonical HS
--   revision labels.
-- * exposure_mapping: the 100% cap is checked per (issuer, dimension) - geography, HS product,
--   or geography+HS cells - so a geography cut and a product cut can both be entered; an HS code
--   needs its revision.
-- * hs_concordance: HS revision concordance (seeded at chapter level only; the WCO correlation
--   tables are loaded through steward.macro.hs.concordance_rows).

create function public.normalize_hs_revision(label text) returns text
language sql immutable set search_path='' as $$
  select case
    when label is null or btrim(label) = '' then null
    when upper(replace(btrim(label), ' ', '')) = 'H0' then 'HS1992'
    when upper(replace(btrim(label), ' ', '')) = 'H1' then 'HS1996'
    when upper(replace(btrim(label), ' ', '')) = 'H2' then 'HS2002'
    when upper(replace(btrim(label), ' ', '')) = 'H3' then 'HS2007'
    when upper(replace(btrim(label), ' ', '')) = 'H4' then 'HS2012'
    when upper(replace(btrim(label), ' ', '')) = 'H5' then 'HS2017'
    when upper(replace(btrim(label), ' ', '')) = 'H6' then 'HS2022'
    when upper(replace(btrim(label), ' ', '')) ~ '^(HS)?(1992|1996|2002|2007|2012|2017|2022)$'
      then 'HS' || substring(upper(replace(btrim(label), ' ', '')) from '(1992|1996|2002|2007|2012|2017|2022)$')
    else upper(replace(btrim(label), ' ', ''))
  end
$$;

alter table public.trade_observation
  add column net_weight numeric,
  add column reporter_iso text,
  add column partner_iso text,
  add column vintage text,
  add column revision integer not null default 0 check (revision >= 0);
alter table public.trade_observation drop constraint trade_observation_flow_check;
alter table public.trade_observation add constraint trade_observation_flow_check
  check (flow in ('import','export','re_export','re_import'));

-- UN M49 (as Comtrade numbers them; ISO numeric plus Comtrade's own codes) -> ISO alpha-2.
create temporary table m49_iso(code text primary key, iso text not null);
insert into m49_iso values
  ('0','W00'),
  ('4','AF'),
  ('8','AL'),
  ('12','DZ'),
  ('16','AS'),
  ('20','AD'),
  ('24','AO'),
  ('28','AG'),
  ('31','AZ'),
  ('32','AR'),
  ('36','AU'),
  ('40','AT'),
  ('44','BS'),
  ('48','BH'),
  ('50','BD'),
  ('51','AM'),
  ('52','BB'),
  ('56','BE'),
  ('60','BM'),
  ('64','BT'),
  ('68','BO'),
  ('70','BA'),
  ('72','BW'),
  ('76','BR'),
  ('84','BZ'),
  ('90','SB'),
  ('92','VG'),
  ('96','BN'),
  ('100','BG'),
  ('104','MM'),
  ('108','BI'),
  ('112','BY'),
  ('116','KH'),
  ('120','CM'),
  ('124','CA'),
  ('132','CV'),
  ('136','KY'),
  ('140','CF'),
  ('144','LK'),
  ('148','TD'),
  ('152','CL'),
  ('156','CN'),
  ('158','TW'),
  ('170','CO'),
  ('174','KM'),
  ('175','YT'),
  ('178','CG'),
  ('180','CD'),
  ('184','CK'),
  ('188','CR'),
  ('191','HR'),
  ('192','CU'),
  ('196','CY'),
  ('203','CZ'),
  ('204','BJ'),
  ('208','DK'),
  ('212','DM'),
  ('214','DO'),
  ('218','EC'),
  ('222','SV'),
  ('226','GQ'),
  ('231','ET'),
  ('232','ER'),
  ('233','EE'),
  ('234','FO'),
  ('238','FK'),
  ('242','FJ'),
  ('246','FI'),
  ('250','FR'),
  ('251','FR'),
  ('254','GF'),
  ('258','PF'),
  ('262','DJ'),
  ('266','GA'),
  ('268','GE'),
  ('270','GM'),
  ('275','PS'),
  ('276','DE'),
  ('288','GH'),
  ('292','GI'),
  ('296','KI'),
  ('300','GR'),
  ('304','GL'),
  ('308','GD'),
  ('312','GP'),
  ('316','GU'),
  ('320','GT'),
  ('324','GN'),
  ('328','GY'),
  ('332','HT'),
  ('340','HN'),
  ('344','HK'),
  ('348','HU'),
  ('352','IS'),
  ('356','IN'),
  ('360','ID'),
  ('364','IR'),
  ('368','IQ'),
  ('372','IE'),
  ('376','IL'),
  ('380','IT'),
  ('381','IT'),
  ('384','CI'),
  ('388','JM'),
  ('392','JP'),
  ('398','KZ'),
  ('400','JO'),
  ('404','KE'),
  ('408','KP'),
  ('410','KR'),
  ('414','KW'),
  ('417','KG'),
  ('418','LA'),
  ('422','LB'),
  ('426','LS'),
  ('428','LV'),
  ('430','LR'),
  ('434','LY'),
  ('438','LI'),
  ('440','LT'),
  ('442','LU'),
  ('446','MO'),
  ('450','MG'),
  ('454','MW'),
  ('458','MY'),
  ('462','MV'),
  ('466','ML'),
  ('470','MT'),
  ('474','MQ'),
  ('478','MR'),
  ('480','MU'),
  ('484','MX'),
  ('490','TW'),
  ('492','MC'),
  ('496','MN'),
  ('498','MD'),
  ('499','ME'),
  ('500','MS'),
  ('504','MA'),
  ('508','MZ'),
  ('512','OM'),
  ('516','NA'),
  ('520','NR'),
  ('524','NP'),
  ('528','NL'),
  ('531','CW'),
  ('533','AW'),
  ('534','SX'),
  ('540','NC'),
  ('548','VU'),
  ('554','NZ'),
  ('558','NI'),
  ('562','NE'),
  ('566','NG'),
  ('570','NU'),
  ('574','NF'),
  ('578','NO'),
  ('579','NO'),
  ('580','MP'),
  ('583','FM'),
  ('584','MH'),
  ('585','PW'),
  ('586','PK'),
  ('591','PA'),
  ('598','PG'),
  ('600','PY'),
  ('604','PE'),
  ('608','PH'),
  ('612','PN'),
  ('616','PL'),
  ('620','PT'),
  ('624','GW'),
  ('626','TL'),
  ('630','PR'),
  ('634','QA'),
  ('638','RE'),
  ('642','RO'),
  ('643','RU'),
  ('646','RW'),
  ('654','SH'),
  ('659','KN'),
  ('660','AI'),
  ('662','LC'),
  ('666','PM'),
  ('670','VC'),
  ('674','SM'),
  ('678','ST'),
  ('682','SA'),
  ('686','SN'),
  ('688','RS'),
  ('690','SC'),
  ('694','SL'),
  ('699','IN'),
  ('702','SG'),
  ('703','SK'),
  ('704','VN'),
  ('705','SI'),
  ('706','SO'),
  ('710','ZA'),
  ('716','ZW'),
  ('724','ES'),
  ('728','SS'),
  ('729','SD'),
  ('740','SR'),
  ('748','SZ'),
  ('752','SE'),
  ('756','CH'),
  ('757','CH'),
  ('760','SY'),
  ('762','TJ'),
  ('764','TH'),
  ('768','TG'),
  ('776','TO'),
  ('780','TT'),
  ('784','AE'),
  ('788','TN'),
  ('792','TR'),
  ('795','TM'),
  ('796','TC'),
  ('798','TV'),
  ('800','UG'),
  ('804','UA'),
  ('807','MK'),
  ('818','EG'),
  ('826','GB'),
  ('834','TZ'),
  ('840','US'),
  ('842','US'),
  ('854','BF'),
  ('858','UY'),
  ('860','UZ'),
  ('862','VE'),
  ('876','WF'),
  ('882','WS'),
  ('887','YE'),
  ('894','ZM');
update public.trade_observation t set reporter_iso = m.iso
  from m49_iso m where t.reporter ~ '^[0-9]+$' and m.code = ltrim(t.reporter, '0');
update public.trade_observation t set partner_iso = m.iso
  from m49_iso m where t.partner ~ '^[0-9]+$' and m.code = case when t.partner ~ '^0+$' then '0' else ltrim(t.partner, '0') end;
update public.trade_observation set reporter_iso = reporter where reporter_iso is null and reporter ~ '^[A-Z]{2}$';
update public.trade_observation set partner_iso = partner where partner_iso is null and partner ~ '^[A-Z]{2}$';
update public.trade_observation set hs_revision = public.normalize_hs_revision(hs_revision)
  where hs_revision is distinct from public.normalize_hs_revision(hs_revision);
drop table m49_iso;
create index trade_observation_reporter_iso on public.trade_observation(reporter_iso, hs_code, period);

update public.exposure_mapping set hs_revision = public.normalize_hs_revision(hs_revision)
  where hs_revision is distinct from public.normalize_hs_revision(hs_revision);
-- NOT VALID: mappings entered before this migration may lack a revision; new and updated rows
-- must carry one.
alter table public.exposure_mapping add constraint exposure_mapping_hs_revision_required
  check (hs_code is null or hs_revision is not null) not valid;

-- Exposure cap per (issuer, dimension).
create function public.exposure_dimension(geography text, hs_code text) returns text
language sql immutable set search_path='' as $$
  select case
    when geography is not null and hs_code is null then 'geography'
    when hs_code is not null and geography is null then 'hs_product'
    else 'geography_hs_product'
  end
$$;

create or replace function public.check_exposure_total() returns trigger
language plpgsql security invoker set search_path='' as $$
declare
  exceeds_limit boolean;
  new_dimension text := public.exposure_dimension(new.geography, new.hs_code);
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
           and public.exposure_dimension(em.geography, em.hs_code) = new_dimension
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
            and public.exposure_dimension(em.geography, em.hs_code) = new_dimension
            and (tg_op <> 'UPDATE' or em.id <> new.id)
            and em.valid_from <= dates.day
            and (em.valid_to is null or em.valid_to >= dates.day)
       ),0) > 1
  ) into exceeds_limit;
  if exceeds_limit then
    raise exception 'mapped % exposure exceeds 100%%', new_dimension;
  end if;
  return new;
end $$;

create table public.hs_concordance (
  from_revision text not null, from_code text not null,
  to_revision text not null, to_code text not null,
  relation text not null check (relation in ('1:1','1:n','n:1','n:n','chapter')),
  note text,
  primary key(from_revision, from_code, to_revision, to_code)
);
-- Chapter numbering and titles are the same in HS2017 and HS2022 (chapter 77 is reserved).
-- Individual subheadings that moved between chapters are NOT tracked at this level; use the
-- WCO correlation table rows (steward.macro.hs.concordance_rows) for exact flows.
insert into public.hs_concordance(from_revision, from_code, to_revision, to_code, relation, note)
select a, lpad(c::text, 2, '0'), b, lpad(c::text, 2, '0'), 'chapter',
       'chapter exists in both revisions; subheading moves not tracked'
  from generate_series(1, 97) c, (values ('HS2017','HS2022'),('HS2022','HS2017')) v(a, b)
 where c <> 77;
alter table public.hs_concordance enable row level security;
revoke all on public.hs_concordance from anon, authenticated;
grant select on public.hs_concordance to authenticated;
grant select, insert, update, delete on public.hs_concordance to app_backend;
create policy owner_read on public.hs_concordance for select to authenticated
  using ((select private.is_owner()));
create policy backend_all on public.hs_concordance for all to app_backend
  using (true) with check (true);
