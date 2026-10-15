delete from public.engine_gate_setting where name = 'watchers';
alter table public.engine_gate_setting drop constraint engine_gate_setting_name_check;
alter table public.engine_gate_setting
  add constraint engine_gate_setting_name_check check (name = 'floor');
