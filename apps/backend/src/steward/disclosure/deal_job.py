"""P6.2: find public deals in the filings of held issuers and store what they state.

For each held issuer the recent 8-K, 6-K, S-4, proxy and tender-offer filings are read. A filing
that names two parties and a deal type becomes (or joins) a `deal_event`, with one state transition
and, when it states any term, one term version, each tied to the passage it rests on. Everything the
filing does not state stays `not_stated`. Rules:

* The state follows the filing's own item: 1.02 terminated, 2.01 closed, 1.01 or a merger proxy
  definitive, anything else announced. Nothing is inferred from the price or the news.
* Roles come only from the sentence ("X to acquire Y": acquirer and target; "X to sell Y": seller
  and buyer); a merger or combination names both as partners. A party is never matched to an
  issuer here: that stays a reviewer's step, so `issuer_id` is null except for the filer itself.
* A filing with fewer than two parties or no deal type stores nothing and is counted, never guessed.
* The same bytes are read once. A later filing about the same parties within 14 days joins the same
  deal and adds a state and, if its terms changed, a new term version.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import asyncpg

from steward.disclosure import deal_extract as dx
from steward.disclosure import deals as dl
from steward.disclosure.deals import NUMERIC_TERMS
from steward.disclosure.fetch import FetchFailure
from steward.disclosure.ingest import DocumentRecord, ingest_document_version, store_span
from steward.disclosure.parse import ParsedDocument, ParseError, parse_html
from steward.disclosure.sec_discovery import DiscoveredFiling

type Pool = asyncpg.Pool | asyncpg.Connection

SOURCE_KEY = "sec.archive"
REGULATOR = "SEC"
DEAL_FORMS = {"8-K", "6-K", "S-4", "DEFM14A", "SC TO-T", "SC 14D9"}
# 8-K items worth reading; a filing with other items only (earnings, Reg FD) is not a deal.
DEAL_ITEMS = ("1.01", "1.02", "2.01", "8.01")
JOIN_WINDOW_DAYS = 14
METHOD = "deal_extract"
SWAP_ROLES = ("seller", "buyer")


@dataclass
class DealOutcome:
    deal_id: int | None = None
    created: bool = False
    state: str | None = None
    term_version_id: int | None = None
    skipped: str | None = None
    rejected_transition: str | None = None  # why the state was not stored (illegal move)


@dataclass
class DealTotals:
    filings: int = 0
    stored: int = 0
    joined: int = 0
    unchanged: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    rejected_transitions: dict[str, int] = field(default_factory=dict)
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def as_detail(self) -> dict[str, Any]:
        return {
            "filings": self.filings,
            "stored": self.stored,
            "joined": self.joined,
            "unchanged": self.unchanged,
            "skipped": dict(self.skipped),
            "rejected_transitions": dict(self.rejected_transitions),
            "failed": self.failed,
            "errors": self.errors[:20],
        }


def wanted(filing: DiscoveredFiling) -> bool:
    """A deal form; an 8-K or 6-K only when one of its items is a deal item."""
    if filing.base_form not in DEAL_FORMS:
        return False
    if filing.base_form in ("8-K", "6-K"):
        return any(i in filing.items for i in DEAL_ITEMS)
    return True


HEADING = re.compile(r"(?im)^[ \t]*item\s+(\d\.\d{2})\b[^\n]*")
MERGER_AGREEMENT = re.compile(
    r"merger agreement|agreement and plan of merger|plan of merger|acquisition agreement"
    r"|purchase agreement|business combination agreement|definitive (?:merger|acquisition)",
    re.IGNORECASE,
)
TERMINATION_WORDS = re.compile(r"terminat", re.IGNORECASE)
COMPLETION_CUE = re.compile(
    r"\b(?:completed|consummated|closed|closing of|completion of)\b", re.IGNORECASE
)
FUTURE_CUE = re.compile(
    r"\b(?:expected|anticipat\w+|will|would|intends?|upon|subject to)\b", re.IGNORECASE
)
PASSAGE_CHARS = 2500


def declared_items(filing: DiscoveredFiling, text: str) -> set[str]:
    """The items the filing itself declares: its own item list, plus an item heading that opens a
    line of the text. A mention such as "see Item 1.02" in running text declares nothing."""
    own = {i for i in re.split(r"[,;\s]+", filing.items or "") if i}
    return own | {m.group(1) for m in HEADING.finditer(text)}


def item_passage(text: str, item: str) -> str | None:
    """The text under the heading of `item`, up to the next item heading; None if it has none."""
    for m in HEADING.finditer(text):
        if m.group(1) == item:
            nxt = HEADING.search(text, m.end())
            return text[m.end() : nxt.start() if nxt else m.end() + PASSAGE_CHARS]
    return None


def terminates_deal(filing: DiscoveredFiling, text: str) -> bool:
    """Item 1.02 ends a deal only when the filing declares it and its passage is about the merger or
    acquisition agreement; a terminated credit facility is not a terminated deal."""
    if "1.02" not in declared_items(filing, text):
        return False
    passage = item_passage(text, "1.02")
    return bool(passage and MERGER_AGREEMENT.search(passage) and TERMINATION_WORDS.search(passage))


def state_for(filing: DiscoveredFiling, found: dx.Extracted, text: str = "") -> str:
    """Closed (2.01) wins over a 1.02 in the same filing, which is usually a repaid facility."""
    items = declared_items(filing, text)
    if "2.01" in items:
        return "closed"
    if terminates_deal(filing, text):
        return "terminated"
    if "1.01" in items or filing.base_form in ("S-4", "DEFM14A"):
        return "definitive"
    return "announced"


def _stated_date(text: str, cue: re.Pattern[str], latest: dt.date) -> dt.date | None:
    for a, b in dx._sentences(text):
        s = text[a:b]
        if not cue.search(s) or FUTURE_CUE.search(s):
            continue
        for m in dx.DATE.finditer(s):
            d = dx._date(m)
            if d and d <= latest:
                return d
    return None


def effective_date_for(
    state: str, filing: DiscoveredFiling, found: dx.Extracted, text: str
) -> dt.date:
    """When the state took effect: a closing or termination date the filing states about the past,
    else the filing date; announced and definitive take the announcement or agreement date."""
    if state == "closed":
        return _stated_date(text, COMPLETION_CUE, filing.filed_at) or filing.filed_at
    if state == "terminated":
        return _stated_date(text, TERMINATION_WORDS, filing.filed_at) or filing.filed_at
    return found.announced_date or filing.filed_at


def roles_for(found: dx.Extracted) -> tuple[str, str]:
    """Roles from the sentence that names the parties; unknown shapes get the neutral role."""
    if found.roles == ("acquirer", "target"):
        return found.roles  # "X to acquire Y", even when the filing calls itself a merger agreement
    if found.deal_type in ("merger", "joint_venture"):
        return "partner", "partner"
    if found.deal_type == "divestiture":
        return SWAP_ROLES
    if found.deal_type in ("acquisition", "tender_offer"):
        return "acquirer", "target"
    return "counterparty", "counterparty"


def transaction_prefix(parties: Sequence[str]) -> str:
    names = sorted({dx.norm_party(p) for p in parties if dx.norm_party(p)})
    return f"txn:{'|'.join(names)}@"


def _evidence_range(found: dx.Extracted, text: str) -> tuple[int, int]:
    """The passage the deal rests on: the first stated term, else the opening of the filing."""
    stated = sorted(
        (t for t in found.terms.values() if t.state == "stated" and t.start is not None),
        key=lambda t: t.start or 0,
    )
    if stated and stated[0].start is not None and stated[0].end is not None:
        return stated[0].start, stated[0].end
    end = min(len(text), 400)
    return 0, max(end, 1)


async def _deal_for(conn: Pool, parties: Sequence[str], announced: dt.date) -> tuple[int, bool]:
    prefix = transaction_prefix(parties)
    # The same parties name the same deal for as long as it is open, whatever the date: a closing
    # filing months after the announcement belongs to it. The date window only separates a repeat
    # deal between the same parties once the earlier one is closed or terminated.
    row = await conn.fetchrow(
        """select e.id from public.deal_event e
            where starts_with(e.transaction_key, $1)
              and not exists (select 1 from public.deal_state_transition t
                               where t.deal_id = e.id and t.state in ('closed', 'terminated'))
            order by e.id limit 1""",
        prefix,
    )
    if row is None:
        row = await conn.fetchrow(
            """select id from public.deal_event
                where starts_with(transaction_key, $1)
                  and abs((announced_at at time zone 'UTC')::date - $2::date) <= $3
                order by id limit 1""",
            prefix,
            announced,
            JOIN_WINDOW_DAYS,
        )
    if row is not None:
        return int(row["id"]), False
    return -1, True


async def _new_deal(
    conn: Pool,
    found: dx.Extracted,
    announced: dt.date,
    known_at: dt.datetime,
    ticker: str | None,
    issuer_id: int,
) -> int:
    key = f"{transaction_prefix(found.parties)}{announced.isoformat()}"
    a, b = found.parties
    announced_at = dt.datetime.combine(announced, dt.time(), dt.UTC)
    deal_id = await conn.fetchval(
        """insert into public.deal_event
             (transaction_key, deal_type, title, ticker, issuer_id, announced_at, known_at)
           values ($1,$2,$3,$4,$5,$6,$7)
           on conflict (transaction_key) do update set transaction_key = excluded.transaction_key
           returning id""",
        key,
        found.deal_type,
        f"{a} / {b}",
        ticker,
        issuer_id,
        announced_at,
        known_at,
    )
    role_a, role_b = roles_for(found)
    for name, role in ((a, role_a), (b, role_b)):
        await conn.execute(
            """insert into public.deal_participant (deal_id, role, name, known_at)
               select $1,$2,$3,$4
                where not exists (select 1 from public.deal_participant
                                   where deal_id = $1 and role = $2 and name = $3)""",
            deal_id,
            role,
            name,
            known_at,
        )
    return int(deal_id)


async def _term_version(
    conn: Pool,
    deal_id: int,
    found: dx.Extracted,
    *,
    version_id: int,
    evidence_id: int,
    known_at: dt.datetime,
    effective: dt.datetime,
) -> int | None:
    stated = found.values()
    if not stated and found.closing_conditions.state != "stated":
        return None
    previous = await conn.fetchrow(
        """select * from public.deal_term_version
            where deal_id = $1 and terms_origin = 'announced' order by version_no desc limit 1""",
        deal_id,
    )
    values = dict(stated)
    carried_currency = found.currency
    carried_unaffected = found.unaffected_date
    carried_closing: str | None = None
    if previous is not None:
        # A later filing that does not restate a term is not a change to it: a new version is
        # written only when this filing states a figure that differs from the last version's.
        closing = found.closing_conditions
        changed = any(previous[n] != v for n, v in values.items()) or (
            closing.state == "stated" and previous["closing_conditions"] is None
        )
        if not changed:
            return None
        # The new version is complete: every term it does not restate is carried forward from the
        # one it supersedes (its provenance is that version, reachable through supersedes_id), so
        # the latest version never loses a term and a diff never reports it as removed.
        for n in NUMERIC_TERMS:
            if n not in values and previous[n] is not None:
                values[n] = previous[n]
        carried_currency = found.currency or previous["currency"]
        carried_unaffected = found.unaffected_date or previous["unaffected_date"]
        carried_closing = previous["closing_conditions"]
    cols = ["deal_id", "version_no", "supersedes_id", "known_at", "event_effective_at"]
    cols += ["document_version_id", "evidence_id", "currency", "unaffected_date"]
    args: list[Any] = [
        deal_id,
        1 if previous is None else int(previous["version_no"]) + 1,
        None if previous is None else int(previous["id"]),
        known_at,
        effective,
        version_id,
        evidence_id,
        carried_currency,
        carried_unaffected,
    ]
    for n in NUMERIC_TERMS:
        cols += [n, f"{n}_state"]
        v = values.get(n)
        args += [v, "stated" if v is not None else "not_stated"]
    closing = found.closing_conditions
    closing_text = closing.text if closing.state == "stated" else carried_closing
    cols += ["closing_conditions", "closing_conditions_state"]
    args += [closing_text, "stated" if closing_text else "not_stated"]
    marks = ",".join(f"${i}" for i in range(1, len(cols) + 1))
    return int(
        await conn.fetchval(
            f"insert into public.deal_term_version ({','.join(cols)}) values ({marks})"  # noqa: S608
            " returning id",
            *args,
        )
    )


async def store_deal_filing(
    pool: asyncpg.Pool,
    filing: DiscoveredFiling,
    content: bytes,
    *,
    issuer_id: int,
    ticker: str | None,
    archive_path: str | None = None,
    now: dt.datetime | None = None,
) -> DealOutcome:
    """Store one filing's text as a document version and, when it states a deal, the deal rows, in
    ONE transaction: a failed deal insert rolls the document back too, so the next run retries it
    instead of finding the bytes already stored and skipping them."""
    async with pool.acquire() as conn, conn.transaction():
        return await _store_deal_filing(
            conn,
            filing,
            content,
            issuer_id=issuer_id,
            ticker=ticker,
            archive_path=archive_path,
            now=now,
        )


async def _current_state(conn: Pool, deal_id: int, deal_type: str) -> str | None:
    rows = await conn.fetch(
        """select id, state, effective_at, known_at, evidence_id, document_version_id
             from public.deal_state_transition where deal_id = $1""",
        deal_id,
    )
    return dl.resolve_state(
        deal_type,
        [
            dl.Transition(
                r["id"],
                r["state"],
                r["effective_at"],
                r["known_at"],
                r["evidence_id"],
                r["document_version_id"],
            )
            for r in rows
        ],
        dl.FAR_FUTURE,
    ).state


async def _store_deal_filing(
    conn: asyncpg.Connection,
    filing: DiscoveredFiling,
    content: bytes,
    *,
    issuer_id: int,
    ticker: str | None,
    archive_path: str | None = None,
    now: dt.datetime | None = None,
) -> DealOutcome:
    try:
        doc: ParsedDocument | None = parse_html(content)
        error = None
    except ParseError as exc:
        doc, error = None, str(exc)
    record = DocumentRecord(
        regulator=REGULATOR,
        external_id=filing.accession,
        stable_url=filing.primary_url,
        form=filing.form,
        known_at=filing.known_at,
        issuer_id=issuer_id,
        source_key=SOURCE_KEY,
        accepted_at=filing.accepted_at,
        filed_at=dt.datetime.combine(filing.filed_at, dt.time.min, dt.UTC),
        date_notes=dict(filing.date_notes),
        amends_external_id=filing.amends_accession,
    )
    stored = await ingest_document_version(
        conn,
        record,
        content,
        archive_path=archive_path,
        parser_version=doc.parser_version if doc else None,
        quarantine_reason=error,
        now=now,
    )
    if not stored.created:
        return DealOutcome(skipped="unchanged")
    if doc is None:
        return DealOutcome(skipped="unreadable")
    found = dx.extract(doc.text)
    if found.deal_type is None:
        return DealOutcome(skipped="no_deal_type")
    if len(found.parties) < 2:
        return DealOutcome(skipped="parties_unclear")
    announced = found.announced_date or filing.filed_at
    effective = dt.datetime.combine(announced, dt.time(), dt.UTC)
    start, end = _evidence_range(found, doc.text)
    async with conn.transaction():
        evidence_id = await store_span(conn, stored.version_id, doc, start, end, method=METHOD)
        deal_id, is_new = await _deal_for(conn, found.parties, announced)
        if is_new:
            deal_id = await _new_deal(conn, found, announced, filing.known_at, ticker, issuer_id)
        deal_type = await conn.fetchval(
            "select deal_type from public.deal_event where id = $1", deal_id
        )
        state = state_for(filing, found, doc.text)
        state_effective = dt.datetime.combine(
            effective_date_for(state, filing, found, doc.text), dt.time(), dt.UTC
        )
        problem = dl.transition_problem(
            deal_type, await _current_state(conn, deal_id, deal_type), state
        )
        if problem is None:
            await conn.execute(
                """insert into public.deal_state_transition
                     (deal_id, state, effective_at, known_at, document_version_id, evidence_id)
                   select $1,$2,$3,$4,$5,$6
                    where not exists (select 1 from public.deal_state_transition
                                       where deal_id = $1 and state = $2
                                         and document_version_id = $5)""",
                deal_id,
                state,
                state_effective,
                filing.known_at,
                stored.version_id,
                evidence_id,
            )
        # an illegal move stores no state; the caller counts the reason
        stored_state = state if problem is None else None
        await conn.execute(
            """insert into public.deal_document (deal_id, document_version_id, role, known_at)
               values ($1,$2,$3,$4) on conflict (deal_id, document_version_id) do nothing""",
            deal_id,
            stored.version_id,
            "amendment" if filing.is_amendment else ("original" if is_new else "other"),
            filing.known_at,
        )
        term_id = await _term_version(
            conn,
            deal_id,
            found,
            version_id=stored.version_id,
            evidence_id=evidence_id,
            known_at=filing.known_at,
            effective=effective,
        )
    return DealOutcome(deal_id, is_new, stored_state, term_id, None, problem)


Fetch = Callable[[str], Awaitable[Any]]


async def collect_deals(
    pool: asyncpg.Pool,
    issuers: Sequence[Any],
    fetch_json: Fetch,
    fetch: Fetch,
    *,
    submissions_url: str,
    since: dt.date,
    per_issuer: int = 10,
    on_done: Callable[[str], Awaitable[None]] | None = None,
) -> DealTotals:
    """Read the recent deal filings of every issuer given. A failed fetch is counted against the
    issuer or filing and never stops the rest; a disabled source (SourceNotEnabled) propagates.
    `on_done(listing_url)` is awaited once every filing of an issuer was handled without a
    failure (a conditional fetcher then remembers the listing's validators)."""
    from steward.disclosure.registry import SourceNotEnabled
    from steward.disclosure.sec_discovery import discover_filings

    totals = DealTotals()
    for issuer in issuers:
        cik = str(int(issuer["cik"])).zfill(10)
        listing_url = submissions_url.format(cik=cik)
        failed_before = totals.failed
        try:
            listing = await fetch_json(listing_url)
            if not getattr(listing, "modified", True):
                totals.unchanged += 1  # the listing has not changed since it was last handled
                continue
            filings = [
                f
                for f in discover_filings(
                    cik, json.loads(listing.content or b"{}"), forms=DEAL_FORMS, since=since
                )
                if wanted(f)
            ][-per_issuer:]
        except SourceNotEnabled:
            raise
        except (FetchFailure, ValueError) as exc:
            totals.failed += 1
            totals.errors.append(f"issuer {issuer['id']}: {type(exc).__name__}")
            continue
        for filing in filings:
            totals.filings += 1
            try:
                got = await fetch(filing.primary_url)
                if not getattr(got, "modified", True) or not got.content:
                    totals.unchanged += 1
                    continue
                out = await store_deal_filing(
                    pool,
                    filing,
                    got.content,
                    issuer_id=int(issuer["id"]),
                    ticker=issuer["ticker"],
                    archive_path=getattr(got, "archive_path", None),
                    now=getattr(got, "retrieved_at", None),
                )
            except SourceNotEnabled:
                raise
            except (FetchFailure, ValueError, asyncpg.PostgresError) as exc:
                totals.failed += 1
                totals.errors.append(f"{filing.accession}: {type(exc).__name__}")
                continue
            if out.rejected_transition:
                reason = out.rejected_transition
                totals.rejected_transitions[reason] = totals.rejected_transitions.get(reason, 0) + 1
            if out.skipped == "unchanged":
                totals.unchanged += 1
            elif out.skipped:
                totals.skipped[out.skipped] = totals.skipped.get(out.skipped, 0) + 1
            elif out.created:
                totals.stored += 1
            else:
                totals.joined += 1
        if on_done is not None and totals.failed == failed_before:
            await on_done(listing_url)
    return totals
