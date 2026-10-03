"""Patent family storage with validated ownership paths, and the patent trend read model.

A patent family is attributed to a holding through (a) a direct applicant, (b) a *dated
subsidiary path*: the applicant is a subsidiary named by a **confirmed** `company_graph_edge`
whose validity window covers the family's priority date, or (c) a suggestion, which is stored
but never attributed. The path is re-validated at read time, so ending or rejecting the edge,
or a `valid_to` before the priority date, removes the family from the holding's trends.
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Any, Literal, cast

import asyncpg

from steward.graph.keys import normalize_object_key
from steward.graph.ops import LegalEvent

OwnershipBasis = Literal["direct", "dated_subsidiary_path", "suggested"]
LEGAL_STATUSES = ("application", "granted", "active", "expired", "unknown")


async def find_subsidiary_path(
    conn: asyncpg.Pool | asyncpg.Connection,
    *,
    ticker: str,
    applicant: str,
    priority_date: dt.date,
    as_of: dt.datetime | None = None,
) -> asyncpg.Record | None:
    """The confirmed subsidiary edge that covers `priority_date` for this applicant."""
    return await conn.fetchrow(
        """select e.id, e.valid_from, e.valid_to, e.object_name
           from public.company_graph_edge e
           where e.subject_key=$1 and e.edge_type='subsidiary' and e.status='confirmed'
             and (e.object_key_norm=$2 or public.graph_norm_object_key(e.object_name)=$2)
             and e.valid_from is not null and e.valid_from<=$3
             and (e.valid_to is null or e.valid_to>=$3)
             and ($4::timestamptz is null or e.known_at<=$4)
           order by e.valid_from desc, e.id limit 1""",
        ticker,
        normalize_object_key(applicant),
        priority_date,
        as_of,
    )


async def store_patent_family(
    pool: asyncpg.Pool,
    *,
    family_key: str,
    priority_date: dt.date | None,
    title: str | None,
    cpc_codes: list[str],
    jurisdictions: list[str],
    forward_citations: int | None,
    backward_citations: int,
    legal_status: str,
    source: str,
    source_url: str,
    ticker: str,
    applicant: str,
    ownership_confidence: Decimal,
    ownership_basis: OwnershipBasis,
    ownership_valid_from: dt.date | None = None,
    evidence_id: int | None = None,
    raw: dict[str, object] | None = None,
    family_id_source: Literal["ops_family_id", "publication", "owner"] = "publication",
    first_publication_date: dt.date | None = None,
    grant_date: dt.date | None = None,
    expiry_date: dt.date | None = None,
    legal_events: Sequence[LegalEvent] = (),
    publications: Iterable[tuple[str, str, str, dt.date | None]] = (),
) -> int:
    """Deduplicate a family across publications; only evidenced dated ownership is attributable."""
    path_edge_id: int | None = None
    owner_from, owner_to = ownership_valid_from, None
    async with pool.acquire() as conn, conn.transaction():
        if ownership_basis == "dated_subsidiary_path":
            if priority_date is None:
                raise ValueError("subsidiary patent attribution needs the family priority date")
            path = await find_subsidiary_path(
                conn, ticker=ticker, applicant=applicant, priority_date=priority_date
            )
            if path is None:
                raise ValueError(
                    "subsidiary patent attribution needs a confirmed subsidiary edge for "
                    f"{applicant!r} covering the priority date {priority_date}"
                )
            path_edge_id = int(path["id"])
            owner_from = path["valid_from"]
            owner_to = path["valid_to"]
        family_id: int = await conn.fetchval(
            """insert into public.patent_family as f
               (family_key,priority_date,first_publication_date,family_title,cpc_codes,jurisdictions,
                citations_forward,citations_backward,legal_status,source,source_url,known_at,raw,
                family_id_source,grant_date,expiry_date,legal_events)
               values($1,$2,$12,$3,$4,$5,$6,$7,$8,$9,$10,now(),$11::jsonb,$13,$14,$15,$16::jsonb)
               on conflict(family_key) do update set
                 priority_date=case when f.priority_date is null then excluded.priority_date
                   when excluded.priority_date is null then f.priority_date
                   else least(f.priority_date,excluded.priority_date) end,
                 first_publication_date=case when f.first_publication_date is null
                   then excluded.first_publication_date
                   when excluded.first_publication_date is null then f.first_publication_date
                   else least(f.first_publication_date,excluded.first_publication_date) end,
                 family_title=coalesce(f.family_title,excluded.family_title),
                 grant_date=case when f.grant_date is null then excluded.grant_date
                   when excluded.grant_date is null then f.grant_date
                   else least(f.grant_date,excluded.grant_date) end,
                 expiry_date=coalesce(excluded.expiry_date,f.expiry_date),
                 legal_events=case when excluded.legal_events='[]'::jsonb then f.legal_events
                                   else excluded.legal_events end,
                 family_id_source=case when excluded.family_id_source='ops_family_id'
                   then 'ops_family_id' else f.family_id_source end,
                 legal_status=case when excluded.legal_status='unknown'
                   then f.legal_status else excluded.legal_status end,
                 citations_forward=greatest(f.citations_forward,excluded.citations_forward),
                 citations_backward=greatest(f.citations_backward,excluded.citations_backward),
                 cpc_codes=(select coalesce(array_agg(distinct c),'{}') from unnest(
                   f.cpc_codes || excluded.cpc_codes) c),
                 jurisdictions=(select coalesce(array_agg(distinct j),'{}') from unnest(
                   f.jurisdictions || excluded.jurisdictions) j)
               returning id""",
            family_key,
            priority_date,
            title,
            cpc_codes,
            jurisdictions,
            forward_citations,
            backward_citations,
            legal_status,
            source,
            source_url,
            json.dumps(raw or {}, default=str),
            first_publication_date,
            family_id_source,
            grant_date,
            expiry_date,
            json.dumps(
                [
                    {
                        "code": e.code,
                        "description": e.description,
                        "date": e.date,
                        "infl": e.influence,
                    }
                    for e in legal_events
                ],
                default=str,
            ),
        )
        for country, number, kind, published in publications:
            await conn.execute(
                """insert into public.patent_publication
                   (family_id,country,doc_number,kind_code,publication_date)
                   values($1,$2,$3,$4,$5)
                   on conflict(country,doc_number,kind_code) do update set
                     family_id=excluded.family_id,
                     publication_date=coalesce(excluded.publication_date,
                                               patent_publication.publication_date)""",
                family_id,
                country,
                number,
                kind,
                published,
            )
        await conn.execute(
            """insert into public.patent_family_owner
               (family_id,ticker,applicant,ownership_confidence,ownership_basis,valid_from,
                valid_to,evidence_id,path_edge_id)
               values($1,$2,$3,$4,$5,$6,$7,$8,$9)
               on conflict(family_id,ticker,applicant) do update set
                 ownership_confidence=greatest(patent_family_owner.ownership_confidence,
                                                excluded.ownership_confidence),
                 ownership_basis=case when excluded.ownership_basis='suggested'
                   then patent_family_owner.ownership_basis else excluded.ownership_basis end,
                 valid_from=coalesce(excluded.valid_from,patent_family_owner.valid_from),
                 valid_to=coalesce(excluded.valid_to,patent_family_owner.valid_to),
                 path_edge_id=coalesce(excluded.path_edge_id,patent_family_owner.path_edge_id),
                 evidence_id=coalesce(patent_family_owner.evidence_id,excluded.evidence_id)""",
            family_id,
            ticker,
            applicant,
            ownership_confidence,
            ownership_basis,
            owner_from,
            owner_to,
            evidence_id,
            path_edge_id,
        )
    return family_id


# SQL predicate shared by every reader: which owner rows may be attributed at `$2`.
ATTRIBUTABLE_OWNER = """
  o.ticker=$1 and (
    (o.ownership_basis='direct'
        and (o.valid_from is null or o.valid_from<=coalesce($2::date,current_date))
        and (o.valid_to is null or o.valid_to>=coalesce($2::date,current_date)))
    or (o.ownership_basis='dated_subsidiary_path' and o.path_edge_id is not null
        and p.priority_date is not null
        and (o.valid_from is null or o.valid_from<=p.priority_date)
        and (o.valid_to is null or o.valid_to>=p.priority_date)
        and exists (
          select 1 from public.company_graph_edge e
          where e.id=o.path_edge_id and e.subject_key=o.ticker and e.edge_type='subsidiary'
            and coalesce(case when $2::timestamptz is null then e.status
                  else public.graph_status_as_of('company_graph_edge',e.id,$2) end,e.status)
                = 'confirmed'
            and e.valid_from is not null and e.valid_from<=p.priority_date
            and (e.valid_to is null or e.valid_to>=p.priority_date))))
