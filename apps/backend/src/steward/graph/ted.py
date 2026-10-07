"""TED (Tenders Electronic Daily) contract award notices as procurement disclosures.

Parsed against the documented TED search API v3 (`POST /v3/notices/search`): a JSON body
`{"query": <expert query>, "fields": [...], "page": n, "limit": m}` answered with
`{"notices": [...], "totalNoticeCount": N}` where each notice carries only the requested eForms
business terms, multilingual ones as a language map (`{"ENG": ["..."]}` or `{"ENG": "..."}`):
`publication-number`, `notice-type`, `notice-title`, `buyer-name`, `winner-name`,
`total-value` / `total-value-cur`, `publication-date`, `links`.

The adapter only *suggests*: an award names a winner by legal name, so a match against the
holding's names becomes a `procurement_award` disclosure for the owner to confirm. The query
syntax and field availability follow the public API documentation and the recorded-format
fixture in tests/fixtures/graph; they have not been verified against the live service.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import asyncpg
import httpx

from steward.graph.keys import normalize_object_key
from steward.graph.relationships import upsert_disclosure
from steward.sources.base import AccessResult, AccessState
from steward.sources.free_market import FreeSourceError
from steward.sources.store import record_access

TED_SEARCH_URL = "https://api.ted.europa.eu/v3/notices/search"
TED_NOTICE_URL = "https://ted.europa.eu/en/notice/-/detail/{number}"
FIELDS = [
    "publication-number",
    "notice-type",
    "notice-title",
    "buyer-name",
    "winner-name",
    "total-value",
    "total-value-cur",
    "publication-date",
    "classification-cpv",
    "identifier-lot",
]
AWARD_NOTICE_TYPES = frozenset({"can-standard", "can-social", "can-desg", "can-modif"})
PAGE_LIMIT = 100


@dataclass(frozen=True, slots=True)
class TedAward:
    number: str
    notice_type: str
    title: str | None
    buyer: str | None
    winners: tuple[str, ...]
    value: Decimal | None
    currency: str | None
    published: dt.date | None
    cpv_codes: tuple[str, ...] = ()
    lot_ids: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        return "modified" if self.notice_type == "can-modif" else "awarded"

    @property
    def url(self) -> str:
        return TED_NOTICE_URL.format(number=self.number)


def _localized(value: object) -> list[str]:
    """Texts of a TED multilingual term, English first."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value if isinstance(v, str)]
    if isinstance(value, Mapping):
        ordered = sorted(value.items(), key=lambda kv: (str(kv[0]).upper() != "ENG", str(kv[0])))
        for _, texts in ordered:
            parsed = _localized(texts)
            if parsed:
                return parsed
    return []


def _date(value: object) -> dt.date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return dt.date.fromisoformat(value[:10])  # "2026-03-01+01:00"
    except ValueError:
        return None


def parse_ted_response(body: Mapping[str, Any]) -> tuple[list[TedAward], int]:
    """(awards, total notice count). Notices that are not award notices or name no winner are
    skipped; a body without a `notices` list is a schema change and raises."""
    notices = body.get("notices")
    if not isinstance(notices, list):
        raise FreeSourceError("TED response lacks a notices list")
    total = body.get("totalNoticeCount")
    awards: list[TedAward] = []
    for notice in notices:
        if not isinstance(notice, Mapping) or not notice.get("publication-number"):
            continue
        notice_type = str(notice.get("notice-type") or "")
        if notice_type and notice_type not in AWARD_NOTICE_TYPES:
            continue
        winners = tuple(dict.fromkeys(_localized(notice.get("winner-name"))))
        if not winners:
            continue
        raw_value = notice.get("total-value")
        value: Decimal | None
        try:
            value = Decimal(str(raw_value)) if raw_value not in (None, "") else None
            if value is not None and (not value.is_finite() or value < 0):
                value = None
        except InvalidOperation:
            value = None
        currencies = _localized(notice.get("total-value-cur"))
        titles, buyers = (
            _localized(notice.get("notice-title")),
            _localized(notice.get("buyer-name")),
        )
        awards.append(
            TedAward(
                number=str(notice["publication-number"]),
                notice_type=notice_type,
                title=titles[0] if titles else None,
                buyer=buyers[0] if buyers else None,
                winners=winners,
                value=value,
                currency=currencies[0] if currencies else None,
                published=_date(notice.get("publication-date")),
                cpv_codes=tuple(dict.fromkeys(_localized(notice.get("classification-cpv")))),
                lot_ids=tuple(dict.fromkeys(_localized(notice.get("identifier-lot")))),
            )
        )
    return awards, int(total) if isinstance(total, int) else len(awards)


