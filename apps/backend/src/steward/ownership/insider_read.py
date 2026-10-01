"""Insider activity per holding: classified transactions and net discretionary activity.

Net discretionary activity counts only open-market purchases and sales of the issuer's
(non-derivative) stock. Everything else a Form 4 reports - option exercises, grants and
awards, gifts, tax withholding, conversions, derivative trades, other codes, initial holdings,
and rows a later amendment restated - is listed with the reason it is excluded. The raw
transaction code is shown as reported but is never the signal: the economic class decides,
and a trade made under a reported Rule 10b5-1 plan is kept apart from the headline figure.
"""

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.ownership.insider_classify import INTERPRETATION
from steward.ownership.pdmr_read import pdmr_access_from_db
from steward.sources.base import AccessResult
from steward.sources.pdmr_uk import pdmr_access

LIMITATIONS = [
    "A Form 4 transaction code states how a trade was reported, not why it was made; the "
    "economic class combines the code with the footnotes and is a rule-based reading, not "
    "evidence of conviction.",
    "Net discretionary activity counts open-market purchases and sales of non-derivative "
    "stock only. Option exercises, grants, gifts, tax withholding, conversions and other "
    "codes are excluded; an exercise followed by a sale shows only the sale.",
    "Trades under a reported Rule 10b5-1 plan are shown separately: the decision predates "
    "the trade. Where the form does not state a plan status it is unknown, not 'no plan', "
    "and those trades stay in the discretionary figure.",
    "Indirect holdings (trusts, spouses, entities) are reported separately from direct "
    "holdings; a transfer between the two is not a market trade.",
    "Dollar values use the reported price per share and are left out of the value figures "
    "where the filing gives no price.",
    "A joint filing reports one set of rows for all owners; the first reporting owner's "
    "name and role are shown.",
    "Rows restated by a later /A amendment are excluded. An amendment whose original cannot "
    "be identified leaves both filings in place (flagged), so activity may be double counted "
    "until the original is found.",
    "Coverage is SEC Section 16 filings for US-reporting issuers; filings are due within two "
    "business days, so very recent trades may be missing.",
]


class InsiderTransactionRow(BaseModel):
    accession: str
    form: str
    filed: dt.date
    owner_name: str | None
    reporting_owner_cik: str
    insider_role: str
    officer_title: str | None
    ownership: Literal["direct", "indirect", "unknown"]
    security_title: str | None
    transaction_date: dt.date | None
    raw_transaction_code: str = Field(
        description="As reported on the form. Not a signal; read economic_class."
    )
    economic_class: str | None
    classification_basis: str | None
    interpretation: str
    shares: Decimal | None
    price_per_share: Decimal | None
    value: Decimal | None
    acquired_disposed: str | None
    shares_owned_after: Decimal | None
    derivative: bool
    exercise_price: Decimal | None
    exercisable_date: dt.date | None
    expiration_date: dt.date | None
    underlying_security_title: str | None
    underlying_shares: Decimal | None
    footnote_text: str | None
    rule_10b5_1_plan: bool | None
    rule_10b5_1_basis: str | None
    rule_10b5_1_adopted: dt.date | None
    superseded_by: str | None
    amendment_link_status: str
    counts_toward_net_discretionary: bool
    exclusion_reason: str | None
    source_url: str


class InsiderNetBreakdown(BaseModel):
    owner_name: str | None
    reporting_owner_cik: str
    insider_role: str
    ownership: Literal["direct", "indirect", "unknown"]
    bought_shares: Decimal
    sold_shares: Decimal
    net_shares: Decimal


class ExclusionOut(BaseModel):
    reason: str
    rows: int
    shares: Decimal = Field(description="Sum of the reported share counts of the excluded rows.")


class NetDiscretionary(BaseModel):
    since: dt.date
    until: dt.date
    bought_shares: Decimal
    sold_shares: Decimal
    net_shares: Decimal
    buy_rows: int
    sell_rows: int
    bought_value: Decimal | None
    sold_value: Decimal | None
    net_value: Decimal | None = Field(
        description="Bought minus sold at the reported prices; null unless every counted "
        "row has a price."
    )
    value_complete: bool
    plan_status_unknown_rows: int = Field(
        description="Counted rows whose Rule 10b5-1 status the form does not state."
    )
    plan_possible_rows: int = Field(
        default=0,
        description="Counted rows whose filing ticks the Rule 10b5-1 check box: some "
        "transaction in the filing was planned, the form does not say which.",
    )
    pre_planned_bought_shares: Decimal
    pre_planned_sold_shares: Decimal
    by_insider: list[InsiderNetBreakdown]
    by_ownership: dict[str, Decimal] = Field(
        description="Net shares by direct / indirect / unknown ownership."
    )
    exclusions: list[ExclusionOut]


