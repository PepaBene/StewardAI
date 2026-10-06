import datetime as dt

from steward.config.jobs import GapResearchSettings, JobsConfig
from steward.research.gaps import SLOTS
from steward.research.web_facts import (
    Candidate,
    FactVerdict,
    check_value,
    classify,
    decide,
    number_in_text,
    parse_count,
    quote_in_text,
)

CFG = GapResearchSettings()
TODAY = dt.date(2026, 10, 6)


def cand(value: str, quote: str) -> Candidate:
    return Candidate(value=value, url="https://x.example/p", quote=quote, reason="r")


def verdict(confidence: float = 0.9, entity: bool = True, supports: bool = True) -> FactVerdict:
    return FactVerdict(
        right_entity=entity, supports_value=supports, confidence=confidence, reasons="ok"
    )


def test_tiers() -> None:
    own = {"csg.com"}
    assert classify("https://investors.csg.com/en", own, CFG) == "T1"
    assert classify("https://www.sec.gov/x", own, CFG) == "T1"
    assert classify("https://www.reuters.com/x", own, CFG) == "T2"
    assert classify("https://en.wikipedia.org/wiki/X", own, CFG) == "T3"
    assert classify("https://www.reddit.com/r/x", own, CFG) == "rejected"
    assert classify("https://random-blog.example/post", own, CFG) == "rejected"


def test_quote_must_be_on_the_page() -> None:
    page = "Annual   results\nwill be published on 18 February 2027 after market close."
    assert quote_in_text("will be published on 18 February 2027", page)
    assert not quote_in_text("will be published on 19 February 2027", page)
    assert not quote_in_text("short", "short")


def test_date_value_checks() -> None:
    slot = SLOTS["next_report_date"]
    ok, problems = check_value(slot, cand("2027-02-18", "results on 18 February 2027"), TODAY)
    assert problems == [] and ok is not None and ok.value_date == dt.date(2027, 2, 18)
    # the quote prints another date
    assert check_value(slot, cand("2027-02-18", "results on 19 February 2027"), TODAY)[1]
    # a date in the past, or too far ahead
    assert check_value(slot, cand("2026-01-05", "results on 5 January 2026"), TODAY)[1]
    assert check_value(slot, cand("2029-02-18", "results on 18 February 2029"), TODAY)[1]
    assert check_value(slot, cand("soon", "results soon"), TODAY)[1]


def test_number_value_checks() -> None:
    slot = SLOTS["shares_outstanding"]
    assert parse_count("1,234,567") == 1234567
    assert parse_count("1.2 billion") is None
    assert number_in_text(1234567, "There were 1 234 567 shares outstanding")
    assert number_in_text(1234567, "1,234,567 ordinary shares")
    assert not number_in_text(1234567, "12,345,678 shares")
    ok, problems = check_value(slot, cand("1234567", "1,234,567 ordinary shares"), TODAY)
    assert problems == [] and ok is not None and ok.value_num == 1234567.0
    assert check_value(slot, cand("1234567", "12,345,678 ordinary shares"), TODAY)[1]


def test_description_must_be_the_quote() -> None:
    slot = SLOTS["company_description"]
    text = "CSG is a European defence group that makes small arms and ammunition worldwide."
    assert check_value(slot, cand(text, text), TODAY)[1] == []
    assert check_value(
        slot, cand("A completely different invented sentence about nothing.", text), TODAY
    )[1]


def test_decisions() -> None:
    date_slot, number_slot = SLOTS["next_report_date"], SLOTS["shares_outstanding"]
    desc = SLOTS["company_description"]
    assert decide(number_slot, "T1", verdict(), 0.8, False)[0] == "approved"
    # a tier 2 figure needs a second agreeing source, a tier 2 date does not
    assert decide(number_slot, "T2", verdict(), 0.8, False)[0] == "needs_review"
    assert decide(number_slot, "T2", verdict(), 0.8, True)[0] == "approved"
    assert decide(date_slot, "T2", verdict(), 0.8, False)[0] == "approved"
    # Wikipedia never fills a figure or a date, but may fill a description
    assert decide(number_slot, "T3", verdict(), 0.8, False)[0] == "rejected"
    assert decide(date_slot, "T3", verdict(), 0.8, False)[0] == "rejected"
    assert decide(desc, "T3", verdict(), 0.8, False)[0] == "approved"
    assert decide(number_slot, "rejected", verdict(), 0.8, True)[0] == "rejected"
    # the approver's doubt keeps it out
    assert decide(number_slot, "T1", verdict(0.5), 0.8, False)[0] == "needs_review"
    assert decide(number_slot, "T1", verdict(entity=False), 0.8, False)[0] == "rejected"
    assert decide(number_slot, "T1", None, 0.8, False)[0] == "needs_review"


def test_estimates_are_unavailable() -> None:
    assert SLOTS["analyst_estimates"].unavailable


def test_job_is_scheduled() -> None:
    config = JobsConfig()
    assert "gap_research" in config.schedules()
    assert "analyst_estimates" not in config.gap_research_settings.slots
