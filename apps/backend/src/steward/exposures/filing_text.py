"""Reading an annual report as text, and checking what an AI says it found in it.

Everything an extractor returns carries a verbatim quote. `verified` is the mechanical gate in
front of every stored row: the quote must be on the page, and the figure, the name or the
currency it supports must be printed inside the quote itself.
"""

import datetime as dt
import re
from dataclasses import dataclass

import asyncpg

from steward.config.holding_profiles import HoldingProfile
from steward.documents.text import html_to_text, plain_to_text
from steward.research.web_facts import quote_in_text
from steward.sources.edgar import EdgarClient, EdgarError, Filing, latest_periodic

SECTION_CAP = 30_000  # characters of one section sent to the model
ITEMS: dict[str, dict[str, str]] = {
    # logical section -> item label, by form family
    "10-K": {"business": "1", "risks": "1A", "mdna": "7", "market_risk": "7A", "notes": "8"},
    "20-F": {"business": "4", "risks": "3", "mdna": "5", "market_risk": "11", "notes": "18"},
    "40-F": {"business": "1", "risks": "1A", "mdna": "7", "market_risk": "7A", "notes": "8"},
}
_ITEM = re.compile(r"(?im)^\s*item\s+(\d{1,2}[a-d]?)\s*[.:—–-]")  # noqa: RUF001
EXPOSURE_WORDS = re.compile(
    r"(?i)currenc|foreign exchange|exchange rate|hedg|commodit|raw material|steel|aluminum|"
    r"aluminium|copper|silicon|wafer|helium|lithium|oil|gas|energy|freight|component|"
    r"manufactur|supply"
)
DEPENDENCY_WORDS = re.compile(
    r"(?i)customer|supplier|vendor|distributor|reseller|sole source|single source|"
    r"depend|concentrat|accounted for|% of (our |total |net )?(revenue|sales|purchases)"
)


@dataclass(frozen=True)
class FilingText:
    ticker: str
    accession: str
    form: str
    source_url: str
    period_end: dt.date | None
    text: str


def form_family(form: str) -> str:
    base = form.upper().removesuffix("/A")
    return base if base in ITEMS else "10-K"


def split_items(text: str) -> dict[str, str]:
    """Item label ("1A", "7A") -> its text. Table-of-contents entries are short, so for each
    label the longest stretch up to the next item heading wins."""
    marks = [(m.start(), m.group(1).upper()) for m in _ITEM.finditer(text)]
    best: dict[str, str] = {}
    for i, (start, label) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        chunk = text[start:end]
        if len(chunk) > len(best.get(label, "")):
            best[label] = chunk
    return best


def relevant(section: str, pattern: re.Pattern[str], cap: int = SECTION_CAP) -> str:
    """The paragraphs of a section that mention the topic, up to `cap` characters."""
    kept: list[str] = []
    size = 0
    for paragraph in section.split("\n"):
        if len(paragraph) > 40 and pattern.search(paragraph):
            if size + len(paragraph) > cap:
                break
            kept.append(paragraph)
            size += len(paragraph) + 1
    return "\n".join(kept)


def sections_for(filing: FilingText, pattern: re.Pattern[str]) -> dict[str, str]:
    """The relevant paragraphs of each logical section of this filing."""
    if filing.form == "document":  # a results release or report with no numbered items
        text = relevant(filing.text, pattern)
        return {"document": text} if text else {}
    items = split_items(filing.text)
    wanted = ITEMS[form_family(filing.form)]
    out = {name: relevant(items.get(label, ""), pattern) for name, label in wanted.items()}
    return {name: text for name, text in out.items() if text}


async def latest_annual_filing(
    pool: asyncpg.Pool, edgar: EdgarClient, ticker: str, profile: HoldingProfile | None
) -> Filing | None:
    """The newest annual report (10-K, 20-F, 40-F) of the holding, or None."""
    cik = await pool.fetchval(
        "select cik from public.xbrl_filing where ticker = $1 limit 1", ticker
    )
    if cik is None and profile is not None:
        cik = profile.sec_cik or await edgar.cik_for_ticker(profile.edgar_ticker())
    if not cik:
        return None
    try:
        annual, _quarter = latest_periodic(await edgar.filings(str(cik)))
    except EdgarError:
        return None
    return annual


async def latest_annual_text(
    pool: asyncpg.Pool, edgar: EdgarClient, ticker: str, profile: HoldingProfile | None
) -> FilingText | None:
    annual = await latest_annual_filing(pool, edgar, ticker, profile)
    if annual is None:
        return None
    try:
        return await filing_text(edgar, ticker, annual)
    except EdgarError:
        return None


async def filing_text(edgar: EdgarClient, ticker: str, filing: Filing) -> FilingText | None:
    url = filing.url(filing.primary_document)
    body = (await edgar.document(url)).decode("utf-8", errors="replace")
    name = filing.primary_document.lower()
    text = html_to_text(body) if name.endswith((".htm", ".html", ".xhtml")) else plain_to_text(body)
    if len(text) < 5_000:
        return None
    return FilingText(ticker, filing.accession, filing.form, url, filing.report_date, text)


# ------------------------------------------------------------------------------ verification

_PERCENT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s?(?:%|percent|per cent)", re.IGNORECASE)
CURRENCY_NAMES: dict[str, tuple[str, ...]] = {
    "USD": ("u.s. dollar", "us dollar", "united states dollar", "usd"),
    "EUR": ("euro", "eur"),
    "GBP": ("pound", "sterling", "gbp"),
    "JPY": ("yen", "jpy"),
    "CNY": ("renminbi", "rmb", "yuan", "cny", "chinese"),
    "CHF": ("swiss franc", "chf"),
    "CAD": ("canadian dollar", "cad"),
    "AUD": ("australian dollar", "aud"),
    "KRW": ("won", "krw"),
    "TWD": ("new taiwan dollar", "taiwan dollar", "twd"),
    "INR": ("rupee", "inr"),
    "MXN": ("peso", "mxn"),
    "BRL": ("real", "reais", "brl"),
    "CZK": ("koruna", "czk"),
    "SEK": ("krona", "sek"),
    "SGD": ("singapore dollar", "sgd"),
}


def percent_in_quote(share: float, quote: str) -> bool:
    """Is `share` (a fraction) printed in the quote as a percentage?"""
    return any(abs(float(m) - share * 100) <= 0.051 for m in _PERCENT.findall(quote))


def word_in_quote(name: str, quote: str) -> bool:
    """The name as a whole word or phrase (with a plural), so 'space' is not 'aerospace'."""
    pattern = rf"(?<!\w){re.escape(' '.join(name.lower().split()))}(?:s|es)?(?!\w)"
    return re.search(pattern, " ".join(quote.lower().split())) is not None


def currency_in_quote(code: str, quote: str) -> bool:
    return any(word_in_quote(word, quote) for word in CURRENCY_NAMES.get(code, (code.lower(),)))


def verified(quote: str, text: str, share: float | None, names: tuple[str, ...]) -> list[str]:
    """Why a finding cannot be stored: an empty list means the quote is on the page, names
    every term it rests on and prints the share it claims."""
    problems: list[str] = []
    if not quote_in_text(quote, text):
        problems.append("the quote is not in the filing text")
    if share is not None and not percent_in_quote(share, quote):
        problems.append("the quote does not print that percentage")
    for name in names:
        if not word_in_quote(name, quote):
            problems.append(f"the quote does not mention {name}")
    return problems
