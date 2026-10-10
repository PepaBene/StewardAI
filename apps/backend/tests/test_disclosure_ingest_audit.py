"""Deep-audit fixes D31 to D38h (disclosure ingest, statements and replay): hand-worked cases."""

import datetime as dt

from steward.disclosure import replay
from steward.disclosure.fetch import backoff_until
from steward.disclosure.financials import concept_choice, metric_for
from steward.disclosure.jobs import FILINGS_PER_ISSUER, new_filings
from steward.disclosure.parse import parse_html
from steward.disclosure.sec_discovery import DiscoveredFiling, link_amendments


def at(month: int, day: int, hour: int = 12) -> dt.datetime:
    return dt.datetime(2026, month, day, hour, tzinfo=dt.UTC)


def filing(acc: str, day: int, form: str = "10-Q", items: str = "", report=None):
    return DiscoveredFiling(
        "0000000001", acc, form, dt.date(2026, 5, day), None, report, "a.htm", items,
        at(5, day), {},
    )  # fmt: skip


# D31 -----------------------------------------------------------------------


def test_after_an_outage_the_oldest_new_filings_are_read_first() -> None:
    filings = [filing(f"0-{d}", d) for d in range(1, 9)]
    cursor = {"known_at": at(5, 2).isoformat(), "accession": "0-2"}
    got = new_filings(filings, cursor)
    # 6 are new; the oldest three are read, so the cursor moves to 0-5 and 0-6..0-8 follow
    assert [f.accession for f in got] == ["0-3", "0-4", "0-5"][:FILINGS_PER_ISSUER]
    # a first run has no history to catch up on and starts from the newest
    assert [f.accession for f in new_filings(filings, {})] == ["0-6", "0-7", "0-8"]


# D36 / D38g ------------------------------------------------------------------


def test_same_day_8k_amendment_links_to_the_filing_with_the_same_items() -> None:
    a = filing("A", 3, "8-K", "2.02", report=dt.date(2026, 5, 3))
    b = filing("B", 3, "8-K", "5.02", report=dt.date(2026, 5, 3))
    amend = DiscoveredFiling(
        "0000000001", "C", "8-K/A", dt.date(2026, 5, 4), None, dt.date(2026, 5, 3), "a.htm",
        "5.02", at(5, 4), {},
    )  # fmt: skip
    linked = link_amendments([a, b, amend])
    assert linked[2].amends_accession == "B"


def test_8k_amendment_with_no_matching_items_is_not_linked_by_guess() -> None:
    a = filing("A", 3, "8-K", "2.02", report=dt.date(2026, 5, 3))
    amend = DiscoveredFiling(
        "0000000001", "C", "8-K/A", dt.date(2026, 5, 4), None, dt.date(2026, 5, 3), "a.htm",
        "7.01", at(5, 4), {},
    )  # fmt: skip
    assert link_amendments([a, amend])[1].amends_accession is None


# D37 -----------------------------------------------------------------------


def test_a_fair_access_block_and_a_server_error_get_a_recorded_wait() -> None:
    now = at(5, 1)
    assert backoff_until(403, now) == now + dt.timedelta(hours=1)
    assert backoff_until(503, now) == now + dt.timedelta(minutes=5)
    assert backoff_until(404, now) is None


# D32 -----------------------------------------------------------------------


def test_feed_acceptance_time_is_corrected_to_true_utc() -> None:
    from steward.disclosure.sec_discovery import _accepted

    # (feed value, Eastern wall time from the filing's ACCEPTANCE-DATETIME header)
    cases = [
        ("2023-11-03T02:08:27.000Z", dt.datetime(2023, 11, 2, 22, 8, 27, tzinfo=dt.UTC)),  # EDT
        ("2024-02-02T04:03:38.000Z", dt.datetime(2024, 2, 1, 23, 3, 38, tzinfo=dt.UTC)),  # EST
        ("2024-08-02T02:03:34.000Z", dt.datetime(2024, 8, 1, 22, 3, 34, tzinfo=dt.UTC)),  # EDT
    ]
    for feed, true_utc in cases:
        assert _accepted(feed) == true_utc


# D33 -----------------------------------------------------------------------

TYPED = b"""<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
 xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi">
<body><div style="display:none"><ix:header><ix:resources>
<xbrli:context id="c1"><xbrli:entity><xbrli:identifier scheme="s">1</xbrli:identifier>
<xbrli:segment><xbrldi:typedMember dimension="ex:PlantAxis"><ex:id>P7</ex:id></xbrldi:typedMember>
</xbrli:segment></xbrli:entity><xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate>
<xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
</ix:resources></ix:header></div>
<p>Revenue <ix:nonFraction name="us-gaap:Revenues" contextRef="c1" unitRef="usd" decimals="0">5
</ix:nonFraction></p></body></html>"""


def test_a_typed_dimension_makes_the_context_dimensional() -> None:
    doc = parse_html(TYPED)
    assert doc.contexts["c1"].dimensions == (("ex:PlantAxis", "P7"),)


# D34 / D35 -----------------------------------------------------------------


def test_one_metric_from_two_concepts_keeps_the_preferred_one() -> None:
    slot = ("net_income", dt.date(2025, 1, 1), dt.date(2025, 12, 31))
    got = concept_choice([("ProfitLoss", slot), ("NetIncomeLoss", slot), ("Revenues", ("r",))])
    assert got == {slot: "NetIncomeLoss"}  # a single-concept slot is not listed


def test_lease_parts_are_never_picked_as_the_whole() -> None:
    slot = ("lease_liability", None, dt.date(2025, 12, 31))
    got = concept_choice([("OperatingLeaseLiability", slot), ("FinanceLeaseLiability", slot)])
    assert got == {slot: None}


# D38c ----------------------------------------------------------------------


def test_dei_share_count_maps_to_shares_outstanding() -> None:
    metric = metric_for("dei:EntityCommonStockSharesOutstanding")
    assert metric is not None and metric.key == "shares_outstanding"


# D38a ----------------------------------------------------------------------


def test_replay_items_carry_the_time_they_became_known() -> None:
    later = at(6, 1)
    items = replay._deals(
        {"deals": [{"id": 1, "title": "X", "state": "rumored", "known_at": later}]}
    )
    kept, leaked = replay.drop_future(items, at(5, 1))
    assert (kept, leaked) == ([], 1)
