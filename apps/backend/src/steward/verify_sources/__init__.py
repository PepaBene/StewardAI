"""Phase 8 verification, operations and zero-cost access gate for the free data sources.

Modules (plan D8.1-D8.6):

- `contract`    typed-failure classification shared by the adapter contract tests
- `shadow`      30-day shadow-run recorder and sign-off report (markdown + JSON)
- `thresholds`  domain error thresholds, sampled reconciliation and the enable gate
- `lifecycle`   retention of raw payloads, derived-metric recompute, migration rollback checks
- `zero_cost`   zero-cost access gate over the endpoint manifest, governor and collectors
- `budgets`     registry of the budgets collectors pass to the governor (inspected by the gate)
- `signal_audit` audit samples, point-in-time checks and the `publish_gate`
- `enforce`     the callable surface that makes the gates stop things in production code:
                `conclusion_gate` / `require_conclusion` (alerts, valuation), `pre_publish_check`
                (derived signal publishers), `budget_for_endpoint` / `reserve_for_endpoint` /
                `record_outcome` (collectors and the production governor)
- `jobs`        job handlers (`shadow_report_handler`, `shadow_snapshot_handler`,
                `raw_retention_handler`, `zero_cost_gate_handler`) for the orchestrator to register
"""
