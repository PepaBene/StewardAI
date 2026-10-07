"""The statement fact store: turning a filing's XBRL facts into `financial_statement_fact` rows.

One module for the SEC instance reader, the ESEF reader and the SEC Financial Statement and
Notes backfill, so a row means the same thing whichever door it came in through:

- `statement`/`line_item` from the shared concept catalog (`steward.sources.xbrl_concepts`);
- `fiscal_period` from the filing's own period focus (SEC) or from the fact's duration
  (ESEF, backfill) - never `str(end.year)` for every fact;
- `currency` of monetary units, `issuer_id`, `cik` (text, leading zeros kept),
  `evidence_id` (a cited filing document version) and a `known_at` that is the filing's
  acceptance time and is set once;
- restatement lineage: a row of an amending filing points at the original row it restates
  (`amends_fact_id`, `amends_accession`, `revision`);
- reconciliation (A13): internal accounting identities and, for SEC, the independent
  companyfacts value of the same fact. A row is `published` only when it was checked and
  nothing failed; a failed check is recorded in `validation` and keeps the row out of every
  peer and ratio.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import asyncpg

from steward.sources.documents import link_public_document
from steward.sources.esef import EsefFact
from steward.sources.xbrl import Fact, Instance
from steward.sources.xbrl_concepts import (
    DOCUMENT_ITEMS,
    classify,
    currency_of,
    is_wanted_text_block,
)

TEXT_BLOCK_CAP = 20_000
# Cover-page facts that are not numbers: stored in value_text so a CIK keeps its zeros.
TEXT_VALUED = {c for c in DOCUMENT_ITEMS if c != "dei:EntityCommonStockSharesOutstanding"}
ANNUAL_BASES = ("US_GAAP", "IFRS")


def basis_of(concept: str) -> str:
    if concept.startswith("us-gaap:"):
        return "US_GAAP"
    if concept.startswith("ifrs-full:"):
        return "IFRS"
    if concept.startswith("dei:"):
        return "cover_page"
    return "issuer_extension"


@dataclass(frozen=True)
class StatementFact:
    """One row of `financial_statement_fact`, before it is stored."""

    ticker: str
    accession: str
    form: str
    filing_url: str
    concept: str
    statement: str
    line_item: str | None
    period_start: dt.date | None
    period_end: dt.date
    fiscal_period: str | None
    basis: str
    value: Decimal | None
    value_text: str | None
    unit: str
    currency: str | None
    dimensions: dict[str, Any]
    source: str
    source_url: str
    published_at: dt.datetime | None
    accepted_at: dt.datetime | None
    known_at: dt.datetime
    text_block: str | None = None
    label: str | None = None
    cik: str | None = None
    issuer_id: int | None = None
    evidence_id: int | None = None
    amends_accession: str | None = None
    is_amendment: bool = False
    decimals: str | None = None
    validation: dict[str, Any] = field(default_factory=dict)
    published: bool = False

    @property
    def is_dimensional(self) -> bool:
        return bool(self.dimensions.get("members")) or bool(self.dimensions.get("typed"))

    @property
    def identity(self) -> tuple[Any, ...]:
        """What makes two rows the same fact in different filings (for restatement lineage)."""
        return (
            self.concept,
            self.period_start,
            self.period_end,
            json.dumps(self.dimensions, sort_keys=True),
            self.unit,
        )


# ------------------------------------------------------------------------------ fiscal period


def _quarter_label(fy: int, quarter: int) -> str:
    return f"FY{fy}Q{quarter}"


def grid_fiscal_period(
    start: dt.date | None, end: dt.date, year_end_month: int | None
) -> tuple[str | None, bool]:
    """(label, assumed) for a fact from its own duration alone: "FY2025", "FY2026Q2",
    "FY2026H1", "FY2026M9" for a nine-month year to date. The fiscal year is named for the
    calendar year it ends in; `year_end_month` anchors the quarters (None: a December year
    end is assumed and `assumed` is True)."""
    assumed = year_end_month is None
    ye = year_end_month or 12
    offset = (end.month - ye) % 12
    qpos = offset // 3 or 4
    fy = end.year + 1 if end.month > ye else end.year
    if start is None:
        return (f"FY{fy}" if qpos == 4 else _quarter_label(fy, qpos)), assumed
    months = round(((end - start).days + 1) / 30.44)
    if 11 <= months <= 13:
        return f"FY{fy}", assumed
    if months in (2, 3, 4):
        return _quarter_label(fy, qpos), assumed
    if months in (5, 6, 7):
        return f"FY{fy}H{1 if qpos <= 2 else 2}", assumed
    if months in (8, 9, 10):
        return f"FY{fy}M9", assumed
    return None, assumed


def sec_fiscal_period(
    start: dt.date | None,
    end: dt.date,
    doc_end: dt.date | None,
    focus_year: int | None,
    focus_period: str | None,
) -> str | None:
    """The fiscal period of a fact in an SEC filing, relative to the filing's dei period focus.

    A 10-Q for Q2 FY2026 holds the quarter and year to date it reports, the comparatives of
    the year before and the prior year-end balance sheet; each is placed on the quarter grid
    by its distance (in quarters) from the document period end."""
    if doc_end is None or focus_year is None or not focus_period:
        return None
    period = focus_period.strip().upper()
    if period in ("FY", "Q4"):
        qn = 4
    elif period in ("H1",):
        qn = 2
    elif period in ("H2",):
        qn = 4
    elif re.fullmatch(r"Q[1-3]", period):
        qn = int(period[1])
    else:
        return None
    days_back = (doc_end - end).days
    quarters_back = round(days_back / 91.31)
    if abs(days_back - quarters_back * 91.31) > 30:
        return None
    position = focus_year * 4 + qn - quarters_back
    fy, qpos = (position - 1) // 4, (position - 1) % 4 + 1
    if start is None:
        return f"FY{fy}" if qpos == 4 else _quarter_label(fy, qpos)
    months = round(((end - start).days + 1) / 30.44)
    if 11 <= months <= 13:
        return f"FY{fy}"
    if months in (2, 3, 4):
        return _quarter_label(fy, qpos)
    if months in (5, 6, 7):
        return f"FY{fy}H{1 if qpos <= 2 else 2}"
    if months in (8, 9, 10):
        return f"FY{fy}M9"
    return None


def parse_document_date(text: str | None) -> dt.date | None:
    if not text:
        return None
    text = text.strip()
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%Y%m%d"):
        try:
            return dt.datetime.strptime(text[:20] if fmt != "%Y-%m-%d" else text[:10], fmt).date()
        except ValueError:
            continue
    return None


def year_end_month_of(text: str | None) -> int | None:
    """Month of `dei:CurrentFiscalYearEndDate` ("--12-31")."""
    match = re.fullmatch(r"--(\d{2})-\d{2}", (text or "").strip())
    if match and 1 <= int(match.group(1)) <= 12:
        return int(match.group(1))
    return None


# ------------------------------------------------------------------------------- known_at


def sec_known_at(accepted_at: dt.datetime | None, filing_date: dt.date) -> dt.datetime:
    """When a SEC filing became public: its acceptance time. Without one the end of the filing
    day (UTC) is used: never midnight, which would put the filing before it existed."""
    if accepted_at is not None:
        return accepted_at
    return dt.datetime.combine(filing_date, dt.time(23, 59, 59), dt.UTC)


# --------------------------------------------------------------------------- building rows


def _best_by_identity(facts: Iterable[Fact]) -> list[Fact]:
    """Duplicate facts (same concept, context and unit) keep the most precise one."""
    best: dict[tuple[Any, ...], Fact] = {}
    for fact in facts:
        key = (fact.concept, fact.context.id, fact.unit or "")
        held = best.get(key)
        if held is None or fact.precision > held.precision:
            best[key] = fact
    return list(best.values())


@dataclass(frozen=True)
class FilingMeta:
    ticker: str
    accession: str
    form: str
    filing_date: dt.date
    filing_url: str
    source_url: str
    accepted_at: dt.datetime | None
    cik: str | None
    issuer_id: int | None = None
    evidence_id: int | None = None
    amends_accession: str | None = None


def sec_statement_facts(instance: Instance, meta: FilingMeta) -> list[StatementFact]:
    """Rows for every numeric fact of the statement catalog, every cover-page fact and every
    kept text block of a SEC instance."""
    doc_end = parse_document_date(instance.text("dei:DocumentPeriodEndDate"))
    focus_year_text = instance.text("dei:DocumentFiscalYearFocus")
    focus_year = int(focus_year_text) if focus_year_text and focus_year_text.isdigit() else None
    focus_period = instance.text("dei:DocumentFiscalPeriodFocus")
    form_upper = meta.form.upper()
    is_amendment = form_upper.endswith("/A") or (
        (instance.text("dei:AmendmentFlag") or "").lower() == "true"
    )
    known_at = sec_known_at(meta.accepted_at, meta.filing_date)
    published_at = dt.datetime.combine(meta.filing_date, dt.time.min, dt.UTC)
    rows: list[StatementFact] = []
    seen_text: set[tuple[str, str]] = set()
    for fact in _best_by_identity(instance.facts):
        context = fact.context
        end = context.end or context.instant
        if end is None:
            continue
        dims: dict[str, Any] = {"members": [list(d) for d in context.dims]}
        if context.typed:
            dims["typed"] = True
        text = fact.text.strip()
        is_block = fact.concept.endswith("TextBlock")
        number = fact.number
        if is_block:
            if not is_wanted_text_block(fact.concept) or not text:
                continue
            if (fact.concept, text) in seen_text and not context.dims:
                continue
            seen_text.add((fact.concept, text))
            statement, line_item = "note", None
            value: Decimal | None = None
            value_text = None
        elif fact.concept in TEXT_VALUED:
            if not text:
                continue
            statement, line_item = classify(fact.concept)
            value, value_text = None, text
        else:
            if number is None:
                continue
            statement, line_item = classify(fact.concept, dimensional=bool(context.dims))
            value, value_text = number, None
        unit = fact.unit or "pure"
        validation: dict[str, Any] = {
            "source_fact_id": fact.id,
            "decimals": fact.decimals,
            "reviewed": False,
        }
        block = None
        if is_block:
            block = text[:TEXT_BLOCK_CAP]
            if len(text) > TEXT_BLOCK_CAP:
                # The cap is a storage limit, not an editorial one: flag it so a reader never
                # mistakes the stored excerpt for the whole note.
                validation["text_truncated"] = True
                validation["text_length"] = len(text)
        rows.append(
            StatementFact(
                ticker=meta.ticker,
                accession=meta.accession,
                form=meta.form,
                filing_url=meta.filing_url,
                concept=fact.concept,
                statement=statement,
                line_item=line_item,
                period_start=context.start,
                period_end=end,
                fiscal_period=sec_fiscal_period(
                    context.start, end, doc_end, focus_year, focus_period
                ),
                basis=basis_of(fact.concept),
                value=value,
                value_text=value_text,
                unit=unit,
                currency=currency_of(unit),
                dimensions=dims,
                source="sec_xbrl",
                source_url=meta.source_url,
                published_at=published_at,
                accepted_at=meta.accepted_at,
                known_at=known_at,
                text_block=block,
                cik=meta.cik,
                issuer_id=meta.issuer_id,
                evidence_id=meta.evidence_id,
                amends_accession=meta.amends_accession,
                is_amendment=is_amendment,
                decimals=fact.decimals,
                validation=validation,
            )
        )
    return rows


def esef_statement_facts(
    facts: Sequence[EsefFact],
    meta: FilingMeta,
    *,
    year_end_month: int | None,
    published_at: dt.datetime | None,
    known_at: dt.datetime,
) -> list[StatementFact]:
    """Rows for an ESEF report's numeric facts (dimensional ones as `segment`) and note text
    blocks. A text block the republished xBRL-JSON carries no text for becomes a row whose
    `value_text` is "unavailable" with the reason in `validation`: the gap stays visible."""
    if year_end_month is None:
        # An annual report ends in the fiscal year-end month; a report whose longest duration
        # is a year tells which month that is. Otherwise a December year end is assumed.
        annual = [
            f.end
            for f in facts
            if f.start is not None and 11 <= round(((f.end - f.start).days + 1) / 30.44) <= 13
        ]
        if annual:
            year_end_month = max(annual).month
    rows: list[StatementFact] = []
    seen: set[tuple[Any, ...]] = set()
    for fact in facts:
        is_block = fact.text is not None or fact.availability == "unavailable"
        dims: dict[str, Any] = {"members": [[k, v] for k, v in sorted(fact.dimensions.items())]}
        key = (fact.concept, fact.start, fact.end, fact.unit, json.dumps(dims), is_block)
        if key in seen:
            continue
        seen.add(key)
        label, assumed = grid_fiscal_period(fact.start, fact.end, year_end_month)
        validation: dict[str, Any] = {
            "xbrl_json_fact": fact.raw.get("id"),
            "decimals": fact.decimals,
            "reviewed": False,
        }
        if assumed:
            validation["fiscal_year_end_assumed"] = "december"
        text_block = None
        value_text = None
        statement, line_item = classify(fact.concept, dimensional=bool(fact.dimensions))
        if is_block:
            statement = "note"
            if fact.availability == "unavailable" or not fact.text:
                value_text = "unavailable"
                validation["text_block"] = "unavailable"
                validation["reason"] = (
                    "the republished xBRL-JSON carries no text for this note block"
                )
            else:
                text_block = fact.text[:TEXT_BLOCK_CAP]
                if len(fact.text) > TEXT_BLOCK_CAP:
                    validation["text_truncated"] = True
                    validation["text_length"] = len(fact.text)
        elif fact.value is None:
            continue
        unit = str(fact.unit)
        rows.append(
            StatementFact(
                ticker=meta.ticker,
                accession=meta.accession,
                form=meta.form,
                filing_url=meta.filing_url,
                concept=fact.concept,
                statement=statement,
                line_item=line_item,
                period_start=fact.start,
                period_end=fact.end,
                fiscal_period=label,
                basis="IFRS" if not fact.concept.startswith("us-gaap:") else "US_GAAP",
                value=None if is_block else fact.value,
                value_text=value_text,
                unit=unit,
                currency=currency_of(unit),
                dimensions=dims,
                source="esef",
                source_url=meta.source_url,
                published_at=published_at,
                accepted_at=None,
                known_at=known_at,
                text_block=text_block,
                issuer_id=meta.issuer_id,
                evidence_id=meta.evidence_id,
                amends_accession=meta.amends_accession,
                is_amendment=meta.amends_accession is not None,
                decimals=fact.decimals,
                validation=validation,
            )
        )
    return rows


# ---------------------------------------------------------------------------- reconciliation

# Identities that must hold within the rounding of the values involved.
_RELATIVE_SLACK = Decimal("0.0005")


def _tolerance(facts: Sequence[StatementFact]) -> Decimal:
    """Rounding room: half a unit of the coarsest precision per value, plus 0.05% of scale."""
    worst = Decimal(0)
    scale = Decimal(0)
    for fact in facts:
        scale = max(scale, abs(fact.value or Decimal(0)))
        if fact.decimals is None or fact.decimals.upper() == "INF":
            continue
        try:
            worst = max(worst, Decimal(5) * Decimal(10) ** (-int(fact.decimals) - 1))
        except ValueError:
            continue
    return worst * max(1, len(facts)) + scale * _RELATIVE_SLACK


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    members: tuple[int, ...] = ()  # indexes into the fact list the check covers


def _pick(
    group: dict[str, list[tuple[int, StatementFact]]], *line_items: str
) -> tuple[int, StatementFact] | None:
    for item in line_items:
        found = group.get(item)
        if found:
            return found[0]
    return None


def has_full_statements(instance: Instance) -> bool:
    """Whether an instance carries financial statements (a restatement) and not only a cover
    page: undimensioned facts of at least two of revenue / net income / total assets / cash
    from operations."""
    wanted = {"revenue", "net_income", "total_assets", "cash_from_operations"}
    found = {
        classify(f.concept)[1]
        for f in instance.facts
        if not f.context.dims and f.number is not None
    }
    return len(found & wanted) >= 2


def internal_checks(facts: Sequence[StatementFact]) -> list[Check]:
    """Accounting identities over the undimensioned numeric facts of each period and unit."""
    groups: dict[tuple[Any, ...], dict[str, list[tuple[int, StatementFact]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for index, fact in enumerate(facts):
        if fact.value is None or fact.is_dimensional or not fact.line_item or fact.text_block:
            continue
        groups[(fact.basis, fact.period_start, fact.period_end, fact.unit)][fact.line_item].append(
            (index, fact)
        )
    checks: list[Check] = []
    for (basis, start, end, unit), items in groups.items():
        where = f"{basis} {start or ''}..{end} {unit}"
        assets = _pick(items, "total_assets")
        liabilities_equity = _pick(items, "total_liabilities_and_equity")
        liabilities = _pick(items, "total_liabilities")
        equity = _pick(items, "equity")
        nci = _pick(items, "noncontrolling_interest")
        if assets and liabilities_equity:
            tol = _tolerance([assets[1], liabilities_equity[1]])
            ok = abs(assets[1].value - liabilities_equity[1].value) <= tol  # type: ignore[operator]
            checks.append(
                Check(
                    "assets_equal_liabilities_and_equity",
                    ok,
                    f"{where}: assets {assets[1].value} vs liabilities and equity "
                    f"{liabilities_equity[1].value}",
                    (assets[0], liabilities_equity[0]),
                )
            )
        if assets and liabilities and equity:
            # Equity may or may not include the non-controlling interest: either closes.
            sums = [(liabilities[1].value or Decimal(0)) + (equity[1].value or Decimal(0))]
            parts = [liabilities[1], equity[1]]
            members = [assets[0], liabilities[0], equity[0]]
            if nci and nci[1].value is not None:
                sums.append(sums[0] + nci[1].value)
                parts.append(nci[1])
                members.append(nci[0])
            tol = _tolerance([assets[1], *parts])
            ok = any(abs((assets[1].value or Decimal(0)) - total) <= tol for total in sums)
            checks.append(
                Check(
                    "assets_equal_liabilities_plus_equity",
                    ok,
                    f"{where}: assets {assets[1].value} vs liabilities + equity {sums}",
                    tuple(members),
                )
            )
        revenue = _pick(items, "revenue")
        cost = _pick(items, "cost_of_revenue")
        gross = _pick(items, "gross_profit")
        if revenue and cost and gross:
            tol = _tolerance([revenue[1], cost[1], gross[1]])
            ok = abs(revenue[1].value - cost[1].value - gross[1].value) <= tol  # type: ignore[operator]
            checks.append(
                Check(
                    "gross_profit_equals_revenue_less_cost",
                    ok,
                    f"{where}: {revenue[1].value} - {cost[1].value} vs {gross[1].value}",
                    (revenue[0], cost[0], gross[0]),
                )
            )
        total_debt = _pick(items, "long_term_debt_total")
        current = _pick(items, "long_term_debt_current")
        noncurrent = _pick(items, "long_term_debt_noncurrent")
        if total_debt and current and noncurrent:
            tol = _tolerance([total_debt[1], current[1], noncurrent[1]])
            ok = abs(total_debt[1].value - current[1].value - noncurrent[1].value) <= tol  # type: ignore[operator]
            checks.append(
                Check(
                    "long_term_debt_equals_current_plus_noncurrent",
                    ok,
                    f"{where}: {total_debt[1].value} vs {current[1].value} + {noncurrent[1].value}",
                    (total_debt[0], current[0], noncurrent[0]),
                )
            )
    return checks


def companyfacts_checks(
    facts: Sequence[StatementFact],
    reference: Iterable[tuple[str, dt.date | None, dt.date, str, str, Decimal]],
) -> list[Check]:
    """Compare each undimensioned numeric fact with the SEC companyfacts value of the same
    accession, concept, period and unit. `reference` rows are
    (accession, start, end, concept, unit, value). A fact companyfacts does not carry is not
    checked (neither passed nor failed)."""
    ref: dict[tuple[Any, ...], Decimal] = {}
    for accession, start, end, concept, unit, value in reference:
        ref[(accession, concept, start, end, unit)] = value
    checks: list[Check] = []
    for index, fact in enumerate(facts):
        if fact.value is None or fact.is_dimensional or fact.text_block:
            continue
        unit = "/".join(part.rsplit(":", 1)[-1] for part in fact.unit.split("/"))
        expected = ref.get((fact.accession, fact.concept, fact.period_start, fact.period_end, unit))
        if expected is None:
            continue
        tol = _tolerance([fact])
        ok = abs(fact.value - expected) <= tol
        checks.append(
            Check(
                "matches_companyfacts",
                ok,
                f"{fact.concept} {fact.period_end}: filing {fact.value} vs companyfacts {expected}",
                (index,),
            )
        )
    return checks


def companyfacts_reference(
    company_facts: dict[str, Any], accession: str
) -> list[tuple[str, dt.date | None, dt.date, str, str, Decimal]]:
    """The SEC companyfacts rows reported by one filing, as `companyfacts_checks` takes them."""
    out: list[tuple[str, dt.date | None, dt.date, str, str, Decimal]] = []
    for taxonomy, concepts in (company_facts.get("facts") or {}).items():
        for name, body in concepts.items():
            for unit, rows in (body.get("units") or {}).items():
                for row in rows:
                    if row.get("accn") != accession:
                        continue
                    try:
                        out.append(
                            (
                                accession,
                                dt.date.fromisoformat(row["start"]) if row.get("start") else None,
                                dt.date.fromisoformat(row["end"]),
                                f"{taxonomy}:{name}",
                                unit,
                                Decimal(str(row["val"])),
                            )
                        )
                    except (KeyError, ValueError, ArithmeticError):
                        continue
    return out


def apply_reconciliation(
    facts: Sequence[StatementFact], checks: Sequence[Check]
) -> list[StatementFact]:
    """Facts with `validation.reconciled` and `published` set from the checks.

    reconciled True  - covered by at least one check and by no failed one: published.
    reconciled False - covered by a failed check: never published, never used in ratios.
    reconciled None  - covered by no check: kept for history, not published."""
    passed: dict[int, list[str]] = defaultdict(list)
    failed: dict[int, list[str]] = defaultdict(list)
    for check in checks:
        for index in check.members:
            (passed if check.passed else failed)[index].append(check.name)
    out: list[StatementFact] = []
    for index, fact in enumerate(facts):
        validation = dict(fact.validation)
        if index in failed:
            validation.update(reconciled=False, failed_checks=sorted(set(failed[index])))
            published = False
        elif index in passed:
            validation.update(reconciled=True, passed_checks=sorted(set(passed[index])))
            published = True
        else:
            validation.update(reconciled=None, reason="no applicable reconciliation check")
            published = False
        out.append(dataclasses.replace(fact, validation=validation, published=published))
    return out


# ------------------------------------------------------------------------------- storage

_INSERT = """insert into public.financial_statement_fact
    (ticker,accession,form,filing_url,concept,label,statement,line_item,period_start,period_end,
     fiscal_period,basis,value,value_text,unit,currency,dimensions,source,source_url,published_at,
     accepted_at,known_at,text_block,validation,published,issuer_id,evidence_id,cik,
     amends_accession,is_amendment,revision,amends_fact_id)
    values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17::jsonb,$18,$19,$20,$21,$22,
           $23,$24::jsonb,$25,$26,$27,$28,$29,$30,$31,$32)"""


async def resolve_issuer_id(
    conn: asyncpg.Connection | asyncpg.Pool, ticker: str, cik: str | None
) -> int | None:
    """The canonical issuer of a holding: by its confirmed security link, else by CIK."""
    row = await conn.fetchrow(
        """select s.issuer_id from public.instrument_security_link l
           join public.security s on s.id=l.security_id
           where l.ticker=$1 and l.status='confirmed' and s.issuer_id is not null""",
        ticker,
    )
    if row is not None:
        return int(row["issuer_id"])
    if cik:
        found = await conn.fetchval(
            "select id from public.issuer where cik = any($1::text[]) order by id limit 1",
            [cik, cik.lstrip("0")],
        )
        return int(found) if found is not None else None
    return None


async def filing_evidence(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    regulator: str,
    external_id: str,
    stable_url: str,
    document_type: str,
    content_hash: str,
    filed_at: dt.datetime | dt.date | None,
    accepted_at: dt.datetime | None,
    known_at: dt.datetime,
    quote: str,
    amends_external_id: str | None = None,
) -> int | None:
    """A cited document version for the filing and the evidence row facts point at."""
    amends_version: int | None = None
    if amends_external_id:
        amends_version = await conn.fetchval(
            """select v.id from public.source_document d
               join public.source_document_version v on v.document_id=d.id
               where d.regulator=$1 and d.external_id=$2 order by v.id desc limit 1""",
            regulator,
            amends_external_id,
        )
    version_id = await link_public_document(
        conn,
        regulator=regulator,
        external_id=external_id,
        stable_url=stable_url,
        document_type=document_type,
        content=b"",
        content_hash=content_hash,
        known_at=known_at,
        filed_at=filed_at,
        accepted_at=accepted_at,
        quote=quote,
        amends_version_id=amends_version,
    )
    evidence = await conn.fetchval(
        """select id from public.source_evidence
           where document_version_id=$1 and quote=$2 order by id limit 1""",
        version_id,
        quote,
    )
    return int(evidence) if evidence is not None else None


async def store_statement_facts(
    conn: asyncpg.Connection,
    ticker: str,
    source: str,
    accession: str,
    facts: Sequence[StatementFact],
) -> dict[str, int]:
    """Replace one filing's rows. `known_at` is carried over from the rows being replaced when
    the filing was stored before (a re-parse never moves the moment the filing was known);
    restatement lineage is linked to the original filing's stored rows.
    Must run inside a transaction."""
    earliest = await conn.fetchval(
        "select min(known_at) from public.financial_statement_fact"
        " where ticker=$1 and accession=$2 and source=$3",
        ticker,
        accession,
        source,
    )
    # Later amendments point at this filing's rows; detach them so the rows can be replaced and
    # point them at the new rows afterwards (matched on the fact's identity).
    referenced = await conn.fetch(
        """select a.id as ref_id, o.concept, o.period_start, o.period_end,
                  o.dimensions::text dims, o.unit
           from public.financial_statement_fact a
           join public.financial_statement_fact o on a.amends_fact_id = o.id
           where o.ticker=$1 and o.accession=$2 and o.source=$3""",
        ticker,
        accession,
        source,
    )
    if referenced:
        await conn.execute(
            "update public.financial_statement_fact set amends_fact_id = null"
            " where id = any($1::bigint[])",
            [r["ref_id"] for r in referenced],
        )
    await conn.execute(
        "delete from public.financial_statement_fact where ticker=$1 and accession=$2"
        " and source=$3",
        ticker,
        accession,
        source,
    )
    originals: dict[tuple[Any, ...], tuple[int, int]] = {}
    amends = next((f.amends_accession for f in facts if f.amends_accession), None)
    if amends:
        for row in await conn.fetch(
            """select id,revision,concept,period_start,period_end,dimensions::text dims,unit
               from public.financial_statement_fact
               where ticker=$1 and accession=$2 and source=$3 order by revision,id""",
            ticker,
            amends,
            source,
        ):
            key = (
                row["concept"],
                row["period_start"],
                row["period_end"],
                json.dumps(json.loads(row["dims"]), sort_keys=True),
                row["unit"],
            )
            originals[key] = (row["id"], row["revision"])
    records = []
    linked = 0
    for fact in facts:
        original = originals.get(fact.identity)
        if original is not None:
            linked += 1
        known_at = earliest if earliest is not None and earliest < fact.known_at else fact.known_at
        records.append(
            (
                fact.ticker,
                fact.accession,
                fact.form,
                fact.filing_url,
                fact.concept,
                fact.label,
                fact.statement,
                fact.line_item,
                fact.period_start,
                fact.period_end,
                fact.fiscal_period,
                fact.basis,
                fact.value,
                fact.value_text,
                fact.unit,
                fact.currency,
                json.dumps(fact.dimensions),
                fact.source,
                fact.source_url,
                fact.published_at,
                fact.accepted_at,
                known_at,
                fact.text_block,
                json.dumps(fact.validation, default=str),
                fact.published,
                fact.issuer_id,
                fact.evidence_id,
                fact.cik,
                fact.amends_accession,
                fact.is_amendment,
                (original[1] + 1) if original else 0,
                original[0] if original else None,
            )
        )
    if records:
        await conn.executemany(_INSERT, records)
    if referenced:
        fresh = {
            (
                r["concept"],
                r["period_start"],
                r["period_end"],
                json.dumps(json.loads(r["dims"]), sort_keys=True),
                r["unit"],
            ): r["id"]
            for r in await conn.fetch(
                """select id,concept,period_start,period_end,dimensions::text dims,unit
                   from public.financial_statement_fact
                   where ticker=$1 and accession=$2 and source=$3 order by id""",
                ticker,
                accession,
                source,
            )
        }
        for r in referenced:
            new_id = fresh.get(
                (
                    r["concept"],
                    r["period_start"],
                    r["period_end"],
                    json.dumps(json.loads(r["dims"]), sort_keys=True),
                    r["unit"],
                )
            )
            if new_id is not None:
                await conn.execute(
                    "update public.financial_statement_fact set amends_fact_id=$2 where id=$1",
                    r["ref_id"],
                    new_id,
                )
    return {"rows": len(records), "linked_to_original": linked}