async def store_award_detail(
    pool: asyncpg.Pool | asyncpg.Connection, disclosure_id: int, award: TedAward
) -> int:
    """Keep the structured fields of an award: one row per lot, or one row when none is named.
    Re-reading a notice updates its rows in place, so a correction or a repeated lot never
    duplicates. The row belongs to a procurement claim and has no revenue share or denominator."""
    currency = award.currency if award.currency and len(award.currency) == 3 else None
    lots = award.lot_ids or ("",)
    await pool.executemany(
        """insert into public.procurement_award_detail
             (disclosure_id, notice_number, lot_id, notice_type, cpv_codes, value, currency,
              status, published)
           values ($1,$2,$3,$4,$5,$6,$7,$8,$9)
           on conflict (notice_number, lot_id) do update set
             disclosure_id = excluded.disclosure_id, notice_type = excluded.notice_type,
             cpv_codes = excluded.cpv_codes, value = excluded.value,
             currency = excluded.currency, status = excluded.status,
             published = excluded.published""",
        [
            (
                disclosure_id,
                award.number,
                lot,
                award.notice_type or None,
                list(award.cpv_codes),
                award.value,
                currency.upper() if currency else None,
                award.status,
                award.published,
            )
            for lot in lots
        ],
    )
    return len(lots)


def build_query(names: Sequence[str], since: dt.date) -> str:
    quoted = " OR ".join('"' + n.replace('"', " ").strip() + '"' for n in names if n.strip())
    if not quoted:
        raise ValueError("at least one winner name is required")
    return f"winner-name=({quoted}) AND publication-date>={since:%Y%m%d}"


async def search_awards(
    http: httpx.AsyncClient,
    names: Sequence[str],
    since: dt.date,
    *,
    max_pages: int = 5,
) -> list[TedAward]:
    awards: list[TedAward] = []
    for page in range(1, max_pages + 1):
        try:
            response = await http.post(
                TED_SEARCH_URL,
                json={
                    "query": build_query(names, since),
                    "fields": FIELDS,
                    "page": page,
                    "limit": PAGE_LIMIT,
                    "scope": "ALL",
                },
            )
        except httpx.HTTPError as exc:
            raise FreeSourceError(
                f"TED network error ({type(exc).__name__})", provider_charged=False
            ) from exc
        if response.status_code != 200:
            raise FreeSourceError(f"TED HTTP {response.status_code}", response.status_code)
        try:
            body = response.json()
        except ValueError as exc:
            raise FreeSourceError("TED sent invalid JSON") from exc
        batch, total = parse_ted_response(body)
        awards += batch
        if page * PAGE_LIMIT >= total or not body.get("notices"):
            break
    return awards


def _fiscal_period(published: dt.date | None) -> str | None:
    return f"FY{published.year}" if published else None


async def collect_ted_awards(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    *,
    ticker: str,
    names: Sequence[str],
    since: dt.date,
) -> int:
    """Store awards won by the holding (a winner name equal to one of its names, normalised)
    as suggested `procurement_award` disclosures. Returns the number stored."""
    wanted = {normalize_object_key(n) for n in names if n.strip()}
    try:
        awards = await search_awards(http, names, since)
    except FreeSourceError as exc:
        await record_access(
            pool,
            "ted",
            "notice_search",
            AccessResult(exc.state or AccessState.TEMPORARILY_FAILED, "TED search failed"),
            status_code=exc.status_code,
        )
        raise
    stored = 0
    for award in awards:
        matched = [w for w in award.winners if normalize_object_key(w) in wanted]
        if not matched:
            continue  # the query is a fuzzy match: only an exact legal-name winner counts
        buyer = award.buyer or "unnamed contracting authority"
        value = f"; value {award.value} {award.currency or ''}".rstrip() if award.value else ""
        passage = (
            f"TED notice {award.number} ({award.notice_type or 'award'}): "
            f"{award.title or 'contract'}"
            f" awarded to {', '.join(award.winners)} by {buyer}{value}."
        )
        claim_id = await upsert_disclosure(
            pool,
            ticker=ticker,
            relationship="procurement_award",
            direction="award_to_issuer",
            passage=passage,
            source_url=award.url,
            source="ted",
            counterparty_key=f"ted-buyer:{normalize_object_key(buyer)}",
            counterparty_name=buyer,
            fiscal_period=_fiscal_period(award.published),
            valid_from=award.published,
            origin="ted_award",
        )
        await store_award_detail(pool, claim_id, award)
        stored += 1
    await record_access(
        pool,
        "ted",
        "notice_search",
        AccessResult(AccessState.AVAILABLE, f"{len(awards)} award notices read"),
    )
    return stored
