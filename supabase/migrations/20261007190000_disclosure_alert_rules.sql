-- P8.5: confirmed changes feed the alert store with the reason each rule fired.
-- The alert kind list gains controlling_stake, insider_buy_cluster, estimate_revision and
-- debt_maturity_change. `why_fired` stores the rule name, its threshold and the inputs it saw, so the
-- page can say why an alert exists. Estimates that are unverified, non-comparable or ungated become
-- review tasks (`quality_review`, subject 'estimate_value'), never alerts. Additive: existing rows
-- are unchanged and `why_fired` is null on them.
-- The kind constraint name `disclosure_alert_kind_check` is the Postgres default; the previous
-- migration already relied on it.

alter table public.disclosure_alert drop constraint disclosure_alert_kind_check;
alter table public.disclosure_alert add constraint disclosure_alert_kind_check
  check (kind in ('insider_trade', 'major_holder', 'relationship_change', 'deal_state',
                  'controlling_stake', 'insider_buy_cluster', 'estimate_revision',
                  'debt_maturity_change'));

alter table public.disclosure_alert add column why_fired jsonb;
comment on column public.disclosure_alert.why_fired is
  'Rule name, threshold and inputs that raised the alert: {"rule","threshold","inputs"}.';

-- Throttle reads count recent alerts per ticker and kind.
create index disclosure_alert_throttle on public.disclosure_alert (ticker, kind, created_at desc);

alter table public.quality_review drop constraint quality_review_subject_type_check;
alter table public.quality_review add constraint quality_review_subject_type_check
  check (subject_type in
    ('claim','observation','document_version','identity_mapping','estimate_value'));
