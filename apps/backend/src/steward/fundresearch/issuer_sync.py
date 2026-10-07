"""Keep the owner's sourced fund facts and NAV in step with what the issuer publishes.

Daily job `fund_issuer_facts`. For each fund it reads the issuer's own structured data (iShares'
product-data API, the WisdomTree factsheet PDF), turns it into the same `fund_owner_fact` keys the
Fund checklist already reads, and adds the published NAV where the issuer states one.

Rules that keep it honest:
- Append-only. A fact is written only when its value differs from the newest recorded one, so
  the history shows real changes and an earlier checklist stays reproducible.
- It never overrides you. When the newest recorded value came from a different source than the
  issuer's, a differing issuer value is reported as a conflict and left alone.
- A field the issuer does not state, or states in a form this reader does not recognise, is
  skipped and named; nothing is guessed or defaulted.
- The NAV is the issuer's published figure with its date and currency, never derived.
"""

# ruff: noqa: E501
from __future__ import annotations

import datetime as dt
import io
import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from urllib.parse import urlparse

import asyncpg
import httpx

from steward import __version__
from steward.jobs.runner import JobContext, JobFailed
from steward.settings import get_settings
from steward.sources.etf_issuers import IssuerError, fetch_ishares_component

Provider = Literal["ishares", "wisdomtree"]

ISHARES_PAGE = "https://www.ishares.com/uk/individual/en/products/{portfolio_id}"
WISDOMTREE_FACTSHEET = (
    "https://dataspanapi.wisdomtree.com/pdr/documents/FACTSHEET/CSL/EU/EN-GB/{isin}/"
)
ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


@dataclass(frozen=True)
class IssuerSource:
    isin: str
    provider: Provider
    portfolio_id: str | None = None  # iShares only

    @property
    def url(self) -> str:
        if self.provider == "ishares":
            assert self.portfolio_id
            return ISHARES_PAGE.format(portfolio_id=self.portfolio_id)
        return WISDOMTREE_FACTSHEET.format(isin=self.isin)


# The funds the owner holds. Any iShares fund with a stored look-through snapshot is added too.
DEFAULT_SOURCES: tuple[IssuerSource, ...] = (
    IssuerSource("IE00B53SZB19", "ishares", "253741"),
    IssuerSource("IE00BDFL4P12", "ishares", "287254"),
    IssuerSource("JE00B78CGV99", "wisdomtree"),
)


@dataclass(frozen=True)
class Candidate:
    value: str
    passage: str


@dataclass(frozen=True)
class NavReading:
    as_of: dt.date
    nav: Decimal
    currency: str


@dataclass
class IssuerReading:
    isin: str
    source_url: str
    as_of: dt.date
    facts: dict[str, Candidate] = field(default_factory=dict)
    nav: NavReading | None = None
    skipped: dict[str, str] = field(default_factory=dict)


class IssuerReadError(Exception):
    """The issuer's document is not the fund asked for or no longer has the expected shape."""


_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}


def parse_issuer_date(text: str | None) -> dt.date | None:
    """`26/Jan/2010`, `30/Sept/2024` or `21/02/2012` (day first)."""
    if not text:
        return None
    m = re.fullmatch(r"\s*(\d{1,2})/([A-Za-z]+|\d{1,2})/(\d{4})\s*", text)
    if not m:
        return None
    day, month, year = m.groups()
    number = int(month) if month.isdigit() else _MONTHS.get(month[:3].lower())
    if number is None:
        return None
    try:
        return dt.date(int(year), number, int(day))
    except ValueError:
        return None


def _yes_no(text: str | None) -> str | None:
    v = (text or "").strip().lower()
    return {"yes": "yes", "no": "no"}.get(v)


def _replication(methodology: str | None, structure: str | None) -> str | None:
    m, s = (methodology or "").strip().lower(), (structure or "").strip().lower()
    if s == "synthetic" or m == "swap" or m.startswith("synthetic"):
        return "synthetic"
    if m == "replicated":
        return "physical_full"
    if m and any(k in m for k in ("sampl", "optimi")):
        return "physical_sampled"
    return None


# ------------------------------------------------------------------------------- iShares


def _points(body: Any, component: str) -> dict[str, dict[str, Any]]:
    try:
        containers = body["componentsByNameMap"][component]["containersByNameMap"]
    except (KeyError, TypeError) as exc:
        raise IssuerReadError(f"iShares {component}: component missing from the response") from exc
    out: dict[str, dict[str, Any]] = {}
    for cname, c in containers.items():
        for name, dp in (c.get("dataPointsByNameMap") or {}).items():
            out[f"{cname}.{name}"] = dp
    return out


