"""Finding the results document by reading the company's own results pages (no model).

The holding profile lists results pages (`results_urls`, filled by IR discovery). Such a page
links each quarter's release; this scores the links against the period being checked and
returns the best one, so a new quarter's document is found without a search call. When no
link clearly matches, the caller falls back to the web search.
"""

import datetime as dt
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx

from steward.earnings.locate import Located, host_allowed

# Words in the link and the text around it that name the period, per fiscal period.
PERIOD_WORDS: dict[str, tuple[str, ...]] = {
    "Q1": ("q1", "1q", "first quarter", "jan-mar", "jan - mar", "january - march", "three months"),
    "Q2": (
        "q2",
        "2q",
        "second quarter",
        "h1",
        "1h",
        "first half",
        "half year",
        "half-year",
        "jan-jun",
        "jan - jun",
        "january - june",
        "six months",
    ),
    "Q3": ("q3", "3q", "third quarter", "nine months", "jan-sep", "jan - sep"),
    "Q4": (
        "q4",
        "4q",
        "fourth quarter",
        "full year",
        "full-year",
        "annual",
        "jan-dec",
        "year-end",
        "year end",
    ),
    "FY": ("full year", "full-year", "annual", "fy", "year-end", "year end", "q4", "4q"),
}
WANTED = ("result", "earnings", "financial", "press release", "tanshin", "report", "release")
UNWANTED = (
    "presentation",
    "slides",
    "transcript",
    "webcast",
    "call",
    "supplement",
    "fact sheet",
    "factsheet",
    "q&a",
    "notice",
    "agm",
    "sustainability",
    "esg",
    "proxy",
    "calendar",
)
DOCUMENT = re.compile(r"\.(pdf|html?)(\W|$)", re.I)
MIN_SCORE = 4


class _Links(HTMLParser):
    """(href, link text, text of the row it sits in) for every anchor."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []
        self._context: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("li", "tr", "p", "div", "dd"):
            self._context = []
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        self._context.append(data)
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            label = re.sub(r"\s+", " ", " ".join(self._text))
            context = re.sub(r"\s+", " ", " ".join(self._context))
            self.links.append((self._href, label, context))
            self._href = None


def score_link(
    href: str,
    label: str,
    context: str,
    fiscal_year: int,
    fiscal_period: str,
    report_date: dt.date,
) -> int:
    """How well a link looks like the results document of this period; 0 when it cannot be.
    Exclusion words count only in the link itself: its row may also hold a slides link."""
    own = f"{href} {label}".lower()
    if not DOCUMENT.search(href) or any(w in own for w in UNWANTED):
        return 0
    haystack = f"{own} {context.lower()}"
    words = PERIOD_WORDS.get(fiscal_period, ())
    period = any(w in haystack for w in words)
    years = {str(fiscal_year), str(fiscal_year)[2:]}
    year = any(re.search(rf"(?<!\d){y}(?!\d)", haystack) for y in years) or (
        str(report_date.year) in haystack
    )
    if not (period and year):
        return 0
    score = 3 + sum(w in haystack for w in WANTED)
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%Y/%m/%d", "%Y-%m-%d"):
        if report_date.strftime(fmt).lower().replace(" 0", " ") in haystack:
            score += 3
            break
    return score


def best_link(
    html: str,
    page_url: str,
    fiscal_year: int,
    fiscal_period: str,
    report_date: dt.date,
    domains: list[str],
) -> tuple[str, int] | None:
    parser = _Links()
    parser.feed(html)
    ranked: list[tuple[int, str]] = []
    for href, label, context in parser.links:
        url = urljoin(page_url, href)
        if domains and not host_allowed(url, domains):
            continue
        score = score_link(href, label, context, fiscal_year, fiscal_period, report_date)
        if score >= MIN_SCORE:
            ranked.append((score, url))
    if not ranked:
        return None
    ranked.sort(key=lambda r: (-r[0], r[1].lower().endswith(".pdf")))
    # Two equally good, different links make the match ambiguous: leave it to the search.
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0] and ranked[0][1] != ranked[1][1]:
        return None
    return ranked[0][1], ranked[0][0]


async def locate_on_pages(
    http: httpx.AsyncClient,
    pages: list[str],
    fiscal_year: int,
    fiscal_period: str,
    report_date: dt.date,
    domains: list[str],
) -> tuple[Located | None, list[str]]:
    """The best-scoring document link over the results pages, and what happened per page."""
    notes: list[str] = []
    for page in pages:
        try:
            response = await http.get(page, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            notes.append(f"{page}: {exc}")
            continue
        found = best_link(
            response.text, str(response.url), fiscal_year, fiscal_period, report_date, domains
        )
        if found is not None:
            url, score = found
            return Located(url, "ir_page", f"link on {page} (score {score})"), notes
        notes.append(f"{page}: no link clearly matching the period")
    return None, notes
