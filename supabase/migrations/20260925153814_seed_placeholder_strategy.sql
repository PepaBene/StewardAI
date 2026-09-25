-- Placeholder strategy so drift has targets to compare against before the owner writes a
-- real one. Inserted only when no strategy version is active, so it never overrides one.
-- Replace it with PUT /config/strategy (or, later, the config editor).

insert into public.config_version (domain, body, schema_version, note, created_by, active)
select 'strategy', $j${"schema_version": 1, "base_currency": "CZK", "fx_source": "cnb", "weight_basis": "total", "goals": "PLACEHOLDER: replace with your real goals.", "horizon_years": null, "notes": "Placeholder strategy inserted to exercise drift in Phase 1. Replace every value.", "categories": ["sector", "geography", "currency"], "classifications": {}, "cash_target": {"target_weight": 0.05, "band": 0.05}, "target_positions": [{"target_weight": 0.1, "band": 0.03, "ticker": "AAPL_US_EQ", "hard_limit": 0.15}, {"target_weight": 0.1, "band": 0.03, "ticker": "MSFT_US_EQ", "hard_limit": 0.15}, {"target_weight": 0.08, "band": 0.02, "ticker": "ASML_US_EQ", "hard_limit": null}], "target_categories": [{"target_weight": 0.5, "band": 0.1, "dimension": "currency", "bucket": "USD"}, {"target_weight": 0.25, "band": 0.1, "dimension": "currency", "bucket": "EUR"}], "rules": [{"id": "max-single-position", "operator": "<=", "on_breach": "severity_2", "description": "", "type": "position_weight", "value": 0.1, "ticker": null}, {"id": "max-cash", "operator": "<=", "on_breach": "severity_2", "description": "", "type": "cash_weight", "value": 0.2}]}$j$::jsonb,
  1, 'Placeholder strategy (Phase 1); replace with real targets',
  (select user_id from private.app_owner limit 1), true
where not exists (select 1 from public.config_version where domain = 'strategy' and active);
