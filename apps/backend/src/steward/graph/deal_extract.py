# ruff: noqa: E501 - extraction patterns are kept on one line each for readability
"""Deal and financing events from SEC 8-K / 6-K items and cited exhibits (D5.9).

The extractor is deliberately conservative and deterministic: it reads the *Item* sections of a
Form 8-K (or, for a Form 6-K that has none, the exhibit text), recognises the item's meaning
(1.01 entry into an agreement, 1.02 termination, 2.01 completion, 2.03 a financial obligation,
3.02 an equity sale, 5.01 a change in control, 5.07 a shareholder vote), and pulls the terms the
text states in words: price per share, aggregate value, consideration mix, exchange ratio,
shares to issue, debt raised or assumed. Anything it cannot read stays absent (never guessed).
Every extracted event is stored as a *suggestion* with the quoted passage as evidence; the owner
confirms it, and only confirmed terms feed dilution and leverage.

The patterns follow the wording of the SEC's Form 8-K item list and common press-release
phrasing; they have been exercised on the recorded-format fixtures in tests/fixtures/graph, not
on a corpus of live filings.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

import asyncpg

from steward.documents.text import html_to_text
from steward.graph.deals import DealTransitionError, deal_key, record_deal_event
from steward.graph.evidence import record_evidence
from steward.sources.edgar import EdgarClient, Filing

ITEM_SPLIT = re.compile(r"(?im)^\s*item\s+(\d\.\d{2})\b[^\n]*$")
SCALE = {"billion": Decimal(10**9), "million": Decimal(10**6), "thousand": Decimal(10**3)}
CURRENCY = {"$": "USD", "€": "EUR", "£": "GBP"}

_DEAL = re.compile(
    r"(merger agreement|agreement and plan of merger|plan of merger|acquisition|to acquire|"
    r"will acquire|tender offer|stock purchase agreement|asset purchase agreement|"
    r"share purchase agreement|business combination)",
    re.I,
)
_FINANCING = re.compile(
    r"(credit agreement|term loan|revolving credit|senior notes|notes due|indenture|"
    r"underwriting agreement|public offering|private placement|convertible notes|"
    r"securities purchase agreement|credit facility|debentures)",
    re.I,
)
_TERMINATE = re.compile(r"\bterminat", re.I)
_AMEND = re.compile(
    r"\b(amendment no\.? ?\d*|first amendment|second amendment|amended and restated|amends?)\b",
    re.I,
)
_CLOSED = re.compile(
    r"(completed|consummated|closed)\s+(?:its |the |their )?(?:previously announced )?(?:acquisition|merger|transaction|sale|purchase)",
    re.I,
)
_APPROVED = re.compile(
    r"(stockholders|shareholders)[^.]{0,80}\b(approved|adopted)\b|\b(approved|adopted)\b[^.]{0,80}merger",
    re.I,
)
_ANNOUNCED = re.compile(
    r"(entered into|signed|announced)[^.]{0,120}(definitive|merger|acquisition|purchase)\s+agreement|definitive agreement",
    re.I,
)

_PRICE = re.compile(
    r"([$€£])\s?(\d{1,4}(?:,\d{3})*(?:\.\d+)?)\s+(?:in cash\s+)?per\s+(?:share|ordinary share|ADS)",
    re.I,
)
_VALUE = re.compile(
    r"(?:aggregate|total|equity|enterprise|transaction|purchase price)[^$€£.]{0,70}?"
    r"([$€£])\s?(\d{1,4}(?:,\d{3})*(?:\.\d+)?)\s*(billion|million)",
    re.I,
)
_VALUE_VALUED = re.compile(
    r"valued at[^$€£.]{0,40}([$€£])\s?(\d{1,4}(?:,\d{3})*(?:\.\d+)?)\s*(billion|million)", re.I
)
_EXCHANGE = re.compile(r"exchange ratio of\s+(\d+(?:\.\d+)?)", re.I)
_SHARES = re.compile(
    r"issu(?:e|ance of)\s+(?:up to\s+)?(?:approximately\s+)?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
    r"(?:\s+(million))?\s+(?:newly[- ]issued\s+)?(?:shares|ordinary shares)",
    re.I,
)
_DEBT = re.compile(
    r"([$€£])\s?(\d{1,4}(?:,\d{3})*(?:\.\d+)?)\s*(billion|million)\s+"
    r"(?:in\s+)?(?:aggregate principal amount of\s+)?(?:new\s+)?(?:senior\s+(?:unsecured\s+)?|secured\s+)?"
    r"(notes|term loan|revolving credit facility|credit facility|loan facility|bridge facility|"
    r"term loan facility)",
    re.I,
)
_ASSUMED = re.compile(
    r"assum(?:e|ption of|ing)[^$€£.]{0,60}?([$€£])\s?(\d{1,4}(?:,\d{3})*(?:\.\d+)?)\s*(billion|million)",
    re.I,
)
_PCT_CASH = re.compile(
    r"(\d{1,3}(?:\.\d+)?)\s?%\s+(?:in\s+|of\s+(?:the\s+)?consideration\s+in\s+)?cash", re.I
)
_PCT_STOCK = re.compile(r"(\d{1,3}(?:\.\d+)?)\s?%\s+(?:in\s+)?(?:stock|shares)", re.I)
_TARGET = re.compile(
    r"(?:to acquire|will acquire|acquisition of|acquire all (?:of )?the (?:issued and )?outstanding "
    r"(?:shares|stock|equity) of|merger with|merge with|purchase of|completed (?:its )?acquisition of)\s+"
    r"(?P<name>[A-Z][A-Za-z0-9&.'\-]*(?: (?:[A-Z][A-Za-z0-9&.'\-]*|of|and|&))*?)"
    r"(?:,| \(| for | in an | in a | in cash| at | pursuant| from |\.|;| a | an | the |$)"
)


@dataclass(slots=True)
class ExtractedEvent:
    event_type: Literal["deal", "financing"]
    state: str
    counterparty: str | None
    terms: dict[str, object]
    quote: str
    item: str | None
    document: str
    form: str
    filed: dt.date
    span: tuple[int, int]
    notes: list[str] = field(default_factory=list)


def _num(text: str, scale: str | None = None) -> Decimal:
    value = Decimal(text.replace(",", ""))
    value = value * SCALE[scale.lower()] if scale else value
    return value.quantize(Decimal(1)) if value == value.to_integral_value() else value


def _quote(text: str, start: int, end: int, width: int = 200) -> tuple[str, tuple[int, int]]:
    lo, hi = max(0, start - width), min(len(text), end + width)
    return " ".join(text[lo:hi].split()), (lo, hi)


def extract_terms(text: str) -> dict[str, object]:
    """Terms stated in words in `text`; absent keys were not stated."""
    terms: dict[str, object] = {}
    price = _PRICE.search(text)
    if price:
        terms["price_per_share"] = str(_num(price.group(2)))
        terms["currency"] = CURRENCY[price.group(1)]
    value = _VALUE.search(text) or _VALUE_VALUED.search(text)
    if value:
        terms["total_value"] = str(_num(value.group(2), value.group(3)))
        terms.setdefault("currency", CURRENCY[value.group(1)])
    exchange = _EXCHANGE.search(text)
    if exchange:
        terms["exchange_ratio"] = exchange.group(1)
    shares = _SHARES.search(text)
    if shares:
        terms["shares_to_issue"] = str(_num(shares.group(1), shares.group(2)))
    raised = Decimal(0)
    instruments: list[str] = []
    facility = Decimal(0)
    for debt in _DEBT.finditer(text):
        amount = _num(debt.group(2), debt.group(3))
        instrument = debt.group(4).lower()
        terms.setdefault("currency", CURRENCY[debt.group(1)])
        if "revolving" in instrument or ("facility" in instrument and "term" not in instrument):
            # a committed line is capacity, not borrowing: it adds no debt until it is drawn
            facility += amount
        else:
            raised += amount
            if instrument not in instruments:
                instruments.append(instrument)
    if raised:
        terms["debt_raised"] = str(raised)
        terms["debt_instrument"] = "; ".join(instruments)
    if facility:
        terms["facility_size"] = str(facility)
    assumed = _ASSUMED.search(text)
    if assumed:
        terms["debt_assumed"] = str(_num(assumed.group(2), assumed.group(3)))
    cash_pct, stock_pct = _PCT_CASH.search(text), _PCT_STOCK.search(text)
    lowered = text.lower()
    if cash_pct and stock_pct:
        terms["cash_pct"], terms["stock_pct"] = cash_pct.group(1), stock_pct.group(1)
    elif re.search(r"all[- ]cash(?! and (?:stock|shares))", lowered):
        terms["cash_pct"], terms["stock_pct"] = "100", "0"
    elif re.search(r"all[- ]stock(?! and (?:cash))", lowered):
        terms["cash_pct"], terms["stock_pct"] = "0", "100"
    if "cash_pct" in terms:
        total = Decimal(str(terms["cash_pct"])) + Decimal(str(terms["stock_pct"]))
        if abs(total - 100) > Decimal("0.5"):
            terms.pop("cash_pct")
            terms.pop("stock_pct")
    elif re.search(r"cash and (?:stock|shares)|stock and cash", lowered):
        terms["consideration_type"] = "cash_and_stock"
    return terms


def extract_counterparty(text: str) -> str | None:
    match = _TARGET.search(text)
    if not match:
        return None
    name = match.group("name").strip(" ,.;")
    return name if len(name) >= 3 else None


def split_items(text: str) -> list[tuple[str, str]]:
    """(item number, section text) of a Form 8-K body."""
    matches = list(ITEM_SPLIT.finditer(text))
    sections: list[tuple[str, str]] = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((match.group(1), text[match.end() : end]))
    return sections


def _classify(item: str | None, text: str, form: str) -> tuple[str, str] | None:
    """(event_type, state) the section describes, or None when it is not about a deal."""
    deal, financing = bool(_DEAL.search(text)), bool(_FINANCING.search(text))
    if item == "2.01":
        return ("deal", "closed") if deal or _CLOSED.search(text) else None
    if item == "1.02":
        if not _TERMINATE.search(text):
            return None
        if deal:
            return ("deal", "terminated")
        return ("financing", "terminated") if financing else None
    if item == "5.01":
        return ("deal", "closed")
    if item == "5.07":
        return ("deal", "approved") if _APPROVED.search(text) and deal else None
    if item == "2.03":
        return (
            ("financing", "closed")
            if financing or re.search(r"\bdirect financial obligation", text, re.I)
            else None
        )
    if item == "3.02":
        return ("financing", "closed")
    if item == "1.01":
        if deal:
            if _AMEND.search(text[:600]) and re.search(r"\bamend", text[:600], re.I):
                return ("deal", "amended")
            return ("deal", "announced")
        if financing:
            return ("financing", "amended" if _AMEND.search(text[:400]) else "announced")
        return None
    # 7.01 / 8.01 / exhibits and 6-K: classify by what the text says happened
    if deal:
        if _CLOSED.search(text):
            return ("deal", "closed")
        if _TERMINATE.search(text[:800]) and re.search(
            r"terminat\w+ (?:the|its) (?:merger|acquisition)", text, re.I
        ):
            return ("deal", "terminated")
        if _APPROVED.search(text[:800]):
            return ("deal", "approved")
        if _ANNOUNCED.search(text):
            return (
                "deal",
                "amended"
                if re.search(r"amended (?:the )?(?:merger|agreement)", text[:800], re.I)
                else "announced",
            )
    elif financing and re.search(
        r"(prices|priced|announces|completed|closes|closed)", text[:600], re.I
    ):
        return (
            "financing",
            "closed" if re.search(r"complet|clos", text[:600], re.I) else "announced",
        )
    return None


def extract_events(
    *,
    form: str,
    filed: dt.date,
    items: str,
    documents: list[tuple[str, str]],
) -> list[ExtractedEvent]:
    """Events from a filing. `documents` are (name, plain text) pairs: the primary document
    first, then cited exhibits. Item sections of the primary 8-K are read separately so one
    filing can yield a deal state and a financing event."""
    out: list[ExtractedEvent] = []
    seen: set[tuple[str, str, str | None]] = set()
    listed = {i.strip() for i in items.split(",") if i.strip()}
    for index, (name, text) in enumerate(documents):
        sections = split_items(text) if index == 0 and form.upper().startswith("8-K") else []
        if not sections:
            sections = [("", text)]
        for item, body in sections:
            item_no = item or None
            if item_no and listed and item_no not in listed:
                continue
            classified = _classify(item_no, body, form)
            if classified is None:
                continue
            event_type, state = classified
            terms = extract_terms(body)
            counterparty = extract_counterparty(body) if event_type == "deal" else None
            anchor = (
                _TARGET.search(body)
                or _PRICE.search(body)
                or _DEBT.search(body)
                or _ANNOUNCED.search(body)
                or _CLOSED.search(body)
                or _DEAL.search(body)
                or _FINANCING.search(body)
            )
            start, end = (anchor.start(), anchor.end()) if anchor else (0, min(len(body), 200))
            quote, rel_span = _quote(body, start, end)
            offset = text.find(body[:40]) if body[:40] in text else 0
            key = (event_type, state, counterparty)
            if key in seen:
                continue
            seen.add(key)
            notes = []
            if event_type == "deal" and counterparty is None:
                notes.append("counterparty not stated in a form the extractor reads")
            out.append(
                ExtractedEvent(
                    event_type=event_type,  # type: ignore[arg-type]
                    state=state,
                    counterparty=counterparty,
                    terms=terms,
                    quote=quote,
                    item=item_no,
                    document=name,
                    form=form,
                    filed=filed,
                    span=(max(offset, 0) + rel_span[0], max(offset, 0) + rel_span[1]),
                    notes=notes,
                )
            )
    return out


async def store_extracted(
    pool: asyncpg.Pool,
    *,
    ticker: str,
    filing: Filing,
    events: list[ExtractedEvent],
    document_url: dict[str, str],
    texts: dict[str, str],
) -> list[int]:
    """Store extracted events as *suggested* deal/financing events with their evidence."""
    ids: list[int] = []
    for event in events:
        url = document_url[event.document]
        evidence_id = await record_evidence(
            pool,
            regulator="sec",
            external_id=f"{filing.accession}:{event.document}",
            url=url,
            document_type=filing.form,
            content=texts[event.document],
            quote=event.quote,
            filed_at=dt.datetime.combine(filing.filing_date, dt.time.min, tzinfo=dt.UTC),
            known_at=filing.accepted_at,
            span=event.span,
        )
        key = deal_key(
            ticker,
            event.event_type,
            event.counterparty or event.terms.get("debt_instrument") or filing.accession,  # type: ignore[arg-type]
        )
        try:
            event_id = await record_deal_event(
                pool,
                ticker=ticker,
                event_type=event.event_type,
                key=key,
                state=event.state,
                source="sec",
                source_url=f"{url}#item-{event.item or 'text'}",
                announced_at=filing.filing_date
                if event.state in {"announced", "proposed"}
                else None,
                effective_at=filing.filing_date,
                counterparty=event.counterparty,
                terms={**event.terms, "extracted_from": f"{filing.form} item {event.item or '-'}"},
                evidence_id=evidence_id,
                status="suggested",
                detail={"quote": event.quote, "notes": event.notes, "accession": filing.accession},
                known_at=filing.accepted_at,
            )
        except DealTransitionError:
            # The filing describes a step the deal could not have taken given what is already
            # recorded (for example a signing after the deal closed): leave it for the owner.
            continue
        ids.append(event_id)
    return ids


DEAL_FORMS = {"8-K", "8-K/A", "6-K", "6-K/A"}
EXHIBIT_TYPES = ("EX-99", "EX-2", "EX-10", "EX-4")


async def scan_filing(
    pool: asyncpg.Pool, edgar: EdgarClient, *, ticker: str, filing: Filing, max_exhibits: int = 4
) -> list[int]:
    """Fetch an 8-K / 6-K and its press-release or agreement exhibits, extract and store."""
    if filing.form not in DEAL_FORMS:
        return []
    urls: dict[str, str] = {}
    texts: dict[str, str] = {}
    primary = filing.primary_document
    urls[primary] = filing.url(primary)
    texts[primary] = html_to_text((await edgar.document(urls[primary])).decode("utf-8", "replace"))
    documents = [(primary, texts[primary])]
    exhibits = [
        name
        for doc_type, name in await edgar.exhibit_documents(filing)
        if doc_type.startswith(EXHIBIT_TYPES) and name != primary
    ][:max_exhibits]
    for name in exhibits:
        urls[name] = filing.url(name)
        texts[name] = html_to_text((await edgar.document(urls[name])).decode("utf-8", "replace"))
        documents.append((name, texts[name]))
    events = extract_events(
        form=filing.form, filed=filing.filing_date, items=filing.items, documents=documents
    )
    return await store_extracted(
        pool, ticker=ticker, filing=filing, events=events, document_url=urls, texts=texts
    )
