import datetime as dt

from steward.earnings.ir_scan import best_link
from steward.sources.edgar import looks_like_results

PAGE = """
<ul>
<li>Q2 Results: Jul 24, 2026
  <a href="/files/q2_2026.pdf">Q2 Results (Jan-Jun 2026) [PDF]</a>
  <a href="/files/q2_2026_supp.pdf">Supplementary Material [PDF]</a>
  <a href="/files/q2_2026_slides.pdf">Presentation Slides</a></li>
<li>Q1 Results: Apr 24, 2026
  <a href="/files/q1_2026.pdf">Q1 Results (Jan-Mar 2026) [PDF]</a></li>
<li>Q2 Results: Jul 25, 2025
  <a href="/files/q2_2025.pdf">Q2 Results (Jan-Jun 2025) [PDF]</a></li>
</ul>
"""
DOMAINS = ["example.com"]
DATE = dt.date(2026, 7, 24)


def test_picks_the_release_for_the_period_not_slides_or_other_years() -> None:
    found = best_link(PAGE, "https://www.example.com/ir/", 2026, "Q2", DATE, DOMAINS)
    assert found is not None
    assert found[0] == "https://www.example.com/files/q2_2026.pdf"


def test_a_new_quarter_is_found_without_changing_anything() -> None:
    found = best_link(
        PAGE, "https://www.example.com/ir/", 2026, "Q1", dt.date(2026, 4, 24), DOMAINS
    )
    assert found is not None and found[0].endswith("q1_2026.pdf")


def test_no_match_for_a_period_not_yet_published() -> None:
    assert (
        best_link(PAGE, "https://www.example.com/ir/", 2026, "Q3", dt.date(2026, 10, 24), DOMAINS)
        is None
    )


def test_links_off_the_allowed_domains_are_ignored() -> None:
    page = '<p>Q2 2026 results <a href="https://other.org/q2_2026.pdf">Q2 2026 results</a></p>'
    assert best_link(page, "https://www.example.com/", 2026, "Q2", DATE, DOMAINS) is None


def test_looks_like_results() -> None:
    release = (
        "Nu Holdings reports second quarter 2026 results. " + "Revenue rose. Net income grew. " * 3
    )
    assert looks_like_results(release)
    assert not looks_like_results("Notice of annual general meeting. Revenue. Net income. Revenue.")
