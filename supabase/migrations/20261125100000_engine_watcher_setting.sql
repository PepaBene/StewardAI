-- The owner also edits how sensitive the heads-up watchers are (insider cluster size, large-move
-- size, review lead time). Same one-row-per-name table as the confidence-floor gates.
alter table public.engine_gate_setting drop constraint engine_gate_setting_name_check;
alter table public.engine_gate_setting
  add constraint engine_gate_setting_name_check check (name in ('floor', 'watchers'));
