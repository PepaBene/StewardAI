"""UK TR-1 parser on three fixtures written to the FCA standard-form wording. The wording and the
plain-text table layout are UNVERIFIED against live RNS announcements."""

# ruff: noqa: E501
import datetime as dt
from decimal import Decimal as D

import pytest

from steward.sources.base import AccessState
from steward.sources.tr1_uk import (
    derive_thresholds,
    is_tr1_announcement,
    parse_tr1_notice,
    tr1_access,
)

UTC = dt.UTC
ANNOUNCED = dt.datetime(2026, 5, 1, 7, 2, tzinfo=UTC)

# Disposal: table cells separated by " | ", dates as 29-Apr-2026, a previous notification.
DISPOSAL = """TR-1: NOTIFICATION OF MAJOR HOLDINGS (to be sent to the relevant issuer and to the FCA)
1a. Identity of the issuer or the underlying issuer of existing shares to which voting rights are attached: ii
Issuer Name
Example Widgets plc
UK or Non-UK Issuer
UK
2. Reason for the notification
An acquisition or disposal of voting rights
3. Details of person subject to the notification obligation iv
Name
Northern Asset Management Ltd
City and country of registered office (if applicable)
Edinburgh, UK
4. Full name of shareholder(s) (if different from 3.) v
Name
City and country of registered office
5. Date on which the threshold was crossed or reached vi:
29-Apr-2026
6. Date on which Issuer notified:
01-May-2026
7. Total positions of person(s) subject to the notification obligation
 | % of voting rights attached to shares | % of voting rights through financial instruments | Total of both in % | Total number of voting rights held in issuer
Resulting situation on the date on which threshold was crossed or reached | 4.97% | 0.00% | 4.97% | 12,345,678
Position of previous notification (if applicable) | 5.12% | 0.00% | 5.12%
8. Notified details of the resulting situation on the date on which the threshold was crossed or reached
"""

# Acquisition with instruments, a different shareholder, written-out dates, no previous position.
ACQUISITION = """Form TR-1: Notification of major holdings
1a. Identity of the issuer or the underlying issuer of existing shares to which voting rights are attached: Sample Energy PLC
UK or Non-UK Issuer: UK
3. Details of person subject to the notification obligation
Name: Beta Capital Partners LLP
City and country of registered office (if applicable): London, United Kingdom
4. Full name of shareholder(s) (if different from 3.)
Name: BCP Nominees Limited
5. Date on which the threshold was crossed or reached: 2 May 2026
6. Date on which Issuer notified: 4 May 2026
7. Total positions of person(s) subject to the notification obligation
Resulting situation on the date on which threshold was crossed or reached 8.10% 1.35% 9.45% 4,250,000
Position of previous notification (if applicable) N/A N/A N/A
8. Notified details of the resulting situation
"""

# No percent stated in the resulting position, and an unreadable crossing date.
NO_PERCENT = """Holding(s) in Company
Form TR-1 Notification of major holdings
1a. Identity of the issuer or the underlying issuer of existing shares to which voting rights are attached: Gamma Mining plc
3. Details of person subject to the notification obligation
Name: Delta Fund
5. Date on which the threshold was crossed or reached: on or about the end of April
6. Date on which Issuer notified: 6 May 2026
7. Total positions of person(s) subject to the notification obligation
Resulting situation on the date on which threshold was crossed or reached | N/A | N/A | N/A | N/A
Position of previous notification (if applicable) | 3.01% | 0.00% | 3.01%
8. Notified details of the resulting situation
"""


def test_disposal_below_five_percent_with_previous_position() -> None:
    notice = parse_tr1_notice(DISPOSAL, ANNOUNCED)
    assert notice is not None
    assert notice.holder == "Northern Asset Management Ltd"
    assert notice.holder_office == "Edinburgh, UK"
    assert notice.issuer == "Example Widgets plc"
    assert notice.shareholder_if_different is None
    assert (notice.percent_shares_after, notice.percent_instruments_after) == (D("4.97"), D("0.00"))
    assert notice.percent_total_after == D("4.97") and notice.voting_rights_after == D(12345678)
    assert notice.percent_total_before == D("5.12")
    assert notice.thresholds_crossed == (5,) and notice.direction == "down"
    assert notice.date_crossed == dt.date(2026, 4, 29)
    assert notice.date_issuer_notified == dt.date(2026, 5, 1)
    assert notice.known_at == ANNOUNCED and notice.gap is None  # not the crossing date


def test_acquisition_keeps_parts_apart_and_a_missing_previous_position_is_null() -> None:
    notice = parse_tr1_notice(ACQUISITION, ANNOUNCED)
    assert notice is not None
    assert notice.holder == "Beta Capital Partners LLP"
    assert notice.shareholder_if_different == "BCP Nominees Limited"
    assert notice.issuer == "Sample Energy PLC"
    assert notice.percent_shares_after == D("8.10")
    assert notice.percent_instruments_after == D("1.35")
    assert notice.percent_total_after == D("9.45")  # as stated, not computed here
    assert notice.percent_total_before is None  # "N/A" is not zero
    assert notice.thresholds_crossed is None and notice.direction is None
    assert notice.date_crossed == dt.date(2026, 5, 2)
    assert notice.date_issuer_notified == dt.date(2026, 5, 4)


def test_notice_without_a_percent_is_a_typed_gap_and_bad_dates_stay_null() -> None:
    notice = parse_tr1_notice(NO_PERCENT, ANNOUNCED)
    assert notice is not None
    assert notice.gap == "no_percent_stated"
    assert notice.percent_total_after is None and notice.voting_rights_after is None
    assert notice.percent_total_before == D("3.01")
    assert notice.date_crossed is None  # unparseable stays null
    assert notice.date_issuer_notified == dt.date(2026, 5, 6)
    assert notice.holder == "Delta Fund" and notice.known_at == ANNOUNCED


def test_missing_cells_make_the_position_row_unreadable_not_guessed() -> None:
    text = ACQUISITION.replace("8.10% 1.35% 9.45% 4,250,000", "9.45%")
    notice = parse_tr1_notice(text, ANNOUNCED)
    assert notice is not None and notice.gap == "position_row_unreadable"
    assert notice.percent_total_after is None and notice.percent_shares_after is None


def test_other_text_is_not_a_notice_and_naive_time_is_refused() -> None:
    assert (
        parse_tr1_notice("Director/PDMR Shareholding. Details of the transaction", ANNOUNCED)
        is None
    )
    assert parse_tr1_notice("Notification of major holdings: see attached PDF", ANNOUNCED) is None
    with pytest.raises(ValueError):
        parse_tr1_notice(DISPOSAL, dt.datetime(2026, 5, 1, 7, 2))


def test_thresholds_up_and_down_and_standing_status() -> None:
    assert derive_thresholds(D("2.5"), D("3.0")) == ((3,), "up")  # reached counts
    assert derive_thresholds(D("4.2"), D("6.1")) == ((5, 6), "up")
    assert derive_thresholds(D("6.0"), D("5.9")) == ((6,), "down")
    assert derive_thresholds(D("3.5"), D("3.9")) == ((), None)
    assert derive_thresholds(None, D("3.9")) == (None, None)
    assert is_tr1_announcement("Holding(s) in Company") and not is_tr1_announcement(
        "Trading update"
    )
    assert tr1_access().state == AccessState.ACCESS_UNVERIFIED
