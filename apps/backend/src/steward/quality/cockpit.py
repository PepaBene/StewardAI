"""The data-quality cockpit (M7.5): what is wrong with the data behind the portfolio, ranked by the
share of the portfolio it touches. Every check names its denominator; a check the app cannot run
yet is listed as not covered rather than left out, so a clean cockpit is not full coverage."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.config.store import load_active
from steward.config.thesis import ThesesConfig
from steward.decision.service import volatility_and_price

log = logging.getLogger(__name__)

STALE_DAYS = 7
FUND_WEIGHT_TOLERANCE = 0.03
QUOTA_WARN_SHARE = 0.8

IssueKind = Literal[
    "stale_price",
    "identity_unresolved",
    "fund_weights",
    "claim_review",
    "source_conflict",
    "provider_access",
    "provider_quota",
    "thesis_gap",
]


class QualityIssue(BaseModel):
    kind: IssueKind
    subject: str = Field(description="Ticker, fund or provider the issue is about.")
    detail: str
    weight: float | None = Field(
        default=None,
        description="Share of the portfolio this touches; None when it has no holding.",
    )
    fix: str = Field(description="What to do about it, in plain words.")


class Check(BaseModel):
    kind: IssueKind
    title: str
    ran: bool
    denominator: str
    issues: int = 0
    note: str | None = None


class NotCovered(BaseModel):
    title: str
    reason: str


class Cockpit(BaseModel):
    as_of: dt.date
    portfolio_value_base: float | None
    issues: list[QualityIssue]
    checks: list[Check]
    not_covered: list[NotCovered]
    affected_weight: float = Field(
        description="Share of the portfolio touched by at least one issue (holdings counted once)."
    )


NOT_COVERED = [
    NotCovered(
        title="Missing corporate actions",
        reason="No feed of expected splits and dividends exists to compare the ledger with.",
    ),
    NotCovered(
        title="Inconsistent filing concepts",
        reason="Concept mapping is not compared across filings yet.",
    ),
    NotCovered(
        title="Estimate coverage",
        reason="Estimate access is reported only through the provider access states shown here.",
    ),
    NotCovered(
        title="Macro revisions",
        reason="Revisions to stored macro observations are not tracked.",
    ),
]


def rank(issues: list[QualityIssue]) -> list[QualityIssue]:
    """Largest affected weight first; issues with no holding last, then by kind and subject."""
    return sorted(
        issues, key=lambda i: (-(i.weight if i.weight is not None else -1.0), i.kind, i.subject)
    )


def affected_weight(issues: list[QualityIssue], weights: dict[str, float]) -> float:
    """Share of the portfolio touched by any issue, each holding counted once."""
    touched = {i.subject for i in issues if i.subject in weights}
    return sum(weights[t] for t in touched)


def fund_weight_problem(total: float, tolerance: float = FUND_WEIGHT_TOLERANCE) -> str | None:
    """A fund whose published rows do not add to about 100% of its assets."""
    if abs(total - 1) <= tolerance:
        return None
    return f"published holdings add to {total:.1%} of the fund, not 100%"


async def _weights(
    pool: asyncpg.Pool,
) -> tuple[dict[str, float], dict[str, str | None], float | None]:
    rows = await pool.fetch(
        "select p.ticker, p.value_base, i.isin from public.position_snapshot p"
        " left join public.instrument i on i.t212_ticker = p.ticker"
        " where p.snapshot_date = (select max(snapshot_date) from public.position_snapshot)"
    )
    total = sum(float(r["value_base"]) for r in rows)
    if total <= 0:
        return {}, {}, None
    return (
        {r["ticker"]: float(r["value_base"]) / total for r in rows},
        {r["ticker"]: r["isin"] for r in rows},
        total,
    )


async def _stale_prices(
    pool: asyncpg.Pool, weights: dict[str, float], today: dt.date
) -> list[QualityIssue]:
    market = await volatility_and_price(pool, sorted(weights))
    out: list[QualityIssue] = []
    for ticker, (_, _, last_day) in market.items():
        if last_day is None:
            out.append(
                QualityIssue(
                    kind="stale_price",
                    subject=ticker,
                    detail="no price history is stored for this holding",
                    weight=weights[ticker],
                    fix="Check the holding's symbol in Config, then run a price sync.",
                )
            )
        elif (today - last_day).days > STALE_DAYS:
            out.append(
                QualityIssue(
                    kind="stale_price",
                    subject=ticker,
                    detail=f"the latest close is {last_day} ({(today - last_day).days} days old)",
                    weight=weights[ticker],
                    fix="Run a price sync; if it stays old the symbol may have changed.",
                )
            )
    return out


async def _identity(pool: asyncpg.Pool, weights: dict[str, float]) -> list[QualityIssue]:
    rows = await pool.fetch(
        "select distinct on (instrument_ticker) instrument_ticker, decision, score"
        " from public.identity_match_decision"
        " where instrument_ticker = any($1::text[])"
        " order by instrument_ticker, decided_at desc, id desc",
        sorted(weights),
    )
    return [
        QualityIssue(
            kind="identity_unresolved",
            subject=r["instrument_ticker"],
            detail=f"the latest identity match is '{r['decision']}'"
            + (f" (score {float(r['score']):.2f})" if r["score"] is not None else ""),
            weight=weights[r["instrument_ticker"]],
            fix="Confirm or reject the match so filings attach to the right issuer.",
        )
        for r in rows
        if r["decision"] in ("suggested", "unresolved")
    ]


async def _fund_weights(
    pool: asyncpg.Pool, weights: dict[str, float], isins: dict[str, str | None]
) -> list[QualityIssue]:
    by_isin = {isin: t for t, isin in isins.items() if isin}
    if not by_isin:
        return []
    rows = await pool.fetch(
        "select s.fund_isin, s.as_of, coalesce(sum(h.weight), 0) as total"
        " from (select distinct on (fund_isin) id, fund_isin, as_of from public.etf_snapshot"
        "       where fund_isin = any($1::text[]) order by fund_isin, as_of desc, id desc) s"
        " left join public.etf_holding h on h.snapshot_id = s.id group by s.fund_isin, s.as_of",
        sorted(by_isin),
    )
    out: list[QualityIssue] = []
    for r in rows:
        problem = fund_weight_problem(float(r["total"]))
        if problem:
            ticker = by_isin[r["fund_isin"]]
            out.append(
                QualityIssue(
                    kind="fund_weights",
                    subject=ticker,
                    detail=f"{problem} (snapshot of {r['as_of']})",
                    weight=weights.get(ticker),
                    fix="Refresh the fund's holdings; until then its look-through is incomplete.",
                )
            )
    return out


async def _claims(pool: asyncpg.Pool, weights: dict[str, float]) -> list[QualityIssue]:
    rows = await pool.fetch(
        "select ticker, count(*) as n from public.web_fact"
        " where status = 'needs_review' and ticker = any($1::text[]) group by ticker",
        sorted(weights),
    )
    return [
        QualityIssue(
            kind="claim_review",
            subject=r["ticker"],
            detail=f"{r['n']} researched claim(s) are waiting for verification",
            weight=weights[r["ticker"]],
            fix="Open the claim, check the quoted source and approve or reject it.",
        )
        for r in rows
    ]


async def _conflicts(pool: asyncpg.Pool) -> list[QualityIssue]:
    n = await pool.fetchval("select count(*) from public.source_conflict where status = 'open'")
    if not n:
        return []
    return [
        QualityIssue(
            kind="source_conflict",
            subject="sources",
            detail=f"{n} open disagreement(s) between sources beyond tolerance",
            fix="Decide which observation to keep; the preferred one is used until you do.",
        )
    ]


async def _providers(pool: asyncpg.Pool) -> tuple[list[QualityIssue], list[QualityIssue]]:
    access = await pool.fetch(
        "select provider, endpoint, state, detail from public.provider_access"
        " where state in ('quota_exhausted', 'temporarily_failed', 'disputed')"
    )
    quota = await pool.fetch(
        "select a.provider, a.endpoint, a.quota, u.credits"
        " from public.provider_access a join lateral ("
        "   select credits from public.provider_quota_usage u"
        "   where u.provider = a.provider and u.endpoint = a.endpoint"
        "   order by window_start desc limit 1) u on true"
        " where a.quota is not null and a.quota > 0"
    )
    access_issues = [
        QualityIssue(
            kind="provider_access",
            subject=f"{r['provider']}/{r['endpoint']}",
            detail=f"access state is '{r['state']}'" + (f": {r['detail']}" if r["detail"] else ""),
            fix="Data from this endpoint is unavailable or unreliable until access recovers.",
        )
        for r in access
    ]
    quota_issues = [
        QualityIssue(
            kind="provider_quota",
            subject=f"{r['provider']}/{r['endpoint']}",
            detail=f"{float(r['credits']):g} of {float(r['quota']):g} credits used this window",
            fix="Space out runs or lower the refresh rate before the free quota runs out.",
        )
        for r in quota
        if float(r["credits"]) >= QUOTA_WARN_SHARE * float(r["quota"])
    ]
    return access_issues, quota_issues


async def _theses(pool: asyncpg.Pool, weights: dict[str, float]) -> list[QualityIssue]:
    stored = await load_active(pool, "theses", ThesesConfig)
    by = {t.ticker: t for t in (stored.body.theses if stored else [])}
    out: list[QualityIssue] = []
    for ticker, weight in weights.items():
        thesis = by.get(ticker)
        if thesis is None:
            out.append(
                QualityIssue(
                    kind="thesis_gap",
                    subject=ticker,
                    detail="no thesis is written for this holding",
                    weight=weight,
                    fix="Write why you own it, what must hold and what would end it, under Config.",
                )
            )
            continue
        gaps = thesis.missing_requirements()
        if gaps:
            out.append(
                QualityIssue(
                    kind="thesis_gap",
                    subject=ticker,
                    detail="the thesis lacks " + "; ".join(gaps),
                    weight=weight,
                    fix="Complete the thesis fields the strategy asks for.",
                )
            )
    return out


async def build_cockpit(pool: asyncpg.Pool) -> Cockpit:
    today = dt.date.today()
    weights, isins, total = await _weights(pool)
    n = len(weights)
    held = f"{n} holdings, {total:,.0f} in base currency" if total else "no holdings"
    stale = await _stale_prices(pool, weights, today) if weights else []
    identity = await _identity(pool, weights) if weights else []
    funds = await _fund_weights(pool, weights, isins) if weights else []
    claims = await _claims(pool, weights) if weights else []
    conflicts = await _conflicts(pool)
    access, quota = await _providers(pool)
    theses = await _theses(pool, weights) if weights else []

    def check(kind: IssueKind, title: str, denominator: str, found: list[QualityIssue]) -> Check:
        return Check(kind=kind, title=title, ran=True, denominator=denominator, issues=len(found))

    checks = [
        check(
            "stale_price",
            "Stale or missing prices",
            f"{held}; stale after {STALE_DAYS} days",
            stale,
        ),
        check("identity_unresolved", "Unresolved identities", held, identity),
        check(
            "fund_weights",
            "Fund weights that do not add up",
            "held funds with a stored holdings snapshot",
            funds,
        ),
        check("claim_review", "Claims awaiting verification", held, claims),
        check(
            "source_conflict", "Open source conflicts", "all stored source observations", conflicts
        ),
        check(
            "provider_access",
            "Provider access problems",
            "every provider endpoint on record",
            access,
        ),
        check(
            "provider_quota",
            "Provider quota use",
            f"endpoints with a stated quota; warn at {QUOTA_WARN_SHARE:.0%}",
            quota,
        ),
        check("thesis_gap", "Theses missing or incomplete", held, theses),
    ]
    issues = rank([*stale, *identity, *funds, *claims, *conflicts, *access, *quota, *theses])
    return Cockpit(
        as_of=today,
        portfolio_value_base=total,
        issues=issues,
        checks=checks,
        not_covered=NOT_COVERED,
        affected_weight=affected_weight(issues, weights),
    )
