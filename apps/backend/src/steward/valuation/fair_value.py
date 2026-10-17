# ruff: noqa: E501
"""Fair-value history recorder (insight plan I0.3) — the clock that cannot be rebuilt later.

The daily `fair_value_snapshot` job writes one `fair_value_point` row per owner, holding (and
watchlist name), basis and day from outputs the app already computes:

  dcf_default        the valuation tab's default base scenario (filed growth), always recomputed
  dcf_bear/base/bull the owner's latest saved DCF set, recomputed on today's inputs
  multiple_*         the owner's latest saved multiple-path set, recomputed on today's inputs
  thesis_target      the thesis base scenario value, else its sell target (owner-entered)
  peer_multiple      the peer-median P/E reference value of the valuation tab
  consensus_target   the latest stored consensus price target (mean, else median)

Nothing is back-filled: history starts the day the recorder first runs. A row is never updated;
a repeat run on the same day inserts nothing (unique owner, ticker, basis, day). Each row keeps
the statements hash it was computed from, so a later read can tell when the inputs moved.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import asyncpg

from steward.analytics.versions import version
from steward.jobs.runner import JobContext
from steward.valuation.drafted import OWNED_SET_SQL

METHOD = "fair_value_snapshot"
DCF_NAMES = ("bear", "base", "bull")


@dataclass
class Point:
    ticker: str
    basis: str
    value: Decimal
    currency: str | None
    price: Decimal | None
    source: str
    method_version: str
    input_hash: str | None = None
    inputs_as_of: dt.date | None = None
    scenario_id: int | None = None
    scenario_version: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def _iso(code: str | None) -> str | None:
    """Three-letter code for the row; GBX stays GBX (the price is quoted in pence)."""
    if not code:
        return None
    code = code.strip().upper()
    return code if len(code) == 3 and code.isalpha() else None


def scenario_basis(names: Sequence[str], selected: str | None, prefix: str) -> dict[str, str]:
    """Scenario name → basis. Named bear/base/bull map by name; otherwise the selected scenario
    (or the only one) is the base case."""
    out: dict[str, str] = {}
    for n in names:
        key = n.strip().lower()
        if key in DCF_NAMES:
            out[n] = f"{prefix}_{key}"
    if not any(b.endswith("_base") for b in out.values()):
        pick = selected if selected in names else (names[0] if len(names) == 1 else None)
        if pick is not None and pick not in out:
            out[pick] = f"{prefix}_base"
    return out


def thesis_point(ticker: str, target: Any) -> Point | None:
    """Thesis base scenario, else the sell target; in the Trading212 quote currency."""
    if target is None:
        return None
    s = getattr(target, "scenarios", None)
    value = s.base if s is not None else target.sell_target
    if value is None or value <= 0:
        return None
    return Point(
        ticker=ticker, basis="thesis_target", value=Decimal(str(value)), currency=_iso(target.currency), price=None,
        source="thesis", method_version=version(METHOD), inputs_as_of=target.set_on,
        detail={"kind": "scenario base" if s is not None else "sell target", "method": target.method, "set_on": target.set_on.isoformat()},
    )  # fmt: skip


async def _tickers(pool: asyncpg.Pool, owner_id: Any) -> list[str]:
    rows = await pool.fetch(
        """select ticker from public.position_snapshot
            where snapshot_date = (select max(snapshot_date) from public.position_snapshot) and quantity > 0
           union
           select ticker from public.watchlist_item where owner_id = $1""",
        owner_id,
    )
    return sorted({str(r["ticker"]) for r in rows})


async def _latest_saved(
    pool: asyncpg.Pool, owner_id: Any, ticker: str, method: str
) -> asyncpg.Record | None:
    return await pool.fetchrow(
        f"""select * from public.company_valuation_scenario
            where owner_id = $1 and ticker = $2 and method = $3 and {OWNED_SET_SQL}
            order by created_at desc, id desc limit 1""",  # noqa: S608
        owner_id, ticker, method,
    )  # fmt: skip


async def points_for(
    pool: asyncpg.Pool, owner_id: Any, ticker: str, theses: dict[str, Any]
) -> tuple[list[Point], list[str]]:
    """Every basis available for one ticker today, and the reasons the others are missing."""
    from steward.company import multiple_path, ratios, service, valuation

    pts: list[Point] = []
    missing: list[str] = []
    t = thesis_point(ticker, theses.get(ticker))
    if t is not None:
        pts.append(t)
    else:
        missing.append("thesis_target: no thesis price target")
    snap = await pool.fetchrow(
        """select as_of, source, target_mean, target_median, target_currency, analyst_count, price_at_snapshot
             from public.consensus_snapshot
            where ticker = $1 and coalesce(target_mean, target_median) is not null
            order by as_of desc, source limit 1""",
        ticker,
    )
    if snap is not None:
        pts.append(Point(
            ticker=ticker, basis="consensus_target", value=snap["target_mean"] or snap["target_median"],
            currency=_iso(snap["target_currency"]), price=snap["price_at_snapshot"], source=snap["source"],
            method_version=version("consensus_snapshot"), inputs_as_of=snap["as_of"],
            detail={"statistic": "mean" if snap["target_mean"] else "median", "analyst_count": snap["analyst_count"]},
        ))  # fmt: skip
    else:
        missing.append("consensus_target: no stored consensus price target")

    company = await service.load_company(pool, ticker)
    if company is None:
        missing.append("filed bases: not in the covered universe")
        return pts, missing
    annual = company.annual
    end = next((c.end for c in reversed(annual.columns) if c.kind == "annual"), None)
    statements = annual.input_hash
    peer_values, label, excluded = await service.peer_values_for(
        pool, company, "global", owner_id, None
    )
    view = ratios.build_ratios(
        ticker, annual, company.quarterly, company.market, sector=company.sector, industry=company.industry,
        peers=peer_values, peer_scope=label, peer_excluded=excluded,
    )  # fmt: skip
    common = {"statements_hash": statements}

    default = valuation.build_valuation(
        ticker,
        annual,
        company.quarterly,
        company.market,
        view,
        valuation.ValuationRequest(selected="base"),
    )
    base = next((s for s in default.scenarios if s.name == "base"), None)
    if (
        base is not None
        and base.value_per_share.value is not None
        and base.value_per_share.value > 0
    ):
        pts.append(Point(
            ticker=ticker, basis="dcf_default", value=base.value_per_share.value, currency=_iso(default.currency),
            price=default.price, source="valuation_tab", method_version=default.method_version,
            input_hash=base.value_per_share.input_hash, inputs_as_of=end, detail=common,
        ))  # fmt: skip
    else:
        missing.append(
            "dcf_default: "
            + (
                "; ".join(base.value_per_share.warnings)
                if base and base.value_per_share.warnings
                else "no default DCF value"
            )
        )

    peer_ref = next(
        (
            r
            for r in default.reference
            if r.method.startswith("Peer median") and r.basis.startswith("P/E")
        ),
        None,
    )
    if peer_ref is not None and peer_ref.mid is not None and peer_ref.mid > 0:
        pts.append(Point(
            ticker=ticker, basis="peer_multiple", value=peer_ref.mid, currency=_iso(default.currency), price=default.price,
            source="valuation_tab", method_version=default.method_version, inputs_as_of=end,
            detail={**common, "method": peer_ref.method},
        ))  # fmt: skip
    else:
        missing.append(
            "peer_multiple: "
            + (peer_ref.reason if peer_ref and peer_ref.reason else "no peer-median P/E value")
        )

    saved = await _latest_saved(pool, owner_id, ticker, "dcf")
    if saved is not None:
        body = (
            json.loads(saved["assumptions"])
            if isinstance(saved["assumptions"], str)
            else saved["assumptions"]
        )
        req = valuation.ValuationRequest(
            scenarios=[valuation.Assumptions(**a) for a in body["scenarios"]],
            selected=body.get("selected"),
        )
        val = valuation.build_valuation(
            ticker, annual, company.quarterly, company.market, view, req
        )
        mapping = scenario_basis([s.name for s in val.scenarios], req.selected, "dcf")
        for s in val.scenarios:
            b = mapping.get(s.name)
            if b and s.value_per_share.value is not None and s.value_per_share.value > 0:
                pts.append(Point(
                    ticker=ticker, basis=b, value=s.value_per_share.value, currency=_iso(val.currency), price=val.price,
                    source="saved_scenario", method_version=val.method_version, input_hash=s.value_per_share.input_hash,
                    inputs_as_of=end, scenario_id=saved["id"], scenario_version=saved["version"],
                    detail={**common, "scenario": s.name, "set": saved["name"], "saved_at": saved["created_at"].isoformat()},
                ))  # fmt: skip
    else:
        missing.append("dcf_*: no saved DCF scenario set")

    msaved = await _latest_saved(pool, owner_id, ticker, "multiple_path")
    if msaved is not None:
        body = (
            json.loads(msaved["assumptions"])
            if isinstance(msaved["assumptions"], str)
            else msaved["assumptions"]
        )
        mreq = multiple_path.MultipleRequest(
            scenarios=[multiple_path.MultipleAssumptions(**a) for a in body["scenarios"]],
            selected=body.get("selected"),
        )
        mval = multiple_path.build(ticker, annual, company.quarterly, company.market, view, mreq)
        mapping = scenario_basis([s.name for s in mval.scenarios], mreq.selected, "multiple")
        for ms in mval.scenarios:
            b = mapping.get(ms.name)
            if b and ms.value_per_share.value is not None and ms.value_per_share.value > 0:
                pts.append(Point(
                    ticker=ticker, basis=b, value=ms.value_per_share.value, currency=_iso(mval.currency), price=mval.price,
                    source="saved_scenario", method_version=mval.method_version, input_hash=ms.value_per_share.input_hash,
                    inputs_as_of=end, scenario_id=msaved["id"], scenario_version=msaved["version"],
                    detail={**common, "scenario": ms.name, "set": msaved["name"], "saved_at": msaved["created_at"].isoformat()},
                ))  # fmt: skip
    else:
        missing.append("multiple_*: no saved multiple-path set")
    return pts, missing


async def store(pool: asyncpg.Pool, owner_id: Any, points: Sequence[Point], day: dt.date) -> int:
    written = 0
    for p in points:
        status = await pool.execute(
            """insert into public.fair_value_point
               (owner_id, ticker, basis, value, currency, price, as_of, scenario_id, scenario_version,
                method_version, input_hash, inputs_as_of, source, detail)
               values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb)
               on conflict (owner_id, ticker, basis, as_of) do nothing""",
            owner_id, p.ticker, p.basis, p.value, p.currency, p.price if p.price and p.price > 0 else None, day,
            p.scenario_id, p.scenario_version, p.method_version, p.input_hash, p.inputs_as_of, p.source,
            json.dumps(p.detail, default=str),
        )  # fmt: skip
        written += str(status).endswith(" 1")
    return written


async def load_theses(pool: asyncpg.Pool) -> dict[str, Any]:
    from steward.config.store import load_active
    from steward.config.thesis import ThesesConfig

    stored = await load_active(pool, "theses", ThesesConfig)
    if stored is None:
        return {}
    return {t.ticker: t.price_target for t in stored.body.theses if t.price_target is not None}


async def record(pool: asyncpg.Pool, owner_id: Any, day: dt.date | None = None) -> dict[str, Any]:
    day = day or dt.date.today()
    theses = await load_theses(pool)
    out: dict[str, Any] = {}
    for ticker in await _tickers(pool, owner_id):
        try:
            pts, missing = await points_for(pool, owner_id, ticker, theses)
        except (ValueError, ArithmeticError) as exc:
            out[ticker] = {"failed": f"{type(exc).__name__}: {exc}"[:200]}
            continue
        out[ticker] = {
            "written": await store(pool, owner_id, pts, day),
            "bases": sorted(p.basis for p in pts),
            "missing": missing,
        }
    return out


async def fair_value_snapshot_handler(ctx: JobContext) -> None:
    """Daily: today's fair value on every basis for each holding and watchlist name."""
    from steward.settings import get_settings

    owner = get_settings().owner_user_id
    async with ctx.step("fair_value_snapshot") as step:
        if not owner:
            step.detail["skipped"] = "OWNER_USER_ID is not set"
            return
        from steward.valuation.fair_value_view import stale_alerts

        oid = uuid.UUID(str(owner))
        step.detail["tickers"] = await record(ctx.pool, oid)
        step.detail["stale_alerts"] = await stale_alerts(ctx.pool, oid)
