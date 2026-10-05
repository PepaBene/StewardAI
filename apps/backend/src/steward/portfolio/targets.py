"""Thesis price targets, checked after each sync next to the drift, rule and risk checks.

Each held position whose thesis has a `price_target` is compared on its Trading212 price:

- at or above the sell target: a `target:sell:<ticker>` finding (severity 2). The thesis has
  played out: re-run the research and update the thesis; sell unless it raises the target.
  Raising the target above the price clears it at the next sync.
- at or below the buy target: a `target:buy:<ticker>` finding (severity 1). A candidate to
  add to, when the thesis is intact and the strategy's caps allow.

Findings are kind `rule` and go through the breach machinery (open, alert once, clear).
A target in a different currency from the instrument's quote (other than a major/minor pair
such as GBP/GBX) cannot be compared and is reported, never guessed.
"""

from dataclasses import dataclass, field

from steward.config.thesis import ThesesConfig
from steward.fx.convert import normalise_currency
from steward.portfolio.breaches import Finding
from steward.portfolio.snapshot import PositionRow

SELL_SEVERITY = 2
BUY_SEVERITY = 1


@dataclass
class TargetCheck:
    findings: list[Finding] = field(default_factory=list)
    # Targets that could not be compared, by ticker (reported, never a finding).
    unavailable: dict[str, str] = field(default_factory=dict)


def _price(value: float, currency: str) -> str:
    return f"{value:,.2f} {currency}"


def evaluate_targets(positions: list[PositionRow], theses: ThesesConfig) -> TargetCheck:
    out = TargetCheck()
    held = {p.ticker: p for p in positions if p.quantity > 0}
    for thesis in theses.theses:
        target = thesis.price_target
        position = held.get(thesis.ticker)
        if target is None or position is None:
            continue
        quote_iso, quote_factor = normalise_currency(position.instrument_currency)
        target_iso, target_factor = normalise_currency(target.currency)
        if quote_iso != target_iso:
            out.unavailable[thesis.ticker] = (
                f"target in {target.currency}, price quoted in {position.instrument_currency}"
            )
            continue
        price = float(position.current_price * quote_factor / target_factor)
        if price <= 0:
            out.unavailable[thesis.ticker] = "no current price"
            continue
        basis = f"({target.method}, set {target.set_on.isoformat()})"
        if target.sell_target is not None and price >= target.sell_target:
            out.findings.append(
                Finding(
                    f"target:sell:{thesis.ticker}",
                    "rule",
                    thesis.ticker,
                    SELL_SEVERITY,
                    f"{thesis.ticker} is at {_price(price, target.currency)}, at or above its "
                    f"thesis sell target of {_price(target.sell_target, target.currency)} "
                    f"{basis}. Re-run the research and update the thesis within 2 weeks: "
                    "sell unless the new thesis raises the target",
                    price,
                    target.sell_target,
                )
            )
        if target.buy_target is not None and price <= target.buy_target:
            out.findings.append(
                Finding(
                    f"target:buy:{thesis.ticker}",
                    "rule",
                    thesis.ticker,
                    BUY_SEVERITY,
                    f"{thesis.ticker} is at {_price(price, target.currency)}, at or below its "
                    f"thesis buy target of {_price(target.buy_target, target.currency)} "
                    f"{basis}: a candidate to add to if the thesis is intact and the caps "
                    "allow",
                    price,
                    target.buy_target,
                )
            )
    return out
