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
import json
import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from urllib.parse import urlparse

import asyncpg
import httpx

from steward import __version__
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
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


@dataclass(frozen=True)
class BenchmarkSeries:
    """The issuer's benchmark, as the 'growth of 10,000' series on its performance chart."""

    symbol: str
    name: str
    currency: str
    basis: Literal["price", "total"] | None  # only when the benchmark's name states it
    points: tuple[tuple[dt.date, Decimal], ...]


@dataclass
class IssuerReading:
    isin: str
    source_url: str
    as_of: dt.date
    facts: dict[str, Candidate] = field(default_factory=dict)
    nav: NavReading | None = None
    benchmark: BenchmarkSeries | None = None
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
    if not isinstance(containers, dict):
        raise IssuerReadError(f"iShares {component}: component missing from the response")
    out: dict[str, dict[str, Any]] = {}
    for cname, c in containers.items():
        named = c.get("dataPointsByNameMap") if isinstance(c, dict) else None
        if not isinstance(named, dict):
            continue  # a node that is not an object carries no data points
        for name, dp in named.items():
            if isinstance(dp, dict):
                out[f"{cname}.{name}"] = dp
    return out


def _value(points: dict[str, dict[str, Any]], key: str, field: str) -> Any:
    """`points[key][field]`, or None when the point is absent."""
    return points.get(key, {}).get(field)


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
        _value(head, "fundNav.navAmount", "formattedAsOfDate"),
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


LENDING_STALE_DAYS = 400  # a lending table older than this no longer says what the fund does now
_REVENUE_SHARE = re.compile(
    r"(\d{1,3}(?:\.\d+)?)\s*%\s+of\s+all\s+securities\s+lending\s+income\s+is\s+paid\s+directly"
    r"\s+into\s+the\s+fund",
    flags=re.IGNORECASE,
)


def parse_ishares_lending(sec_lending: Any, today: dt.date) -> IssuerReading:
    """Securities-lending facts from the `secLending` component, in a scratch reading.

    `securities_lending` is recorded as yes only when the latest one-year table is recent and
    shows shares on loan; zero or an old table is unknown, never `no`. The fund's share of the
    income is the issuer's own sentence."""
    out = IssuerReading(isin="", source_url="", as_of=today)
    points = _points(sec_lending, "secLending")
    as_of_text = _shown(points, "download.secLendingAsOfDate")
    as_of = parse_issuer_date(as_of_text)
    if as_of is None or (today - as_of).days > LENDING_STALE_DAYS or as_of > today:
        out.skipped["securities_lending"] = (
            f"the issuer's lending statistics are dated {as_of_text!r}: not current"
        )
    else:
        row: Any = _value(points, "summary.oneYear", "value")
        kinds: Any = _value(points, "summary.returnTypes", "value")
        on_loan: Decimal | None
        try:
            on_loan = Decimal(str(row[list(kinds).index("avgOnLoan")]).replace(",", ""))
        except (TypeError, ValueError, IndexError, InvalidOperation):
            on_loan = None
        if on_loan is None or on_loan <= 0:
            out.skipped["securities_lending"] = (
                "the one-year table shows no average amount on loan: not recorded as yes or no"
            )
        else:
            out.facts["securities_lending"] = Candidate(
                "yes",
                f"Securities lending statistics as of {as_of_text}: average on loan over one year {on_loan}%",
            )
    m = _REVENUE_SHARE.search(json.dumps(sec_lending))
    share = Decimal(m.group(1)) / 100 if m else None
    if share is None or not (0 < share <= 1):
        out.skipped["lending_revenue_share"] = "no stated share of the lending income"
    else:
        out.facts["lending_revenue_share"] = Candidate(
            format(share.normalize(), "f"),
            f"{m.group(0) if m else ''} (fraction of the lending income paid into the fund)",
        )
    return out


