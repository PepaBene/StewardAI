"""Live impact of a draft Checks config (Settings > Checks).

The checks engine is pure: `evaluate(inputs, rules)` returns every check's state. The preview loads
each current holding's stored inputs once, evaluates them with the active rules and with the draft
rules, and reports which checks flip. Nothing is saved or cached.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import Pool
from steward.checks.contract import Check
from steward.checks.service import evaluate
from steward.config.checks import ChecksConfig, load_checks

router = APIRouter()
log = logging.getLogger(__name__)

MAX_HOLDINGS = 60


class StateTally(BaseModel):
    passes: int
    fails: int
    unknown: int
    risk_flags: int


class CheckChange(BaseModel):
    id: str
    label: str
    was: str
    now: str


class HoldingPreview(BaseModel):
    ticker: str
    name: str | None
    current: StateTally
    would_be: StateTally
    changes: list[CheckChange]
    changed: bool


class ChecksPreview(BaseModel):
    status: Literal["complete", "partial", "unavailable"]
    holdings: list[HoldingPreview]
    changed_count: int = Field(description="Holdings with at least one check that would flip.")
    holdings_total: int
    skipped: list[str] = Field(description="Holdings whose inputs could not be loaded.")


def tally(checks: Sequence[Check], risks: Sequence[Check]) -> StateTally:
    """Counts over the scored checks; informational checks are never counted."""
    scored = [c for c in checks if not c.informational]
    return StateTally(
        passes=sum(1 for c in scored if c.state == "pass"),
        fails=sum(1 for c in scored if c.state == "fail"),
        unknown=sum(1 for c in scored if c.state == "unknown"),
        risk_flags=sum(1 for c in risks if c.state == "fail" and not c.informational),
    )


def diff_checks(current: Sequence[Check], draft: Sequence[Check]) -> list[CheckChange]:
    """Checks whose state differs between the active rules and the draft."""
    now = {c.id: c for c in draft}
    out: list[CheckChange] = []
    for c in current:
        d = now.get(c.id)
        if d is not None and d.state != c.state:
            out.append(CheckChange(id=c.id, label=c.label, was=c.state, now=d.state))
    return out


def preview_holding(
    ticker: str,
    name: str | None,
    current: tuple[list[Check], list[Check]],
    draft: tuple[list[Check], list[Check]],
) -> HoldingPreview:
    changes = [*diff_checks(current[0], draft[0]), *diff_checks(current[1], draft[1])]
    return HoldingPreview(
        ticker=ticker,
        name=name,
        current=tally(*current),
        would_be=tally(*draft),
        changes=changes,
        changed=bool(changes),
    )


@router.post("/checks/preview", tags=["config"])
async def preview_checks(body: ChecksConfig, pool: Pool, owner: Owner) -> ChecksPreview:
    """Per holding, the current and would-be check states under the draft config, and how many
    holdings change. Evaluates the real checks engine with the draft limits against today's stored
    inputs; saves and caches nothing. Up to 60 holdings, largest first."""
    from steward.checks.load import load_inputs
    from steward.dividends.service import load_context
    from steward.valuation.fair_value import load_theses

    rows = await pool.fetch(
        """select p.ticker, p.value_account, i.name
             from public.position_snapshot p
             left join public.instrument i on i.t212_ticker = p.ticker
            where p.snapshot_date = (select max(snapshot_date) from public.position_snapshot)
              and p.quantity > 0 and p.value_account is not null and p.value_account > 0
            order by p.value_account desc"""
    )
    rules, _ = await load_checks(pool)
    ctx = await load_context(pool)
    theses = await load_theses(pool)
    holdings: list[HoldingPreview] = []
    skipped: list[str] = []
    for r in rows[:MAX_HOLDINGS]:
        try:
            inp = await load_inputs(pool, owner.user_id, r["ticker"], div_ctx=ctx, theses=theses)
        except (ValueError, ArithmeticError) as exc:
            log.warning("checks preview skipped %s: %s", r["ticker"], exc)
            skipped.append(r["ticker"])
            continue
        if inp is None:
            skipped.append(r["ticker"])
            continue
        holdings.append(
            preview_holding(r["ticker"], r["name"], evaluate(inp, rules), evaluate(inp, body))
        )
    status_: Literal["complete", "partial", "unavailable"] = (
        "unavailable"
        if not holdings
        else "partial"
        if skipped or len(rows) > MAX_HOLDINGS
        else "complete"
    )
    return ChecksPreview(
        status=status_,
        holdings=holdings,
        changed_count=sum(1 for h in holdings if h.changed),
        holdings_total=len(rows),
        skipped=skipped,
    )