"""


async def attributed_families(
    pool: asyncpg.Pool, ticker: str, as_of: dt.datetime | None = None
) -> list[dict[str, Any]]:
    """One row per family (a family with several owner rows is counted once)."""
    rows = await pool.fetch(
        f"""select distinct on (p.family_key) p.family_key,p.priority_date,p.cpc_codes,
                  p.citations_forward,p.citations_backward,p.legal_status,p.grant_date,
                  p.expiry_date,p.jurisdictions,p.first_publication_date,p.family_id_source
           from public.patent_family p join public.patent_family_owner o on o.family_id=p.id
           where {ATTRIBUTABLE_OWNER}
             and ($2::timestamptz is null or p.known_at<=$2)
           order by p.family_key,p.known_at""",  # noqa: S608 - constant predicate, bound params
        ticker,
        as_of,
    )
    return [dict(r) for r in rows]


def _years(a: dt.date, b: dt.date) -> float:
    return (b - a).days / 365.25


def patent_trend(
    rows: Iterable[Mapping[str, object]], *, today: dt.date | None = None
) -> list[dict[str, object]]:
    """Patent families by CPC code and priority year.

    * Each family counts once however many owner rows, publications or CPC symbols it has, and
      its citations are summed once (a family with two applicants used to double-count).
    * `legal_status_counts` splits applications, grants, active and expired rights.
    * Grant timing is the median years from priority to grant; expiry is the *recorded*
      expiry where known, else flagged as an estimate of 20 years from priority."""
    today = today or dt.date.today()
    families: dict[str, Mapping[str, object]] = {}
    for row in rows:
        families.setdefault(str(row["family_key"]), row)
    buckets: dict[tuple[str, int], dict[str, Any]] = {}
    for key, row in families.items():
        priority = row.get("priority_date")
        if not isinstance(priority, dt.date):
            continue
        codes = row.get("cpc_codes") or []
        if not isinstance(codes, (list, tuple)):
            continue
        status = str(row.get("legal_status") or "unknown")
        if status not in LEGAL_STATUSES:
            status = "unknown"
        grant = row.get("grant_date")
        expiry = row.get("expiry_date")
        expiry_estimated = False
        if not isinstance(expiry, dt.date) and status not in {"application", "unknown"}:
            try:
                expiry = priority.replace(year=priority.year + 20)
            except ValueError:
                expiry = priority + dt.timedelta(days=365 * 20 + 5)
            expiry_estimated = True
        for code in {str(c) for c in codes}:
            item = buckets.setdefault(
                (code, priority.year),
                {
                    "families": set(),
                    "forward_citations": 0,
                    "families_with_citation_data": 0,
                    "status": dict.fromkeys(LEGAL_STATUSES, 0),
                    "years_to_grant": [],
                    "expiring_5y": 0,
                    "expired": 0,
                    "expiry_estimated": False,
                },
            )
            item["families"].add(key)
            cited = row.get("citations_forward")
            if cited is not None:  # NULL = the source could not supply it: unknown, not 0
                item["forward_citations"] += int(cited)  # type: ignore[call-overload]
                item["families_with_citation_data"] += 1
            item["status"][status] += 1
            if isinstance(grant, dt.date) and grant >= priority:
                item["years_to_grant"].append(_years(priority, grant))
            if isinstance(expiry, dt.date):
                if expiry < today:
                    item["expired"] += 1
                elif expiry <= today.replace(year=today.year + 5):
                    item["expiring_5y"] += 1
                item["expiry_estimated"] = item["expiry_estimated"] or expiry_estimated
    return [
        {
            "cpc": cpc,
            "year": year,
            "families": len(cast("set[str]", item["families"])),
            "forward_citations": (
                item["forward_citations"] if item["families_with_citation_data"] else None
            ),
            "families_with_citation_data": item["families_with_citation_data"],
            "legal_status_counts": item["status"],
            "median_years_to_grant": (
                round(statistics.median(item["years_to_grant"]), 2)
                if item["years_to_grant"]
                else None
            ),
            "expiring_within_5y": item["expiring_5y"],
            "expired": item["expired"],
            "expiry_is_estimated": item["expiry_estimated"],
            "interpretation": "counts and citations are research signals, not patent value",
        }
        for (cpc, year), item in sorted(buckets.items())
    ]


def cpc_subclass(code: str) -> str | None:
    """`H01L21/02` -> `H01L` (section, class, subclass)."""
    cleaned = "".join(code.split())
    return cleaned[:4] if len(cleaned) >= 4 and cleaned[0].isalpha() else None


def competitor_overlap(
    own: Iterable[Mapping[str, object]],
    peers: Mapping[str, Iterable[Mapping[str, object]]],
) -> list[dict[str, object]]:
    """Technology overlap of the holding's families with each peer's, on CPC subclass.

    `overlap_share` is the part of the holding's families that has at least one subclass the
    peer also files in; `jaccard` compares the two subclass sets. Peers without attributed
    families are reported with `peer_families = 0`, never as zero overlap."""

    def subclasses(rows: Iterable[Mapping[str, object]]) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for row in rows:
            codes = row.get("cpc_codes") or []
            if not isinstance(codes, (list, tuple)):
                continue
            subs = {s for c in codes if (s := cpc_subclass(str(c)))}
            if subs:
                out.setdefault(str(row["family_key"]), set()).update(subs)
        return out

    mine = subclasses(own)
    my_set = set().union(*mine.values()) if mine else set()
    result: list[dict[str, object]] = []
    for peer, rows in sorted(peers.items()):
        theirs = subclasses(rows)
        their_set = set().union(*theirs.values()) if theirs else set()
        shared = my_set & their_set
        overlapping = sum(1 for subs in mine.values() if subs & their_set)
        union = my_set | their_set
        result.append(
            {
                "peer": peer,
                "peer_families": len(theirs),
                "own_families": len(mine),
                "overlap_share": (round(overlapping / len(mine), 4) if mine and theirs else None),
                "jaccard": round(len(shared) / len(union), 4) if union and theirs else None,
                "shared_subclasses": sorted(shared),
                "interpretation": "technology overlap on CPC subclass; not a freedom-to-operate"
                " or infringement assessment",
            }
        )
    return result


async def competitor_overlap_rows(
    pool: asyncpg.Pool, ticker: str, as_of: dt.datetime | None = None
) -> list[dict[str, object]]:
    """Overlap against peers the owner confirmed (peer_set) or marked as competitor edges whose
    object key is a ticker we hold patent data for."""
    peer_rows = await pool.fetch(
        """select peer_ticker t from public.peer_set
            where ticker=$1 and status='confirmed' and ($2::timestamptz is null or known_at<=$2)
           union
           select object_key from public.company_graph_edge
            where subject_key=$1 and edge_type='competitor' and status='confirmed'
              and ($2::timestamptz is null or known_at<=$2)""",
        ticker,
        as_of,
    )
    own = await attributed_families(pool, ticker, as_of)
    peers = {
        str(r["t"]): await attributed_families(pool, str(r["t"]), as_of)
        for r in peer_rows
        if str(r["t"]) != ticker
    }
    return competitor_overlap(own, peers)
