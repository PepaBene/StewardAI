"""P2.5: read values out of stored ESEF reports (xBRL-JSON) into observations.

`foreign_job.collect_esef` stores the report as a document version and reads nothing from it. This
module has a pure parser and a writer:

* `parse_esef_facts(json_bytes)` reads the facts of a filings.xbrl.org report. Only a short
  allow-list of IFRS concepts is read (`CONCEPTS`); every other concept is counted in `ignored`.
  A fact is `{"value", "decimals"?, "dimensions": {concept, entity, period, unit?, language?,
  <axis>: <member>...}}`. A period is `start/end` or one instant; an "end" or instant at midnight
  is the exclusive end ("2022-01-01T00:00:00" is the close of 31 December 2021), checked on a real
  report whose balance sheet dates only agree with the filing under that reading.
* Nothing is invented: a concept the report does not state is absent, not zero. A fact that cannot
  be read, or whose contexts disagree, is returned in `skipped` with a named reason (`REASONS`).
  A fact with an extra dimension axis (a segment, a component of equity) keeps its axes in
  `dimensions` and is never merged into the consolidated value, which has none.
* `store_esef_facts` writes one `source_evidence` row and one `disclosure_observation` per fact,
  tied to the document version, with `known_at` = the version's `known_at` (visible only when
  `known_at <= as_of`). Rows are append-only: the identity includes the parser version, so a
  re-run adds nothing and a changed parser adds new rows beside the old ones.
* `read_pending_esef_versions` finds archived ESEF versions not yet read by this parser version.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import asyncpg

from steward.disclosure.ingest import record_observation
from steward.storage import EvidenceStore

ESEF_PARSER_VERSION = "esef-xbrl-json-1"
EXTRACTION_METHOD = "xbrl-json"
PER_RUN = 10


@dataclass(frozen=True)
class Concept:
    metric: str
    kind: Literal["duration", "instant"]
    unit: Literal["monetary", "shares"]


CONCEPTS: dict[str, Concept] = {
    "ifrs-full:Revenue": Concept("revenue", "duration", "monetary"),
    "ifrs-full:ProfitLoss": Concept("net_income", "duration", "monetary"),
    "ifrs-full:ProfitLossFromOperatingActivities": Concept(
        "operating_income", "duration", "monetary"
    ),
    "ifrs-full:CashFlowsFromUsedInOperatingActivities": Concept(
        "operating_cash_flow", "duration", "monetary"
    ),
    "ifrs-full:Assets": Concept("total_assets", "instant", "monetary"),
    "ifrs-full:Equity": Concept("total_equity", "instant", "monetary"),
    "ifrs-full:Liabilities": Concept("total_liabilities", "instant", "monetary"),
    "ifrs-full:CashAndCashEquivalents": Concept("cash", "instant", "monetary"),
    "ifrs-full:CurrentBorrowings": Concept("borrowings_current", "instant", "monetary"),
    "ifrs-full:NoncurrentBorrowings": Concept("borrowings_noncurrent", "instant", "monetary"),
    "ifrs-full:NumberOfSharesOutstanding": Concept("shares_outstanding", "instant", "shares"),
}

REASONS = (
    "malformed_fact",
    "nil_value",
    "unparseable_value",
    "bad_decimals",
    "bad_period",
    "period_kind_mismatch",
    "no_unit",
    "unit_mismatch",
    "conflicting_duplicates",
)
_NOT_AXES = {"concept", "entity", "period", "unit", "language"}
_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?)?Z?$")
_CURRENCY = re.compile(r"^iso4217:([A-Z]{3})$")


class EsefParseError(ValueError):
    """The bytes are not an xBRL-JSON report (not JSON, or no `facts` object)."""


@dataclass(frozen=True)
class EsefFact:
    fact_id: str
    concept: str
    metric: str
    entity: str | None
    period_start: dt.date | None
    period_end: dt.date
    is_instant: bool
    value: Decimal
    original_string: str
    unit: str  # "USD" or "shares"
    currency: str | None
    decimals: int | None  # None: exact ("INF") or not stated
    scale: int  # 6 for decimals -6; 0 when exact or positive decimals
    dimensions: dict[str, str]  # axis -> member; empty for the consolidated value

    @property
    def consolidated(self) -> bool:
        return not self.dimensions


@dataclass(frozen=True)
class SkippedFact:
    fact_id: str | None
    concept: str | None
    reason: str  # one of REASONS
    detail: str


@dataclass
class EsefFacts:
    facts: list[EsefFact] = field(default_factory=list)
    skipped: list[SkippedFact] = field(default_factory=list)
    ignored: int = 0  # facts of concepts outside the allow-list
    duplicates_collapsed: int = 0  # identical repeats of a stated fact

    def skipped_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self.skipped:
            out[s.reason] = out.get(s.reason, 0) + 1
        return out


def _date(text: str) -> tuple[dt.date, bool] | None:
    """(date, exclusive_midnight). A non-midnight time of day is not a date boundary: None."""
    m = _TIME.match(text.strip())
    if m is None:
        return None
    try:
        day = dt.date.fromisoformat(m.group(1))
    except ValueError:
        return None
    if m.group(2) is None:
        return day, False
    if (m.group(2), m.group(3), m.group(4)) != ("00", "00", "00"):
        return None
    return day, True


def _period(text: str) -> tuple[dt.date | None, dt.date, bool] | None:
    """(start, end, is_instant), end inclusive. None when the period is not a plain date range."""
    parts = text.split("/")
    if len(parts) == 1:
        end = _date(parts[0])
        if end is None:
            return None
        day = end[0] - dt.timedelta(days=1) if end[1] else end[0]
        return None, day, True
    if len(parts) != 2:
        return None
    start, end = _date(parts[0]), _date(parts[1])
    if start is None or end is None:
        return None
    last = end[0] - dt.timedelta(days=1) if end[1] else end[0]
    first = start[0]
    if last < first:
        return None
    return first, last, False


def _decimal(raw: Any) -> Decimal | None:
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        out = Decimal(str(raw).strip())
    except InvalidOperation:
        return None
    return out if out.is_finite() else None


def _decimals(raw: Any) -> tuple[bool, int | None]:
    """(ok, decimals). Missing and "INF" mean exact: ok with None."""
    if raw is None:
        return True, None
    if isinstance(raw, bool):
        return False, None
    if isinstance(raw, int):
        return True, raw
    if isinstance(raw, str):
        text = raw.strip()
        if text.upper() == "INF":
            return True, None
        if re.fullmatch(r"-?\d+", text):
            return True, int(text)
    return False, None


def _unit(text: str | None, want: str) -> tuple[str, str | None] | None:
    """(unit, currency) when the unit is the single one this concept needs."""
    if text is None:
        return None
    if want == "shares":
        return ("shares", None) if text == "xbrli:shares" else None
    m = _CURRENCY.match(text)
    return (m.group(1), m.group(1)) if m else None


def parse_esef_facts(json_bytes: bytes) -> EsefFacts:
    try:
        doc = json.loads(json_bytes)
    except (ValueError, UnicodeDecodeError) as exc:
        raise EsefParseError("not JSON") from exc
    raw_facts = doc.get("facts") if isinstance(doc, dict) else None
    if not isinstance(raw_facts, dict):
        raise EsefParseError("no facts object: not an xBRL-JSON report")

    out = EsefFacts()
    candidates: dict[tuple[Any, ...], list[EsefFact]] = defaultdict(list)
    for fact_id, fact in raw_facts.items():
        dims = fact.get("dimensions") if isinstance(fact, dict) else None
        concept = dims.get("concept") if isinstance(dims, dict) else None
        if not isinstance(dims, dict) or not isinstance(concept, str):
            out.skipped.append(SkippedFact(str(fact_id), None, "malformed_fact", "no concept"))
            continue
        spec = CONCEPTS.get(concept)
        if spec is None:
            out.ignored += 1
            continue

        def skip(reason: str, detail: str, fid: str = str(fact_id), c: str = concept) -> None:
            out.skipped.append(SkippedFact(fid, c, reason, detail))

        if fact.get("value") is None:
            skip("nil_value", "the report states this fact as nil")
            continue
        value = _decimal(fact["value"])
        if value is None:
            skip("unparseable_value", f"value {str(fact['value'])[:40]!r} is not a number")
            continue
        ok, decimals = _decimals(fact.get("decimals"))
        if not ok:
            skip("bad_decimals", f"decimals {str(fact.get('decimals'))[:20]!r}")
            continue
        period = _period(str(dims.get("period", "")))
        if period is None:
            skip("bad_period", f"period {str(dims.get('period'))[:60]!r}")
            continue
        start, end, instant = period
        if instant != (spec.kind == "instant"):
            skip("period_kind_mismatch", f"{concept} is a {spec.kind} concept")
            continue
        unit_text = dims.get("unit")
        if unit_text is None:
            skip("no_unit", "numeric fact without a unit")
            continue
        unit = _unit(str(unit_text), spec.unit)
        if unit is None:
            skip("unit_mismatch", f"unit {str(unit_text)[:40]!r} for {concept}")
            continue
        entity = dims.get("entity")
        axes = {
            k: str(v) for k, v in dims.items() if k not in _NOT_AXES and not k.startswith("xbrl:")
        }
        row = EsefFact(
            fact_id=str(fact_id),
            concept=concept,
            metric=spec.metric,
            entity=str(entity) if entity is not None else None,
            period_start=start,
            period_end=end,
            is_instant=instant,
            value=value,
            original_string=str(fact["value"]).strip(),
            unit=unit[0],
            currency=unit[1],
            decimals=decimals,
            scale=max(0, -decimals) if decimals is not None else 0,
            dimensions=axes,
        )
        key = (
            row.metric,
            row.entity,
            row.period_start,
            row.period_end,
            tuple(sorted(axes.items())),
        )
        candidates[key].append(row)

    for rows in candidates.values():
        first = rows[0]
        if any(r.value != first.value or r.unit != first.unit for r in rows[1:]):
            ids = ",".join(r.fact_id for r in rows)
            for r in rows:
                out.skipped.append(
                    SkippedFact(
                        r.fact_id,
                        r.concept,
                        "conflicting_duplicates",
                        f"{len(rows)} facts ({ids[:80]}) for one context differ in value or unit",
                    )
                )
            continue
        # identical repeats: keep the most precise statement, then the first id
        best = max(rows, key=lambda r: (r.decimals is None, r.decimals or 0))
        out.duplicates_collapsed += len(rows) - 1
        out.facts.append(best)
    out.facts.sort(key=lambda f: (f.metric, f.period_end, sorted(f.dimensions.items()), f.fact_id))
    return out


# ------------------------------------------------------------------------------------- storing
@dataclass
class StoreResult:
    extracted: int = 0  # observations newly written
    unchanged: int = 0  # already present for this parser version
    skipped: dict[str, int] = field(default_factory=dict)  # parser reasons plus `other_entity`

    def count(self, reason: str, n: int = 1) -> None:
        if n:
            self.skipped[reason] = self.skipped.get(reason, 0) + n


def _same_entity(entity: str | None, lei: str) -> bool:
    return entity is not None and entity.rsplit(":", 1)[-1].upper() == lei.upper()


async def store_esef_facts(
    pool: asyncpg.Pool,
    document_version_id: int,
    facts: EsefFacts,
    *,
    issuer_id: int | None = None,
    lei: str | None = None,
    parser_version: str = ESEF_PARSER_VERSION,
) -> StoreResult:
    """Write the facts of one stored version. The issuer is the document's issuer unless given;
    with a `lei`, facts about any other reporting entity are left out (`other_entity`). Skips of
    the parser are carried into the result. Safe to repeat."""
    version = await pool.fetchrow(
        """select v.known_at, v.quarantined, d.issuer_id
             from public.source_document_version v
             join public.source_document d on d.id = v.document_id
            where v.id = $1""",
        document_version_id,
    )
    if version is None:
        raise ValueError(f"no document version {document_version_id}")
    if version["quarantined"]:
        raise ValueError(f"document version {document_version_id} is quarantined")
    issuer = issuer_id if issuer_id is not None else version["issuer_id"]
    if issuer is None:
        raise ValueError(f"document version {document_version_id} has no issuer")

    result = StoreResult()
    for reason, n in facts.skipped_counts().items():
        result.count(reason, n)
    async with pool.acquire() as conn, conn.transaction():
        for fact in facts.facts:
            if lei is not None and not _same_entity(fact.entity, lei):
                result.count("other_entity")
                continue
            quote = json.dumps(
                {
                    "fact": fact.fact_id,
                    "concept": fact.concept,
                    "entity": fact.entity,
                    "period": [str(fact.period_start), str(fact.period_end)],
                    "value": fact.original_string,
                    "unit": fact.unit,
                    "dimensions": fact.dimensions,
                },
                sort_keys=True,
            )
            evidence_id = await conn.fetchval(
                # The same quote in the same version is one evidence row whatever parser read it
                # (a unique index says so); a new parser version reuses it.
                """select id from public.source_evidence
                    where document_version_id = $1 and md5(quote) = md5($2)
                      and observation_id is null and page is null and table_name is null
                      and span_start is null and span_end is null""",
                document_version_id,
                quote,
            )
            if evidence_id is None:
                evidence_id = await conn.fetchval(
                    """insert into public.source_evidence
                         (document_version_id, table_name, quote, section, row_ref,
                          extraction_method, parser_version, confidence)
                       values($1, null, $2, $3, $4, $5, $6, 1) returning id""",
                    document_version_id,
                    quote,
                    fact.concept,
                    fact.fact_id,
                    EXTRACTION_METHOD,
                    parser_version,
                )
            new_id = await record_observation(
                conn,
                metric=fact.metric,
                issuer_id=int(issuer),
                version_id=document_version_id,
                evidence_id=int(evidence_id),
                known_at=version["known_at"],
                period_start=fact.period_start,
                period_end=fact.period_end,
                is_instant=fact.is_instant,
                value=fact.value,
                original_string=fact.original_string,
                unit=fact.unit,
                scale=fact.scale,
                currency=fact.currency,
                basis="as_filed",
                dimensions=fact.dimensions,
                parser_version=parser_version,
            )
            if new_id is None:
                result.unchanged += 1
            else:
                result.extracted += 1
    return result


async def read_pending_esef_versions(
    pool: asyncpg.Pool,
    store: EvidenceStore | None,
    *,
    limit: int = PER_RUN,
    parser_version: str = ESEF_PARSER_VERSION,
) -> dict[str, Any]:
    """Read the newest archived ESEF versions this parser version has not read yet. A version is
    marked read (`source_document_version.parser_version`) once its report was parsed, including
    one that holds no allow-listed fact or cannot be parsed, so it is not retried every run; a
    failed archive read is not marked and is tried again."""
    detail: dict[str, Any] = {
        "versions": 0,
        "extracted": 0,
        "unchanged": 0,
        "ignored": 0,
        "skipped": {},
        "failed": 0,
        "errors": [],
    }
    if store is None:
        detail["skipped_reason"] = "no evidence store configured"
        return detail
    rows = await pool.fetch(
        """select v.id version_id, v.archive_path, d.issuer_id, i.lei
             from public.source_document_version v
             join public.source_document d on d.id = v.document_id
             left join public.issuer i on i.id = d.issuer_id
            where d.regulator = 'ESEF' and v.archive_path is not null and not v.quarantined
              and d.issuer_id is not null and v.parser_version is distinct from $1
            order by v.id desc limit $2""",
        parser_version,
        limit,
    )
    skipped: dict[str, int] = detail["skipped"]
    for row in rows:
        detail["versions"] += 1
        try:
            body = await store.get(row["archive_path"])
        except Exception as exc:  # the archive read may fail for many reasons; try again later
            detail["failed"] += 1
            detail["errors"].append(f"version {row['version_id']}: {type(exc).__name__}")
            continue
        try:
            facts = parse_esef_facts(body)
            stored = await store_esef_facts(
                pool,
                int(row["version_id"]),
                facts,
                issuer_id=int(row["issuer_id"]),
                lei=row["lei"],
                parser_version=parser_version,
            )
        except (EsefParseError, ValueError, asyncpg.PostgresError) as exc:
            detail["failed"] += 1
            detail["errors"].append(f"version {row['version_id']}: {type(exc).__name__}")
            if isinstance(exc, EsefParseError):
                skipped["unreadable_report"] = skipped.get("unreadable_report", 0) + 1
                await _mark_read(pool, int(row["version_id"]), parser_version)
            continue
        detail["extracted"] += stored.extracted
        detail["unchanged"] += stored.unchanged
        detail["ignored"] += facts.ignored
        for reason, n in stored.skipped.items():
            skipped[reason] = skipped.get(reason, 0) + n
        await _mark_read(pool, int(row["version_id"]), parser_version)
    detail["errors"] = detail["errors"][:20]
    return detail


async def _mark_read(pool: asyncpg.Pool, version_id: int, parser_version: str) -> None:
    await pool.execute(
        "update public.source_document_version set parser_version = $2 where id = $1",
        version_id,
        parser_version,
    )
