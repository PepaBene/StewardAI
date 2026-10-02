# ruff: noqa: E501 - patterns stay on one line each
"""Passage-level review of customer / supplier mentions (D5.8).

EDGAR full-text search only says that a filer's document contains the holding's name and the
word "customer" or "supplier" somewhere. That proves neither a relationship nor its direction.
This module fetches the cited filing through the EdgarClient, finds the paragraphs that name the
holding, and reads them:

* both counterparties: the filer (name and CIK) and the holding;
* the direction, from the verbs ("accounted for 12% of our revenue", "we purchase ... from",
  "is our sole supplier"), relative to the *holding*: if the filer buys from the holding, the
  filer is the holding's customer (`issuer_sells_to`); if the filer sells to the holding, the
  filer is the holding's supplier (`issuer_buys_from`). Mixed or unreadable wording stays
  `unknown` and goes to the owner;
* named versus anonymous (issuer filings: "Customer A", "one customer");
* the reported share, with its basis (the share is of the *filer's* revenue or purchases when
  the filer wrote it), and the fiscal period with its start and end dates.

Resolutions are stored as suggestions with the paragraph as evidence; the owner confirms them.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

import asyncpg

from steward.documents.text import html_to_text
from steward.graph.evidence import record_evidence
from steward.graph.keys import normalize_object_key
from steward.graph.relationships import (
    detect_contradictions,
    upsert_disclosure,
)
from steward.sources.edgar import EdgarClient, EdgarError

MAX_ATTEMPTS = 3
ISSUER_CONCENTRATION_BASES = ("issuer_revenue", "issuer_purchases")
MAX_PARAGRAPH = 1800
_REL_WORDS = re.compile(
    r"\b(customers?|suppliers?|vendors?|purchas\w+|sales|revenues?|supply|supplies|supplied|"
    r"contract|procure\w*|sole[- ]source|accounted for|represented)\b",
    re.I,
)
_SELLS_TO_HOLDING = [
    # the filer sells to the holding: the holding is the filer's customer
    r"{h}[^.]{{0,80}}\b(?:accounted for|represented|comprised|made up)\b[^.]{{0,60}}\b(?:our|total|net|consolidated)\b[^.]{{0,30}}\b(?:revenues?|sales)\b",
    r"\bsales? to {h}\b",
    r"{h} (?:is|was|are|were|remains) (?:one of )?(?:our|the company'?s) (?:\w+ ){{0,3}}customers?",
    r"\bour (?:\w+ ){{0,2}}customers?,? (?:including )?{h}",
    r"\bwe (?:\w+ ){{0,2}}(?:sell|sold|supply|supplied|provide|provided|license|licensed)\b[^.]{{0,100}}\bto {h}",
]
_BUYS_FROM_HOLDING = [
    # the filer buys from the holding: the holding is the filer's supplier
    r"{h} (?:is|was|are|were|remains) (?:one of )?(?:our|the company'?s) (?:\w+ ){{0,3}}(?:suppliers?|vendors?)",
    r"\bwe (?:\w+ ){{0,2}}(?:purchase|purchased|source|sourced|buy|bought|obtain|obtained|rely on)\b[^.]{{0,100}}\bfrom {h}",
    r"\bwe rely on {h} (?:for|to)\b",
    r"\bpurchases? from {h}\b",
    r"\b(?:supplied|manufactured|provided) by {h}\b",
    r"\bour (?:\w+ ){{0,2}}(?:suppliers?|vendors?),? (?:including )?{h}",
]
_PERCENT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s?%")
_PERIOD = re.compile(
    r"(?P<kind>fiscal year|year|quarter|three months|six months|nine months|twelve months|52 weeks|fiscal quarter)"
    r"(?:s)? ended (?P<month>[A-Z][a-z]+)\.? (?P<day>\d{1,2}),? (?P<year>\d{4})",
    re.I,
)
_ANON = re.compile(
    r"\b(?:customers?|distributors?|suppliers?|vendors?|partners?) ([A-F])\b|"
    r"\b(one|two|three|a single|single|a|largest) (?:significant |major |large |top )?(?:customers?|suppliers?)\b",
    re.I,
)
_MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}


@dataclass(slots=True)
class PassageResolution:
    relationship: Literal["customer", "supplier"]
    direction: Literal["issuer_sells_to", "issuer_buys_from", "unknown"]
    counterparty_name: str | None
    counterparty_key: str | None
    anonymous: bool
    reported_share: Decimal | None
    share_basis: str | None
    fiscal_period: str | None
    period_start: dt.date | None
    period_end: dt.date | None
    passage: str
    reasons: list[str] = field(default_factory=list)
    confidence: Decimal = Decimal("0.5")
    context: str = ""


def paragraphs(text: str) -> list[str]:
    """Paragraphs of the converted filing text, long ones cut at sentence ends."""
    out: list[str] = []
    for block in re.split(r"\n\s*\n|\n", text):
        block = " ".join(block.split())
        if len(block) < 40:
            continue
        while len(block) > MAX_PARAGRAPH:
            cut = block.rfind(". ", 0, MAX_PARAGRAPH)
            cut = cut + 1 if cut > 400 else MAX_PARAGRAPH
            out.append(block[:cut].strip())
            block = block[cut:].strip()
        out.append(block)
    return out


def _name_pattern(names: Iterable[str]) -> str:
    variants = sorted(
        {re.escape(n.strip()) for n in names if len(n.strip()) >= 3}, key=len, reverse=True
    )
    if not variants:
        raise ValueError("at least one holding name is required")
    return "(?:" + "|".join(variants) + ")"


def mentions(paragraph: str, names: Iterable[str]) -> bool:
    return re.search(rf"(?<!\w){_name_pattern(names)}(?!\w)", paragraph, re.I) is not None


def parse_period(paragraph: str) -> tuple[str | None, dt.date | None, dt.date | None]:
    """(fiscal_period, start, end) from "for the fiscal year ended December 31, 2025"."""
    match = _PERIOD.search(paragraph)
    if not match:
        return None, None, None
    month = _MONTHS.get(match.group("month").lower())
    if month is None:
        return None, None, None
    try:
        end = dt.date(int(match.group("year")), month, int(match.group("day")))
    except ValueError:
        return None, None, None
    kind = match.group("kind").lower()
    months = {
        "three months": 3,
        "quarter": 3,
        "fiscal quarter": 3,
        "six months": 6,
        "nine months": 9,
    }.get(kind, 12)
    # The period starts `months` months before the day after it ends (June 30 -> April 1).
    following = end + dt.timedelta(days=1)
    start_month = following.month - months
    year = following.year
    while start_month <= 0:
        start_month += 12
        year -= 1
    start = dt.date(
        year, start_month, min(following.day, calendar.monthrange(year, start_month)[1])
    )
    if months == 12:
        label = f"FY{end.year}"
    elif months == 3:
        label = f"{end.year}Q{(end.month - 1) // 3 + 1}"
    else:
        label = f"{end.year}M{months}"
    return label, start, end


def _sentences(paragraph: str) -> list[str]:
    return [x for x in re.split(r"(?<=[.;])\s+", paragraph) if x]


def _share_near(paragraph: str, names: Sequence[str], anchor: int) -> Decimal | None:
    """The percentage in a sentence that names the holding (or, failing that, in the sentence
    holding the relationship verb), the one closest to the verb."""
    sentences = _sentences(paragraph)
    offsets: list[int] = []
    position = 0
    for sentence in sentences:
        offsets.append(paragraph.find(sentence, position))
        position = offsets[-1] + len(sentence)
    anchor_index = max(i for i, off in enumerate(offsets) if off <= anchor) if offsets else 0
    named = [i for i, sentence in enumerate(sentences) if mentions(sentence, names)]
    candidates = [i for i in (named or [anchor_index]) if _PERCENT.search(sentences[i])]
    if not candidates:
        return None
    nearest = min(candidates, key=lambda i: abs(i - anchor_index))
    values = [Decimal(m.group(1)) / 100 for m in _PERCENT.finditer(sentences[nearest])]
    values = [v for v in values if v <= 1]
    return values[0] if len(values) == 1 else None


def _share_basis(paragraph: str, own_filing: bool) -> str | None:
    """Denominator of a percentage as the text states it; `counterparty_*` when the filer wrote
    it about its own revenue or purchases (not the holding's)."""
    lowered = paragraph.lower()
    prefix = "issuer" if own_filing else "counterparty"
    if re.search(r"\b(revenues?|net sales|sales)\b", lowered):
        return f"{prefix}_revenue"
    if re.search(r"\b(purchases|cost of (?:revenue|sales|goods|products)|procurement)\b", lowered):
        return f"{prefix}_purchases"
    return None


_NEGATION = re.compile(r"\b(?:not|never|no longer|none)\b|n't", re.I)


def _direction_hits(patterns: Sequence[str], holding: str, paragraph: str) -> list[re.Match[str]]:
    """Matches of the direction patterns, minus negated ones ("we do not purchase from X")."""
    hits = []
    for pattern in patterns:
        match = re.search(pattern.format(h=holding), paragraph, re.I)
        if match and not _NEGATION.search(paragraph[max(0, match.start() - 12) : match.end()]):
            hits.append(match)
    return hits


def analyze_counterparty_filing(
    text: str,
    *,
    holding_names: Sequence[str],
    filer_name: str,
    filer_cik: str | None,
) -> list[PassageResolution]:
    """Resolve what *another* filer's document says about the holding. Only paragraphs that
    name the holding and use relationship wording are read; a paragraph whose verbs point both
    ways, or neither, is returned with direction `unknown`."""
    h = _name_pattern(holding_names)
    resolutions: list[PassageResolution] = []
    key = (
        f"cik:{int(filer_cik):010d}"
        if filer_cik and filer_cik.isdigit()
        else normalize_object_key(filer_name)
    )
    for paragraph in paragraphs(text):
        if not mentions(paragraph, holding_names) or not _REL_WORDS.search(paragraph):
            continue
        sells = _direction_hits(_SELLS_TO_HOLDING, h, paragraph)
        buys = _direction_hits(_BUYS_FROM_HOLDING, h, paragraph)
        reasons: list[str] = []
        if sells and not buys:
            relationship: Literal["customer", "supplier"] = "supplier"
            direction: Literal["issuer_sells_to", "issuer_buys_from", "unknown"] = (
                "issuer_buys_from"
            )
            anchor = sells[0].start()
            reasons.append(f"{filer_name} states it sells to the holding")
        elif buys and not sells:
            relationship, direction = "customer", "issuer_sells_to"
            anchor = buys[0].start()
            reasons.append(f"{filer_name} states it buys from the holding")
        elif sells and buys:
            relationship, direction = "customer", "unknown"
            anchor = 0
            reasons.append("wording points both ways: the owner decides the direction")
        else:
            # The name sits near relationship words without saying who sells to whom
            # (competitor lists, licensing, boilerplate): nothing to record.
            continue
        share = _share_near(paragraph, holding_names, anchor) if direction != "unknown" else None
        basis = _share_basis(paragraph, False) if share is not None else None
        if share is not None and basis is None:
            share = None
            reasons.append("a percentage is present but its denominator is not stated")
        label, start, end = parse_period(paragraph)
        resolutions.append(
            PassageResolution(
                relationship=relationship,
                direction=direction,
                counterparty_name=filer_name,
                counterparty_key=key,
                anonymous=False,
                reported_share=share,
                share_basis=basis,
                fiscal_period=label,
                period_start=start,
                period_end=end,
                passage=paragraph,
                context=paragraph,
                reasons=reasons,
                confidence=Decimal("0.7") if direction != "unknown" else Decimal("0.3"),
            )
        )
    return resolutions


_NAME = r"[A-Z][A-Za-z0-9&.\-]+(?: [A-Z][A-Za-z0-9&.\-]+){0,3}"
_NAMED_AFTER = re.compile(
    rf"(?:customer|supplier|vendor|distributor)s?,? (?:was |were |is )?(?P<n>{_NAME})"
)
_NAMED_BEFORE = re.compile(rf"(?P<m>{_NAME}),? (?:a|our) (?:customer|supplier)")
_GENERIC = re.compile(r"[A-F]|Customer|Supplier|One|Two|Three|The|Our|Its")


def _clause_basis(clause: str, relationship: str) -> str | None:
    lowered = clause.lower()
    if "receivable" in lowered:
        return "issuer_receivables"  # a share of receivables is not a revenue or purchase share
    if relationship == "customer" and re.search(r"revenues?|net sales|\bsales\b", lowered):
        return "issuer_revenue"
    if relationship == "supplier" and re.search(r"purchases|cost of|procurement", lowered):
        return "issuer_purchases"
    return None


def analyze_issuer_filing(text: str, *, holding_names: Sequence[str]) -> list[PassageResolution]:
    """Resolve the holding's *own* concentration disclosures: named customers/suppliers or
    "Customer A" / "one customer" with the share of the holding's revenue or purchases.

    One resolution per clause that carries a percentage. `holding_names` is accepted for symmetry
    with `analyze_counterparty_filing`: in the issuer's own filing every concentration clause is
    about the issuer."""
    del holding_names
    resolutions: list[PassageResolution] = []
    for paragraph in paragraphs(text):
        lowered = paragraph.lower()
        if "%" not in paragraph or not re.search(
            r"accounted for|represented|comprised|concentration|significant", lowered
        ):
            continue
        label, start, end = parse_period(paragraph)
        for clause in re.split(r"(?<=[.;]) | and (?=[A-Z])", paragraph):
            share = _PERCENT.search(clause)
            clause_lower = clause.lower()
            is_customer = bool(re.search(r"\bcustomers?\b", clause_lower))
            is_supplier = bool(re.search(r"\b(suppliers?|vendors?)\b", clause_lower))
            if not share or not (is_customer or is_supplier):
                continue
            value = Decimal(share.group(1)) / 100
            if value > 1:
                continue
            relationship: Literal["customer", "supplier"] = (
                "customer" if is_customer else "supplier"
            )
            direction: Literal["issuer_sells_to", "issuer_buys_from"] = (
                "issuer_sells_to" if relationship == "customer" else "issuer_buys_from"
            )
            basis = _clause_basis(clause, relationship) or _clause_basis(paragraph, relationship)
            named = _NAMED_AFTER.search(clause) or _NAMED_BEFORE.search(clause)
            candidate = (
                (named.group("n") if named and "n" in named.groupdict() else None)
                or (named.groupdict().get("m") if named else None)
                or ""
            ).strip()
            counterparty = None if not candidate or _GENERIC.fullmatch(candidate) else candidate
            anonymous = counterparty is None
            anon = _ANON.search(clause)
            reasons = ["issuer's own concentration disclosure"]
            if anonymous:
                reasons.append(
                    "counterparty not named: stored as anonymous, never merged with a named one"
                )
            if anon and anon.group(1):
                reasons.append(f"labelled {anon.group(0)}")
            if basis not in ISSUER_CONCENTRATION_BASES:
                reasons.append(
                    "share is not of the issuer's revenue or purchases: no concentration"
                )
            resolutions.append(
                PassageResolution(
                    relationship=relationship,
                    direction=direction,
                    counterparty_name=counterparty,
                    counterparty_key=None,
                    anonymous=anonymous,
                    reported_share=value,
                    share_basis=basis,
                    fiscal_period=label,
                    period_start=start,
                    period_end=end,
                    passage=clause.strip(),
                    context=paragraph,
                    reasons=reasons,
                    confidence=Decimal("0.7")
                    if basis in ISSUER_CONCENTRATION_BASES
                    else Decimal("0.4"),
                )
            )
    return resolutions


# ---------------------------------------------------------------------------- the pipeline


async def review_hit(
    pool: asyncpg.Pool,
    edgar: EdgarClient,
    hit: asyncpg.Record,
    *,
    holding_names: Sequence[str],
) -> dict[str, object]:
    """Fetch one filing-hit's document, resolve its passages and store them as suggestions."""
    try:
        raw = await edgar.document(hit["url"])
    except EdgarError as exc:
        attempts = hit["attempts"] + 1
        await pool.execute(
            """update public.filing_hit set attempts=$2::int, review_note=$3,
                 review_state=case when $2::int>=$4::int then 'failed' else 'pending' end, updated_at=now()
               where id=$1""",
            hit["id"],
            attempts,
            f"fetch failed: {exc}"[:300],
            MAX_ATTEMPTS,
        )
        return {"hit_id": hit["id"], "state": "failed" if attempts >= MAX_ATTEMPTS else "pending"}
    text = html_to_text(raw.decode("utf-8", "replace"))
    resolutions = analyze_counterparty_filing(
        text,
        holding_names=holding_names,
        filer_name=hit["filer_name"] or "unknown filer",
        filer_cik=hit["cik"],
    )
    if not resolutions:
        await pool.execute(
            """update public.filing_hit set review_state='dismissed', fetched_at=now(),
                 review_note='no paragraph names the holding next to relationship wording',
                 updated_at=now() where id=$1""",
            hit["id"],
        )
        return {"hit_id": hit["id"], "state": "dismissed", "disclosures": []}
    ids: list[int] = []
    for res in resolutions:
        evidence_id = await record_evidence(
            pool,
            regulator="sec",
            external_id=f"{hit['accession']}:{hit['file'] or 'primary'}",
            url=hit["url"],
            document_type=hit["form"],
            content=text,
            quote=res.context or res.passage,
            filed_at=dt.datetime.combine(hit["filed_at"], dt.time.min, tzinfo=dt.UTC)
            if hit["filed_at"]
            else None,
        )
        disclosure_id = await upsert_disclosure(
            pool,
            ticker=hit["ticker"],
            relationship=res.relationship,
            direction=res.direction,
            passage=res.passage,
            source_url=hit["url"],
            source="sec_edgar_passage",
            counterparty_key=res.counterparty_key,
            counterparty_name=res.counterparty_name,
            anonymous_counterparty=res.anonymous,
            reported_share=res.reported_share,
            share_basis=res.share_basis,
            fiscal_period=res.fiscal_period,
            valid_from=res.period_start,
            valid_to=res.period_end,
            evidence_id=evidence_id,
            origin="passage_review",
        )
        await detect_contradictions(pool, disclosure_id)
        ids.append(disclosure_id)
    resolved = any(r.direction != "unknown" for r in resolutions)
    # The customer and supplier keyword searches queue the same document twice: one read serves both.
    await pool.execute(
        """update public.filing_hit set review_state=$3, fetched_at=now(), disclosure_id=$4,
             updated_at=now(), review_note='read together with hit '||$5
           where ticker=$1 and url=$2 and kind='relationship_candidate'
             and review_state='pending' and id<>$5""",
        hit["ticker"],
        hit["url"],
        "resolved" if resolved else "fetched",
        ids[0],
        hit["id"],
    )
    await pool.execute(
        """update public.filing_hit set review_state=$2, fetched_at=now(), passage=$3,
             passage_hash=md5($3), disclosure_id=$4, updated_at=now(), review_note=$5
           where id=$1""",
        hit["id"],
        "resolved" if resolved else "fetched",
        resolutions[0].passage,
        ids[0],
        "; ".join(resolutions[0].reasons)[:300],
    )
    return {"hit_id": hit["id"], "state": "resolved" if resolved else "fetched", "disclosures": ids}


async def review_pending_hits(
    pool: asyncpg.Pool,
    edgar: EdgarClient,
    holding_names: dict[str, Sequence[str]],
    *,
    limit: int = 20,
) -> list[dict[str, object]]:
    """Work through the review queue: fetch, resolve and store, newest filings first."""
    hits = await pool.fetch(
        """select * from public.filing_hit
           where kind='relationship_candidate' and review_state='pending'
             and ticker=any($1::text[])
           order by filed_at desc nulls last,id desc limit $2""",
        list(holding_names),
        limit,
    )
    results: list[dict[str, object]] = []
    for hit in hits:
        # An earlier hit on the same document may already have resolved this one.
        state = await pool.fetchval(
            "select review_state from public.filing_hit where id=$1", hit["id"]
        )
        if state != "pending":
            continue
        results.append(
            await review_hit(pool, edgar, hit, holding_names=holding_names[hit["ticker"]])
        )
    return results
