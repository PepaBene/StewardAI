-- P6.7: a held company's deal moving to a new documented state is worth telling the owner about.
-- The alert kind list gains 'deal_state'. A rumoured state never raises one (it is unverified); the
-- scan runs only over transitions that carry a dated document. Additive: existing rows are unchanged.

alter table public.disclosure_alert drop constraint disclosure_alert_kind_check;
alter table public.disclosure_alert add constraint disclosure_alert_kind_check
  check (kind in ('insider_trade', 'major_holder', 'relationship_change', 'deal_state'));
