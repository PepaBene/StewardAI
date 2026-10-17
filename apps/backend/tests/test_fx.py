"""ČNB and ECB parsing, and conversion over their rates."""

import datetime as dt
from decimal import Decimal

import httpx
import pytest

from steward.fx import FxSourceError, FxTable, MissingRateError, fetch_cnb, parse_cnb, parse_ecb

# An excerpt of the real ČNB file for 25 Sep 2026 (English version).
CNB_TEXT = """25 Sep 2026 #186
Country|Currency|Amount|Code|Rate
EMU|euro|1|EUR|24.350
Japan|yen|100|JPY|13.546
United Kingdom|pound|1|GBP|28.299
USA|dollar|1|USD|21.359
"""

ECB_CSV = """KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE,OBS_STATUS
EXR.D.CZK.EUR.SP00.A,D,CZK,EUR,SP00,A,2026-09-24,24.360,A
EXR.D.CZK.EUR.SP00.A,D,CZK,EUR,SP00,A,2026-09-25,24.350,A
EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-09-25,1.1400,A
EXR.D.GBP.EUR.SP00.A,D,GBP,EUR,SP00,A,2026-09-25,0.86050,A
"""


def test_parse_cnb_normalises_per_unit_and_keeps_published_figures() -> None:
    rates = {r.currency: r for r in parse_cnb(CNB_TEXT)}
    assert rates["USD"].rate_date == dt.date(2026, 9, 25)
    assert rates["USD"].rate == Decimal("21.359")
    # 100 JPY = 13.546 CZK -> 1 JPY = 0.13546 CZK; the published pair stays as it was.
    assert rates["JPY"].rate == Decimal("0.13546")
    assert (rates["JPY"].published_amount, rates["JPY"].published_rate) == (
        Decimal(100),
        Decimal("13.546"),
    )


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not a date\nCountry|Currency|Amount|Code|Rate\nUSA|dollar|1|USD|21.3\n",
        "25 Sep 2026 #186\nCountry|Currency|Amount|Code|Rate\nUSA|dollar|1|USD|abc\n",
        "25 Sep 2026 #186\nCountry|Currency|Code\nUSA|dollar|USD\n",
    ],
)
def test_parse_cnb_rejects_malformed_files(text: str) -> None:
    with pytest.raises(FxSourceError):
        parse_cnb(text)


def test_parse_ecb_inverts_to_eur_per_unit() -> None:
    rates = parse_ecb(ECB_CSV)
    usd = next(r for r in rates if r.currency == "USD")
    assert usd.published_rate == Decimal("1.1400")
    assert abs(usd.rate * Decimal("1.1400") - 1) < Decimal("1e-20")
    assert len([r for r in rates if r.currency == "CZK"]) == 2


def test_table_uses_latest_rate_on_or_before_the_date() -> None:
    rates = parse_ecb(ECB_CSV)
    as_of_24 = FxTable.as_of("ecb", rates, dt.date(2026, 9, 24))
    conversion = as_of_24.conversion("EUR", "CZK")
    assert conversion.rate == Decimal("24.360")
    assert conversion.rate_date == dt.date(2026, 9, 24)
    with pytest.raises(MissingRateError):
        as_of_24.conversion("USD", "CZK")  # no USD rate until the 25th


def test_cnb_conversions_match_manual_calculation() -> None:
    fx = FxTable("cnb", parse_cnb(CNB_TEXT))
    # USD -> CZK: straight from the fixing.
    assert fx.convert(Decimal("100"), "USD", "CZK") == Decimal("2135.900")
    # GBX (pence) -> CZK: 250 pence = 2.50 GBP, x 28.299.
    assert fx.convert(Decimal("250"), "GBX", "CZK") == Decimal("70.74750")
    # USD -> EUR crosses through CZK: 100 x 21.359 / 24.350.
    assert fx.convert(Decimal("100"), "USD", "EUR") == Decimal("100") * Decimal("21.359") / Decimal(
        "24.350"
    )
    # CZK -> CZK and GBX -> GBP need no rate.
    assert fx.conversion("CZK", "CZK").rate_date is None
    assert fx.convert(Decimal("250"), "GBX", "GBP") == Decimal("2.50")


def test_ecb_cross_rate_matches_manual_calculation() -> None:
    fx = FxTable.as_of("ecb", parse_ecb(ECB_CSV), dt.date(2026, 9, 25))
    # USD -> CZK through EUR: 1 USD = (1/1.14) EUR = 24.35/1.14 CZK.
    manual = Decimal("24.350") / Decimal("1.1400")
    assert abs(fx.conversion("USD", "CZK").rate - manual) < Decimal("1e-20")


def test_missing_currency_is_an_error_not_a_zero() -> None:
    fx = FxTable("cnb", parse_cnb(CNB_TEXT))
    with pytest.raises(MissingRateError, match="CNB reference rate for CHF"):
        fx.convert(Decimal(1), "CHF", "CZK")


async def test_fetch_cnb_asks_for_the_snapshot_date() -> None:
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, text=CNB_TEXT)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        rates = await fetch_cnb(http, dt.date(2026, 9, 27))
    assert seen[0].params["date"] == "27.09.2026"
    # A Sunday request gets the last valid fixing, dated as ČNB dates it.
    assert {r.rate_date for r in rates} == {dt.date(2026, 9, 25)}


# The shape of ČNB's yearly file: a header per currency list, which restarts mid-year when
# the list changes (here RUB is dropped).
CNB_YEAR_TEXT = """Date|1 EUR|100 HUF|1 RUB|1 USD
02.01.2026|24.335|6.123|0.215|22.050
05.01.2026|24.300|6.110|0.214|22.100
Date|1 EUR|100 HUF|1 USD
06.01.2026|24.280|6.100|22.150
"""


def test_parse_cnb_year_reads_every_day_and_a_changed_header() -> None:
    from steward.fx.sources import parse_cnb_year

    rates = parse_cnb_year(CNB_YEAR_TEXT)
    by = {(r.rate_date, r.currency): r for r in rates}
    assert len(rates) == 11
    assert by[(dt.date(2026, 1, 2), "USD")].rate == Decimal("22.050")
    assert by[(dt.date(2026, 1, 5), "HUF")].rate == Decimal("0.0611")  # per 1 unit
    assert by[(dt.date(2026, 1, 6), "USD")].rate == Decimal("22.150")
    assert (dt.date(2026, 1, 6), "RUB") not in by
    with pytest.raises(FxSourceError):
        parse_cnb_year("02.01.2026|24.335\n")
