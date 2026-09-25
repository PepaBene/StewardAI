"""Currency conversion over one source's reference rates.

Every rate is stored the same way whatever the source: units of the source's home
currency (CZK for ČNB, EUR for ECB) for ONE unit of the foreign currency. Converting A to
B is then `rate(A) / rate(B)`, with the home currency at 1, so any pair both sources cover
converts through the home currency exactly as a manual calculation would.

Trading212 quotes some instruments in minor units (GBX = pence). Those convert through
their major currency with a fixed factor.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

FxSourceName = Literal["cnb", "ecb"]

HOME_CURRENCY: dict[FxSourceName, str] = {"cnb": "CZK", "ecb": "EUR"}

# Minor-unit codes Trading212 uses, mapped to (major currency, major units per minor unit).
MINOR_UNITS: dict[str, tuple[str, Decimal]] = {
    "GBX": ("GBP", Decimal("0.01")),
    "GBp": ("GBP", Decimal("0.01")),
}


class MissingRateError(LookupError):
    def __init__(self, currency: str, source: FxSourceName) -> None:
        super().__init__(f"no {source.upper()} reference rate for {currency}")
        self.currency = currency
        self.source = source


@dataclass(frozen=True)
class FxRate:
    source: FxSourceName
    rate_date: dt.date
    currency: str
    # Home-currency units for one unit of `currency`.
    rate: Decimal
    published_amount: Decimal
    published_rate: Decimal

    @property
    def home_currency(self) -> str:
        return HOME_CURRENCY[self.source]


def normalise_currency(code: str) -> tuple[str, Decimal]:
    """(ISO currency, factor) for a Trading212 currency code: 'GBX' -> ('GBP', 0.01)."""
    if code in MINOR_UNITS:
        return MINOR_UNITS[code]
    return code.upper(), Decimal(1)


@dataclass(frozen=True)
class Conversion:
    rate: Decimal
    # Date of the oldest reference rate involved; None when no rate was needed.
    rate_date: dt.date | None


class FxTable:
    """The latest rate per currency from one source, on or before a given date."""

    def __init__(self, source: FxSourceName, rates: list[FxRate]) -> None:
        self.source = source
        self.home_currency = HOME_CURRENCY[source]
        self._rates: dict[str, FxRate] = {}
        for rate in rates:
            if rate.source != source:
                raise ValueError(f"{rate.source} rate in a {source} table")
            current = self._rates.get(rate.currency)
            if current is None or rate.rate_date > current.rate_date:
                self._rates[rate.currency] = rate

    @classmethod
    def as_of(cls, source: FxSourceName, rates: list[FxRate], date: dt.date) -> "FxTable":
        return cls(source, [r for r in rates if r.source == source and r.rate_date <= date])

    def rates(self) -> list[FxRate]:
        return sorted(self._rates.values(), key=lambda r: r.currency)

    def _home_per_unit(self, iso: str) -> tuple[Decimal, dt.date | None]:
        if iso == self.home_currency:
            return Decimal(1), None
        found = self._rates.get(iso)
        if found is None:
            raise MissingRateError(iso, self.source)
        return found.rate, found.rate_date

    def conversion(self, from_code: str, to_code: str) -> Conversion:
        """Units of `to_code` for one unit of `from_code` (minor units handled)."""
        from_iso, from_factor = normalise_currency(from_code)
        to_iso, to_factor = normalise_currency(to_code)
        if from_iso == to_iso:
            return Conversion(from_factor / to_factor, None)
        from_rate, from_date = self._home_per_unit(from_iso)
        to_rate, to_date = self._home_per_unit(to_iso)
        dates = [d for d in (from_date, to_date) if d is not None]
        rate = from_factor * from_rate / (to_rate * to_factor)
        return Conversion(rate, min(dates) if dates else None)

    def convert(self, amount: Decimal, from_code: str, to_code: str) -> Decimal:
        return amount * self.conversion(from_code, to_code).rate
