"""Schedule 13D/G beneficial-ownership and control stakes, a channel separate from Form 13F.

A 13D/G says who controls or holds more than 5 % of one class of an issuer, as of the event
date, in the filer's own words and percentages. Form 13F lists institutional positions as of
a quarter end; fund holdings list a fund's portfolio. The three describe different things,
overlap and are filed at different lags, so this module never adds, nets or blends them:
stakes are stored and read in their own tables and carry their own limitations.

Scope of collection: only issuers the portfolio holds (matched by CIK, then reconciled by
CUSIP to the held security) and the filings of configured tracked managers (matched to held
issuers the same way). Filings without structured XML (everything before December 2024) are
counted as unstructured and skipped, not guessed.

Pure functions (`reconcile_class`, `compute_changes`, `chain_priors`) hold the logic and are
tested without a database.
"""

import datetime as dt
import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, cast

import asyncpg
import httpx

from steward import __version__
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.jobs.runner import JobContext, JobFailed
from steward.settings import get_settings
from steward.sources.documents import link_public_document
from steward.sources.edgar import EdgarClient, EdgarError, Filing
from steward.sources.schedule13 import (
    SCHEDULE13_FORMS,
    ReportingPerson,
    Schedule13Error,
    Schedule13Filing,
    parse_schedule13_xml,
    stake_of,
)

# A percent difference above this (percent points) between the filing's percent and the
# percent recomputed from the class's shares outstanding is flagged, not corrected.
PERCENT_TOLERANCE = Decimal("1.0")
FIVE_PERCENT = Decimal(5)


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Snapshot:
    """What a chain comparison needs from one stored or freshly parsed filing."""

    accession: str
    filed: dt.date
    schedule: str
    persons: Sequence[ReportingPerson]
    intent_text: str | None = None

    @property
    def stake(self) -> tuple[Decimal | None, Decimal | None]:
        return stake_of(list(self.persons))


@dataclass(frozen=True)
class Change:
    change_type: str
    person_key: str = ""
    person_name: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class ClassReconciliation:
    class_status: str  # cusip_match | class_unreconciled
    class_label: str | None
    class_shares_outstanding: Decimal | None
    class_shares_as_of: dt.date | None
    computed_percent: Decimal | None
    percent_check: str  # consistent | inconsistent | not_checkable
    note: str


