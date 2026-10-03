"""UK PDMR (MAR Article 19) disclosure adapter: a pilot parser for RNS announcements.

Persons discharging managerial responsibilities (PDMRs) and their closely associated persons
notify the issuer of their transactions, and the issuer publishes the notification through a
regulatory information service (RNS) within three business days. The notification follows
the template of the UK MAR implementing technical standards:

    1. Details of the PDMR / closely associated person      a) Name
    2. Reason for the notification                           a) Position/status
                                                             b) Initial notification / Amendment
    3. Details of the issuer                                 a) Name  b) LEI
    4. Details of the transaction(s)                         a) Instrument and identification code
                                                             b) Nature of the transaction
                                                             c) Price(s) and volume(s)
                                                             d) Aggregated information
                                                             e) Date of the transaction
                                                             f) Place of the transaction

Input is the existing RNS announcement stream (`news.regulatory` / `news.feeds.RawItem`,
whose Investegate headline is the announcement type): `is_pdmr_announcement` selects the
items, and the announcement body is parsed here.

Status of retrieval (checked live, 2026-10-03): the announcement headlines come from the
Investegate company page the news pipeline reads (its `robots.txt` allows `/company/` and
`/announcement/`); an announcement page such as
`/announcement/rns/vodafone-group--vod/director-pdmr-shareholding/9782046` is server-rendered
HTML (HTTP 200, no JavaScript needed) that contains the MAR template, so the body IS
retrievable and parseable without a browser. The live page showed two parser faults, fixed:
the last field (place of the transaction) ran on into the issuer's boilerplate and the
instrument kept the words "and identification code" of the label. The site asks private
investors to use it personally and not to redistribute or process it commercially: one page per
notification, at the news job's pace, no bulk crawling. Parsed notifications are persisted
(`pdmr_notification` / `pdmr_transaction`) by `ownership.pdmr_collect` from the RNS stream
and read back by `/holdings/{ticker}/pdmr-notifications`; every retrieval attempt, failed ones
included, is recorded, and `ownership.pdmr_read.pdmr_access_from_db` derives the standing
status from those attempts, so "available" only appears once a notification was really parsed.
`fetch_pdmr` returns a typed `PdmrFetchResult`: a retrieved page that does not contain the
template is `no_template` (access unverified), a 401/403 is `access_unverified`, a 429 is
`quota_exhausted`; it never turns a failed fetch into an empty list of transactions. The
nature of each transaction is mapped to the same economic classes as the US Form 4 pipeline;
a purchase or sale counts as open-market only when the stated place of the transaction is a
trading venue.
"""

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import httpx

from steward.documents.fetch import FetchError, default_headers, fetch_document
from steward.documents.text import html_to_text
from steward.sources.base import AccessResult, AccessState, access_from_http

MAX_BYTES = 2_000_000

_PDMR_HEADLINE = re.compile(
    r"\bPDMR\b|director[/ ]+pdmr|directors?['\u2019]? dealings?|notification of "
    r"(?:major )?(?:transaction|dealing)s? by (?:persons|directors)|"
    r"person(?:s)? discharging managerial",
    re.IGNORECASE,
)


def is_pdmr_announcement(headline: str, event_type: str | None = None) -> bool:
    """True for an RNS announcement that is a PDMR transaction notification.

    The event rules label these `insider_transaction`; the headline test also catches them
    when rules are not applied. A generic 'Holding(s) in Company' notice is not a PDMR one.
    """
    return bool(_PDMR_HEADLINE.search(headline)) or event_type == "insider_transaction"


@dataclass(frozen=True)
class PdmrTransaction:
    instrument: str | None
    isin: str | None
    nature: str | None
    price: Decimal | None
    currency: str | None
    volume: Decimal | None
    transaction_date: dt.date | None
    venue: str | None
    economic_class: str
    classification_basis: str


@dataclass(frozen=True)
class PdmrNotification:
    person_name: str | None
    position: str | None
    is_amendment: bool
    issuer_name: str | None
    lei: str | None
    transactions: list[PdmrTransaction] = field(default_factory=list)


@dataclass(frozen=True)
class PdmrFetchResult:
    access: AccessResult
    notification: PdmrNotification | None = None
    no_template: bool = False  # page retrieved, but it is not the MAR template


def pdmr_access() -> AccessResult:
    """Standing status of the UK PDMR pipeline: until a notification is parsed, unverified."""
    return AccessResult(
        AccessState.ACCESS_UNVERIFIED,
        "UK PDMR notifications: announcement bodies are retrievable as server-rendered HTML "
        "(checked live 2026-10-03) and the MAR Article 19 template parser reads them, but "
        "no notification has been parsed by the job yet, so UK insider activity is not "
        "demonstrated.",
    )