class JurisdictionCoverage(BaseModel):
    jurisdiction: str
    source: str
    access: str
    detail: str | None


class InsiderActivityOut(BaseModel):
    ticker: str
    net_discretionary: NetDiscretionary
    transactions: list[InsiderTransactionRow]
    jurisdiction_coverage: list[JurisdictionCoverage]
    limitations: list[str]


def ownership_nature(value: str | None) -> Literal["direct", "indirect", "unknown"]:
    if value == "D":
        return "direct"
    if value == "I":
        return "indirect"
    return "unknown"


def insider_role(
    is_director: bool, is_officer: bool, is_ten_percent_owner: bool, title: str | None
) -> str:
    parts = []
    if is_officer:
        parts.append(f"officer ({title})" if title else "officer")
    if is_director:
        parts.append("director")
    if is_ten_percent_owner:
        parts.append("10% owner")
    return ", ".join(parts) or "other reporting person"


@dataclass(frozen=True)
class Verdict:
    counts: bool
    reason: str | None


def verdict(row: dict[str, Any]) -> Verdict:
    """Whether a row counts towards the headline figure, and if not why not."""
    if row["superseded_by_accession"]:
        return Verdict(False, "superseded_by_amendment")
    cls = row["economic_class"]
    if cls is None:
        return Verdict(False, "not_classified_legacy_row")
    if cls not in {"open_market_buy", "open_market_sell"}:
        return Verdict(False, cls)
    if row["derivative"]:
        return Verdict(False, "derivative_security")
    if row["rule_10b5_1_plan"] is True:
        return Verdict(False, "pre_planned_10b5_1")
    if row["shares"] is None or row["shares"] <= 0:
        return Verdict(False, "no_share_count")
    return Verdict(True, None)


def summarise(
    rows: list[dict[str, Any]], since: dt.date, until: dt.date
) -> tuple[NetDiscretionary, list[bool]]:
    """Net discretionary activity over already-windowed rows; second value flags each row."""
    bought = sold = Decimal(0)
    bought_value = sold_value = Decimal(0)
    buy_rows = sell_rows = unknown_plan = possible_plan = 0
    priced = True
    planned_bought = planned_sold = Decimal(0)
    by_insider: dict[tuple[str, str], dict[str, Any]] = {}
    exclusions: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal(0), Decimal(0)])
    flags: list[bool] = []
    for row in rows:
        v = verdict(row)
        flags.append(v.counts)
        shares = row["shares"] or Decimal(0)
        cls = row["economic_class"]
        if row["rule_10b5_1_plan"] is True and v.reason == "pre_planned_10b5_1":
            if cls == "open_market_buy":
                planned_bought += shares
            else:
                planned_sold += shares
        if not v.counts:
            reason = v.reason or "excluded"
            exclusions[reason][0] += 1
            exclusions[reason][1] += shares
            continue
        buy = cls == "open_market_buy"
        if row["rule_10b5_1_plan"] is None:
            unknown_plan += 1
            if row.get("rule_10b5_1_basis") == "form_checkbox":
                possible_plan += 1
        price = row["price_per_share"]
        if price is None or price <= 0:
            priced = False
        elif buy:
            bought_value += shares * price
        else:
            sold_value += shares * price
        if buy:
            bought += shares
            buy_rows += 1
        else:
            sold += shares
            sell_rows += 1
        nature = ownership_nature(row["direct_indirect"])
        entry = by_insider.setdefault(
            (row["reporting_owner_cik"], nature),
            {
                "owner_name": row["owner_name"],
                "reporting_owner_cik": row["reporting_owner_cik"],
                "insider_role": insider_role(
                    row["is_director"],
                    row["is_officer"],
                    row["is_ten_percent_owner"],
                    row["officer_title"],
                ),
                "ownership": nature,
                "bought_shares": Decimal(0),
                "sold_shares": Decimal(0),
            },
        )
        entry["bought_shares" if buy else "sold_shares"] += shares
    breakdown = [
        InsiderNetBreakdown(**e, net_shares=e["bought_shares"] - e["sold_shares"])
        for e in by_insider.values()
    ]
    breakdown.sort(key=lambda b: (-abs(b.net_shares), b.reporting_owner_cik))
    by_ownership: dict[str, Decimal] = {"direct": Decimal(0), "indirect": Decimal(0)}
    by_ownership["unknown"] = Decimal(0)
    for b in breakdown:
        by_ownership[b.ownership] += b.net_shares
    summary = NetDiscretionary(
        since=since,
        until=until,
        bought_shares=bought,
        sold_shares=sold,
        net_shares=bought - sold,
        buy_rows=buy_rows,
        sell_rows=sell_rows,
        bought_value=bought_value if priced and buy_rows else None,
        sold_value=sold_value if priced and sell_rows else None,
        net_value=(bought_value - sold_value) if priced and (buy_rows or sell_rows) else None,
        value_complete=priced,
        plan_status_unknown_rows=unknown_plan,
        plan_possible_rows=possible_plan,
        pre_planned_bought_shares=planned_bought,
        pre_planned_sold_shares=planned_sold,
        by_insider=breakdown,
        by_ownership=by_ownership,
        exclusions=[
            ExclusionOut(reason=r, rows=int(c[0]), shares=c[1])
            for r, c in sorted(exclusions.items())
        ],
    )
    return summary, flags


