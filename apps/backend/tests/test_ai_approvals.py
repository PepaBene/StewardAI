"""The AI approver's mechanical checks (they can only block a confirmation) and its config."""

from steward.approvals.ai import AI_PREFIX, listing_checks
from steward.config.holding_profiles import HoldingProfile
from steward.config.jobs import AiApprovalSettings, JobsConfig


def _profile(ticker: str, **extra: object) -> HoldingProfile:
    return HoldingProfile.model_validate({"ticker": ticker, "company_name": "X", **extra})


def _failed(checks: list) -> list[str]:  # type: ignore[type-arg]
    return [c.name for c in checks if c.ok is False]


def test_us_listing_with_matching_symbol_passes() -> None:
    checks = listing_checks(
        "FB_US_EQ", "US30303M1027", "XNAS", "META", _profile("FB_US_EQ", sec_ticker="META")
    )
    assert _failed(checks) == []
    assert any(c.name == "symbol" and c.ok for c in checks)


def test_us_listing_with_another_symbol_fails() -> None:
    checks = listing_checks("AAPL_US_EQ", "US0378331005", "XNAS", "AAPD", _profile("AAPL_US_EQ"))
    assert _failed(checks) == ["symbol"]


def test_listing_on_the_wrong_market_for_the_ticker_suffix_fails() -> None:
    # A Xetra-suffixed Trading212 ticker matched to a Tokyo listing is a different listing.
    checks = listing_checks("CUPd_EQ", "JP3519400000", "XTKS", "4519", None)
    assert _failed(checks) == ["market suffix"]


def test_amsterdam_listing_for_an_amsterdam_ticker_passes() -> None:
    checks = listing_checks("CSGa_EQ", "NL0015073TS8", "XAMS", "CSG", None)
    assert _failed(checks) == []


def test_us_isin_on_a_european_venue_does_not_count_as_a_pass() -> None:
    checks = listing_checks("XYZl_EQ", "US1234567890", "XLON", "XYZ", None)
    assert next(c for c in checks if c.name == "isin country").ok is None


def test_defaults_enable_both_queues_at_a_high_bar() -> None:
    cfg = AiApprovalSettings()
    assert cfg.listings and cfg.peers and cfg.min_confidence >= 0.8


def test_job_is_scheduled_by_default() -> None:
    assert "ai_approvals" in JobsConfig().schedules()
    assert AI_PREFIX == "AI approval"