_BASIS = (
    ("total", re.compile(r"\b(total|net|gross)\s+return\b", re.IGNORECASE)),
    ("price", re.compile(r"\bprice\s+(return|index)\b", re.IGNORECASE)),
)


def parse_ishares_benchmark(performance: Any, portfolio_id: str) -> BenchmarkSeries | None:
    """The benchmark's 'growth of 10,000' series with its dates, or None when absent."""
    chart = _points(performance, "performance")
    values = _value(chart, "chart.benchmarkData", "value")
    dates = _value(chart, "chart.benchmarkData", "asOfDate")
    name = _shown(chart, "chart.benchmarkName")
    currency = _shown(chart, "chart.benchmarkCurrencySymbol")
    if (
        not isinstance(values, list)
        or not isinstance(dates, list)
        or len(values) != len(dates)
        or len(values) < 2
        or not name
        or not currency
        or not re.fullmatch(r"[A-Z]{3}", currency)
    ):
        return None
    points: list[tuple[dt.date, Decimal]] = []
    for d, v in zip(dates, values, strict=True):
        try:
            day = dt.datetime.strptime(str(d), "%Y%m%d").date()
            level = Decimal(str(v))
        except (ValueError, InvalidOperation):
            return None  # a malformed series is not stored partly
        if level <= 0 or (points and day <= points[-1][0]):
            return None
        points.append((day, level))
    basis: Literal["price", "total"] | None = None
    for kind, pattern in _BASIS:
        if pattern.search(name):
            basis = kind  # type: ignore[assignment]
            break
    return BenchmarkSeries(
        symbol=f"ishares-bm-{portfolio_id}",
        name=name,
        currency=currency,
        basis=basis,
        points=tuple(points),
    )


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


AUTO_PASSAGE_SUFFIX = "(read automatically from the issuer's data)"


@dataclass(frozen=True)
class RecordedFact:
    value: str
    source_url: str
    passage: str | None = None

    @property
    def automatic(self) -> bool:
        """Written by this sync, not entered by the owner: the host of an owner's source URL
        (an ishares.com page) says nothing about who recorded the value."""
        return (self.passage or "").endswith(AUTO_PASSAGE_SUFFIX)


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
        elif latest.automatic and host_family(latest.source_url) == host_family(source_url):
            plan.writes[key] = cand
        else:
            plan.conflicts[key] = (
                f"issuer says {cand.value!r}; you recorded {latest.value!r} from {latest.source_url}"
            )
    return plan


def plan_nav_write(
    reading: NavReading | None,
    existing: list[tuple[Decimal, str, str, str | None]],
    source_url: str,
) -> Literal["write", "unchanged", "conflict", "none"]:
    """`existing` are the recorded (nav, currency, source_url, origin) rows for the reading's
    date, newest last. A first reading for the date is written; the same figure is unchanged; a
    different one is a revision (written) only over a row the sync itself wrote from the issuer's
    own source family. An owner-entered row, or one of unknown origin (null, written before origin
    was recorded), is never superseded, whatever page it cites: a conflict."""
    if reading is None:
        return "none"
    if not existing:
        return "write"
    nav, currency, src, origin = existing[-1]
    if nav == reading.nav and currency == reading.currency:
        return "unchanged"
    if origin == "issuer" and host_family(src) == host_family(source_url):
        return "write"
    return "conflict"


# ------------------------------------------------------------------------ fetch and store