def _shown(points: dict[str, dict[str, Any]], key: str) -> str | None:
    dp = points.get(key)
    if not dp:
        return None
    value = dp.get("formattedValue") or dp.get("value")
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    return None if text in ("", "-") else text


def parse_ishares(
    key_facts: Any, header: Any, source: IssuerSource, today: dt.date
) -> IssuerReading:
    facts = _points(key_facts, "keyFundFacts")
    isin = _shown(facts, "default.isin")
    if isin != source.isin:
        raise IssuerReadError(
            f"iShares portfolio {source.portfolio_id} answers ISIN {isin}, expected {source.isin}"
        )
    out = IssuerReading(isin=source.isin, source_url=source.url, as_of=today)

    def put(key: str, value: str | None, passage: str, why: str) -> None:
        if value is None:
            out.skipped[key] = why
        else:
            out.facts[key] = Candidate(value, passage)

    methodology = _shown(facts, "default.fundMethodologyTypeCode")
    structure = _shown(facts, "default.productStructure")
    put(
        "replication_method",
        _replication(methodology, structure),
        f"Methodology: {methodology}; Product Structure: {structure}",
        f"methodology {methodology!r} / structure {structure!r} not recognised",
    )
    inception_text = _shown(facts, "default.inceptionDate") or _shown(facts, "default.launchDate")
    inception = parse_issuer_date(inception_text)
    put(
        "inception_date",
        inception.isoformat() if inception else None,
        f"Share class launch / inception date: {inception_text}",
        "no readable launch date",
    )
    domicile = _shown(facts, "default.domicile")
    put("domicile", domicile, f"Domicile: {domicile}", "no domicile stated")
    ucits = _yes_no(_shown(facts, "default.ucitsCompliantFlag"))
    put("ucits", ucits, f"UCITS: {ucits}", "no UCITS flag stated")
    index = _shown(facts, "default.indexSeriesName")
    put("tracking_index", index, f"Benchmark Index: {index}", "no benchmark index stated")

    head = _points(header, "fundHeader")
    amount_text, as_of_text = (
        _shown(head, "fundNav.navAmount"),
        (head.get("fundNav.navAmount") or {}).get("formattedAsOfDate"),
    )
    currency = _shown(facts, "default.seriesBaseCurrencyCode")
    as_of = parse_issuer_date(as_of_text)
    try:
        amount = Decimal((amount_text or "").replace(",", ""))
    except InvalidOperation:
        amount = None
    if (
        amount is not None
        and amount > 0
        and as_of is not None
        and as_of <= today
        and currency
        and re.fullmatch(r"[A-Z]{3}", currency)
    ):
        out.nav = NavReading(as_of=as_of, nav=amount, currency=currency)
    else:
        out.skipped["nav"] = (
            f"no usable published NAV (value {amount_text!r}, date {as_of_text!r}, currency {currency!r})"
        )
    return out


# --------------------------------------------------------------------------- WisdomTree


def pdf_text(content: bytes) -> str:
    from pypdf import PdfReader

    try:
        return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(content)).pages)
    except Exception as exc:  # pypdf raises many types on a malformed document
        raise IssuerReadError(f"the factsheet is not a readable PDF: {exc}") from exc


def _after(text: str, label: str) -> str | None:
    """The rest of the line after a table label, e.g. `Domicile Jersey` -> `Jersey`."""
    m = re.search(rf"^{re.escape(label)}[ \t]+(.+)$", text, flags=re.MULTILINE)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else None


