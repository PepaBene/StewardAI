from steward.config.holding_profiles import HoldingProfile
from steward.config.jobs import JobsConfig
from steward.fundamentals.ir_discovery import (
    check_url_shape,
    host_denied,
    is_blocked_page,
    merge_pages,
    page_text,
    registrable,
)

DENIED = ["wikipedia.org", "finance.yahoo.com"]


def test_registrable_domain() -> None:
    assert registrable("investors.csg.com") == "csg.com"
    assert registrable("www.chugai-pharm.co.jp") == "chugai-pharm.co.jp"
    assert registrable("example.com") == "example.com"


def test_denied_hosts_include_subdomains() -> None:
    assert host_denied("https://en.wikipedia.org/wiki/X", DENIED)
    assert host_denied("https://finance.yahoo.com/quote/X", DENIED)
    assert not host_denied("https://investors.csg.com/en", DENIED)


def test_url_must_come_from_search_or_the_ir_domain() -> None:
    seen = {"https://investors.csg.com/en"}
    assert check_url_shape("https://investors.csg.com/en", "csg.com", seen, DENIED) == []
    # not returned by the search, but on the IR page's own domain: allowed
    assert check_url_shape("https://investors.csg.com/en/results", "csg.com", seen, DENIED) == []
    # not returned and on another domain: a hallucination guard rejects it
    assert check_url_shape("https://other.example/ir", "csg.com", seen, DENIED)
    assert check_url_shape("http://investors.csg.com/en", "csg.com", seen, DENIED)
    assert check_url_shape(
        "https://en.wikipedia.org/wiki/CSG",
        "csg.com",
        seen | {"https://en.wikipedia.org/wiki/CSG"},
        DENIED,
    )


def test_page_text_strips_scripts_and_tags() -> None:
    title, text = page_text(
        b"<html><head><title>CSG &amp; Investors</title><script>var x=1</script></head>"
        b"<body><h1>Results</h1><p>Q2   2026</p></body></html>"
    )
    assert title == "CSG & Investors"
    assert text == "CSG & Investors Results Q2 2026" or "Results Q2 2026" in text
    assert "var x" not in text


def test_error_pages_are_recognised() -> None:
    assert is_blocked_page("Just a moment...", "Enable JavaScript and cookies")
    assert is_blocked_page("Investors", "Results and reports " * 200) is None


def test_merge_never_overwrites_owner_fields() -> None:
    profile = HoldingProfile.model_validate(
        {"ticker": "AAA_US_EQ", "ir_url": "https://owner.example/ir"}
    )
    merged, added = merge_pages(
        profile,
        "https://auto.example/ir",
        ["https://auto.example/results"],
        "https://auto.example/calendar",
    )
    assert str(merged.ir_url) == "https://owner.example/ir"
    assert "ir_url" not in added
    assert added["financial_calendar_url"] == "https://auto.example/calendar"
    assert [str(u) for u in merged.results_urls] == ["https://auto.example/results"]


def test_job_is_scheduled_with_defaults() -> None:
    config = JobsConfig()
    assert "ir_discovery" in config.schedules()
    assert config.ir_discovery_settings.min_confidence >= 0.5
    assert "wikipedia.org" in config.ir_discovery_settings.denied_domains
