"""Customer / supplier / procurement disclosures: storage, contradictions and concentration.

A disclosure is one dated statement from one document about one counterparty. The unique key is
(ticker, relationship, direction, normalised counterparty or anonymous passage, fiscal period,
document url), so re-reading a filing updates a disclosure instead of duplicating it and a
decided (confirmed, rejected, disputed) row keeps its decision.

Shares carry their basis. A customer that "accounted for 12% of *our* revenue" in the issuer's
own filing (`issuer_revenue`) is concentration risk for the issuer; "the holding accounted for
12% of *the filer's* revenue" (`counterparty_revenue`) is the filer's concentration and says
nothing about the holding's. Only issuer-basis shares of confirmed, dated, directed
disclosures enter concentration.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, Literal

import asyncpg

Relationship = Literal["customer", "supplier", "procurement_award"]
Direction = Literal["issuer_sells_to", "issuer_buys_from", "award_to_issuer", "unknown"]
ShareBasis = Literal[
    "issuer_revenue",
    "issuer_purchases",
    "counterparty_revenue",
    "counterparty_purchases",
    "procurement_value",
    "other",
]
ISSUER_BASES = ("issuer_revenue", "issuer_purchases")
EXPECTED_DIRECTION: dict[str, str] = {
    "customer": "issuer_sells_to",
    "supplier": "issuer_buys_from",
    "procurement_award": "award_to_issuer",
}


async def upsert_disclosure(
    pool: asyncpg.Pool | asyncpg.Connection,
    *,
    ticker: str,
    relationship: Relationship,
    direction: Direction,
    passage: str,
    source_url: str,
    source: str,
    counterparty_key: str | None = None,
    counterparty_name: str | None = None,
    anonymous_counterparty: bool = False,
    reported_share: Decimal | None = None,
    share_basis: str | None = None,
    fiscal_period: str | None = None,
    valid_from: dt.date | None = None,
    valid_to: dt.date | None = None,
    evidence_id: int | None = None,
    status: str = "suggested",
    decision_reason: str | None = None,
    origin: str = "owner_review",
    claim_label: str | None = None,
    denominator_kind: str | None = None,
) -> int:
    """Insert or refresh a disclosure; a decided row keeps its status and reported figures.

    `claim_label` is the filing's own wording for an anonymous claim ("Customer A"); it is stored
    only for an anonymous claim and is never a real name."""
    if claim_label and not anonymous_counterparty:
        raise ValueError("a claim label belongs to an anonymous counterparty")
    if anonymous_counterparty and (counterparty_name or counterparty_key):
        raise ValueError("an anonymous counterparty carries no name or key")
    if not anonymous_counterparty and not (counterparty_name or counterparty_key):
        raise ValueError("a named counterparty needs a name or key")
    if valid_from and valid_to and valid_to < valid_from:
        raise ValueError("valid_to precedes valid_from")
    if reported_share is not None and not (Decimal(0) <= reported_share <= Decimal(1)):
        raise ValueError("reported_share must be a fraction between 0 and 1")
    row_id: int = await pool.fetchval(
        """insert into public.relationship_disclosure as t
           (ticker,relationship,direction,counterparty_key,counterparty_name,anonymous_counterparty,
            reported_share,share_basis,fiscal_period,valid_from,valid_to,evidence_id,passage,
            source_url,source,status,decision_reason,decided_at,origin,claim_label,
            denominator_kind)
           values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,
                  case when $16 in ('confirmed','rejected') then now() else null end,$18,$19,$20)
           on conflict (ticker,relationship,direction,
                        (coalesce(counterparty_key_norm, 'anon:' || md5(passage))),
                        (coalesce(fiscal_period, '')),source_url,
                        (coalesce(denominator_kind, '')))
           do update set
             counterparty_name=coalesce(t.counterparty_name,excluded.counterparty_name),
             claim_label=coalesce(t.claim_label,excluded.claim_label),
             evidence_id=coalesce(t.evidence_id,excluded.evidence_id),
             reported_share=case when t.status in ('confirmed','rejected','disputed')
                                 then t.reported_share
                                 else coalesce(excluded.reported_share,t.reported_share) end,
             share_basis=case when t.status in ('confirmed','rejected','disputed')
                              then t.share_basis
                              else coalesce(excluded.share_basis,t.share_basis) end,
             valid_from=case when t.status in ('confirmed','rejected','disputed') then t.valid_from
                             else coalesce(excluded.valid_from,t.valid_from) end,
             valid_to=case when t.status in ('confirmed','rejected','disputed') then t.valid_to
                           else coalesce(excluded.valid_to,t.valid_to) end,
             status=case when t.status in ('confirmed','rejected','disputed') then t.status
                         else excluded.status end,
             decided_at=case when t.status in ('confirmed','rejected','disputed')
                               then t.decided_at
                             when excluded.status in ('confirmed','rejected') then now()
                             else t.decided_at end,
             decision_reason=coalesce(excluded.decision_reason,t.decision_reason)
           returning id""",
        ticker,
        relationship,
        direction,
        counterparty_key,
        counterparty_name,
        anonymous_counterparty,
        reported_share,
        share_basis,
        fiscal_period,
        valid_from,
        valid_to,
        evidence_id,
        passage,
        source_url,
        source,
        status,
        decision_reason,
        origin,
        claim_label,
        denominator_kind,
    )
    return row_id


async def detect_contradictions(pool: asyncpg.Pool, disclosure_id: int) -> list[int]:
    """Record contradictions this disclosure creates; returns the new contradiction ids."""
    async with pool.acquire() as conn, conn.transaction():
        d = await conn.fetchrow(
            "select * from public.relationship_disclosure where id=$1", disclosure_id
        )
        if d is None or d["status"] == "rejected":
            return []
        found: list[tuple[str, int | None, dict[str, Any]]] = []
        expected = EXPECTED_DIRECTION.get(d["relationship"])
        if d["direction"] != "unknown" and expected and d["direction"] != expected:
            found.append(
                (
                    "direction_conflict",
                    None,
                    {"relationship": d["relationship"], "direction": d["direction"]},
                )
            )
        if d["reported_share"] is not None and d["fiscal_period"]:
            peers = await conn.fetch(
                """select id,reported_share,counterparty_name,anonymous_counterparty
                   from public.relationship_disclosure
                   where ticker=$1 and relationship=$2 and fiscal_period=$3 and id<>$4
                     and status<>'rejected' and share_basis is not distinct from $5
                     and reported_share is not null""",
                d["ticker"],
                d["relationship"],
                d["fiscal_period"],
                d["id"],
                d["share_basis"],
            )
            for p in peers:
                same_named = (
                    not d["anonymous_counterparty"]
                    and not p["anonymous_counterparty"]
                    and d["counterparty_key_norm"] is not None
                    and await conn.fetchval(
                        "select counterparty_key_norm=$2 from public.relationship_disclosure"
                        " where id=$1",
                        p["id"],
                        d["counterparty_key_norm"],
                    )
                )
                gap = abs(Decimal(p["reported_share"]) - Decimal(d["reported_share"]))
                if same_named and gap > Decimal("0.01"):
                    found.append(
                        (
                            "share_conflict",
                            p["id"],
                            {
                                "share": str(d["reported_share"]),
                                "other_share": str(p["reported_share"]),
                            },
                        )
                    )
                elif (
                    d["anonymous_counterparty"] != p["anonymous_counterparty"]
                ) and gap <= Decimal("0.005"):
                    found.append(
                        (
                            "named_vs_anonymous",
                            p["id"],
                            {
                                "share": str(d["reported_share"]),
                                "named": d["counterparty_name"] or p["counterparty_name"],
                            },
                        )
                    )
            if d["share_basis"] in ISSUER_BASES:
                total = await conn.fetchval(
                    """select coalesce(sum(reported_share),0) from public.relationship_disclosure
                       where ticker=$1 and relationship=$2 and fiscal_period=$3
                         and share_basis=$4 and status<>'rejected'
                         and reported_share is not null""",
                    d["ticker"],
                    d["relationship"],
                    d["fiscal_period"],
                    d["share_basis"],
                )
                if Decimal(total) > 1:
                    found.append(("share_total_exceeds_one", None, {"total": str(total)}))
        ids: list[int] = []
        for kind, other, detail in found:
            import json

            cid = await conn.fetchval(
                """insert into public.relationship_contradiction
                   (ticker,relationship,counterparty_key_norm,fiscal_period,kind,disclosure_id,
                    other_disclosure_id,detail)
                   values($1,$2,$3,$4,$5,$6,$7,$8::jsonb)
                   on conflict (disclosure_id, (coalesce(other_disclosure_id, 0)), kind)
                   do nothing returning id""",
                d["ticker"],
                d["relationship"],
                d["counterparty_key_norm"],
                d["fiscal_period"],
                kind,
                d["id"],
                other,
                json.dumps(detail),
            )
            if cid is not None:
                ids.append(int(cid))
        return ids


async def concentration(
    pool: asyncpg.Pool, ticker: str, as_of: dt.datetime | None = None
) -> list[dict[str, Any]]:
    """Customer / supplier concentration from confirmed, dated, directed, issuer-basis
    disclosures. Anonymous counterparties count (their share is real concentration) but are
    reported separately and never merged with named ones; unknown direction, other bases and
    unconfirmed rows are excluded."""
    rows = await pool.fetch(
        """select fiscal_period,relationship,share_basis,
              count(*) filter(where not anonymous_counterparty)::int named_count,
              count(*) filter(where anonymous_counterparty)::int anonymous_count,
              coalesce(sum(reported_share) filter(where not anonymous_counterparty),0)
                named_share_sum,
              coalesce(sum(reported_share) filter(where anonymous_counterparty),0)
                anonymous_share_sum,
              coalesce(sum(reported_share),0) reported_share_sum,
              count(*) filter(where reported_share is null)::int missing_share_count,
              max(reported_share) largest_share
           from public.relationship_disclosure
           where ticker=$1 and status='confirmed' and direction<>'unknown'
             and relationship in ('customer','supplier')
             and share_basis in ('issuer_revenue','issuer_purchases')
             and valid_from is not null and fiscal_period is not null
             and valid_from<=coalesce($2::date,current_date)
             and ($2::timestamptz is null or known_at<=$2)
           group by fiscal_period,relationship,share_basis
           order by fiscal_period desc,relationship""",
        ticker,
        as_of,
    )
    contradictions = await pool.fetch(
        """select fiscal_period,relationship,count(*)::int n
           from public.relationship_contradiction
           where ticker=$1 and status='open' group by fiscal_period,relationship""",
        ticker,
    )
    open_by_key = {(r["fiscal_period"], r["relationship"]): r["n"] for r in contradictions}
    out: list[dict[str, Any]] = []
    for r in rows:
        item = dict(r)
        item["share_exceeds_total"] = Decimal(item["reported_share_sum"]) > 1
        item["open_contradictions"] = open_by_key.get(
            (item["fiscal_period"], item["relationship"]), 0
        )
        out.append(item)
    return out


async def review_queue(
    pool: asyncpg.Pool, ticker: str | None = None, limit: int = 100
) -> dict[str, Sequence[Any]]:
    """Everything awaiting an owner decision: filing hits to read, suggested disclosures and
    open contradictions."""
    hits = await pool.fetch(
        """select id,ticker,filer_name,form,filed_at,url,review_state,review_state status,
                  review_note,attempts,disclosure_id
           from public.filing_hit
           where kind='relationship_candidate' and review_state in ('pending','fetched','failed')
             and ($1::text is null or ticker=$1)
           order by filed_at desc nulls last,id desc limit $2""",
        ticker,
        limit,
    )
    suggested = await pool.fetch(
        """select id,ticker,relationship,direction,counterparty_name,anonymous_counterparty,
                  reported_share,share_basis,fiscal_period,passage,source_url,known_at,origin,
                  status
           from public.relationship_disclosure
           where status in ('suggested','disputed') and ($1::text is null or ticker=$1)
           order by known_at desc,id desc limit $2""",
        ticker,
        limit,
    )
    contradictions = await pool.fetch(
        """select id,ticker,relationship,fiscal_period,kind,disclosure_id,other_disclosure_id,
                  detail,created_at,status
           from public.relationship_contradiction
           where status='open' and ($1::text is null or ticker=$1)
           order by created_at desc,id desc limit $2""",
        ticker,
        limit,
    )
    return {
        "filing_hits": [dict(r) for r in hits],
        "suggested_disclosures": [dict(r) for r in suggested],
        "contradictions": [dict(r) for r in contradictions],
    }


async def portfolio_knock_on(
    pool: asyncpg.Pool, as_of: dt.datetime | None = None
) -> list[dict[str, Any]]:
    """Confirmed, dated customer / supplier links between two *held* companies.

    A counterparty counts as another holding when its normalised name equals the normalised
    name or an alias of an open position. Only confirmed disclosures with a known direction and
    a start date, and confirmed customer / supplier edges, are used; shares are the
    issuer-basis shares as reported and are never imputed or summed across holdings."""
    held = await pool.fetch(
        """select i.t212_ticker ticker, i.name,
                  coalesce(array_agg(a.alias) filter (where a.alias is not null), '{}') aliases
           from public.instrument i left join public.entity_alias a
             on a.ticker=i.t212_ticker and a.kind in ('legal_name','other_name','alias')
           where i.t212_ticker in (select ticker from public.position_snapshot
             where snapshot_date=coalesce(
               (select max(snapshot_date) from public.account_snapshot),
               (select max(snapshot_date) from public.position_snapshot)))
           group by i.t212_ticker, i.name"""
    )
    by_name: dict[str, str] = {}
    for row in held:
        for name in [row["name"], *row["aliases"]]:
            if name:
                by_name.setdefault(
                    await pool.fetchval("select public.graph_norm_object_key($1)", name),
                    row["ticker"],
                )
    disclosures = await pool.fetch(
        """select ticker,relationship,direction,counterparty_name,counterparty_key_norm,
                  reported_share,share_basis,fiscal_period,valid_from,id
           from public.relationship_disclosure
           where status='confirmed' and direction<>'unknown' and valid_from is not null
             and not anonymous_counterparty and relationship in ('customer','supplier')
             and valid_from<=coalesce($1::date,current_date)
             and ($1::timestamptz is null or known_at<=$1)""",
        as_of,
    )
    edges = await pool.fetch(
        """select subject_key ticker,edge_type relationship,object_name counterparty_name,
                  object_key_norm counterparty_key_norm,valid_from,id
           from public.company_graph_edge
           where status='confirmed' and edge_type in ('customer','supplier')
             and valid_from is not null and valid_from<=coalesce($1::date,current_date)
             and (valid_to is null or valid_to>=coalesce($1::date,current_date))
             and ($1::timestamptz is null or known_at<=$1)""",
        as_of,
    )
    out: list[dict[str, Any]] = []
    for row in disclosures:
        other = by_name.get(row["counterparty_key_norm"] or "")
        if other and other != row["ticker"]:
            out.append(
                {
                    "ticker": row["ticker"],
                    "counterparty_ticker": other,
                    "relationship": row["relationship"],
                    "direction": row["direction"],
                    "reported_share": row["reported_share"],
                    "share_basis": row["share_basis"],
                    "fiscal_period": row["fiscal_period"],
                    "source": f"disclosure:{row['id']}",
                }
            )
    for edge in edges:
        other = by_name.get(edge["counterparty_key_norm"])
        if other and other != edge["ticker"]:
            out.append(
                {
                    "ticker": edge["ticker"],
                    "counterparty_ticker": other,
                    "relationship": edge["relationship"],
                    "direction": "issuer_sells_to"
                    if edge["relationship"] == "customer"
                    else "issuer_buys_from",
                    "reported_share": None,
                    "share_basis": None,
                    "fiscal_period": None,
                    "source": f"edge:{edge['id']}",
                }
            )
    return out