def parse_wisdomtree(text: str, source: IssuerSource, today: dt.date) -> IssuerReading:
    isin = _after(text, "ISIN")
    if isin is None or isin.split()[0] != source.isin:
        raise IssuerReadError(f"the factsheet is for ISIN {isin}, expected {source.isin}")
    doc = re.search(r"Document Date:\s*(\d{2}/\d{2}/\d{4})", text)
    out = IssuerReading(
        isin=source.isin,
        source_url=source.url,
        as_of=(parse_issuer_date(doc.group(1)) if doc else None) or today,
    )

    def put(key: str, value: str | None, passage: str, why: str) -> None:
        if value is None:
            out.skipped[key] = why
        else:
            out.facts[key] = Candidate(value, passage)

    replication_line = re.search(
        r"^Replication Method[ \t]+(.+?)\n(?:UCITS Eligible)", text, flags=re.MULTILINE | re.DOTALL
    )
    replication_text = (
        re.sub(r"\s+", " ", replication_line.group(1)).strip() if replication_line else None
    )
    put(
        "replication_method",
        "synthetic"
        if replication_text and replication_text.lower().startswith("synthetic")
        else None,
        f"Replication Method: {replication_text}",
        f"replication {replication_text!r} not recognised",
    )
    inception_text = _after(text, "Inception Date")
    inception = parse_issuer_date(inception_text.split()[0]) if inception_text else None
    put(
        "inception_date",
        inception.isoformat() if inception else None,
        f"Inception Date: {inception_text}",
        "no readable inception date",
    )
    domicile = _after(text, "Domicile")
    put("domicile", domicile, f"Domicile: {domicile}", "no domicile stated")
    eligible, compliant = (
        _yes_no(_after(text, "UCITS Eligible")),
        _yes_no(_after(text, "UCITS Compliant")),
    )
    put(
        "ucits",
        compliant,
        f"UCITS Eligible: {eligible}. UCITS Compliant: {compliant}",
        "no UCITS compliance stated",
    )
    index = re.search(
        r"Index Information\s*\nIndex[ \t]*\n?(.+?)\nIndex Provider", text, flags=re.DOTALL
    )
    index_text = re.sub(r"\s+", " ", index.group(1)).strip() if index else None
    put("tracking_index", index_text, f"Index: {index_text}", "no index stated")
    # An exchange-traded commodity has no NAV per unit in this factsheet; none is recorded.
    out.skipped["nav"] = "the issuer's factsheet states no NAV per unit"
    return out


# -------------------------------------------------------------------------- write planning

_ISSUER_FAMILIES = {
    "ishares.com": "ishares",
    "blackrock.com": "ishares",
    "wisdomtree.com": "wisdomtree",
    "wisdomtree.eu": "wisdomtree",
}


def host_family(url: str) -> str:
    """The issuer a source belongs to (iShares and BlackRock are one), else the bare host."""
    host = (urlparse(url).hostname or "").lower()
    for known, family in _ISSUER_FAMILIES.items():
        if host == known or host.endswith("." + known):
            return family
    return host


@dataclass(frozen=True)
class RecordedFact:
    value: str
    source_url: str


@dataclass
class FactPlan:
    writes: dict[str, Candidate] = field(default_factory=dict)
    unchanged: list[str] = field(default_factory=list)
    conflicts: dict[str, str] = field(default_factory=dict)


def plan_fact_writes(
    candidates: dict[str, Candidate], recorded: dict[str, RecordedFact], source_url: str
) -> FactPlan:
    """What to append: new keys and changed values from the issuer's own source family. A
    differing value under a source from elsewhere (the owner's own) is a conflict, not a write."""
    plan = FactPlan()
    for key, cand in candidates.items():
        latest = recorded.get(key)
        if latest is None:
            plan.writes[key] = cand
        elif latest.value.strip().lower() == cand.value.strip().lower():
            plan.unchanged.append(key)
        elif host_family(latest.source_url) == host_family(source_url):
            plan.writes[key] = cand
        else:
            plan.conflicts[key] = (
                f"issuer says {cand.value!r}; you recorded {latest.value!r} from {latest.source_url}"
            )
    return plan


def plan_nav_write(
    reading: NavReading | None,
    existing: list[tuple[Decimal, str, str]],
    source_url: str,
) -> Literal["write", "unchanged", "conflict", "none"]:
    """`existing` are the recorded (nav, currency, source_url) rows for the reading's date, newest
    last. A first reading for the date is written; the same figure is unchanged; a different one
    from the issuer's own source family is a revision (written); from elsewhere, a conflict."""
    if reading is None:
        return "none"
    if not existing:
        return "write"
    nav, currency, src = existing[-1]
    if nav == reading.nav and currency == reading.currency:
        return "unchanged"
    return "write" if host_family(src) == host_family(source_url) else "conflict"


# ------------------------------------------------------------------------ fetch and store


