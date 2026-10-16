"""Identity, reference data and group structure (`reference_data`, weekly).

Per holding, in order (news matching, Stooq symbols and structure all key off the result):

1. OpenFIGI maps the ISIN to its share class and every listing (monthly, or when new).
2. ESMA FIRDS gives each EU venue's record and the issuer LEI; FITRS gives liquidity,
   turnover and the most relevant market, which sets the home market. US issuers take their
   home market from EDGAR, others without FITRS data from the home-country listing.
3. GLEIF Level 1 gives the legal entity (names go into the news aliases) and cross-checks the
   ISIN -> LEI mapping; Level 2 gives direct and ultimate consolidating parents (or the
   reported reason there is none) and the direct children, whose names become aliases.
4. Wikidata (monthly, optional) adds multilingual aliases and suggested relationships:
   parents, subsidiaries and owners it alone reports stay suggested unless GLEIF lists them,
   and inferred competitors are always suggestions. Relationships the owner rejected are
   never suggested again.
5. Trust tiers (Phase 4): GLEIF is tier 1, Wikidata tier 3. A Wikidata parent that GLEIF
   contradicts (GLEIF reports a different direct and ultimate parent) is `disputed`, with
   GLEIF's parent kept beside it, until the owner decides; it never becomes confirmed on
   Wikidata's word.

A FIRDS termination on the home market, or a GLEIF successor, inactive status or lapsed
registration, raises a lifecycle event. An instrument that cannot be resolved keeps the
reason in `instrument.identity_error` (shown on the Activity page) and the job carries on.
"""

import datetime as dt
import hashlib
import json
import logging
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import asyncpg
import httpx

from steward import __version__
from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.jobs import JobsConfig, ReferenceSettings
from steward.config.store import load_active
from steward.graph.successors import reconcile_parent_change
from steward.jobs.runner import JobContext
from steward.news.link import short_name
from steward.reference.events import LifecycleEvent, entity_events, listing_events, raise_events
from steward.reference.lifecycle import name_key, sync_firds_names, sync_gleif_issuer
from steward.settings import get_settings
from steward.sources import esma, openfigi
from steward.sources.edgar import EXCHANGE_MIC, EdgarClient, EdgarError
from steward.sources.esma import home_country
from steward.sources.gleif import GleifClient, GleifError, LegalEntity, ReportingException
from steward.sources.wikidata import LANGUAGES, WikidataClient, WikidataError, WikidataLink

logger = logging.getLogger(__name__)

# Home-country composite exchange code -> operating MIC, when FITRS has no most relevant market.
COMPOSITE_MIC: dict[str, str] = {
    "LN": "XLON", "GR": "XETR", "FP": "XPAR", "NA": "XAMS", "SW": "XSWX", "DC": "XCSE",
    "SS": "XSTO", "NO": "XOSL", "FH": "XHEL", "IM": "XMIL", "SM": "XMAD", "BB": "XBRU",
    "ID": "XDUB", "AV": "XWBO", "PL": "XLIS", "CP": "XPRA", "PW": "XWAR", "JP": "XTKS",
    "CN": "XTSE", "AU": "XASX",
}  # fmt: skip


@dataclass
class ReferenceDeps:
    http: httpx.AsyncClient
    gleif: GleifClient
    wikidata: WikidataClient | None = None
    edgar: EdgarClient | None = None
    openfigi_key: str | None = None
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC)


@dataclass
class HoldingResult:
    ticker: str
    share_class_figi: str | None = None
    home_mic: str | None = None
    issuer_lei: str | None = None
    issuer_id: int | None = None
    price_symbol: str | None = None
    listings: int = 0
    children: int = 0
    events: list[LifecycleEvent] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    firds_history: list[esma.FirdsRecord] = field(default_factory=list, repr=False)

    def detail(self) -> dict[str, Any]:
        return {
            "share_class_figi": self.share_class_figi,
            "home_mic": self.home_mic,
            "issuer_lei": self.issuer_lei,
            "price_symbol": self.price_symbol,
            "listings": self.listings,
            "children": self.children,
            "events": [e.headline for e in self.events],
            "problems": self.problems,
            "notes": self.notes,
        }


def object_key(lei: str | None, qid: str | None, name: str) -> str:
    if lei:
        return lei
    if qid:
        return f"wd:{qid}"
    return f"name:{' '.join(name.casefold().split())}"