def add_ishares_extras(
    reading: IssuerReading, source: IssuerSource, sec_lending: Any, performance: Any, today: dt.date
) -> None:
    """Fold securities lending and the benchmark series into a reading. Each part that cannot
    be read is left out with its reason; the rest of the reading stands."""
    assert source.portfolio_id
    try:
        if isinstance(sec_lending, Exception):
            raise sec_lending
        lending = parse_ishares_lending(sec_lending, today)
        reading.facts.update(lending.facts)
        reading.skipped.update(lending.skipped)
    except (IssuerError, IssuerReadError, httpx.HTTPError) as exc:
        reading.skipped["securities_lending"] = f"{type(exc).__name__}: {exc}"[:200]
    try:
        if isinstance(performance, Exception):
            raise performance
        series = parse_ishares_benchmark(performance, source.portfolio_id)
    except (IssuerError, IssuerReadError, httpx.HTTPError) as exc:
        reading.skipped["benchmark"] = f"{type(exc).__name__}: {exc}"[:200]
        return
    if series is None:
        reading.skipped["benchmark"] = "no usable benchmark series on the issuer's chart"
        return
    reading.benchmark = series
    first, last = series.points[0][0], series.points[-1][0]
    passage = f"Benchmark {series.name}: {len(series.points)} daily points {first} to {last}"
    reading.facts["benchmark_symbol"] = Candidate(series.symbol, passage)
    reading.facts["benchmark_currency"] = Candidate(series.currency, passage)
    if series.basis is not None:
        reading.facts["benchmark_basis"] = Candidate(series.basis, f"Benchmark name: {series.name}")
    else:
        reading.skipped["benchmark_basis"] = (
            f"the benchmark's name {series.name!r} does not say whether it is a price or total return index"
        )


async def fetch_reading(
    http: httpx.AsyncClient, source: IssuerSource, today: dt.date
) -> IssuerReading:
    if source.provider == "ishares":
        assert source.portfolio_id
        key_facts = await fetch_ishares_component(http, source.portfolio_id, "keyFundFacts")
        header = await fetch_ishares_component(http, source.portfolio_id, "fundHeader")
        reading = parse_ishares(key_facts, header, source, today)
        extras: list[Any] = []
        for component in ("secLending", "performance"):
            try:
                extras.append(await fetch_ishares_component(http, source.portfolio_id, component))
            except (IssuerError, httpx.HTTPError) as exc:
                extras.append(exc)
        add_ishares_extras(reading, source, extras[0], extras[1], today)
        return reading
    response = await http.get(source.url)
    if response.status_code != 200:
        raise IssuerReadError(f"WisdomTree factsheet answered HTTP {response.status_code}")
    return parse_wisdomtree(pdf_text(response.content), source, today)


async def extra_sources(pool: asyncpg.Pool, known: set[str]) -> list[IssuerSource]:
    """iShares funds the app already holds look-through snapshots for."""
    rows = await pool.fetch(
        "select distinct fund_isin, portfolio_id from public.etf_snapshot where provider='ishares'"
    )
    found = [
        IssuerSource(r["fund_isin"], "ishares", r["portfolio_id"])
        for r in rows
        if r["fund_isin"] not in known
        and ISIN.match(r["fund_isin"] or "")
        and (r["portfolio_id"] or "").isdigit()
    ]
    return [*found, *await held_wisdomtree_sources(pool, known | {s.isin for s in found})]


async def held_wisdomtree_sources(pool: asyncpg.Pool, known: set[str]) -> list[IssuerSource]:
    """WisdomTree products the account has held, found by the instrument's name; the
    factsheet URL needs only the ISIN. The reader checks the ISIN inside the document."""
    rows = await pool.fetch(
        """select distinct i.isin from public.instrument i
           join public.position_snapshot p on p.ticker = i.t212_ticker
           where i.name ilike 'wisdomtree%' and i.isin is not null"""
    )
    return [
        IssuerSource(r["isin"], "wisdomtree")
        for r in rows
        if r["isin"] not in known and ISIN.match(r["isin"])
    ]


