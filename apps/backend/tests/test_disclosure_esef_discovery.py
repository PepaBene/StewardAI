"""P2.5: ESEF discovery from a filing-index fixture shaped like the public filings.xbrl.org API
(shape unverified against the live service)."""

import datetime as dt
from typing import Any

import pytest

from steward.disclosure.esef_discovery import REASONS, discover_esef

UTC = dt.UTC
LEI_A = "5493001KJTIIGC8Y1R12"
LEI_B = "529900T8BM49AURSDO55"


def entry(
    rid: str | None,
    lei: str | None = LEI_A,
    period: str = "2025-12-31",
    added: str | None = "2026-03-10 09:15:22.123456",
    processed: str | None = "2026-03-10 09:20:01",
    package: str | None = "/pkg/a.zip",
    **attrs: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "period_end": period,
        "date_added": added,
        "processed": processed,
        "package_url": package,
        "json_url": "/pkg/a.json",
        "report_url": "/pkg/index.html",
        "country": "DK",
        "error_count": 0,
        "warning_count": 2,
    }
    body.update(attrs)
    row: dict[str, Any] = {"type": "filing", "attributes": body}
    if rid is not None:
        row["id"] = rid
    if lei:
        row["relationships"] = {"entity": {"data": {"type": "entity", "id": lei}}}
    return row


def test_clean_entry_is_known_when_processed_never_at_period_end() -> None:
    out = discover_esef({"data": [entry("1")]})
    assert out.skipped == []
    (found,) = out.discovered
    assert found.known_at == dt.datetime(2026, 3, 10, 9, 20, 1, tzinfo=UTC)  # the later of the two
    assert found.period_end == dt.date(2025, 12, 31) and found.lei == LEI_A
    assert found.package_url == "https://filings.xbrl.org/pkg/a.zip"
    assert found.json_url == "https://filings.xbrl.org/pkg/a.json" and found.country == "DK"


def test_the_live_index_names_the_entity_only_by_link_and_filing_id() -> None:
    """Shape seen on filings.xbrl.org (2026-10-07): no `data` under the entity relationship."""
    row = entry("4261", lei=None, fxo_id=f"{LEI_A}-2025-12-31-ESEF-DK-0")
    row["relationships"] = {"entity": {"links": {"related": f"/api/entities/{LEI_A}"}}}
    (found,) = discover_esef({"data": [row]}).discovered
    assert found.lei == LEI_A
    bare = entry("4262", lei=None, fxo_id=f"{LEI_B}-2025-12-31-ESEF-DK-0")
    (found,) = discover_esef({"data": [bare]}).discovered
    assert found.lei == LEI_B  # the filing id is the fallback


def test_entries_with_processing_errors_are_skipped_and_reported() -> None:
    index = {
        "data": [
            entry("1"),
            entry("2", error_count=3),
            entry("3", errors=[{"code": "xbrl.5.1", "message": "Duplicate fact"}], error_count=1),
        ]
    }
    out = discover_esef(index)
    assert [f.filing_id for f in out.discovered] == ["1"]
    assert [(s.filing_id, s.reason) for s in out.skipped] == [
        ("2", "processing_errors"),
        ("3", "processing_errors"),
    ]
    assert "3 error(s)" in out.skipped[0].detail and "Duplicate fact" in out.skipped[1].detail
    assert out.skipped[1].lei == LEI_A


def test_every_other_defect_is_a_named_skip_not_a_guess() -> None:
    index = {
        "data": [
            entry("1", lei=None),
            entry("2", period="soon"),
            entry("3", package=None),
            entry("4", added=None, processed=None),
            entry("5", added="2025-06-01 00:00:00", processed=None),  # before its period end
            entry("6"),
            entry("6"),
            entry(None),
            "not an object",
        ]
    }
    out = discover_esef(index)
    assert [s.reason for s in out.skipped] == [
        "no_lei",
        "bad_period_end",
        "no_package_url",
        "no_added_time",
        "added_before_period_end",
        "duplicate",
        "no_filing_id",
        "no_filing_id",
    ]
    assert {s.reason for s in out.skipped} <= set(REASONS)
    assert [f.filing_id for f in out.discovered] == ["6"]


def test_a_single_time_is_used_and_offsets_are_honoured() -> None:
    out = discover_esef({"data": [entry("1", added=None, processed="2026-03-10T10:00:00+01:00")]})
    assert out.discovered[0].known_at == dt.datetime(2026, 3, 10, 9, 0, tzinfo=UTC)


def test_incremental_polling_and_ordering() -> None:
    index = {
        "data": [
            entry("1", added="2026-03-01 00:00:00", processed=None),
            entry("2", lei=LEI_B, added="2026-04-01 00:00:00", processed=None),
        ]
    }
    everything = discover_esef(index)
    assert [f.filing_id for f in everything.discovered] == ["2", "1"]  # newest first
    later = discover_esef(index, since=dt.datetime(2026, 3, 15, tzinfo=UTC))
    assert [f.filing_id for f in later.discovered] == ["2"] and later.skipped == []


def test_a_body_that_is_not_an_index_raises_instead_of_reading_as_empty() -> None:
    with pytest.raises(ValueError):
        discover_esef({"errors": [{"status": "500"}]})
    assert discover_esef({"data": []}).discovered == []
