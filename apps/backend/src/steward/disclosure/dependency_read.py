"""P5: read dependency claims for one holding as of a date.

Reads `relationship_disclosure` in place. A claim's review state is resolved as of the date from
`graph_status_log`, so a claim confirmed after `as_of` is still a suggestion in an earlier view.

Beyond the holding's own claims the view also reads:
  * claims OTHER reporters made about this holding (so a second hop and a two-sided check exist);
  * owner-assigned weights, always labelled as such and never counted as reported;
  * the structured fields of procurement awards (a contract signal, never a revenue share);
  * a counterparty's entity: an identifier match, a candidate that needs review, or "ambiguous".
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import asyncpg

from steward.disclosure import dependencies as dep
from steward.disclosure import entities as ent

type Pool = asyncpg.Pool | asyncpg.Connection

STALE_DAYS = 548  # about 18 months: a claim this old is flagged, not hidden
CLAIM_COLUMNS = """id, ticker, relationship, direction, counterparty_key, counterparty_name,
                  anonymous_counterparty, reported_share, fiscal_period, valid_from, valid_to,
                  evidence_id, passage, source_url, status, known_at, claim_shape,
                  counterparty_count, denominator_kind, period_end, claim_label"""


def _aware(value: dt.datetime | None) -> dt.datetime:
    now = value or dt.datetime.now(dt.UTC)
    return now if now.tzinfo else now.replace(tzinfo=dt.UTC)


def _s(v: Decimal | None) -> str | None:
    return None if v is None else str(v)


def _shape(row: Any) -> str:
    if row["claim_shape"]:
        return str(row["claim_shape"])
    return "anonymous" if row["anonymous_counterparty"] else "named"


async def _status_as_of(pool: Pool, ids: list[int], as_of: dt.datetime) -> dict[int, str]:
    """Status of each claim at `as_of`; absent ids have never changed."""
    if not ids:
        return {}
    rows = await pool.fetch(
        """select row_id, from_status, to_status, changed_at
             from public.graph_status_log
            where table_name = 'relationship_disclosure' and row_id = any($1::bigint[])
            order by row_id, changed_at, id""",
        ids,
    )
    out: dict[int, str] = {}
    seen_first: set[int] = set()
    for r in rows:
        rid = int(r["row_id"])
        if r["changed_at"] <= as_of:
            out[rid] = r["to_status"]
        elif rid not in seen_first and rid not in out:
            out[rid] = r["from_status"] or "suggested"
        seen_first.add(rid)
    return out


def _to_dep(r: Any, state: str) -> dep.Dependency:
    shape = _shape(r)
    return dep.Dependency(
        id=int(r["id"]),
        reporter=r["ticker"],
        relationship=r["relationship"],
        shape=shape,  # type: ignore[arg-type]
        status=state,  # type: ignore[arg-type]
        known_at=r["known_at"],
        passage=r["passage"],
        label=None if shape == "named" else r["claim_label"] or r["counterparty_name"],
        counterparty_key=r["counterparty_key"] if shape == "named" else None,
        counterparty_name=r["counterparty_name"] if shape == "named" else None,
        counterparty_count=r["counterparty_count"],
        share=r["reported_share"],
        denominator=r["denominator_kind"],
        period_end=r["period_end"],
        fiscal_period=r["fiscal_period"],
        valid_from=r["valid_from"],
        valid_to=r["valid_to"],
        evidence_id=r["evidence_id"],
    )


async def _own_claims(
    pool: Pool, ticker: str, when: dt.datetime
) -> tuple[list[dep.Dependency], dict[int, Any]]:
    rows = await pool.fetch(
        f"select {CLAIM_COLUMNS} from public.relationship_disclosure"  # noqa: S608 - constants
        " where ticker = $1 and known_at <= $2 order by id",
        ticker,
        when,
    )
    states = await _status_as_of(pool, [int(r["id"]) for r in rows], when)
    deps = [_to_dep(r, states.get(int(r["id"]), r["status"])) for r in rows]
    return deps, {int(r["id"]): r for r in rows}


async def _issuer_for_ticker(pool: Pool, ticker: str) -> Any:
    return await pool.fetchrow(
        """select i.id, i.legal_name, i.cik, i.lei
             from public.instrument n join public.issuer i on i.cik = n.cik
            where n.t212_ticker = $1 limit 1""",
        ticker,
    )


async def _resolve_keys(
    pool: Pool, deps: list[dep.Dependency], when: dt.datetime
) -> tuple[dict[int, str], dict[int, ent.Resolution]]:
    """Entity key per named claim: its own `counterparty_key`, else a CONFIRMED reviewed mapping,
    else none. Unkeyed names get a Resolution so the page can say what is needed (a review)."""
    keys: dict[int, str] = {}
    resolved: dict[int, ent.Resolution] = {}
    unkeyed: list[dep.Dependency] = []
    for d in deps:
        if d.shape != "named":
            continue
        if d.counterparty_key:
            keys[d.id] = d.counterparty_key
        elif d.counterparty_name:
            unkeyed.append(d)
    if not unkeyed:
        return keys, resolved
    names = sorted({d.counterparty_name.lower() for d in unkeyed if d.counterparty_name})
    mapped = await pool.fetch(
        """select lower(m.mention) as mention, i.id, i.legal_name, i.cik, i.lei,
                  (select n.t212_ticker from public.instrument n where n.cik = i.cik limit 1) ticker
             from public.disclosure_entity_mapping m
             join public.issuer i on i.id = m.issuer_id
            where m.mention_kind = 'counterparty' and m.status = 'confirmed'
              and lower(m.mention) = any($1::text[])
              and (m.decided_at is null or m.decided_at <= $2)""",
        names,
        when,
    )
    confirmed = {m["mention"]: m for m in mapped}
    for d in unkeyed:
        m = confirmed.get((d.counterparty_name or "").lower())
        if m:
            keys[d.id] = ent.key_for(
                ent.IssuerRef(m["id"], m["legal_name"], m["cik"], m["lei"], m["ticker"])
            )
            resolved[d.id] = ent.Resolution("matched", (m["id"],), "registered_name")
            continue
        prefix = (ent.norm(d.counterparty_name or "").split() or [""])[0]
        if not prefix:
            continue
        issuers = await pool.fetch(
            """select id, legal_name, cik, lei from public.issuer
                where lower(legal_name) like $1 || '%' limit 50""",
            prefix,
        )
        aliases = await pool.fetch(
            """select issuer_id, name, kind, valid_from, valid_to from public.issuer_alias
                where lower(name) like $1 || '%' limit 50""",
            prefix,
        )
        resolved[d.id] = ent.resolve(
            d.counterparty_name,
            [ent.IssuerRef(i["id"], i["legal_name"], i["cik"], i["lei"]) for i in issuers],
            [
                ent.Alias(a["issuer_id"], a["name"], a["kind"], a["valid_from"], a["valid_to"])
                for a in aliases
            ],
            when.date(),
        )
    return keys, resolved


async def _inbound(
    pool: Pool, ticker: str, when: dt.datetime
) -> tuple[list[dep.Dependency], dict[int, Any]]:
    """What OTHER reporters said about this holding (by key, or by a confirmed reviewed mapping)."""
    issuer = await _issuer_for_ticker(pool, ticker)
    names: list[str] = []
    if issuer is not None:
        names = [
            r["mention"]
            for r in await pool.fetch(
                """select lower(mention) mention from public.disclosure_entity_mapping
                    where mention_kind = 'counterparty' and status = 'confirmed'
                      and issuer_id = $1""",
                issuer["id"],
            )
        ]
    rows = await pool.fetch(
        f"select {CLAIM_COLUMNS} from public.relationship_disclosure"  # noqa: S608 - constants
        """ where ticker <> $1 and known_at <= $2 and relationship in ('customer', 'supplier')
              and (counterparty_key = $1 or lower(counterparty_name) = any($3::text[]))
            order by id""",
        ticker,
        when,
        names,
    )
    states = await _status_as_of(pool, [int(r["id"]) for r in rows], when)
    deps = [_to_dep(r, states.get(int(r["id"]), r["status"])) for r in rows]
    return deps, {int(r["id"]): r for r in rows}


async def _owner_weights(pool: Pool, ticker: str, when: dt.datetime) -> list[Any]:
    return list(
        await pool.fetch(
            """select id, counterparty_key, counterparty_name, share, denominator_kind, as_of,
                      note, assigned_by, created_at
                 from public.owner_dependency_weight
                where ticker = $1 and relationship = 'customer' and created_at <= $2
                  and (retired_at is null or retired_at > $2)
                order by id""",
            ticker,
            when,
        )
    )


async def _awards(pool: Pool, ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not ids:
        return {}
    rows = await pool.fetch(
        """select disclosure_id, notice_number, lot_id, notice_type, cpv_codes, value, currency,
                  start_date, end_date, status, published
             from public.procurement_award_detail where disclosure_id = any($1::bigint[])
            order by notice_number, lot_id""",
        ids,
    )
    out: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(int(r["disclosure_id"]), []).append(
            {
                "notice_number": r["notice_number"],
                "lot_id": r["lot_id"] or None,
                "notice_type": r["notice_type"],
                "cpv_codes": list(r["cpv_codes"]),
                "value": _s(r["value"]),
                "currency": r["currency"],
                "start_date": r["start_date"],
                "end_date": r["end_date"],
                "status": r["status"],
                "published": r["published"],
            }
        )
    return out


def _scenario_json(s: dep.Scenario) -> dict[str, Any]:
    return {
        "origin": s.origin,
        "shock": str(s.shock),
        "impacts": [
            {
                "entity": i.entity,
                "hop": i.hop,
                "lower": str(i.lower),
                "upper": str(i.upper),
                "coverage": str(i.coverage),
                "via": list(i.via),
                "source_age_days": i.source_age_days,
                "uses_owner_assigned": i.uses_owner_assigned,
            }
            for i in s.impacts
        ],
        "non_quantified": [
            {"supplier": n.supplier, "customer": n.customer, "edge_id": n.edge_id, "note": n.note}
            for n in s.non_quantified
        ],
        "notes": list(s.notes),
    }


async def dependency_view(
    pool: Pool, ticker: str, as_of: dt.datetime | None = None, *, shock: Decimal = Decimal("0.2")
) -> dict[str, Any]:
    when = _aware(as_of)
    deps, raw = await _own_claims(pool, ticker, when)
    inbound, _ = await _inbound(pool, ticker, when)
    keys, resolutions = await _resolve_keys(pool, deps, when)
    weights = await _owner_weights(pool, ticker, when)
    live = dep.visible(deps, when)
    awards = await _awards(pool, [d.id for d in live if d.relationship == "procurement_award"])
    claims = []
    for d in live:
        r = raw[d.id]
        age = max(0, (when - d.known_at).days)
        res = resolutions.get(d.id)
        claims.append(
            {
                "id": d.id,
                "relationship": d.relationship,
                "shape": d.shape,
                "status": d.status,
                "counterparty": d.counterparty_name or d.label,
                "counterparty_key": keys.get(d.id) if d.shape == "named" else None,
                "counterparty_count": d.counterparty_count,
                "share": _s(d.share),
                "denominator": d.denominator,
                "period_end": d.period_end,
                "valid_from": d.valid_from,
                "valid_to": d.valid_to,
                "known_at": d.known_at,
                "passage": d.passage,
                "source_url": r["source_url"],
                "evidence_id": d.evidence_id,
                "problems": dep.problems(d),
                "screen": dep.screen_passage(d.passage),
                "stale": age > STALE_DAYS,
                "entity": None
                if res is None
                else {
                    "status": res.status,
                    "basis": res.basis,
                    "needs_review": res.needs_review,
                    "candidates": list(res.issuer_ids),
                    "note": res.note,
                },
                "awards": awards.get(d.id, []),
            }
        )
    conc = [
        {
            "relationship": c.relationship,
            "denominator": c.denominator,
            "period": c.period,
            "entries": [
                {
                    "id": e.id,
                    "label": e.label,
                    "shape": e.shape,
                    "share": str(e.share),
                    "evidence_id": e.evidence_id,
                    "known_at": e.known_at,
                }
                for e in c.entries
            ],
            "groups": [
                {"id": g.id, "label": g.label, "count": g.count, "share": str(g.share)}
                for g in c.groups
            ],
            "lower_bound": str(c.lower_bound),
            "reported_floor": str(c.reported_floor),
            "unknown_remainder": str(c.unknown_remainder),
            "largest": c.largest.label if c.largest else None,
            "exceeds_whole": c.exceeds_whole,
            "suggested_not_counted": c.suggested_not_counted,
            "notes": list(c.notes),
        }
        for c in dep.concentration(deps, when)
    ]
    # Scenario edges. Outbound: this holding's own named customers (a key from the filing or a
    # reviewed mapping). Inbound: other reporters' customer claims naming this holding. Owner
    # weights are added and labelled. Only a share of TOTAL revenue carries a number.
    edges: list[dep.Edge] = [
        dep.Edge(
            d.id,
            d.reporter,
            keys[d.id],
            d.share if d.denominator == "total_revenue" else None,
            d.status,
            d.known_at,
            d.valid_to,
        )
        for d in live
        if d.relationship == "customer" and d.shape == "named" and d.id in keys
    ]
    edges += [
        dep.Edge(
            d.id,
            d.reporter,
            ticker,
            d.share if d.denominator == "total_revenue" else None,
            d.status,
            d.known_at,
            d.valid_to,
        )
        for d in dep.visible(inbound, when)
        if d.relationship == "customer"
    ]
    edges += [
        dep.Edge(
            -int(w["id"]),
            ticker,
            w["counterparty_key"],
            w["share"] if w["denominator_kind"] == "total_revenue" else None,
            "confirmed",
            w["created_at"],
            None,
            owner_assigned=True,
        )
        for w in weights
    ]
    scenarios = [
        _scenario_json(dep.shock_scenario(edges, origin, shock, when))
        for origin in sorted({e.customer for e in edges})
    ]
    key_of = {**keys, **{d.id: ticker for d in inbound if d.shape == "named"}}
    pairwise = [
        d
        for d in [*dep.visible(deps, when), *dep.visible(inbound, when)]
        if d.status in ("confirmed", "suggested", "disputed")
    ]
    conflicts = [
        {"kind": c.kind, "claim_ids": [c.a_id, c.b_id], "note": c.note}
        for c in dep.reconcile(pairwise, key_of)
    ]
    notes: list[str] = []
    if not deps:
        notes.append("No relationship claims are known for this holding by that date.")
    unresolved = sum(
        1 for d in live if d.shape == "named" and d.relationship == "customer" and d.id not in keys
    )
    if unresolved:
        notes.append(
            f"{unresolved} named customer(s) have no entity key, so they cannot be placed in a "
            "scenario until a reviewer confirms who they are."
        )
    return {
        "ticker": ticker,
        "as_of": when,
        "claims": claims,
        "concentration": conc,
        "scenarios": scenarios,
        "conflicts": conflicts,
        "owner_weights": [
            {
                "id": int(w["id"]),
                "counterparty": w["counterparty_name"] or w["counterparty_key"],
                "counterparty_key": w["counterparty_key"],
                "share": str(w["share"]),
                "denominator": w["denominator_kind"],
                "as_of": w["as_of"],
                "note": w["note"],
                "assigned_by": w["assigned_by"],
            }
            for w in weights
        ],
        "inbound_claims": len(inbound),
        "notes": notes,
    }


async def portfolio_view(pool: Pool, as_of: dt.datetime | None = None) -> dict[str, Any]:
    """Portfolio-weighted dependency on named customers, from confirmed claims only."""
    when = _aware(as_of)
    held = await pool.fetch(
        """select ticker, value_account from public.position_snapshot
            where snapshot_date = (select max(snapshot_date) from public.account_snapshot
                                    where snapshot_date <= $1::date)""",
        when,
    )
    total = sum((Decimal(r["value_account"]) for r in held), Decimal(0))
    weights = {r["ticker"]: Decimal(r["value_account"]) / total for r in held if total > 0}
    deps: list[dep.Dependency] = []
    keys: dict[int, str] = {}
    for ticker in sorted(weights):
        own, _ = await _own_claims(pool, ticker, when)
        k, _res = await _resolve_keys(pool, own, when)
        deps += own
        keys.update(k)
    exposure = dep.portfolio_exposure(weights, deps, keys, when)
    return {
        "as_of": when,
        "holdings": len(weights),
        "covered_weight": str(exposure.covered_weight),
        "uncovered": list(exposure.uncovered),
        "flows": [
            {
                "holding": f.holding,
                "counterparty": f.counterparty,
                "weight": str(f.weight),
                "share": str(f.share),
                "exposure": str(f.exposure),
                "period": f.period,
                "claim_id": f.claim_id,
            }
            for f in exposure.flows
        ],
        "by_counterparty": {k: str(v) for k, v in exposure.by_counterparty.items()},
        "notes": list(exposure.notes),
    }


async def relationship_changes(
    pool: Pool, ticker: str, since: dt.datetime, now: dt.datetime, *, min_share: Decimal
) -> list[dict[str, Any]]:
    """New and ended material relationships between two moments, each with its source passage."""
    s, n = _aware(since), _aware(now)
    at_since, _ = await _own_claims(pool, ticker, s)
    at_now, raw = await _own_claims(pool, ticker, n)
    out: list[dict[str, Any]] = []
    for c in dep.changes(at_since, at_now, s, n, min_share=min_share):
        r = raw.get(c.dep.id)
        who = c.dep.counterparty_name or c.dep.label or "an unnamed counterparty"
        out.append(
            {
                "kind": c.kind,
                "claim_id": c.dep.id,
                "relationship": c.dep.relationship,
                "counterparty": who,
                "share": _s(c.dep.share),
                "passage": c.dep.passage,
                "source_url": r["source_url"] if r else None,
                "known_at": c.dep.known_at,
            }
        )
    return out