async def upsert_relationship(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    ticker: str,
    subject: str,
    subject_name: str | None,
    type: str,
    object_name: str,
    object_lei: str | None = None,
    qid: str | None = None,
    source: str,
    confirmed: bool,
    source_ref: str | None = None,
    as_of: dt.date | None = None,
    exception_reason: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Insert or corroborate a relationship. A tier-1 source (GLEIF) or the owner confirms;
    Wikidata and filings alone only suggest. An owner's rejection is never overturned."""
    key = object_key(object_lei, qid, object_name)
    await conn.execute(
        "insert into public.entity_relationship (ticker, subject, subject_name, object_key,"
        " object_name, object_lei, type, source, sources, source_ref, as_of, status,"
        " exception_reason, detail) values ($1, $2, $3, $4, $5, $6, $7, $8, array[$8], $9,"
        " $10, $11, $12, $13::jsonb)"
        " on conflict (ticker, type, object_key) do update set"
        " sources = (select array_agg(distinct s) from unnest(entity_relationship.sources"
        "   || array[excluded.source]) s),"
        " source = case when excluded.status = 'confirmed' then excluded.source"
        "   else entity_relationship.source end,"
        " status = case when entity_relationship.status = 'rejected' then 'rejected'"
        "   when entity_relationship.status = 'disputed' and excluded.status <> 'confirmed'"
        "   then 'disputed'"
        "   when excluded.status = 'confirmed' or entity_relationship.status = 'confirmed'"
        "   then 'confirmed' else 'suggested' end,"
        " object_name = excluded.object_name,"
        " object_lei = coalesce(excluded.object_lei, entity_relationship.object_lei),"
        " source_ref = coalesce(excluded.source_ref, entity_relationship.source_ref),"
        " as_of = coalesce(excluded.as_of, entity_relationship.as_of),"
        " detail = coalesce(excluded.detail, entity_relationship.detail)",
        ticker,
        subject,
        subject_name,
        key,
        object_name,
        object_lei,
        type,
        source,
        source_ref,
        as_of,
        "confirmed" if confirmed else "suggested",
        exception_reason,
        json.dumps(detail) if detail else None,
    )


def _same_entity(a: asyncpg.Record, b: asyncpg.Record) -> bool:
    if a["object_lei"] and b["object_lei"]:
        return bool(a["object_lei"] == b["object_lei"])
    return bool(short_name(a["object_name"]).casefold() == short_name(b["object_name"]).casefold())


async def reconcile_structure(conn: asyncpg.Connection | asyncpg.Pool, ticker: str) -> list[int]:
    """Mark Wikidata parents that GLEIF contradicts as disputed (and clear disputes GLEIF no
    longer supports). Returns the ids now disputed."""
    rows = await conn.fetch(
        "select id, type, object_key, object_name, object_lei, source, sources, source_ref,"
        " status from public.entity_relationship where ticker = $1"
        " and type in ('parent', 'ultimate_parent')",
        ticker,
    )
    gleif = [r for r in rows if "gleif" in r["sources"] and r["status"] == "confirmed"]
    disputed: list[int] = []
    for row in rows:
        if row["sources"] != ["wikidata"] or row["status"] not in ("suggested", "disputed"):
            continue
        # Wikidata's "parent organization" may be GLEIF's direct or ultimate parent.
        contradicted = bool(gleif) and not any(_same_entity(row, g) for g in gleif)
        if contradicted:
            other = next((g for g in gleif if g["type"] == row["type"]), gleif[0])
            conflict = {
                "source": "gleif",
                "tier": 1,
                "type": other["type"],
                "object_key": other["object_key"],
                "object_name": other["object_name"],
                "source_ref": other["source_ref"],
            }
            await conn.execute(
                "update public.entity_relationship set status = 'disputed',"
                " conflict = $2::jsonb where id = $1",
                row["id"],
                json.dumps(conflict),
            )
            disputed.append(row["id"])
        elif row["status"] == "disputed":
            await conn.execute(
                "update public.entity_relationship set status = 'suggested', conflict = null"
                " where id = $1",
                row["id"],
            )
    return disputed


async def replace_aliases(
    conn: asyncpg.Connection | asyncpg.Pool,
    ticker: str,
    source: str,
    kinds: list[str],
    aliases: list[tuple[str, str]],
) -> None:
    """Set the (alias, kind) rows a source contributes for these kinds."""
    await conn.execute(
        "delete from public.entity_alias where ticker = $1 and source = $2"
        " and kind = any($3::text[])",
        ticker,
        source,
        kinds,
    )
    rows = [(ticker, a, k, source) for a, k in dict.fromkeys(aliases) if a and len(a) >= 2]
    await conn.executemany(
        "insert into public.entity_alias (ticker, alias, kind, source) values ($1, $2, $3, $4)"
        " on conflict (ticker, alias) do nothing",
        rows,
    )


async def store_entity(conn: asyncpg.Connection | asyncpg.Pool, e: LegalEntity) -> None:
    await conn.execute(
        "insert into public.legal_entity (lei, legal_name, other_names, jurisdiction,"
        " legal_form, category, hq_address, legal_address, hq_country, status, successor_lei,"
        " registration_status, next_renewal, raw, fetched_at) values ($1, $2, $3, $4, $5, $6,"
        " $7::jsonb, $8::jsonb, $9, $10, $11, $12, $13, $14::jsonb, now())"
        " on conflict (lei) do update set legal_name = excluded.legal_name,"
        " other_names = excluded.other_names, jurisdiction = excluded.jurisdiction,"
        " legal_form = excluded.legal_form, category = excluded.category,"
        " hq_address = excluded.hq_address, legal_address = excluded.legal_address,"
        " hq_country = excluded.hq_country, status = excluded.status,"
        " successor_lei = excluded.successor_lei,"
        " registration_status = excluded.registration_status,"
        " next_renewal = excluded.next_renewal, raw = excluded.raw, fetched_at = now()",
        e.lei,
        e.legal_name,
        e.other_names,
        e.jurisdiction,
        e.legal_form,
        e.category,
        json.dumps(e.hq_address) if e.hq_address else None,
        json.dumps(e.legal_address) if e.legal_address else None,
        e.hq_country,
        e.status,
        e.successor_lei,
        e.registration_status,
        e.next_renewal,
        json.dumps(e.raw),
    )


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


async def _openfigi(
    pool: asyncpg.Pool, deps: ReferenceDeps, ticker: str, isin: str, result: HoldingResult
) -> openfigi.Resolution | None:
    try:
        listings = await openfigi.map_isin(deps.http, isin, deps.openfigi_key)
    except (openfigi.OpenFigiError, httpx.HTTPError) as exc:
        result.problems.append(f"OpenFIGI: {exc}")
        return None
    if not listings:
        result.problems.append(f"OpenFIGI knows no listing for {isin}")
        return None
    resolution = openfigi.resolve(listings, isin)
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("delete from public.instrument_identifier where ticker = $1", ticker)
        await conn.executemany(
            "insert into public.instrument_identifier (ticker, figi, composite_figi,"
            " share_class_figi, listing_ticker, exch_code, name, security_type, security_type2,"
            " market_sector, security_description, raw) values ($1, $2, $3, $4, $5, $6, $7,"
            " $8, $9, $10, $11, $12::jsonb) on conflict do nothing",
            [
                (
                    ticker,
                    x.figi,
                    x.composite_figi,
                    x.share_class_figi,
                    x.ticker,
                    x.exch_code,
                    x.name,
                    x.security_type,
                    x.security_type2,
                    x.market_sector,
                    x.security_description,
                    json.dumps(x.raw),
                )
                for x in listings[:1000]
            ],
        )
        # Country composites only (not the currency-suffixed OTC variants like "SAPNOK").
        country_codes = set(openfigi.HOME_COMPOSITE.values())
        tickers = [
            (x.ticker, "listing_ticker")
            for x in resolution.composites
            if x.ticker and x.exch_code in country_codes
        ]
        await replace_aliases(conn, ticker, "openfigi", ["listing_ticker"], tickers[:20])
    return resolution


async def _esma(
    pool: asyncpg.Pool,
    deps: ReferenceDeps,
    ticker: str,
    isin: str,
    result: HoldingResult,
) -> tuple[list[esma.FirdsRecord], esma.FitrsRecord | None, dict[str, dt.datetime | None]]:
    previous = {
        r["mic"]: r["terminated_at"]
        for r in await pool.fetch(
            "select mic, terminated_at from public.listing where isin = $1", isin
        )
    }
    try:
        records, history = await esma.firds_with_history(deps.http, isin)
        liquidity = await esma.fitrs(deps.http, isin)
        result.firds_history = history
    except (esma.EsmaError, httpx.HTTPError, ValueError) as exc:
        result.notes.append(f"ESMA registers: {exc}")
        return [], None, previous
    async with pool.acquire() as conn, conn.transaction():
        known_at = deps.now()
        for record in history:
            encoded = json.dumps(record.raw, sort_keys=True, separators=(",", ":")).encode()
            publication = esma._ts(record.raw.get("publication_date"))
            await conn.execute(
                """insert into public.listing_lifecycle_snapshot
                   (isin,mic,publication_date,status,terminated_at,issuer_lei,currency,
                    source_url,content_hash,known_at,raw)
                   values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb)
                   on conflict do nothing""",
                record.isin,
                record.mic,
                publication.date() if publication else None,
                record.status,
                record.terminated_at,
                record.issuer_lei,
                record.currency,
                f"{esma.FIRDS_URL}?q=isin:{isin}",
                hashlib.sha256(encoded).hexdigest(),
                known_at,
                encoded.decode(),
            )
        for r in records:
            await conn.execute(
                "insert into public.listing (isin, mic, full_name, short_name, cfi, currency,"
                " issuer_lei, admitted_at, first_trade_at, terminated_at, status, raw,"
                " fetched_at) values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,"
                " $12::jsonb, now()) on conflict (isin, mic) do update set"
                " full_name = excluded.full_name, short_name = excluded.short_name,"
                " cfi = excluded.cfi, currency = excluded.currency,"
                " issuer_lei = excluded.issuer_lei, admitted_at = excluded.admitted_at,"
                " first_trade_at = excluded.first_trade_at,"
                " terminated_at = excluded.terminated_at, status = excluded.status,"
                " raw = excluded.raw, fetched_at = now()",
                r.isin,
                r.mic,
                r.full_name,
                r.short_name,
                r.cfi,
                r.currency,
                r.issuer_lei,
                r.admitted_at,
                r.first_trade_at,
                r.terminated_at,
                r.status,
                json.dumps(r.raw),
            )
        if liquidity is not None:
            await conn.execute(
                "insert into public.listing_liquidity (isin, liquid, avg_daily_turnover,"
                " turnover_currency, avg_daily_trades, most_relevant_market, period_from,"
                " period_to, raw, fetched_at) values ($1, $2, $3, $4, $5, $6, $7, $8,"
                " $9::jsonb, now()) on conflict (isin) do update set liquid = excluded.liquid,"
                " avg_daily_turnover = excluded.avg_daily_turnover,"
                " turnover_currency = excluded.turnover_currency,"
                " avg_daily_trades = excluded.avg_daily_trades,"
                " most_relevant_market = excluded.most_relevant_market,"
                " period_from = excluded.period_from, period_to = excluded.period_to,"
                " raw = excluded.raw, fetched_at = now()",
                isin,
                liquidity.liquid,
                liquidity.avg_daily_turnover,
                liquidity.turnover_currency,
                liquidity.avg_daily_trades,
                liquidity.most_relevant_market,
                liquidity.period_from,
                liquidity.period_to,
                json.dumps(liquidity.raw),
            )
    result.listings = sum(1 for r in records if r.status != "CANC")
    return records, liquidity, previous


async def _home_market(
    deps: ReferenceDeps,
    isin: str,
    cik: str | None,
    liquidity: esma.FitrsRecord | None,
    resolution: openfigi.Resolution | None,
    result: HoldingResult,
) -> str | None:
    if isin.startswith("US") and cik and deps.edgar is not None:
        try:
            exchanges = await deps.edgar.exchanges(cik)
        except (EdgarError, httpx.HTTPError) as exc:
            result.notes.append(f"EDGAR exchanges: {exc}")
            exchanges = []
        mic = next((EXCHANGE_MIC[e] for e in exchanges if e in EXCHANGE_MIC), None)
        if mic:
            return mic
    # FITRS's most relevant market is the most relevant EU venue: the home market only when
    # it is in the issuer's country (for a UK or Swiss share it is some EU venue).
    mrm = liquidity.most_relevant_market if liquidity else None
    if mrm and esma.MIC_COUNTRY.get(mrm) == isin[:2]:
        return mrm
    home = resolution.home if resolution else None
    if home is not None and home.exch_code in COMPOSITE_MIC:
        return COMPOSITE_MIC[home.exch_code]
    return None


def _country(address: dict[str, Any] | None) -> str | None:
    return str(address.get("country") or "")[:2].upper() or None if address else None


def plausible_issuer(entity: LegalEntity, isin: str, name: str) -> bool:
    """Whether a FIRDS issuer LEI can be the share's issuer. An EU venue trading a foreign share
    whose issuer has no LEI reports its own operator's LEI instead (TP ICAP's for a US share),
    so the entity must sit in the ISIN's country or carry the instrument's name."""
    countries = {(entity.jurisdiction or "")[:2].upper(), _country(entity.legal_address)}
    if isin[:2].upper() in countries:
        return True
    words = [w for w in name_key(short_name(name) or name).split() if len(w) >= 2]
    names = " ".join(name_key(n) for n in [entity.legal_name, *entity.other_names])
    return bool(words) and words[0] in names.split()


async def _gleif(
    pool: asyncpg.Pool,
    deps: ReferenceDeps,
    ticker: str,
    name: str,
    isin: str,
    firds_lei: str | None,
    settings: ReferenceSettings,
    result: HoldingResult,
    today: dt.date,
    cik: str | None = None,
) -> LegalEntity | None:
    successor_entity: LegalEntity | None = None
    try:
        by_isin = await deps.gleif.for_isin(isin)
        lei = firds_lei or (by_isin.lei if by_isin else None)
        if lei is None:
            result.problems.append("no issuer LEI (FIRDS and GLEIF have none for this ISIN)")
            return None
        if by_isin is not None and firds_lei and by_isin.lei != firds_lei:
            result.notes.append(
                f"GLEIF maps {isin} to {by_isin.lei}, FIRDS to {firds_lei}; FIRDS kept"
            )
        entity = (
            by_isin if by_isin is not None and by_isin.lei == lei else await deps.gleif.record(lei)
        )
        if (
            entity is not None
            and entity is not by_isin
            and not plausible_issuer(entity, isin, name)
        ):
            result.notes.append(
                f"FIRDS names {entity.legal_name} ({lei}) as the issuer of {isin}; neither its"
                " country nor its name fits, so it is taken for the venue's own LEI"
            )
            entity = by_isin
            if entity is None:
                result.problems.append("no issuer LEI (FIRDS gave the venue's, GLEIF has none)")
                return None
        if entity is None:
            result.problems.append(f"GLEIF has no record for LEI {lei}")
            return None
        lei = entity.lei
        raw_bytes = json.dumps(entity.raw, sort_keys=True, separators=(",", ":")).encode()
        initial_registration = (entity.raw.get("attributes", {}).get("registration") or {}).get(
            "initialRegistrationDate"
        )
        valid_from = None
        if initial_registration:
            parsed_date = esma._ts(initial_registration)
            valid_from = parsed_date.date() if parsed_date else None
        await pool.execute(
            """insert into public.legal_entity_snapshot
               (lei,known_at,valid_from,legal_name,other_names,entity_status,
                registration_status,successor_lei,source_url,content_hash,raw)
               values($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10,$11::jsonb)
               on conflict do nothing""",
            lei,
            deps.now(),
            valid_from,
            entity.legal_name,
            json.dumps(entity.other_names),
            entity.status,
            entity.registration_status,
            entity.successor_lei,
            f"https://api.gleif.org/api/v1/lei-records/{lei}",
            hashlib.sha256(raw_bytes).hexdigest(),
            raw_bytes.decode(),
        )
        direct = await deps.gleif.parent(lei, "direct")
        ultimate = await deps.gleif.parent(lei, "ultimate")
        children = await deps.gleif.children(lei)
        if deps.gleif.children_total > len(children):
            result.notes.append(
                f"GLEIF: {len(children)} of {deps.gleif.children_total} direct children read"
            )
        if entity.successor_lei:
            try:
                successor_entity = await deps.gleif.record(entity.successor_lei)
            except GleifError as exc:  # the link is still recorded by LEI in the snapshot
                result.notes.append(f"GLEIF successor {entity.successor_lei}: {exc}")
    except GleifError as exc:
        result.problems.append(f"GLEIF: {exc}")
        return None

    before = await pool.fetchrow(
        "select status, successor_lei, registration_status, legal_name"
        " from public.legal_entity where lei = $1",
        lei,
    )
    previous_parents = {
        r["object_lei"]
        for r in await pool.fetch(
            "select object_lei from public.entity_relationship where ticker=$1 and subject=$2"
            " and type='parent' and source='gleif' and object_lei is not null",
            ticker,
            lei,
        )
    }
    async with pool.acquire() as conn, conn.transaction():
        await store_entity(conn, entity)
        issuer_sync = await sync_gleif_issuer(
            conn,
            entity,
            before_name=before["legal_name"] if before else None,
            today=today,
            known_at=deps.now(),
            successor=successor_entity,
            cik=cik,
        )
        result.issuer_id = issuer_sync.issuer_id
        if issuer_sync.renamed_from:
            result.notes.append(
                f"legal name changed: {issuer_sync.renamed_from!r} -> {entity.legal_name!r}"
            )
        await sync_firds_names(
            conn, issuer_sync.issuer_id, result.firds_history, known_at=deps.now()
        )
        await conn.execute("delete from public.entity_reporting_exception where lei = $1", lei)
        for kind, parent in (("parent", direct), ("ultimate_parent", ultimate)):
            if isinstance(parent, LegalEntity):
                await store_entity(conn, parent)
                await upsert_relationship(
                    conn,
                    ticker=ticker,
                    subject=lei,
                    subject_name=entity.legal_name,
                    type=kind,
                    object_name=parent.legal_name,
                    object_lei=parent.lei,
                    source="gleif",
                    confirmed=True,
                    source_ref=f"https://search.gleif.org/#/record/{lei}",
                    as_of=None,  # GLEIF gives no start date; the fetch date is not one
                )
            elif isinstance(parent, ReportingException):
                await conn.execute(
                    "insert into public.entity_reporting_exception (lei, category, reason)"
                    " values ($1, $2, $3) on conflict (lei, category) do update"
                    " set reason = excluded.reason, fetched_at = now()",
                    lei,
                    parent.category,
                    parent.reason,
                )
        for child in children:
            await upsert_relationship(
                conn,
                ticker=ticker,
                subject=lei,
                subject_name=entity.legal_name,
                type="subsidiary",
                object_name=child.legal_name,
                object_lei=child.lei,
                source="gleif",
                confirmed=True,
                source_ref=f"https://search.gleif.org/#/record/{child.lei}",
                as_of=None,  # GLEIF gives no start date; the fetch date is not one
            )
        names = [(entity.legal_name, "legal_name"), (short_name(entity.legal_name), "legal_name")]
        names += [(n, "other_name") for n in entity.other_names]
        subsidiaries = [(c.legal_name, "subsidiary") for c in children]
        await replace_aliases(
            conn,
            ticker,
            "gleif",
            ["legal_name", "other_name", "subsidiary"],
            names + subsidiaries[: settings.max_subsidiary_aliases],
        )
    result.issuer_lei = lei
    result.children = len(children)
    if isinstance(direct, LegalEntity) and previous_parents and direct.lei not in previous_parents:
        await reconcile_parent_change(
            pool,
            ticker=ticker,
            change_kind="parent_changed",
            from_key=sorted(previous_parents)[0],
            to_key=direct.lei,
            observed_source="gleif",
        )
    if entity.successor_lei and (before is None or before["successor_lei"] != entity.successor_lei):
        await reconcile_parent_change(
            pool,
            ticker=ticker,
            change_kind="successor_named",
            from_key=lei,
            to_key=entity.successor_lei,
            observed_source="gleif",
        )
    after = {
        "status": entity.status,
        "successor_lei": entity.successor_lei,
        "registration_status": entity.registration_status,
    }
    result.events += entity_events(
        ticker, name, lei, dict(before) if before else None, after, today
    )
    return entity


async def _wikidata(
    pool: asyncpg.Pool,
    deps: ReferenceDeps,
    ticker: str,
    isin: str,
    lei: str | None,
    known_qid: str | None,
    subject_name: str | None,
    result: HoldingResult,
    languages: Sequence[str] = LANGUAGES,
) -> None:
    assert deps.wikidata is not None
    try:
        qid = known_qid or await deps.wikidata.find(lei, isin)
        if qid is None:
            result.notes.append("Wikidata: no item for this LEI or ISIN")
            return
        props = await deps.wikidata.properties(qid)
        aliases = await deps.wikidata.aliases(qid, languages)
        competitors = await deps.wikidata.competitors(qid)
    except (WikidataError, httpx.HTTPError, ValueError) as exc:
        result.notes.append(f"Wikidata: {exc}")
        return
    kinds = {"parent": "parent", "subsidiary": "subsidiary", "owned_by": "owner"}
    source_ref = f"https://www.wikidata.org/wiki/{qid}"
    subject = lei or f"wd:{qid}"

    def values(kind: str) -> list[WikidataLink]:
        return [p for p in props if p.kind == kind]

    summary = {
        "qid": qid,
        "industry": [p.label for p in values("industry")],
        "products": [p.label for p in values("product")][:20],
        "ceo": [p.label for p in values("ceo")],
        "chair": [p.label for p in values("chair")],
        "website": next((p.label for p in values("website")), None),
        "part_of": [p.label for p in values("part_of")],
        "fetched_at": deps.now().isoformat(),
    }
    async with pool.acquire() as conn, conn.transaction():
        for link in [*props, *competitors]:
            rel_type = "competitor" if link.kind == "competitor" else kinds.get(link.kind)
            if rel_type is None:
                continue
            await upsert_relationship(
                conn,
                ticker=ticker,
                subject=subject,
                subject_name=subject_name,
                type=rel_type,
                object_name=link.label,
                object_lei=link.lei,
                qid=link.qid,
                source="wikidata",
                confirmed=False,
                source_ref=source_ref,
                detail={"qid": link.qid},
            )
        await replace_aliases(conn, ticker, "wikidata", ["alias"], [(a, "alias") for a in aliases])
        await conn.execute(
            "update public.instrument set wikidata_qid = $2, wikidata = $3::jsonb"
            " where t212_ticker = $1",
            ticker,
            qid,
            json.dumps(summary),
        )


async def resolve_holding(
    pool: asyncpg.Pool,
    deps: ReferenceDeps,
    ticker: str,
    settings: ReferenceSettings,
    profiles: HoldingProfilesConfig,
    *,
    force: bool = False,
) -> HoldingResult:
    result = HoldingResult(ticker)
    inst = await pool.fetchrow(
        "select t212_ticker, name, isin, cik, share_class_figi, security_type, home_mic,"
        " issuer_lei, price_symbol, identity_resolved_at, wikidata_qid, wikidata"
        " from public.instrument where t212_ticker = $1",
        ticker,
    )
    if inst is None or not inst["isin"]:
        result.problems.append("Trading212 gave no ISIN for this instrument")
        await _record(pool, ticker, result, inst)
        return result
    isin: str = inst["isin"]
    now = deps.now()
    today = now.date()
    name = inst["name"] or ticker
    profile = profiles.for_ticker(ticker)

    stale = inst["identity_resolved_at"] is None or now - inst[
        "identity_resolved_at"
    ] > dt.timedelta(days=settings.openfigi_refresh_days)
    has_ids = await pool.fetchval(
        "select exists (select 1 from public.instrument_identifier where ticker = $1)", ticker
    )
    resolution = None
    if force or stale or not has_ids:
        resolution = await _openfigi(pool, deps, ticker, isin, result)
    if resolution is None and has_ids:
        rows = await pool.fetch(
            "select raw from public.instrument_identifier where ticker = $1", ticker
        )
        listings = openfigi.parse_mapping({"data": [json.loads(r["raw"]) for r in rows]})
        resolution = openfigi.resolve(listings, isin)
    if resolution is not None:
        result.share_class_figi = resolution.share_class_figi
        derived = openfigi.stooq_symbol(resolution.home)
        result.price_symbol = (profile.price_symbol if profile else None) or derived
    if result.share_class_figi is None:
        result.problems.append("no share-class FIGI")

    records, liquidity, previous = await _esma(pool, deps, ticker, isin, result)
    firds_leis = Counter(r.issuer_lei for r in records if r.issuer_lei)
    firds_lei = firds_leis.most_common(1)[0][0] if firds_leis else None
    cik = (profile.sec_cik.zfill(10) if profile and profile.sec_cik else None) or inst["cik"]
    result.home_mic = await _home_market(deps, isin, cik, liquidity, resolution, result)
    if result.home_mic is None:
        result.problems.append("no home market (MIC)")
    home = next((r for r in records if r.mic == result.home_mic), None)
    if home is not None:
        result.events += listing_events(
            ticker,
            name,
            isin,
            result.home_mic,
            home.mic in previous,
            previous.get(home.mic),
            home.terminated_at,
            today,
        )

    entity = await _gleif(pool, deps, ticker, name, isin, firds_lei, settings, result, today, cik)

    wikidata = json.loads(inst["wikidata"]) if inst["wikidata"] else None
    wikidata_due = (
        force
        or wikidata is None
        or now - dt.datetime.fromisoformat(wikidata["fetched_at"])
        > dt.timedelta(days=settings.wikidata_refresh_days)
    )
    if deps.wikidata is not None and settings.wikidata_enabled and wikidata_due:
        await _wikidata(
            pool,
            deps,
            ticker,
            isin,
            result.issuer_lei,
            inst["wikidata_qid"],
            entity.legal_name if entity else name,
            result,
            settings.wikidata_languages,
        )
    disputed = await reconcile_structure(pool, ticker)
    if disputed:
        result.notes.append(f"{len(disputed)} Wikidata parent(s) contradicted by GLEIF")
    await _record(pool, ticker, result, inst, security_type=_security_type(resolution))
    return result


def _security_type(resolution: openfigi.Resolution | None) -> str | None:
    home = resolution.home if resolution else None
    return home.security_type2 or home.security_type if home else None


async def _record(
    pool: asyncpg.Pool,
    ticker: str,
    result: HoldingResult,
    inst: asyncpg.Record | None,
    security_type: str | None = None,
) -> None:
    if inst is None:
        return
    country = home_country(result.home_mic, inst["isin"]) if inst["isin"] else None
    await pool.execute(
        "update public.instrument set share_class_figi = coalesce($2, share_class_figi),"
        " security_type = coalesce($3, security_type), home_mic = coalesce($4, home_mic),"
        " home_country = coalesce($5, home_country), issuer_lei = coalesce($6, issuer_lei),"
        " price_symbol = coalesce($7, price_symbol),"
        " identity_resolved_at = case when $8::text is null then now()"
        "   else identity_resolved_at end,"
        " identity_error = $8 where t212_ticker = $1",
        ticker,
        result.share_class_figi,
        security_type,
        result.home_mic,
        country,
        result.issuer_lei,
        result.price_symbol,
        "; ".join(result.problems) or None,
    )


# ---------------------------------------------------------------------------
# The job
# ---------------------------------------------------------------------------


async def holding_tickers(pool: asyncpg.Pool) -> list[str]:
    rows = await pool.fetch(
        # The latest sync's day (account_snapshot is written even when no position is
        # left, so a fully closed portfolio gives no holdings rather than stale ones).
        "select ticker from public.position_snapshot where snapshot_date = coalesce("
        " (select max(snapshot_date) from public.account_snapshot),"
        " (select max(snapshot_date) from public.position_snapshot))"
    )
    return sorted({r["ticker"] for r in rows})


async def bench_tickers(pool: asyncpg.Pool, held: Sequence[str]) -> list[str]:
    """Tickers with a stored thesis that are not held: the bench (principle 7d). They get prices
    like holdings so their expected return and risk can be ranked before a purchase."""
    from steward.config.thesis import ThesesConfig

    theses = await load_active(pool, "theses", ThesesConfig)
    owned = set(held)
    return sorted({t.ticker for t in (theses.body.theses if theses else [])} - owned)


async def run_reference_data(ctx: JobContext, deps: ReferenceDeps) -> None:
    pool = ctx.pool
    async with ctx.step("load_config") as step:
        jobs = await load_active(pool, "jobs", JobsConfig)
        profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
        await ctx.record_config_version(jobs.id if jobs else None)
        await ctx.record_config_version(profiles.id if profiles else None)
        settings = (jobs.body if jobs else JobsConfig()).reference
        only = ctx.params.get("ticker")
        tickers = [only] if isinstance(only, str) and only else await holding_tickers(pool)
        step.detail["tickers"] = tickers
    events: list[LifecycleEvent] = []
    unresolved: list[str] = []
    for ticker in tickers:
        async with ctx.step(f"identity:{ticker}") as step:
            result = await resolve_holding(
                pool,
                deps,
                ticker,
                settings,
                profiles.body if profiles else HoldingProfilesConfig(),
                force=bool(ctx.params.get("force")),
            )
            step.detail.update(result.detail())
            events += result.events
            if result.problems:
                unresolved.append(f"{ticker}: {'; '.join(result.problems)}")
    async with ctx.step("lifecycle_events") as step:
        created = await raise_events(pool, events, deps.now(), ctx.job_id)
        step.detail.update(events=[e.headline for e in events], created=created)
        step.detail["unresolved"] = unresolved


async def reference_data_handler(ctx: JobContext) -> None:
    settings = get_settings()
    agent = f"StewardAI/{__version__} ({settings.sec_user_agent or 'personal portfolio tool'})"
    async with httpx.AsyncClient(timeout=60.0, headers={"User-Agent": agent}) as http:
        deps = ReferenceDeps(
            http=http,
            gleif=GleifClient(http),
            wikidata=WikidataClient(http, agent),
            edgar=EdgarClient(http, settings.sec_user_agent) if settings.sec_user_agent else None,
            openfigi_key=(
                settings.openfigi_api_key.get_secret_value() if settings.openfigi_api_key else None
            ),
        )
        await run_reference_data(ctx, deps)
