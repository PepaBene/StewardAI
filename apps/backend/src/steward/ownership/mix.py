# ruff: noqa: E501
"""Ownership by holder type (insight I13.3): institutions, insiders and the rest, as shares of
shares outstanding, with the top 25 institutional holders.

- Institutions: Form 13F filers' share positions in the latest stored period (managers above
  USD 100 M only, so a floor), from `ownership.read.holding_ownership`.
- Insiders: the latest `shares_owned_after` each officer or director reported on Form 4, direct
  and indirect lines summed (non-derivative securities). Ten-percent owners who are neither are
  left out: they are usually institutions already counted in 13F.
- Public and other: the rest of shares outstanding, shown only when both figures above exist and
  are inside 0..100 % together; an overlap (an insider holding through a 13F filer) is reported,
  not netted.
- A holder's own portfolio share (the position's value over the holder's whole 13F book) is given
  only for managers you track, whose whole book is stored; other managers' books are not loaded.
Every share needs a shares-outstanding figure; without one the mix is unavailable and says why.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.analytics.versions import version
from steward.ownership.read import holding_ownership

ZERO = Decimal(0)


class HolderTypeShare(BaseModel):
    type: Literal["institutions", "insiders", "public_other"]
    label: str
    share: float | None = Field(description="Fraction of shares outstanding.")
    shares: float | None
    basis: str
    holders: int | None = None


class InstitutionRow(BaseModel):
    cik: str
    name: str | None
    shares: float
    value_usd: float | None
    share_of_outstanding: float | None
    change_pct: float | None = Field(description="Against the previous 13F period, fraction.")
    portfolio_share: float | None = Field(
        description="The position's value over the holder's whole 13F book (tracked managers only)."
    )


class InsiderRow(BaseModel):
    name: str
    role: str
    shares: float
    as_of: dt.date
    share_of_outstanding: float | None


class OwnershipMix(BaseModel):
    ticker: str
    status: Literal["complete", "partial", "unavailable"]
    reason: str | None
    shares_outstanding: float | None
    shares_outstanding_as_of: dt.date | None
    period: dt.date | None = Field(description="13F report period of the institutional figures.")
    mix: list[HolderTypeShare]
    institutions: list[InstitutionRow]
    insiders: list[InsiderRow]
    coverage: list[str] = Field(description="What each figure covers and leaves out.")
    method_version: str


def split(inst: Decimal | None, insiders: Decimal | None) -> tuple[Decimal | None, str | None]:
    """The rest of shares outstanding, or None with the reason when it cannot be stated."""
    if inst is None or insiders is None:
        return None, "needs both the institutional and the insider share"
    total = inst + insiders
    if total > 1:
        return (
            None,
            f"institutions and insiders add up to {float(total):.0%}: they overlap, so the rest is not stated",
        )
    return Decimal(1) - total, None


async def insider_holdings(
    pool: asyncpg.Pool, ticker: str
) -> list[tuple[str, str, Decimal, dt.date]]:
    """(name, role, shares, as of) per officer or director: the latest reported holding per owner
    and ownership line, summed over the lines."""
    rows = await pool.fetch(
        """with latest as (
             select distinct on (t.reporting_owner_cik, t.direct_indirect)
                    t.reporting_owner_cik, t.owner_name, t.officer_title, t.is_director, t.is_officer,
                    t.shares_owned_after, t.transaction_date
             from public.insider_filing f join public.insider_transaction t using (accession)
             where (f.ticker = $1 or exists (select 1 from public.insider_filing_ticker l
                                             where l.accession = f.accession and l.ticker = $1))
               and not t.derivative and t.shares_owned_after is not null
               and (t.is_director or t.is_officer) and f.superseded_by_accession is null
             order by t.reporting_owner_cik, t.direct_indirect, t.transaction_date desc, f.filed desc
           )
           select reporting_owner_cik, max(owner_name) as name, max(officer_title) as title,
                  bool_or(is_director) as director, bool_or(is_officer) as officer,
                  sum(shares_owned_after) as shares, max(transaction_date) as as_of
           from latest group by reporting_owner_cik order by sum(shares_owned_after) desc""",
        ticker,
    )
    out = []
    for r in rows:
        role = r["title"] or ("Director" if r["director"] else "Officer")
        out.append((r["name"] or r["reporting_owner_cik"], role, r["shares"], r["as_of"]))
    return out


async def ownership_mix(
    pool: asyncpg.Pool, ticker: str, tracked: dict[str, tuple[dt.date, Decimal]] | None = None
) -> OwnershipMix:
    """`tracked`: cik (ten digits) -> (13F period, the manager's whole book value) for the
    managers you track; a share is given only when that period is the one shown."""
    own = await holding_ownership(pool, ticker, periods=2, top=25)
    out_shares = own.shares_outstanding
    denominator = out_shares.shares if out_shares and out_shares.method != "manual" else None
    ratio = out_shares.ads_ratio if out_shares else Decimal(1)
    coverage: list[str] = []
    if own.applicable:
        coverage.append(own.basis_label or "13F filers only (managers above USD 100 M); a floor")
    else:
        coverage.append(own.not_applicable_reason or "Form 13F does not cover this holding")
    insiders = await insider_holdings(pool, ticker)
    coverage.append(
        "Insiders: officers' and directors' latest Form 4 holdings (US issuers only); ten-percent "
        "owners who are neither are left out"
        if insiders
        else "Insiders: no Form 4 holding reported (Form 4 covers US issuers only)"
    )
    if denominator is None or denominator <= 0:
        return OwnershipMix(
            ticker=ticker, status="unavailable",
            reason="No shares-outstanding figure is stored, so no share of the company can be stated.",
            shares_outstanding=None, shares_outstanding_as_of=None,
            period=own.periods[0].period if own.periods else None, mix=[], institutions=[], insiders=[],
            coverage=coverage, method_version=version("ownership_mix"),
        )  # fmt: skip
    inst_share = (
        Decimal(str(own.pct_of_shares_outstanding))
        if own.pct_of_shares_outstanding is not None
        else None
    )
    inst_shares = own.periods[0].shares * ratio if own.periods else None
    ins_total = sum((s for _n, _r, s, _d in insiders), ZERO) if insiders else None
    ins_share = ins_total / denominator if ins_total is not None else None
    rest, why = split(inst_share, ins_share)
    if why:
        coverage.append(f"Public and other: {why}")
    mix = [
        HolderTypeShare(type="institutions", label="Institutions (13F filers)", share=None if inst_share is None else float(inst_share),
                        shares=None if inst_shares is None else float(inst_shares), basis=coverage[0],
                        holders=own.periods[0].holders if own.periods else None),
        HolderTypeShare(type="insiders", label="Insiders (officers and directors)", share=None if ins_share is None else float(ins_share),
                        shares=None if ins_total is None else float(ins_total), basis="latest Form 4 holdings",
                        holders=len(insiders) or None),
        HolderTypeShare(type="public_other", label="Public and other holders", share=None if rest is None else float(rest),
                        shares=None if rest is None else float(rest * denominator), basis="the rest of shares outstanding"),
    ]  # fmt: skip
    period = own.periods[0].period if own.periods else None
    books = {cik: value for cik, (p, value) in (tracked or {}).items() if p == period and value > 0}
    institutions = [
        InstitutionRow(
            cik=h.cik, name=h.manager_name, shares=float(h.shares), value_usd=None if h.value_usd is None else float(h.value_usd),
            share_of_outstanding=h.pct_of_shares_outstanding, change_pct=h.shares_change_pct,
            portfolio_share=float(h.value_usd / books[h.cik.zfill(10)])
            if h.value_usd is not None and h.cik.zfill(10) in books else None,
        )
        for h in own.top_holders
    ]  # fmt: skip
    if institutions and not any(r.portfolio_share is not None for r in institutions):
        coverage.append(
            "A holder's own portfolio share is shown only for the managers you track, whose whole 13F book is stored."
        )
    status: Literal["complete", "partial", "unavailable"] = (
        "complete" if all(m.share is not None for m in mix) else "partial"
    )
    return OwnershipMix(
        ticker=ticker, status=status, reason=None if status == "complete" else "Some holder types cannot be measured; see coverage.",
        shares_outstanding=float(denominator), shares_outstanding_as_of=out_shares.as_of if out_shares else None,
        period=own.periods[0].period if own.periods else None, mix=mix, institutions=institutions,
        insiders=[InsiderRow(name=n, role=r, shares=float(s), as_of=d, share_of_outstanding=float(s / denominator)) for n, r, s, d in insiders[:25]],
        coverage=coverage, method_version=version("ownership_mix"),
    )  # fmt: skip
