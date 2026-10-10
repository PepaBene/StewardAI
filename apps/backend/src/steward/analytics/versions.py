"""Method versions for every calculation. Bump the version when a formula or rule changes: a
saved result keeps the version it was computed under (plan engineering rule 4)."""

METHOD_VERSIONS: dict[str, str] = {
    "price_return": "1.0.0",
    "total_return": "1.0.0",
    "cagr": "1.0.0",
    "twr": "1.0.0",
    "xirr": "1.0.0",
    "attribution": "1.0.0",
    "look_through": "1.0.0",
    "fund_overlap": "1.0.0",
    "portfolio_earnings_yield": "1.0.0",
    "concentration": "1.0.0",
    "dcf": "1.0.0",
    "reverse_dcf": "1.0.0",
    "risk": "1.2.0",
    "scenario_shock": "1.0.0",
    "theme_exposure": "1.0.0",
    "rebalance": "1.1.0",
    "whatif_case": "1.0.0",
    "pretrade": "1.2.0",
    "ranking": "1.0.0",
    "pit_value": "1.0.0",
    "ratios": "1.0.0",
    "composite_qvm": "1.1.0",
    "universe_membership": "1.0.0",
    "screen": "2.1.0",
    "statements": "1.0.0",
    "company_ratios": "1.1.0",
    "peer_compare": "1.0.0",
    "valuation_scenarios": "1.0.0",
    "capital_allocation": "1.0.0",
    "segments": "1.0.0",
    "company_report": "1.0.0",
    "backtest": "1.0.0",
}


def version(method: str) -> str:
    return METHOD_VERSIONS[method]
