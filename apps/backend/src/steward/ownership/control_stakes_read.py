"""Read model for Schedule 13D/G control stakes of a held security.

The response is its own channel. It carries no total, no combined percentage and no field
that mixes in Form 13F or fund holdings: a 13D/G percentage is the filer's own beneficial
ownership of one share class on its event date, and holders overlap across channels.
"""

import datetime as dt
from decimal import Decimal
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

LIMITATIONS = [
    "Schedule 13D/G reports beneficial ownership and control of one share class by the "
    "filer's own count on its event date. It is not additive with Form 13F positions or fund "
    "holdings: the same shares can appear in all three, so no total is computed.",
    "Percentages are the filer's own; reconciliation recomputes a cross-check from the "
    "class's shares outstanding but never overwrites the filer's figure.",
    "The filing-level stake is the largest single reporting-person figure. Group members "
    "report overlapping beneficial ownership, so members are never summed; for a group it "
    "is a lower bound.",
    "Only structured XML filings (December 2024 onward) are parsed. Earlier text or HTML "
    "13D/G filings are not read, and a missing stake is not evidence of no stake.",
    "A 13G is a passive-investor filing; intent is only stated in a 13D (Item 4).",
]


class StakeHolderOut(BaseModel):
    person_name: str
    person_cik: str | None
    member_of_group: bool | None
    sole_voting: Decimal | None
    shared_voting: Decimal | None
    sole_dispositive: Decimal | None
    shared_dispositive: Decimal | None
    aggregate_shares: Decimal | None
    percent_of_class: Decimal | None = Field(description="Filer-reported, in percent points.")
    person_type: str | None


class StakeReconciliationOut(BaseModel):
    match_basis: Literal["cusip", "issuer_cik"]
    class_status: Literal["cusip_match", "class_unreconciled"]
    class_label: str | None
    class_shares_outstanding: Decimal | None
    class_shares_as_of: dt.date | None
    computed_percent: Decimal | None = Field(
        description="Cross-check only: stake shares over the class's shares outstanding."
    )
    percent_check: Literal["consistent", "inconsistent", "not_checkable"]
    note: str | None


class StakeChangeOut(BaseModel):
    change_type: str
    person_name: str | None
    old_value: str | None
    new_value: str | None
    detail: str | None


class ControlStakeOut(BaseModel):
    accession: str
    form: str
    schedule: Literal["13D", "13G"]
    is_amendment: bool
    amendment_no: int | None
    filed: dt.date
    event_date: dt.date | None
    issuer_name: str | None
    issuer_cusip: str | None
    class_title: str | None
    stake_shares: Decimal | None
    stake_percent: Decimal | None
    stake_basis: str
    intent_text: str | None
    prior_accession: str | None
    holders: list[StakeHolderOut]
    reconciliation: StakeReconciliationOut
    changes: list[StakeChangeOut]
    source_url: str


class ControlStakesOut(BaseModel):
    ticker: str
    channel: Literal["beneficial_ownership_13dg"] = "beneficial_ownership_13dg"
    not_additive_with: list[str] = Field(
        default_factory=lambda: ["form_13f_positions", "fund_holdings", "insider_forms_3_4_5"]
    )
    stakes: list[ControlStakeOut] = Field(
        description="Latest filing of each amendment chain (not restated by a later filing)."
    )
    superseded: list[ControlStakeOut] = Field(
        description="Earlier filings of those chains, newest first, with their own changes."
    )
    limitations: list[str]


async def holding_control_stakes(pool: asyncpg.Pool, ticker: str) -> ControlStakesOut:
    rows = await pool.fetch(
        """select f.*, l.match_basis, l.class_status, l.class_label,
                  l.class_shares_outstanding, l.class_shares_as_of, l.computed_percent,
                  l.percent_check, l.note,
                  exists(select 1 from public.control_stake_filing n
                         where n.prior_accession = f.accession) as is_superseded
           from public.control_stake_filing f
           join public.control_stake_issuer_link l on l.accession = f.accession
           where l.ticker = $1 order by f.filed desc, f.accession desc""",
        ticker,
    )
    accessions = [r["accession"] for r in rows]
    holders: dict[str, list[StakeHolderOut]] = {}
    for h in await pool.fetch(
        "select * from public.control_stake_holder where accession = any($1::text[])"
        " order by accession, row_index",
        accessions,
    ):
        holders.setdefault(h["accession"], []).append(
            StakeHolderOut(**{k: h[k] for k in StakeHolderOut.model_fields})
        )
    changes: dict[str, list[StakeChangeOut]] = {}
    for c in await pool.fetch(
        "select * from public.control_stake_change where accession = any($1::text[])"
        " order by accession, id",
        accessions,
    ):
        changes.setdefault(c["accession"], []).append(
            StakeChangeOut(**{k: c[k] for k in StakeChangeOut.model_fields})
        )

    def build(r: asyncpg.Record) -> ControlStakeOut:
        return ControlStakeOut(
            **{
                k: r[k]
                for k in (
                    "accession",
                    "form",
                    "schedule",
                    "is_amendment",
                    "amendment_no",
                    "filed",
                    "event_date",
                    "issuer_name",
                    "issuer_cusip",
                    "class_title",
                    "stake_shares",
                    "stake_percent",
                    "stake_basis",
                    "intent_text",
                    "prior_accession",
                    "source_url",
                )
            },
            holders=holders.get(r["accession"], []),
            reconciliation=StakeReconciliationOut(
                **{k: r[k] for k in StakeReconciliationOut.model_fields}
            ),
            changes=changes.get(r["accession"], []),
        )

    current = [build(r) for r in rows if not r["is_superseded"]]
    older = [build(r) for r in rows if r["is_superseded"]]
    return ControlStakesOut(
        ticker=ticker, stakes=current, superseded=older, limitations=list(LIMITATIONS)
    )
