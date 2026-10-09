"""Risk, scenarios and rebalancing decisions (plan Phase 6).

Pure calculators on the X-Ray leaves and the stored return series, plus a database-facing
`service`. Nothing here writes to Trading212 or places a trade: every output is a read-only
estimate with its inputs, its method version and what it could not measure.

- `shocks`: scenario shocks over a complete portfolio (M6.2, M6.5 FX case)
- `themes`: exposure to a theme or event through issuer, fund, sector, country and currency (M6.3)
- `rebalance`: target weights, bands and proposed trades (M6.4)
- `pretrade`: pass/flag list citing the strategy's principles for a proposed trade (M6.7)
- `whatif`: named what-if cases built on the proposed-portfolio X-Ray (M6.5)
- `series`, `risk_report`: return series and the extended risk statistics (M6.1)
"""