async def fetch_reading(
    http: httpx.AsyncClient, source: IssuerSource, today: dt.date
) -> IssuerReading:
    if source.provider == "ishares":
        assert source.portfolio_id
        key_facts = await fetch_ishares_component(http, source.portfolio_id, "keyFundFacts")
        header = await fetch_ishares_component(http, source.portfolio_id, "fundHeader")
        return parse_ishares(key_facts, header, source, today)
    response = await http.get(source.url)
    if response.status_code != 200:
        raise IssuerReadError(f"WisdomTree factsheet answered HTTP {response.status_code}")
    return parse_wisdomtree(pdf_text(response.content), source, today)


async def extra_sources(pool: asyncpg.Pool, known: set[str]) -> list[IssuerSource]:
    """iShares funds the app already holds look-through snapshots for."""
    rows = await pool.fetch(
        "select distinct fund_isin, portfolio_id from public.etf_snapshot where provider='ishares'"
    )
    return [
        IssuerSource(r["fund_isin"], "ishares", r["portfolio_id"])
        for r in rows
        if r["fund_isin"] not in known
        and ISIN.match(r["fund_isin"] or "")
        and (r["portfolio_id"] or "").isdigit()
    ]


async def store_reading(
    pool: asyncpg.Pool, owner_id: uuid.UUID, reading: IssuerReading
) -> dict[str, Any]:
    recorded = {
        r["key"]: RecordedFact(r["value"], r["source_url"])
        for r in await pool.fetch(
            """select distinct on (key) key, value, source_url from public.fund_owner_fact
               where owner_id=$1 and fund_isin=$2 order by key, id desc""",
            owner_id,
            reading.isin,
        )
    }
    plan = plan_fact_writes(reading.facts, recorded, reading.source_url)
    nav_state = "none"
    async with pool.acquire() as conn, conn.transaction():
        for key, cand in plan.writes.items():
            await conn.execute(
                """insert into public.fund_owner_fact
                   (owner_id, fund_isin, key, value, source_url, passage, as_of)
                   values ($1,$2,$3,$4,$5,$6,$7)""",
                owner_id,
                reading.isin,
                key,
                cand.value[:500],
                reading.source_url,
                f"{cand.passage} (read automatically from the issuer's data)"[:1000],
                reading.as_of,
            )
        if reading.nav is not None:
            existing = [
                (r["nav"], r["currency"], r["source_url"])
                for r in await conn.fetch(
                    """select nav, currency, source_url from public.fund_nav_point
                       where owner_id=$1 and fund_isin=$2 and as_of=$3 order by id""",
                    owner_id,
                    reading.isin,
                    reading.nav.as_of,
                )
            ]
            nav_state = plan_nav_write(reading.nav, existing, reading.source_url)
            if nav_state == "write":
                await conn.execute(
                    """insert into public.fund_nav_point
                       (owner_id, fund_isin, as_of, nav, currency, source_url)
                       values ($1,$2,$3,$4,$5,$6)""",
                    owner_id,
                    reading.isin,
                    reading.nav.as_of,
                    reading.nav.nav,
                    reading.nav.currency,
                    reading.source_url,
                )
    return {
        "written": sorted(plan.writes),
        "unchanged": len(plan.unchanged),
        "conflicts": plan.conflicts,
        "skipped": reading.skipped,
        "nav": nav_state,
    }


async def fund_issuer_facts_handler(ctx: JobContext) -> None:
    async with ctx.step("fund_issuer_facts") as step:
        raw_owner = get_settings().owner_user_id
        if not raw_owner:
            step.detail["skipped"] = "OWNER_USER_ID is not set: the facts belong to the owner"
            return
        owner_id = uuid.UUID(raw_owner)
        today = dt.datetime.now(dt.UTC).date()
        sources = [*DEFAULT_SOURCES]
        sources += await extra_sources(ctx.pool, {s.isin for s in sources})
        results: dict[str, Any] = {}
        failed = 0
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            headers={"User-Agent": f"StewardAI/{__version__}"},
            follow_redirects=True,
        ) as http:
            for source in sources:
                try:
                    reading = await fetch_reading(http, source, today)
                    results[source.isin] = await store_reading(ctx.pool, owner_id, reading)
                except (
                    IssuerError,
                    IssuerReadError,
                    httpx.HTTPError,
                    asyncpg.PostgresError,
                ) as exc:
                    # Keep what is stored; the next run tries again.
                    results[source.isin] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
                    failed += 1
        step.detail["funds"] = results
        step.detail["checked_at"] = dt.datetime.now(dt.UTC).isoformat()
        if failed == len(sources):
            raise JobFailed("no fund could be read from its issuer")