_ISIN = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}[0-9])\b")
_LEI = re.compile(r"\b([A-Z0-9]{18}[0-9]{2})\b")
_TRADING_VENUE = re.compile(
    r"\b(XLON|AIMX|XLOM|XPOS|CHIX|BATE|BATS|TRQX|AQXE|XOFF-?TV|London Stock Exchange|"
    r"AIM|Cboe|Aquis|Turquoise)\b",
    re.IGNORECASE,
)
_OUTSIDE = re.compile(r"outside (?:of )?a trading venue|XOFF|XXXX|off[- ]market", re.IGNORECASE)


def _section(text: str, start: str, end: str | None) -> str:
    begin = re.search(start, text, re.IGNORECASE)
    if not begin:
        return ""
    rest = text[begin.end() :]
    stop = re.search(end, rest, re.IGNORECASE) if end else None
    return rest[: stop.start()] if stop else rest


def _after(label: str, block: str, stop: str) -> str | None:
    match = re.search(label + r"\s*[:\-]?\s*(.*?)(?=" + stop + r"|$)", block, re.I | re.S)
    if not match:
        return None
    value = " ".join(match.group(1).split()).strip(" :-")
    return value or None


def _date(raw: str | None) -> dt.date | None:
    if not raw:
        return None
    iso = re.search(r"\d{4}-\d{2}-\d{2}", raw)
    if iso:
        return dt.date.fromisoformat(iso.group(0))
    cleaned = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", raw)
    for pattern, fmt in (
        (r"\d{1,2} [A-Za-z]+ \d{4}", "%d %B %Y"),
        (r"\d{1,2} [A-Za-z]{3} \d{4}", "%d %b %Y"),
        (r"\d{1,2}/\d{1,2}/\d{4}", "%d/%m/%Y"),
    ):
        found = re.search(pattern, cleaned)
        if found:
            try:
                return dt.datetime.strptime(found.group(0), fmt).date()
            except ValueError:
                continue
    return None


def _decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None


def classify_nature(nature: str | None, venue: str | None) -> tuple[str, str]:
    """Economic class of a PDMR transaction from its stated nature and place.

    Same classes as the Form 4 pipeline. Mechanics (exercise, award, gift, tax) are
    recognised before purchase / sale; a purchase or sale is open-market only when the
    notification gives a trading venue as the place of the transaction."""
    text = (nature or "").lower()
    if not text:
        return "other", "no nature of transaction stated"
    # Word boundaries: a bare substring would read "invested" as a vesting and "cisa" as ISA.
    if re.search(r"\bexercis", text):
        return "option_exercise", "nature states an exercise"
    if re.search(r"\b(?:award|grant|vest|allot|incentive|ltip|nil[- ]cost|share plan|bonus)", text):
        return "grant_award", "nature states an award, grant or vesting"
    if re.search(r"\bgift", text):
        return "gift", "nature states a gift"
    if re.search(r"\btax|\bwithh|\bto cover\b", text):
        return "tax_withholding", "nature states tax or sell-to-cover"
    if re.search(
        r"\b(?:dividend|scrip|reinvest|transfer|conversion|pension)|\b(?:isa|sipp)\b", text
    ):
        return "other", "nature states a reinvestment, transfer or conversion"
    side = None
    if re.search(r"\b(?:purchase|acquisition|acquire|bought|buy)", text):
        side = "open_market_buy"
    elif re.search(r"\b(?:sale|sold|disposal|dispose|sell)", text):
        side = "open_market_sell"
    if side is None:
        return "other", "nature not recognised"
    place = venue or ""
    if _OUTSIDE.search(place) or not _TRADING_VENUE.search(place):
        return "other", "purchase or sale, but the place is not stated as a trading venue"
    return side, "purchase or sale on a stated trading venue"


_PRICE_VOLUME = re.compile(
    r"(?P<cur>£|GBP|€|EUR|\$|USD)?\s?(?P<price>\d[\d,]*(?:\.\d+)?)\s?(?P<pence>p\b|pence)?"
    r"\s+(?P<volume>\d[\d,]*(?:\.\d+)?)(?!\d)",
    re.IGNORECASE,
)


def _price_volume(block: str) -> tuple[Decimal | None, str | None, Decimal | None]:
    cleaned = re.sub(r"price\(s\)|volume\(s\)|price|volume", " ", block, flags=re.I)
    if re.search(r"\bnil\b|\bn/?a\b", cleaned, re.I):
        number = re.findall(r"\d[\d,]*(?:\.\d+)?", cleaned)
        return None, None, _decimal(number[-1]) if number else None
    match = _PRICE_VOLUME.search(cleaned)
    if not match:
        return None, None, None
    cur = (match.group("cur") or "").upper()
    currency = {"£": "GBP", "€": "EUR", "$": "USD"}.get(cur, cur or None)
    if match.group("pence"):
        currency = "GBX"
    return _decimal(match.group("price")), currency, _decimal(match.group("volume"))