def coverage(access: AccessResult | None = None) -> list[JurisdictionCoverage]:
    pdmr = access or pdmr_access()
    return [
        JurisdictionCoverage(
            jurisdiction="US",
            source="SEC Forms 3, 4 and 5 (Section 16)",
            access="available",
            detail=None,
        ),
        JurisdictionCoverage(
            jurisdiction="GB",
            source="PDMR notifications under UK MAR Article 19 (regulatory announcements)",
            access=pdmr.state.value,
            detail=pdmr.detail,
        ),
    ]


async def holding_insider_activity(
    pool: asyncpg.Pool,
    ticker: str,
    *,
    since: dt.date | None = None,
    until: dt.date | None = None,
    limit: int = 200,
    known_by: dt.date | None = None,
) -> InsiderActivityOut:
    """`known_by` is a point-in-time cutoff on the filing date: filings after it, and the
    amendments that would have restated rows, are treated as not yet known."""
    until = until or dt.date.today()
    since = since or until - dt.timedelta(days=365)
    uk_access = await pdmr_access_from_db(pool)
    records = await pool.fetch(
        """select f.accession, f.form, f.filed, f.source_url,
                  case when $4::date is null or sup.filed <= $4::date
                       then f.superseded_by_accession end as superseded_by_accession,
                  f.amendment_link_status, t.*
           from public.insider_filing f join public.insider_transaction t using (accession)
           left join public.insider_filing sup on sup.accession = f.superseded_by_accession
           where (f.ticker = $1 or exists (select 1 from public.insider_filing_ticker l
                                           where l.accession = f.accession and l.ticker = $1))
             and coalesce(t.transaction_date, f.filed) between $2 and $3
             and ($4::date is null or f.filed <= $4::date)
           order by coalesce(t.transaction_date, f.filed) desc, f.filed desc,
                    f.accession desc, t.row_index""",
        ticker,
        since,
        until,
        known_by,
    )
    rows = [dict(r) for r in records]
    summary, counted = summarise(rows, since, until)
    out: list[InsiderTransactionRow] = []
    for row, counts in zip(rows[:limit], counted[:limit], strict=True):
        v = verdict(row)
        cls = row["economic_class"]
        shares, price = row["shares"], row["price_per_share"]
        out.append(
            InsiderTransactionRow(
                accession=row["accession"],
                form=row["form"],
                filed=row["filed"],
                owner_name=row["owner_name"],
                reporting_owner_cik=row["reporting_owner_cik"],
                insider_role=insider_role(
                    row["is_director"],
                    row["is_officer"],
                    row["is_ten_percent_owner"],
                    row["officer_title"],
                ),
                officer_title=row["officer_title"],
                ownership=ownership_nature(row["direct_indirect"]),
                security_title=row["security_title"],
                transaction_date=row["transaction_date"],
                raw_transaction_code=row["transaction_code"],
                economic_class=cls,
                classification_basis=row["classification_basis"],
                interpretation=INTERPRETATION.get(
                    cls or "", "Stored before classification was added; re-load to classify."
                ),
                shares=shares,
                price_per_share=price,
                value=shares * price if shares is not None and price is not None else None,
                acquired_disposed=row["acquired_disposed"],
                shares_owned_after=row["shares_owned_after"],
                derivative=row["derivative"],
                exercise_price=row["exercise_price"],
                exercisable_date=row["exercisable_date"],
                expiration_date=row["expiration_date"],
                underlying_security_title=row["underlying_security_title"],
                underlying_shares=row["underlying_shares"],
                footnote_text=row["footnote_text"],
                rule_10b5_1_plan=row["rule_10b5_1_plan"],
                rule_10b5_1_basis=row["rule_10b5_1_basis"],
                rule_10b5_1_adopted=row["rule_10b5_1_adopted"],
                superseded_by=row["superseded_by_accession"],
                amendment_link_status=row["amendment_link_status"],
                counts_toward_net_discretionary=counts,
                exclusion_reason=v.reason,
                source_url=row["source_url"],
            )
        )
    return InsiderActivityOut(
        ticker=ticker,
        net_discretionary=summary,
        transactions=out,
        jurisdiction_coverage=coverage(uk_access),
        limitations=list(LIMITATIONS),
    )