async def store_reading(
    pool: asyncpg.Pool, owner_id: uuid.UUID, reading: IssuerReading
) -> dict[str, Any]:
    recorded = {
        r["key"]: RecordedFact(r["value"], r["source_url"], r["passage"])
        for r in await pool.fetch(
            """select distinct on (key) key, value, source_url, passage from public.fund_owner_fact
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
                f"{cand.passage[: 1000 - len(AUTO_PASSAGE_SUFFIX) - 1]} {AUTO_PASSAGE_SUFFIX}",
                reading.as_of,
            )
        if reading.nav is not None:
            existing = [
                (r["nav"], r["currency"], r["source_url"], r["origin"])
                for r in await conn.fetch(
                    """select nav, currency, source_url, origin from public.fund_nav_point
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
                       (owner_id, fund_isin, as_of, nav, currency, source_url, origin)
                       values ($1,$2,$3,$4,$5,$6,'issuer')""",
                    owner_id,
                    reading.isin,
                    reading.nav.as_of,
                    reading.nav.nav,
                    reading.nav.currency,
                    reading.source_url,
                )
        bench = await store_benchmark(conn, reading) if reading.benchmark else None
    return {
        "written": sorted(plan.writes),
        "unchanged": len(plan.unchanged),
        "conflicts": plan.conflicts,
        "skipped": reading.skipped,
        "nav": nav_state,
        "benchmark_points": bench,
    }


async def store_benchmark(conn: asyncpg.Connection, reading: IssuerReading) -> int:
    """Upsert the benchmark series (only changed or new days are written) and its coverage row.
    The row says the series is the issuer's, rebased, and whether it is a total-return index
    only when the issuer's own name for it says so."""
    series = reading.benchmark
    assert series is not None
    days = [d for d, _ in series.points]
    levels = [v for _, v in series.points]
    result = await conn.execute(
        """insert into public.price_bar (symbol, date, close, source, bar_type, currency, quote_unit)
           select $1, d, c, 'issuer', 'adjusted', $4, $4 from unnest($2::date[], $3::numeric[]) as t(d, c)
           on conflict (symbol, date, source, bar_type) do update set close = excluded.close, fetched_at = now()
           where public.price_bar.close is distinct from excluded.close""",
        series.symbol,
        days,
        levels,
        series.currency,
    )
    reason = (
        f"The issuer's published benchmark series ({series.name}), rebased to 10,000 on "
        f"{days[0]}; an index level, not a quote."
    )
    await conn.execute(
        """insert into public.fund_index_coverage (subject_kind, subject_key, label, tier, origin,
             total_return_available, reason, source_url, evidence, checked_at)
           values ('index', $1, $2, 'issuer_downloadable', 'derived', $3, $4, $5, $6::jsonb, now())
           on conflict (subject_kind, subject_key) do update set label = excluded.label,
             total_return_available = excluded.total_return_available, reason = excluded.reason,
             source_url = excluded.source_url, evidence = excluded.evidence, checked_at = now()
           where public.fund_index_coverage.origin = 'derived'
             and (public.fund_index_coverage.label, public.fund_index_coverage.total_return_available,
                  public.fund_index_coverage.reason, public.fund_index_coverage.source_url,
                  public.fund_index_coverage.evidence)
                 is distinct from (excluded.label, excluded.total_return_available, excluded.reason,
                  excluded.source_url, excluded.evidence)""",
        series.symbol,
        series.name,
        None if series.basis is None else series.basis == "total",
        reason,
        reading.source_url,
        json.dumps({"first": str(days[0]), "last": str(days[-1]), "points": len(days)}),
    )
    return int(result.rsplit(" ", 1)[-1]) if result.startswith("INSERT") else 0


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
    stored = await load_active(ctx.pool, "jobs", JobsConfig)
    jobs = stored.body if stored else JobsConfig()
    if jobs.fund_documents:
        from steward.fundresearch import kid_facts

        async with ctx.step("fund_documents") as step:
            raw_owner = get_settings().owner_user_id
            if raw_owner:
                step.detail.update(
                    await kid_facts.read_documents(
                        ctx.pool,
                        kid_facts.make_client(ctx.pool),
                        jobs.autopilot_settings,
                        jobs.fund_documents,
                        uuid.UUID(raw_owner),
                    )
                )