def parse_pdmr_notification(text: str) -> PdmrNotification | None:
    """Parse the MAR template from the announcement's plain text; None when it is absent."""
    flat = " ".join(text.replace(" | ", " ").split())
    if not re.search(r"details of the (?:PDMR|person discharging)", flat, re.I) or not re.search(
        r"details of the transaction", flat, re.I
    ):
        return None
    part1 = _section(flat, r"details of the (?:PDMR|person discharging)", r"\b2\.?\s+Reason for")
    part2 = _section(flat, r"\b2\.?\s+Reason for the notification", r"\b3\.?\s+Details of the")
    part3 = _section(flat, r"\b3\.?\s+Details of the issuer", r"\b4\.?\s+Details of the")
    # Section 4 ends where the announcement does ("- ends -", then the issuer's boilerplate).
    part4 = _section(
        flat,
        r"\b4\.?\s+Details of the transaction",
        r"-\s*ends\s*-|\bFor (?:more|further) information\b",
    )
    name = _after(r"a\)\s*Name", part1, r"$^")
    position = _after(r"a\)\s*Position(?:/status)?", part2, r"\bb\)")
    initial = _after(r"b\)\s*Initial notification\s*/?\s*Amendment", part2, r"$^") or ""
    issuer = _after(r"a\)\s*Name", part3, r"\bb\)\s*LEI")
    lei_block = _after(r"b\)\s*LEI", part3, r"$^") or ""
    lei = _LEI.search(lei_block)
    transactions: list[PdmrTransaction] = []
    # The template repeats section 4 per instrument / date / place; split on its a) item.
    blocks = re.split(r"(?=\ba\)\s*Description of the financial instrument)", part4, flags=re.I)
    for block in (b for b in blocks if re.search(r"description of the financial", b, re.I)):
        instrument_text = _after(
            # The template's label is "Description of the financial instrument, type of
            # instrument and identification code" (as published on Investegate, 2026-10-03).
            r"a\)\s*Description of the financial instrument(?:,?\s*type of instrument)?"
            r"(?:,?\s*(?:and\s+)?(?:an\s+)?identification code)?",
            block,
            r"\bb\)\s*Nature",
        )
        nature = _after(r"b\)\s*Nature of the transaction", block, r"\bc\)\s*Price")
        pv = _after(r"c\)\s*Price\(s\) and volume\(s\)", block, r"\bd\)\s*Aggregated")
        when = _after(r"e\)\s*Date of the transaction", block, r"\bf\)\s*Place")
        venue = _after(r"f\)\s*Place of the transaction", block, r"$^")
        isin = _ISIN.search(instrument_text or "")
        price, currency, volume = _price_volume(pv or "")
        economic_class, basis = classify_nature(nature, venue)
        transactions.append(
            PdmrTransaction(
                instrument=instrument_text,
                isin=isin.group(1) if isin else None,
                nature=nature,
                price=price,
                currency=currency,
                volume=volume,
                transaction_date=_date(when),
                venue=venue,
                economic_class=economic_class,
                classification_basis=basis,
            )
        )
    if not transactions:
        return None
    return PdmrNotification(
        person_name=name,
        position=position,
        is_amendment=initial.lower().startswith("amend"),
        issuer_name=issuer,
        lei=lei.group(1) if lei else None,
        transactions=transactions,
    )


async def fetch_pdmr(http: httpx.AsyncClient, url: str) -> PdmrFetchResult:
    """Fetch and parse one announcement; a denial or an unparseable page is a typed state."""
    try:
        document = await fetch_document(http, url, max_bytes=MAX_BYTES, headers=default_headers())
    except FetchError as exc:
        status = re.search(r"HTTP (\d{3})", str(exc))
        if status:
            return PdmrFetchResult(access_from_http(int(status.group(1))))
        return PdmrFetchResult(AccessResult(AccessState.TEMPORARILY_FAILED, str(exc)))
    text = html_to_text(document.data.decode("utf-8", errors="replace"))
    notification = parse_pdmr_notification(text)
    if notification is None:
        return PdmrFetchResult(
            AccessResult(
                AccessState.ACCESS_UNVERIFIED,
                "Page retrieved but it does not contain the MAR Article 19 template "
                "(script-rendered or changed layout); no transactions were read.",
            ),
            no_template=True,
        )
    return PdmrFetchResult(AccessResult(AccessState.AVAILABLE), notification)
