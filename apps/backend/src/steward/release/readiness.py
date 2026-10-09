"""The release gate (M8.5): which marketed features may be switched on in the owner's app.

A feature is switched on only when its core input is reachable on the free tier and has data, a
sample of its displayed facts has been compared with the source, and the zero-cost audit passes.
A feature whose core input has no accessible free source stays labelled partial or unavailable.
The gate never substitutes a paid endpoint and never invents a figure to fill a gap."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.release import audit as audit_ledger
from steward.verify_sources.zero_cost import audit_zero_cost

Status = Literal["ready", "partial", "unavailable", "untested"]
AuditState = Literal["none", "thin", "passed", "mismatch"]

# Access states in which an endpoint cannot be used at no cost.
STORE_COUNT_CAP = 1000  # store row counts stop here and show as "1,000+ rows"
DEAD_STATES = {"paid_only", "not_covered"}
TEMPORARY_STATES = {"quota_exhausted", "temporarily_failed", "disputed"}

# Tables that hold a feature's own data. Constant names only; never built from input.
STORES: dict[str, str] = {
    "holdings": "position_snapshot",
    "prices": "price_return_series",
    "listings": "security_listing",
    "fundamentals": "financial_statement_fact",
    "fund holdings": "etf_snapshot",
    "fund facts": "fund_factsheet",
    "macro series": "macro_observation",
    "graph": "entity_relationship",
}


@dataclass(frozen=True)
class Feature:
    id: str
    title: str
    plan: str
    needs: tuple[str, ...] = ()
    required: tuple[tuple[str, str], ...] = ()
    optional: tuple[tuple[str, str], ...] = ()
    limit: str | None = None


FEATURES: tuple[Feature, ...] = (
    Feature(
        "discover", "Discover and saved screens", "M1", needs=("listings",),
        optional=(("fmp", "analyst_estimates"),),
        limit="Covers only the measured universe; estimate filters need a paid plan.",
    ),
    Feature(
        "company", "Company workbench", "M2", needs=("fundamentals", "prices"),
        optional=(("fmp", "profile"),),
        limit="Statements come from filings; sector and industry only from a stored profile.",
    ),
    Feature(
        "funds", "Fund pages", "M3", needs=("fund facts", "fund holdings"),
        limit="Issuer facts are read for iShares and WisdomTree only.",
    ),
    Feature(
        "performance", "Portfolio performance", "M4", needs=("holdings", "prices"),
        limit="Price changes only where dividends are not recorded.",
    ),
    Feature(
        "xray", "X-Ray and overlap", "M5", needs=("holdings", "fund holdings"),
        limit="Unknown fund weight stays unknown; coverage is shown.",
    ),
    Feature(
        "risk", "Risk, scenarios and rebalance", "M6", needs=("holdings", "prices"),
        limit="Sensitivities come from a short price history.",
    ),
    Feature("journal", "Decision journal and scorecard", "M7", limit="Needs five decisions."),
    Feature(
        "quality", "Data-quality cockpit", "M7", needs=("holdings",),
        limit="Missing corporate actions and macro revisions are not checked.",
    ),
    Feature(
        "macro", "Macro and trade context", "M5", needs=("macro series",),
        optional=(("ecb", "sdmx"), ("fred", "observations"), ("un_comtrade", "trade")),
        limit="Trade needs a registered key; revisions are not tracked.",
    ),
    Feature(
        "estimates", "Analyst estimates and revisions", "M2",
        required=(("fmp", "analyst_estimates"),),
        limit="No verified free consensus feed; event estimates only.",
    ),
    Feature(
        "ownership", "Ownership and insiders", "M2", needs=("graph",),
        optional=(("finnhub", "ownership"),),
        limit="Filed holdings only; no real-time ownership.",
    ),
    Feature(
        "patents", "Patent research", "M2", required=(("epo_ops", "published_data"),),
        limit="Bibliographic data only.",
    ),
)  # fmt: skip


class InputState(BaseModel):
    label: str
    kind: Literal["endpoint", "store"]
    required: bool
    ok: bool
    detail: str


class FeatureReadiness(BaseModel):
    id: str
    title: str
    plan: str
    status: Status
    reason: str
    limit: str | None
    inputs: list[InputState]
    audit: AuditState
    audited_facts: int = Field(description="Facts compared with a source that matched.")
    can_activate: bool


class Readiness(BaseModel):
    as_of: dt.date
    features: list[FeatureReadiness]
    zero_cost_passed: bool
    zero_cost_violations: int
    ready: int
    activatable: int


def _endpoint_input(
    spec: tuple[str, str], access: dict[tuple[str, str], dict[str, object]], required: bool
) -> InputState:
    label = f"{spec[0]} / {spec[1]}"
    row = access.get(spec)
    if row is None:
        return InputState(
            label=label, kind="endpoint", required=required, ok=False, detail="not on record"
        )
    state = str(row["state"])
    ok = state == "available" and row["tested_at"] is not None
    detail = state.replace("_", " ")
    if state == "available" and row["tested_at"] is None:
        detail = "available but never tested"
    return InputState(label=label, kind="endpoint", required=required, ok=ok, detail=detail)


def _store_input(name: str, counts: dict[str, int]) -> InputState:
    n = counts.get(name, 0)
    return InputState(
        label=name,
        kind="store",
        required=True,
        ok=n > 0,
        detail=(f"{STORE_COUNT_CAP:,}+ rows" if n > STORE_COUNT_CAP else f"{n:,} rows")
        if n
        else "no data stored",
    )


def assess(
    feature: Feature,
    access: dict[tuple[str, str], dict[str, object]],
    counts: dict[str, int],
    audit_rows: list[audit_ledger.AuditRow],
    zero_cost_passed: bool,
) -> FeatureReadiness:
    inputs = [_endpoint_input(s, access, True) for s in feature.required]
    inputs += [_store_input(n, counts) for n in feature.needs]
    inputs += [_endpoint_input(s, access, False) for s in feature.optional]
    required = [i for i in inputs if i.required]
    optional = [i for i in inputs if not i.required]

    def dead(i: InputState) -> bool:
        return i.kind == "store" or any(i.detail == s.replace("_", " ") for s in DEAD_STATES)

    missing = [i for i in required if not i.ok]
    if any(dead(i) or i.detail == "not on record" for i in missing):
        status: Status = "unavailable"
        first = next(i for i in missing if dead(i) or i.detail == "not on record")
        reason = f"{first.label}: {first.detail}"
    elif any(i.detail.endswith("never tested") or i.detail == "access unverified" for i in missing):
        status = "untested"
        first = missing[0]
        reason = f"{first.label} has not been tested for free access yet"
    elif missing:
        status = "partial"
        reason = f"{missing[0].label} is {missing[0].detail} for now"
    elif any(not i.ok for i in optional):
        status = "partial"
        bad = next(i for i in optional if not i.ok)
        reason = f"core inputs are present; {bad.label} is {bad.detail}"
    else:
        status = "ready"
        reason = "every input is reachable on the free tier and holds data"

    standing = audit_ledger.newest_per_fact(audit_rows)
    mine = [r for r in standing if r.feature == feature.id]
    matches = sum(r.result == "match" for r in mine)
    subjects = audit_ledger.feature_match_subjects(audit_rows, feature.id)
    if any(r.result == "mismatch" for r in mine):
        audit_state: AuditState = "mismatch"
    elif matches >= audit_ledger.MIN_FACTS_PER_FEATURE and (
        subjects >= audit_ledger.MIN_SUBJECTS_PER_FEATURE
    ):
        audit_state = "passed"
    elif matches:
        audit_state = "thin"
    else:
        audit_state = "none"
    return FeatureReadiness(
        id=feature.id,
        title=feature.title,
        plan=feature.plan,
        status=status,
        reason=reason,
        limit=feature.limit,
        inputs=inputs,
        audit=audit_state,
        audited_facts=matches,
        can_activate=status == "ready" and audit_state == "passed" and zero_cost_passed,
    )


async def _access(pool: asyncpg.Pool) -> dict[tuple[str, str], dict[str, object]]:
    rows = await pool.fetch(
        "select provider, endpoint, state, tested_at from public.provider_access"
    )
    return {(r["provider"], r["endpoint"]): dict(r) for r in rows}


async def _counts(pool: asyncpg.Pool) -> dict[str, int]:
    out: dict[str, int] = {}
    for name, table in STORES.items():
        # Table names come from the constant STORES map above, never from a request.
        # Capped count: one past the cap says "more than the cap" without scanning a big table.
        out[name] = int(
            await pool.fetchval(
                f"select count(*) from (select 1 from public.{table} limit {STORE_COUNT_CAP + 1}) t"  # noqa: S608
            )
        )
    return out


async def build_readiness(pool: asyncpg.Pool, owner_id: str) -> Readiness:
    access = await _access(pool)
    counts = await _counts(pool)
    rows = await audit_ledger.list_rows(pool, owner_id)
    zero = await audit_zero_cost(pool)
    # An empty endpoint manifest proves nothing, so it does not pass.
    zero_ok = zero.passed and zero.checked > 0
    features = [assess(f, access, counts, rows, zero_ok) for f in FEATURES]
    return Readiness(
        as_of=dt.date.today(),
        features=features,
        zero_cost_passed=zero_ok,
        zero_cost_violations=len(zero.violations),
        ready=sum(f.status == "ready" for f in features),
        activatable=sum(f.can_activate for f in features),
    )
