"""Linking items to holdings through each holding's news profile.

A holding is matched by its company name and aliases, its ticker written as a ticker
("$AAPL", "(NASDAQ: AAPL)"), or its ISIN. Named executives and brands link too. Named
competitors, suppliers and customers, the top constituents of a held fund, and the watch topics
link an item to the holding with the relation recorded, so news about a key supplier reaches
the owner even when the holding is not mentioned. Items that match nothing are dropped.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import cache

from steward.config.holding_profiles import HoldingProfile

_COMPANY_SUFFIX = re.compile(
    r"[,\s]+(inc\.?|incorporated|corp\.?|corporation|co\.?|company|plc|ltd\.?|limited|"
    r"holdings?|group|n\.?\s?v\.?|se|ag|s\.?a\.?|a/s|ab|asa|oyj|s\.?p\.?a\.?|class [a-c]|"
    r"adr|\(the\))$",
    re.IGNORECASE,
)


def short_name(name: str) -> str:
    """ "Apple Inc." -> "Apple", "ASML Holding N.V." -> "ASML", "Novo Nordisk A/S" ->
    "Novo Nordisk"."""
    previous = None
    text = name.strip()
    while previous != text:
        previous = text
        text = _COMPANY_SUFFIX.sub("", text).strip(" ,.")
    return re.sub(r"^the\s+", "", text, flags=re.IGNORECASE)


# How sure a link of each kind is, and which kinds reach the holding only indirectly.
LINK_CONFIDENCE: dict[str, float] = {
    "source": 1.0,
    "isin": 1.0,
    "ticker": 0.95,
    "name": 0.85,
    "alias": 0.75,
    # A named executive or a product brand is about the company itself, but a name alone
    # is weaker evidence than the company's own.
    "executive": 0.7,
    "brand": 0.7,
    "supplier": 0.6,
    "customer": 0.6,
    # A top constituent of a held fund: the fund is reached through it, not named.
    "constituent": 0.5,
    "competitor": 0.5,
    "topic": 0.4,
}
INDIRECT_VIA: frozenset[str] = frozenset(
    {"competitor", "supplier", "customer", "constituent", "topic"}
)


@dataclass(frozen=True)
class Link:
    ticker: str
    via: str  # name, alias, ticker, isin, source, competitor, supplier, customer, topic
    term: str

    @property
    def confidence(self) -> float:
        """How sure the link is: by identifier or the source itself, by name, or only through
        a competitor, supplier, customer or topic."""
        return LINK_CONFIDENCE.get(self.via, 0.5)

    def as_json(self) -> dict[str, str | float]:
        return {
            "ticker": self.ticker,
            "via": self.via,
            "term": self.term,
            "confidence": self.confidence,
        }


@dataclass
class HoldingTerms:
    ticker: str
    names: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    isin: str | None = None
    relations: list[tuple[str, str]] = field(default_factory=list)  # (via, term)
    topics: list[str] = field(default_factory=list)

    def display_name(self) -> str:
        return self.names[0] if self.names else self.ticker


@cache
def _places() -> frozenset[str]:
    from babel import Locale

    return frozenset(name.casefold() for name in Locale("en").territories.values())


_PUBLIC_BODY = re.compile(r"\s+(?:governments?|armed forces|army|navy|military|ministry)$", re.I)


def names_a_company(term: str) -> bool:
    """Whether a customer, supplier or competitor term can stand for its company in a story.
    A place or its state ("Ukraine", "Germany government") is in every story about the place,
    and a common noun ("bottlers") in every story about the trade: neither is linked."""
    text = term.strip()
    if not any(ch.isupper() for ch in text):
        return False
    return _PUBLIC_BODY.sub("", text).casefold() not in _places()


def holding_terms(
    ticker: str,
    profile: HoldingProfile | None,
    instrument_name: str | None,
    isin: str | None,
) -> HoldingTerms:
    names: list[str] = []
    for raw in (profile.company_name if profile else "", instrument_name or ""):
        if raw:
            names.extend([raw, short_name(raw)])
    terms = HoldingTerms(ticker=ticker, names=list(dict.fromkeys(n for n in names if n)))
    terms.isin = isin
    symbol = (
        profile.edgar_ticker()
        if profile
        else ticker.split("_")[0].rstrip("abcdefghijklmnopqrstuvwxyz")
    )
    if symbol:
        terms.symbols = [symbol]
    if profile:
        terms.aliases = list(profile.aliases)
        terms.relations = [
            *(("executive", e) for e in profile.executives),
            *(("brand", b) for b in profile.brands),
            *(
                (via, term)
                for via, names in (
                    ("competitor", profile.competitors),
                    ("supplier", profile.suppliers),
                    ("customer", profile.customers),
                )
                for term in names
                if names_a_company(term)
            ),
        ]
        terms.topics = list(profile.watch_topics)
    return terms


_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")


def _name_pattern(term: str) -> re.Pattern[str]:
    """Whole-word match. Short or all-caps names (SAP, ASML) must match their case exactly
    so ordinary words do not. A single-word name (Target, Apple, Shell) is often an ordinary
    word too, so it matches as written or in capitals ("TARGET"), never "price target".
    Longer multi-word names match case-insensitively."""
    escaped = re.escape(term).replace(r"\ ", r"\s+")
    if _CJK.search(term):
        # CJK text has no spaces: a name is followed by a particle or more text ("トヨタは"),
        # which the word-boundary test below would refuse.
        return re.compile(escaped)
    if len(term) <= 4 or term.isupper():
        return re.compile(rf"(?<![\w&]){escaped}(?![\w&])")
    if not re.search(r"\s", term.strip()):
        upper = re.escape(term.upper())
        return re.compile(rf"(?<![\w&])(?:{escaped}|{upper})(?![\w&])")
    return re.compile(rf"(?<![\w&]){escaped}(?![\w&])", re.IGNORECASE)


def topic_pattern(topic: str) -> re.Pattern[str]:
    """Whole-word match for a watch topic: "AI" must not match "said". A short all-caps topic
    (AI, EV) matches its case exactly; any other topic matches case-insensitively."""
    escaped = re.escape(topic).replace(r"\ ", r"\s+")
    flags = 0 if (len(topic) <= 4 and topic.isupper()) else re.IGNORECASE
    return re.compile(rf"(?<!\w){escaped}(?!\w)", flags)


def _symbol_pattern(symbol: str) -> re.Pattern[str]:
    s = re.escape(symbol)
    exchanges = r"(?:NASDAQ|NYSE|Nasdaq|NYSE\s+American|XETRA|Xetra|ETR|FRA|CPH|LSE|AMS|EPA|SWX)"
    # "$700 million" and "$700.50" are prices, not the cashtag $700.
    price = r"(?![.,]\d)(?!\s*(?i:million|billion|trillion|thousand|mn|bn|m|b|k)\b)"
    return re.compile(rf"(\${s}\b{price}|\(\s*{s}\s*\)|{exchanges}\s*:\s*{s}\b)")


class Linker:
    def __init__(self, holdings: Sequence[HoldingTerms]) -> None:
        self._holdings = list(holdings)
        self._patterns: list[tuple[str, str, str, re.Pattern[str]]] = []
        for h in self._holdings:
            for name in h.names:
                if len(name) >= 2:
                    self._patterns.append((h.ticker, "name", name, _name_pattern(name)))
            for alias in h.aliases:
                self._patterns.append((h.ticker, "alias", alias, _name_pattern(alias)))
            for symbol in h.symbols:
                self._patterns.append((h.ticker, "ticker", symbol, _symbol_pattern(symbol)))
            if h.isin:
                self._patterns.append((h.ticker, "isin", h.isin, re.compile(re.escape(h.isin))))
            for via, term in h.relations:
                self._patterns.append((h.ticker, via, term, _name_pattern(term)))
            for topic in h.topics:
                self._patterns.append((h.ticker, "topic", topic, topic_pattern(topic)))

    @property
    def tickers(self) -> list[str]:
        return [h.ticker for h in self._holdings]

    def link(self, text: str, direct: Iterable[str] = ()) -> list[Link]:
        """Every holding the text touches, the most direct reason first per holding.
        `direct` holdings (the source is about them) are linked without a match."""
        known = set(self.tickers)
        found: dict[str, Link] = {}
        for ticker in direct:
            if ticker in known:
                found[ticker] = Link(ticker, "source", ticker)
        rank = [
            "source",
            "name",
            "alias",
            "ticker",
            "isin",
            "executive",
            "brand",
            "competitor",
            "supplier",
            "customer",
            "constituent",
            "topic",
        ]
        for ticker, via, term, pattern in self._patterns:
            if pattern.search(text) is None:
                continue
            current = found.get(ticker)
            if current is None or rank.index(via) < rank.index(current.via):
                found[ticker] = Link(ticker, via, term)
        return sorted(found.values(), key=lambda link: (rank.index(link.via), link.ticker))