def intent_fingerprint(text: str | None) -> str | None:
    if not text:
        return None
    normal = " ".join(text.lower().split())
    return hashlib.sha256(normal.encode()).hexdigest()[:16]


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _fmt(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def _powers(person: ReportingPerson) -> tuple[Decimal | None, ...]:
    return (
        person.sole_voting,
        person.shared_voting,
        person.sole_dispositive,
        person.shared_dispositive,
    )


def compute_changes(prev: Snapshot, cur: Snapshot) -> list[Change]:
    """Stake, group and intent differences between two consecutive filings of one chain.

    Group amendments matter: a member joining or leaving, or any member's beneficial
    ownership moving, changes the reported stake. Unknown values compare as unknown: a
    figure that goes from known to missing is reported as a change to unknown, never as 0.
    """
    changes: list[Change] = []
    before = {p.key: p for p in prev.persons}
    after = {p.key: p for p in cur.persons}
    for key, person in after.items():
        if key not in before:
            changes.append(
                Change(
                    "holder_added",
                    key,
                    person.name,
                    new_value=_fmt(person.aggregate_shares),
                    detail="reporting person joined the filing",
                )
            )
    for key, person in before.items():
        if key not in after:
            changes.append(
                Change(
                    "holder_removed",
                    key,
                    person.name,
                    old_value=_fmt(person.aggregate_shares),
                    detail="reporting person no longer on the filing",
                )
            )
    for key, new in after.items():
        old = before.get(key)
        if old is None:
            continue
        if old.aggregate_shares != new.aggregate_shares:
            changes.append(
                Change(
                    "holder_shares_changed",
                    key,
                    new.name,
                    _fmt(old.aggregate_shares),
                    _fmt(new.aggregate_shares),
                )
            )
        if old.percent_of_class != new.percent_of_class:
            changes.append(
                Change(
                    "holder_percent_changed",
                    key,
                    new.name,
                    _fmt(old.percent_of_class),
                    _fmt(new.percent_of_class),
                )
            )
        if _powers(old) != _powers(new):
            changes.append(
                Change(
                    "holder_power_changed",
                    key,
                    new.name,
                    ", ".join(str(_fmt(v)) for v in _powers(old)),
                    ", ".join(str(_fmt(v)) for v in _powers(new)),
                    detail="sole voting, shared voting, sole dispositive, shared dispositive",
                )
            )
        if old.member_of_group != new.member_of_group:
            changes.append(
                Change(
                    "group_status_changed",
                    key,
                    new.name,
                    str(old.member_of_group),
                    str(new.member_of_group),
                )
            )
    old_shares, old_percent = prev.stake
    new_shares, new_percent = cur.stake
    if old_shares != new_shares:
        changes.append(
            Change(
                "stake_shares_changed",
                old_value=_fmt(old_shares),
                new_value=_fmt(new_shares),
                detail="largest reporting-person figure; group members overlap, never summed",
            )
        )
    if old_percent != new_percent:
        changes.append(
            Change(
                "stake_percent_changed", old_value=_fmt(old_percent), new_value=_fmt(new_percent)
            )
        )
    if (
        old_percent is not None
        and new_percent is not None
        and old_percent > FIVE_PERCENT >= new_percent
    ):
        changes.append(
            Change(
                "fell_below_5_percent",
                old_value=_fmt(old_percent),
                new_value=_fmt(new_percent),
                detail="reported percent crossed down through the 5 % reporting threshold",
            )
        )
    if prev.schedule != cur.schedule:
        changes.append(
            Change(
                "schedule_type_changed",
                old_value=prev.schedule,
                new_value=cur.schedule,
                detail="13G (passive) to 13D (control intent) or back",
            )
        )
    if intent_fingerprint(prev.intent_text) != intent_fingerprint(cur.intent_text):
        changes.append(
            Change(
                "intent_changed",
                old_value=(prev.intent_text or "")[:300] or None,
                new_value=(cur.intent_text or "")[:300] or None,
                detail="Item 4 purpose of the transaction changed",
            )
        )
    return changes


def chain_priors(snapshots: Sequence[Snapshot]) -> dict[str, str | None]:
    """For each filing of one issuer class, the latest earlier filing it shares a reporting
    person with (its predecessor in the amendment chain), else None.

    Two unrelated holders of the same class therefore stay in separate chains; a group that
    gains a member continues the chain of the person it already contained.
    """
    ordered = sorted(snapshots, key=lambda s: (s.filed, s.accession))
    priors: dict[str, str | None] = {}
    for index, current in enumerate(ordered):
        keys = {p.key for p in current.persons}
        prior = next(
            (
                earlier.accession
                for earlier in reversed(ordered[:index])
                if keys & {p.key for p in earlier.persons}
            ),
            None,
        )
        priors[current.accession] = prior
    return priors


def reconcile_class(
    *,
    cusip_matched: bool,
    class_title: str | None,
    stake_shares: Decimal | None,
    stake_percent: Decimal | None,
    shares_row: dict[str, Any] | None,
) -> ClassReconciliation:
    """Tie a filing to the held security's share class and sanity-check its percent.

    The filing's CUSIP names the class; the held instrument's CUSIP comes from its ISIN or
    OpenFIGI. When it matches, the class's shares outstanding (per-class XBRL when the issuer
    tags classes, else the single reported figure) recompute the percent as a cross-check.
    The recomputed percent is never written over the filer's own percent.
    """
    if not cusip_matched:
        return ClassReconciliation(
            "class_unreconciled",
            None,
            None,
            None,
            None,
            "not_checkable",
            "Issuer matched by CIK but the filing's CUSIP is not the held security's: the "
            "stake may be in another share class and is not applied to this holding.",
        )
    if shares_row is None:
        return ClassReconciliation(
            "cusip_match",
            None,
            None,
            None,
            None,
            "not_checkable",
            "No shares-outstanding figure is stored for this holding.",
        )
    as_of = shares_row.get("as_of")
    if shares_row.get("ads_ratio_reported") is not None:
        return ClassReconciliation(
            "cusip_match",
            None,
            None,
            as_of,
            None,
            "not_checkable",
            "Shares outstanding are home-market shares of an ADS programme; the filing's "
            "class is not comparable without an ADS-ratio conversion.",
        )
    classes = [c for c in shares_row.get("classes") or [] if isinstance(c, dict)]
    label: str | None = None
    denominator: Decimal | None = None
    if classes:
        title = _norm(class_title or "")
        matches = [c for c in classes if _norm(str(c.get("label", ""))) in title and title]
        if len(matches) == 1:
            label = str(matches[0].get("label"))
            denominator = Decimal(str(matches[0].get("shares")))
        else:
            return ClassReconciliation(
                "cusip_match",
                None,
                None,
                as_of,
                None,
                "not_checkable",
                "The issuer reports several share classes and the filing's class title does "
                "not identify exactly one of them.",
            )
    else:
        label = class_title
        denominator = cast(Decimal, shares_row["shares"])
    if denominator is None or denominator <= 0 or stake_shares is None:
        return ClassReconciliation(
            "cusip_match",
            label,
            denominator,
            as_of,
            None,
            "not_checkable",
            "The filing reports no share count to check against.",
        )
    computed = (stake_shares / denominator * 100).quantize(Decimal("0.01"))
    if stake_percent is None:
        check = "not_checkable"
        note = "The filing reports no percent; the recomputed percent is shown for reference."
    elif abs(computed - stake_percent) <= PERCENT_TOLERANCE:
        check = "consistent"
        note = "Reported percent agrees with shares over the class's shares outstanding."
    else:
        check = "inconsistent"
        note = (
            "Reported percent differs from shares over the class's shares outstanding (the "
            "filing's own denominator may be a different, earlier date); the filer's figure "
            "is kept."
        )
    return ClassReconciliation("cusip_match", label, denominator, as_of, computed, check, note)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


@dataclass
class HeldIssuers:
    """Held securities indexed for matching a filing's issuer."""

    by_cik: dict[str, list[str]] = field(default_factory=dict)
    by_cusip: dict[str, str] = field(default_factory=dict)
    ticker_cusips: dict[str, set[str]] = field(default_factory=dict)

    def tickers_for(self, issuer_cik: str | None, cusip: str | None) -> list[tuple[str, bool]]:
        """(ticker, cusip_matched) for each held security the filing's issuer matches."""
        if cusip and cusip in self.by_cusip:
            return [(self.by_cusip[cusip], True)]
        if issuer_cik and issuer_cik in self.by_cik:
            return [(t, False) for t in self.by_cik[issuer_cik]]
        return []


async def load_held(pool: asyncpg.Pool) -> HeldIssuers:
    # Imported here: ownership.collect pulls in the whole 13F stack.
    from steward.ownership.collect import portfolio_cusips

    held = HeldIssuers()
    rows = await pool.fetch(
        """select distinct i.t212_ticker as ticker, i.cik
           from public.position_snapshot p join public.instrument i on i.t212_ticker = p.ticker
           where p.snapshot_date = (select max(snapshot_date) from public.account_snapshot)
             and i.cik is not null order by i.t212_ticker"""
    )
    for row in rows:
        held.by_cik.setdefault(str(row["cik"]).zfill(10), []).append(row["ticker"])
    allowed = {t for tickers in held.by_cik.values() for t in tickers}
    for cusip, ticker in (await portfolio_cusips(pool)).items():
        if ticker in allowed:
            held.by_cusip[cusip] = ticker
            held.ticker_cusips.setdefault(ticker, set()).add(cusip)
    return held


def _snapshot(row: asyncpg.Record, persons: list[asyncpg.Record]) -> Snapshot:
    return Snapshot(
        accession=row["accession"],
        filed=row["filed"],
        schedule=row["schedule"],
        intent_text=row["intent_text"],
        persons=[
            ReportingPerson(
                cik=p["person_cik"],
                name=p["person_name"],
                member_of_group=p["member_of_group"],
                citizenship=p["citizenship"],
                sole_voting=p["sole_voting"],
                shared_voting=p["shared_voting"],
                sole_dispositive=p["sole_dispositive"],
                shared_dispositive=p["shared_dispositive"],
                aggregate_shares=p["aggregate_shares"],
                percent_of_class=p["percent_of_class"],
                person_type=p["person_type"],
            )
            for p in persons
        ],
    )


async def relink_chain(
    conn: asyncpg.Connection, issuer_cusip: str | None, issuer_cik: str | None
) -> int:
    """Recompute predecessor links and differences for every filing of one issuer class.

    Rewritten whole each time, so filings that arrive out of order (an old amendment loaded
    after a newer one) still produce the right chain. Returns the number of changes stored.
    """
    filings = await conn.fetch(
        """select * from public.control_stake_filing
           where (issuer_cusip = $1 and $1 is not null)
              or (issuer_cusip is null and $1 is null and issuer_cik = $2)""",
        issuer_cusip,
        issuer_cik,
    )
    if not filings:
        return 0
    holders: dict[str, list[asyncpg.Record]] = {}
    for p in await conn.fetch(
        "select * from public.control_stake_holder where accession = any($1::text[])"
        " order by accession, row_index",
        [f["accession"] for f in filings],
    ):
        holders.setdefault(p["accession"], []).append(p)
    snapshots = {f["accession"]: _snapshot(f, holders.get(f["accession"], [])) for f in filings}
    priors = chain_priors(list(snapshots.values()))
    accessions = list(snapshots)
    await conn.execute(
        "delete from public.control_stake_change where accession = any($1::text[])", accessions
    )
    stored = 0
    for accession, prior in priors.items():
        await conn.execute(
            "update public.control_stake_filing set prior_accession = $2 where accession = $1",
            accession,
            prior,
        )
        if prior is None:
            continue
        rows = [
            (
                accession,
                prior,
                c.change_type,
                c.person_key,
                c.person_name,
                c.old_value,
                c.new_value,
                c.detail,
            )
            for c in compute_changes(snapshots[prior], snapshots[accession])
        ]
        await conn.executemany(
            """insert into public.control_stake_change
               (accession, prior_accession, change_type, person_key, person_name, old_value,
                new_value, detail) values ($1,$2,$3,$4,$5,$6,$7,$8)""",
            rows,
        )
        stored += len(rows)
        # Version chain of the source documents: the amendment amends its predecessor.
        await conn.execute(
            """update public.source_document_version v set amends_version_id = p.id
               from public.source_document d, public.source_document pd,
                    public.source_document_version p
               where d.regulator = 'sec' and d.external_id = $1 and v.document_id = d.id
                 and pd.regulator = 'sec' and pd.external_id = $2 and p.document_id = pd.id
                 and v.amends_version_id is null
                 and p.id = (select max(id) from public.source_document_version
                             where document_id = pd.id)""",
            accession,
            prior,
        )
    return stored


async def store_filing(
    pool: asyncpg.Pool,
    filing: Filing,
    parsed: Schedule13Filing,
    source_url: str,
    content: bytes,
    held: HeldIssuers,
) -> list[str]:
    """Store one parsed filing with provenance, holders and issuer-class reconciliation.

    Returns the tickers it applies to (empty when the issuer is not a held issuer; such a
    filing is not stored)."""
    matches = held.tickers_for(parsed.issuer_cik, parsed.issuer_cusip)
    if not matches:
        return []
    persons = parsed.persons
    shares, percent = stake_of(persons)
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """insert into public.control_stake_filing
               (accession, form, schedule, is_amendment, amendment_no, filed, event_date,
                issuer_cik, issuer_cusip, issuer_name, class_title, filer_cik, intent_text,
                intent_hash, stake_shares, stake_percent, source_url)
               values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
               on conflict (accession) do update set
                 form=excluded.form, schedule=excluded.schedule,
                 is_amendment=excluded.is_amendment, amendment_no=excluded.amendment_no,
                 filed=excluded.filed, event_date=excluded.event_date,
                 issuer_cik=excluded.issuer_cik, issuer_cusip=excluded.issuer_cusip,
                 issuer_name=excluded.issuer_name, class_title=excluded.class_title,
                 filer_cik=excluded.filer_cik, intent_text=excluded.intent_text,
                 intent_hash=excluded.intent_hash, stake_shares=excluded.stake_shares,
                 stake_percent=excluded.stake_percent, source_url=excluded.source_url,
                 loaded_at=now()""",
            filing.accession,
            filing.form,
            parsed.schedule,
            parsed.is_amendment,
            parsed.amendment_no,
            filing.filing_date,
            parsed.event_date,
            parsed.issuer_cik,
            parsed.issuer_cusip,
            parsed.issuer_name,
            parsed.class_title,
            parsed.filer_cik,
            parsed.intent_text,
            intent_fingerprint(parsed.intent_text),
            shares,
            percent,
            source_url,
        )
        await link_public_document(
            conn,
            regulator="sec",
            external_id=filing.accession,
            stable_url=source_url,
            document_type=filing.form,
            content=content,
            known_at=dt.datetime.now(dt.UTC),
            filed_at=filing.accepted_at or filing.filing_date,
            accepted_at=filing.accepted_at,
            published_at=filing.filing_date,
            domain_table="control_stake_filing",
            record_key=filing.accession,
            quote=parsed.issuer_name,
        )
        await conn.execute(
            "delete from public.control_stake_holder where accession = $1", filing.accession
        )
        await conn.executemany(
            """insert into public.control_stake_holder
               (accession,row_index,person_key,person_cik,person_name,member_of_group,
                citizenship,sole_voting,shared_voting,sole_dispositive,shared_dispositive,
                aggregate_shares,percent_of_class,person_type)
               values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)""",
            [
                (
                    filing.accession,
                    i,
                    p.key,
                    p.cik,
                    p.name,
                    p.member_of_group,
                    p.citizenship,
                    p.sole_voting,
                    p.shared_voting,
                    p.sole_dispositive,
                    p.shared_dispositive,
                    p.aggregate_shares,
                    p.percent_of_class,
                    p.person_type,
                )
                for i, p in enumerate(persons)
            ],
        )
        await conn.execute(
            "delete from public.control_stake_issuer_link where accession = $1", filing.accession
        )
        for ticker, cusip_matched in matches:
            shares_row = await conn.fetchrow(
                "select shares, as_of, classes, ads_ratio_reported from public.shares_outstanding"
                " where ticker = $1 order by fetched_at desc, id desc limit 1",
                ticker,
            )
            recon = reconcile_class(
                cusip_matched=cusip_matched,
                class_title=parsed.class_title,
                stake_shares=shares,
                stake_percent=percent,
                shares_row=_shares_dict(shares_row),
            )
            await conn.execute(
                """insert into public.control_stake_issuer_link
                   (accession,ticker,match_basis,class_status,class_label,
                    class_shares_outstanding,class_shares_as_of,computed_percent,percent_check,
                    note) values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
                filing.accession,
                ticker,
                "cusip" if cusip_matched else "issuer_cik",
                recon.class_status,
                recon.class_label,
                recon.class_shares_outstanding,
                recon.class_shares_as_of,
                recon.computed_percent,
                recon.percent_check,
                recon.note,
            )
        await relink_chain(conn, parsed.issuer_cusip, parsed.issuer_cik)
    return [t for t, _ in matches]


def _shares_dict(row: asyncpg.Record | None) -> dict[str, Any] | None:
    if row is None:
        return None
    import json

    classes = row["classes"]
    if isinstance(classes, str):
        classes = json.loads(classes)
    return {
        "shares": row["shares"],
        "as_of": row["as_of"],
        "classes": classes or [],
        "ads_ratio_reported": row["ads_ratio_reported"],
    }


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


async def _load_xml(edgar: EdgarClient, filing: Filing) -> tuple[bytes, str] | None:
    """The filing's structured XML, or None for a legacy text/HTML filing."""
    try:
        names = await edgar.filing_files(filing)
    except (EdgarError, httpx.HTTPError):
        names = []
    candidates = [filing.primary_document, *names]
    seen: set[str] = set()
    for name in candidates:
        if name in seen or not name.lower().endswith(".xml"):
            continue
        seen.add(name)
        url = filing.url(name)
        try:
            body = await edgar.document(url, max_bytes=8 * 1024 * 1024)
        except (EdgarError, httpx.HTTPError):
            continue
        lowered = body.lower()
        if b"schedule13" in lowered or b"13d" in lowered[:4000] or b"13g" in lowered[:4000]:
            return body, url
    return None


async def collect_control_stakes(
    ctx: JobContext,
    edgar: EdgarClient,
    held: HeldIssuers,
    tracked_ciks: Sequence[str],
    *,
    since: dt.date | None = None,
) -> dict[str, int]:
    """Fetch and store new 13D/G filings of held issuers and of tracked managers."""
    counts = {
        "issuers": len(held.by_cik),
        "tracked_filers": len(tracked_ciks),
        "candidates": 0,
        "stored": 0,
        "unstructured_skipped": 0,
        "not_held_skipped": 0,
        "parse_failed": 0,
    }
    start = since or dt.date.today() - dt.timedelta(days=365 * 2)
    seen: dict[str, Filing] = {}
    async with ctx.step("discover-filings") as step:
        for cik in [*held.by_cik, *tracked_ciks]:
            try:
                filings = await edgar.filings(cik, since=start)
            except (EdgarError, httpx.HTTPError) as exc:
                step.detail.setdefault("errors", []).append(f"{cik}: {exc}")
                continue
            for f in filings:
                if f.form in SCHEDULE13_FORMS and f.filing_date >= start:
                    seen.setdefault(f.accession, f)
        counts["candidates"] = len(seen)
        step.detail["candidates"] = len(seen)
    async with ctx.step("store-filings") as step:
        for accession, filing in sorted(seen.items(), key=lambda kv: (kv[1].filing_date, kv[0])):
            if await ctx.pool.fetchval(
                "select exists(select 1 from public.control_stake_filing where accession = $1)",
                accession,
            ):
                continue
            loaded = await _load_xml(edgar, filing)
            if loaded is None:
                counts["unstructured_skipped"] += 1
                continue
            body, url = loaded
            try:
                parsed = parse_schedule13_xml(body, filing.form)
            except Schedule13Error:
                counts["parse_failed"] += 1
                continue
            tickers = await store_filing(ctx.pool, filing, parsed, url, body, held)
            if tickers:
                counts["stored"] += 1
            else:
                counts["not_held_skipped"] += 1
        step.detail.update(counts)
    return counts


async def control_stakes_handler(ctx: JobContext) -> None:
    """`control_stakes` job: Schedule 13D/G filings for held issuers and tracked managers."""
    stored = await load_active(ctx.pool, "jobs", JobsConfig)
    await ctx.record_config_version(stored.id if stored else None)
    settings = (stored.body if stored else JobsConfig()).ownership
    agent = get_settings().sec_user_agent
    if not agent:
        raise JobFailed("SEC_USER_AGENT is not set: the SEC requires a contact User-Agent")
    held = await load_held(ctx.pool)
    tracked = [m.cik.zfill(10) for m in settings.tracked_managers]
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, read=40.0)) as http:
        edgar = EdgarClient(http, f"{agent} StewardAI/{__version__}")
        await collect_control_stakes(ctx, edgar, held, tracked)
