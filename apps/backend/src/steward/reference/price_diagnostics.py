"""Per-series price diagnostics of one holding (plan D3.7, audit A16/B21).

One row per stored series (symbol, source, bar type): exchange, quote currency and unit, last
session, observed sessions, sessions missing against the exchange's real calendar, and how often
it disagrees with another source's series of the same listing. A holding's series live under
different provider symbols (`cez.cz` at Stooq, `CEZ.PR` at Yahoo, the EODHD and Alpha Vantage
codes); they are joined through the holding's provider-symbol mapping, not by symbol string, so
cross-source disagreement is actually found. The row of the series readers serve is marked
`chosen`.
"""

import datetime as dt
from decimal import Decimal
from typing import Any

import asyncpg

from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.reference.calendar import expected_sessions
from steward.reference.price_series import SeriesRef, choose_one, holding_symbols, series_mic
from steward.sources.market import major_currency, unit_factor

DISAGREEMENT_PCT = Decimal("0.02")
# Only recent sessions are compared across sources: older closes differ legitimately by the
# dividend adjustments some sources apply and others do not.
COMPARE_SESSIONS = 20


def _ref_key(symbol: str, source: str) -> SeriesRef:
    return SeriesRef(symbol, source)  # type: ignore[arg-type]


async def holding_price_diagnostics(
    pool: asyncpg.Pool, ticker: str, today: dt.date | None = None
) -> list[dict[str, Any]]:
    today = today or dt.datetime.now(dt.UTC).date()
    jobs = await load_active(pool, "jobs", JobsConfig)
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    calendar = (jobs.body if jobs else JobsConfig()).calendar
    symbols = (
        await holding_symbols(
            pool, [ticker], profiles.body if profiles else HoldingProfilesConfig()
        )
    )[ticker]
    refs = symbols.refs()
    if not refs:
        return []
    chosen = await choose_one(pool, symbols)
    rows = await pool.fetch(
        """select symbol, source, bar_type, mic, currency, quote_unit, access_state,
                  coalesce(session_date, date) as session, close
           from public.price_bar
           where (symbol, source) in (select * from unnest($1::text[], $2::text[]))
           order by symbol, source, bar_type, coalesce(session_date, date)""",
        [r.symbol for r in refs],
        [r.source for r in refs],
    )
    series: dict[tuple[str, str, str], list[asyncpg.Record]] = {}
    for r in rows:
        series.setdefault((r["symbol"], r["source"], r["bar_type"]), []).append(r)

    # Closes by (bar type, session) per series, in the major currency, for cross-source checks.
    comparable: dict[tuple[str, str, str], dict[dt.date, tuple[Decimal, str | None]]] = {}
    for key, items in series.items():
        comparable[key] = {
            r["session"]: (
                Decimal(r["close"]) * unit_factor(r["currency"]),
                major_currency(r["currency"]),
            )
            for r in items[-COMPARE_SESSIONS * 3 :]
        }

    out: list[dict[str, Any]] = []
    for (symbol, source, bar_type), items in sorted(series.items()):
        latest = items[-1]
        mic = next((r["mic"] for r in reversed(items) if r["mic"]), None) or series_mic(
            symbol, source
        )
        first_day, last_day = items[0]["session"], latest["session"]
        expected = await expected_sessions(pool, calendar, mic, first_day, last_day)
        observed = {r["session"] for r in items}
        missing = [d for d in expected.days if d not in observed]
        mine = comparable[(symbol, source, bar_type)]
        recent = sorted(mine)[-COMPARE_SESSIONS:]
        differing: set[dt.date] = set()
        for (other_symbol, other_source, other_type), theirs in comparable.items():
            if (other_symbol, other_source) == (symbol, source) or other_type != bar_type:
                continue
            for day in recent:
                if day not in theirs:
                    continue
                (a, ccy_a), (b, ccy_b) = mine[day], theirs[day]
                if ccy_a is None or ccy_a != ccy_b or max(a, b) == 0:
                    continue
                if abs(a - b) / max(a, b) > DISAGREEMENT_PCT:
                    differing.add(day)
        is_chosen = chosen is not None and chosen == _ref_key(symbol, source)
        stale = (
            (
                await expected_sessions(pool, calendar, mic, last_day + dt.timedelta(days=1), today)
            ).count
            if is_chosen
            else None
        )
        out.append(
            {
                "symbol": symbol,
                "source": source,
                "mic": mic,
                "currency": latest["currency"],
                "quote_unit": latest["quote_unit"],
                "bar_type": bar_type,
                "last_session": last_day,
                "observed_sessions": len(observed),
                "disagreements": len(differing),
                "missing_days": len(missing),
                "access_state": latest["access_state"],
                "chosen": is_chosen,
                "expected_sessions": expected.count,
                "calendar_basis": expected.basis,
                "stale_sessions": stale,
            }
        )
    out.sort(key=lambda r: (not r["chosen"], -(r["last_session"].toordinal()), r["source"]))
    return out
