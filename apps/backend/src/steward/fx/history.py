"""Converting amounts dated over a period with stored reference rates.

`FxTable` converts at one date; this answers "units of B for one A on day D" for any day,
with the same rule the sync uses: the latest rate of the source on or before D (weekends
and holidays carry the previous fixing forward). A rate older than `max_age` days before D
is not used, so a long gap in the stored rates shows up as a missing conversion instead of
silently reusing a stale rate.
"""

import bisect
import datetime as dt
from collections.abc import Iterable
from decimal import Decimal

import asyncpg

from steward.fx.convert import HOME_CURRENCY, FxSourceName, normalise_currency

DEFAULT_MAX_AGE = dt.timedelta(days=10)


class FxHistory:
    def __init__(
        self,
        source: FxSourceName,
        rows: Iterable[tuple[str, dt.date, Decimal]],
        max_age: dt.timedelta = DEFAULT_MAX_AGE,
    ) -> None:
        self.source = source
        self.home_currency = HOME_CURRENCY[source]
        self.max_age = max_age
        series: dict[str, list[tuple[dt.date, Decimal]]] = {}
        for currency, day, rate in rows:
            series.setdefault(currency, []).append((day, rate))
        self._dates: dict[str, list[dt.date]] = {}
        self._rates: dict[str, list[Decimal]] = {}
        for currency, points in series.items():
            points.sort()
            self._dates[currency] = [d for d, _ in points]
            self._rates[currency] = [r for _, r in points]

    def _home_per_unit(self, iso: str, day: dt.date) -> Decimal | None:
        if iso == self.home_currency:
            return Decimal(1)
        dates = self._dates.get(iso)
        if not dates:
            return None
        i = bisect.bisect_right(dates, day) - 1
        if i < 0 or day - dates[i] > self.max_age:
            return None
        return self._rates[iso][i]

    def conversion(self, from_code: str, to_code: str, day: dt.date) -> Decimal | None:
        """Units of `to_code` for one unit of `from_code` on `day`; None without a rate."""
        from_iso, from_factor = normalise_currency(from_code)
        to_iso, to_factor = normalise_currency(to_code)
        if from_iso == to_iso:
            return from_factor / to_factor
        from_rate = self._home_per_unit(from_iso, day)
        to_rate = self._home_per_unit(to_iso, day)
        if from_rate is None or to_rate is None:
            return None
        return from_factor * from_rate / (to_rate * to_factor)


async def load_fx_history(
    pool: asyncpg.Pool,
    source: FxSourceName,
    currencies: Iterable[str],
    start: dt.date,
    end: dt.date,
    max_age: dt.timedelta = DEFAULT_MAX_AGE,
) -> FxHistory:
    """Stored rates of `currencies` (ISO or Trading212 minor codes) covering start..end,
    including the last rate before `start` so the first days can be carried forward."""
    isos = sorted({normalise_currency(c)[0] for c in currencies} - {HOME_CURRENCY[source]})
    rows = []
    if isos:
        rows = await pool.fetch(
            "select currency, rate_date, rate from public.fx_rate where source = $1"
            " and currency = any($2::text[]) and rate_date <= $4 and rate_date >= $3",
            source,
            isos,
            start - max_age,
            end,
        )
    return FxHistory(
        source, [(r["currency"], r["rate_date"], r["rate"]) for r in rows], max_age=max_age
    )
