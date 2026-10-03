"""Stored UK PDMR notifications and the honest standing status of the UK channel.

`pdmr_access_from_db` derives the access state from what the collector actually did: the UK
channel is `available` only once a notification has been parsed; before that, and when every
attempt failed, it stays `access_unverified` with the counts of what happened. Nothing here
turns a failed retrieval into an empty list of transactions."""

import datetime as dt
from decimal import Decimal
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.sources.base import AccessResult, AccessState

Status = Literal[
    "parsed",
    "no_template",
    "access_unverified",
    "paid_only",
    "quota_exhausted",
    "temporarily_failed",
]

LIMITATIONS = [
    "UK PDMR notifications come from regulatory announcements (RNS) read by this app; an "
    "announcement that could not be retrieved or parsed is listed with its status, never "
    "dropped silently, and the absence of a row is not evidence of no dealing.",
    "A purchase or sale counts as open market only when the notification states a trading "
    "venue as the place of the transaction.",
    "Retrieval of announcement bodies has not been verified against the live site.",
]


class PdmrTransactionOut(BaseModel):
    instrument: str | None
    isin: str | None
    nature: str | None
    price: Decimal | None
    currency: str | None
    volume: Decimal | None
    transaction_date: dt.date | None
    venue: str | None
    economic_class: str
    classification_basis: str


class PdmrNotificationOut(BaseModel):
    id: int
    source_url: str
    headline: str | None
    announced_at: dt.datetime | None
    status: Status
    detail: str | None
    attempts: int
    person_name: str | None
    position: str | None
    is_amendment: bool | None
    issuer_name: str | None
    lei: str | None
    transactions: list[PdmrTransactionOut]


class PdmrStatus(BaseModel):
    state: str = Field(description="AccessState of the UK PDMR channel.")
    detail: str | None
    parsed: int
    attempted: int
    by_status: dict[str, int]


class HoldingPdmrOut(BaseModel):
    ticker: str
    status: PdmrStatus = Field(
        description="Channel-wide (all issuers) counts and state, not specific to `ticker`; "
        "see `ticker_status` for this holding."
    )
    ticker_status: PdmrStatus | None = Field(
        default=None,
        description="The same counts restricted to this ticker (case-insensitive). `state` is "
        "`available` once one of this ticker's announcements was parsed, else "
        "`access_unverified`.",
    )
    notifications: list[PdmrNotificationOut]
    limitations: list[str]


def access_from_counts(by_status: dict[str, int]) -> AccessResult:
    total = sum(by_status.values())
    parsed = by_status.get("parsed", 0)
    if total == 0:
        return AccessResult(
            AccessState.ACCESS_UNVERIFIED,
            "No UK PDMR announcement has been retrieved yet: UK insider activity is not "
            "available (the collector has not run, or the RNS stream has no PDMR items).",
        )
    failed = {k: v for k, v in sorted(by_status.items()) if k != "parsed"}
    summary = ", ".join(f"{v} {k}" for k, v in failed.items())
    if parsed == 0:
        return AccessResult(
            AccessState.ACCESS_UNVERIFIED,
            f"{total} PDMR announcement(s) attempted, none parsed ({summary}): UK insider "
            "activity is not available.",
        )
    detail = f"{parsed} of {total} PDMR announcement(s) parsed" + (
        f"; not read: {summary}" if failed else ""
    )
    return AccessResult(AccessState.AVAILABLE, detail)


async def pdmr_access_from_db(pool: asyncpg.Pool) -> AccessResult:
    rows = await pool.fetch(
        "select status, count(*) as n from public.pdmr_notification group by status"
    )
    return access_from_counts({r["status"]: int(r["n"]) for r in rows})


async def holding_pdmr(pool: asyncpg.Pool, ticker: str, *, limit: int = 100) -> HoldingPdmrOut:
    rows = await pool.fetch(
        "select * from public.pdmr_notification where lower(ticker) = lower($1)"
        " order by announced_at desc nulls last, id desc limit $2",
        ticker,
        limit,
    )
    ids = [r["id"] for r in rows]
    transactions: dict[int, list[PdmrTransactionOut]] = {}
    for t in await pool.fetch(
        "select * from public.pdmr_transaction where notification_id = any($1::bigint[])"
        " order by notification_id, row_index",
        ids,
    ):
        transactions.setdefault(t["notification_id"], []).append(
            PdmrTransactionOut(**{k: t[k] for k in PdmrTransactionOut.model_fields})
        )
    status_rows = await pool.fetch(
        "select status, count(*) as n from public.pdmr_notification group by status"
    )
    by_status = {r["status"]: int(r["n"]) for r in status_rows}
    access = access_from_counts(by_status)
    own_rows = await pool.fetch(
        "select status, count(*) as n from public.pdmr_notification"
        " where lower(ticker) = lower($1) group by status",
        ticker,
    )
    own = {r["status"]: int(r["n"]) for r in own_rows}
    own_access = access_from_counts(own)
    return HoldingPdmrOut(
        ticker=ticker,
        ticker_status=PdmrStatus(
            state=own_access.state.value,
            detail=own_access.detail,
            parsed=own.get("parsed", 0),
            attempted=sum(own.values()),
            by_status=own,
        ),
        status=PdmrStatus(
            state=access.state.value,
            detail=access.detail,
            parsed=by_status.get("parsed", 0),
            attempted=sum(by_status.values()),
            by_status=by_status,
        ),
        notifications=[
            PdmrNotificationOut(
                id=r["id"],
                source_url=r["source_url"],
                headline=r["headline"],
                announced_at=r["announced_at"],
                status=r["status"],
                detail=r["detail"],
                attempts=r["attempts"],
                person_name=r["person_name"],
                position=r["position"],
                is_amendment=r["is_amendment"],
                issuer_name=r["issuer_name"],
                lei=r["lei"],
                transactions=transactions.get(r["id"], []),
            )
            for r in rows
        ],
        limitations=list(LIMITATIONS),
    )
