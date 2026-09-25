"""Print the account's open positions: `uv run python -m steward.trading212 [--env demo|live]`.

Credentials come from T212_API_KEY and T212_API_SECRET. The default environment is
demo; live has to be asked for explicitly.
"""

import argparse
import asyncio
import sys

from steward.logging import configure_logging
from steward.settings import get_settings
from steward.trading212.client import Trading212Client, Trading212Error


async def _print_positions(env: str) -> int:
    settings = get_settings()
    if settings.t212_api_key is None or settings.t212_api_secret is None:
        print("Set T212_API_KEY and T212_API_SECRET (see .env.example).", file=sys.stderr)
        return 2
    async with Trading212Client(
        settings.t212_api_key.get_secret_value(),
        settings.t212_api_secret.get_secret_value(),
        env=env,  # type: ignore[arg-type]
    ) as client:
        try:
            summary = await client.get_account_summary()
            positions = await client.get_positions()
        except Trading212Error as exc:
            print(f"Trading212 error: {exc}", file=sys.stderr)
            return 1

    print(f"Trading212 {env} account {summary.id} ({summary.currency})")
    print(f"Total value {summary.total_value:>14,.2f}  cash {summary.cash.available_to_trade:,.2f}")
    print()
    header = f"{'Ticker':<16}{'Qty':>14}{'Avg price':>12}{'Price':>12}{'Ccy':>5}"
    header += f"{'Value':>14}{'P/L':>12}"
    print(header)
    for p in sorted(positions, key=lambda p: p.wallet_impact.current_value, reverse=True):
        print(
            f"{p.instrument.ticker:<16}{p.quantity:>14,.4f}{p.average_price_paid:>12,.2f}"
            f"{p.current_price:>12,.2f}{p.instrument.currency:>5}"
            f"{p.wallet_impact.current_value:>14,.2f}{p.wallet_impact.unrealized_profit_loss:>12,.2f}"
        )
    print(f"\n{len(positions)} open positions; values and P/L in {summary.currency}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", choices=["demo", "live"], default="demo")
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level, settings.secret_values())
    sys.exit(asyncio.run(_print_positions(args.env)))


if __name__ == "__main__":
    main()
