# ruff: noqa: E501 - SQL and response text stay on one line each
"""Owner-only discovery: stock and fund screens over the covered universe, peer baselines,
saved screens, column sets, CSV export and universe snapshots (plan Phase 1).

`POST /discover/stocks` and `/discover/funds` only read: POST carries the filter body. Writes are
limited to saved screens, column sets and universe snapshots; broker data is never touched.
"""

import datetime as dt
import json
from typing import Annotated, Literal

import asyncpg
from fastapi import APIRouter, HTTPException, Path, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from api.auth import Owner
from api.db import Pool
from steward.analytics.contract import input_hash
from steward.analytics.versions import version
from steward.discovery import baselines as bl
from steward.discovery import build, composite, read
from steward.discovery.engine import DiscoveryResult, Missing, Record, run_discovery
from steward.discovery.export import to_csv
from steward.discovery.metrics import (
    FUND_CATALOGUE,
    STOCK_CATALOGUE,
    DiscoverFilter,
    MetricDef,
    metric_state,
    validate_filters,
)
from steward.discovery.universe import (
    UniverseMember,
    UniverseSnapshot,
    describe_universe,
    freeze,
    scope_for_date,
)

router = APIRouter(prefix="/discover", tags=["discover"])
MAX_FILTERS = 12

UNIVERSE_TEXT = {
    "covered": (
        "Covered universe",
        "Instruments the app has identity for, with whatever data sits behind each. Not a market-wide universe.",
    ),
    "watchlist": ("Watchlist", "Covered instruments on your watchlist."),
    "holdings": ("Holdings", "Covered instruments in the latest portfolio snapshot."),
    "snapshot": (
        "Frozen universe",
        "Membership as stored in a dated universe snapshot, delisted names included.",
    ),
}
STOCK_CAVEATS = [
    "Only instruments the app covers can appear; counts show how many have data for each filter.",
    "Each issuer is measured on its own latest annual period and accounting basis; compare the period end shown.",
    "Returns use stored adjusted closes; market cap and yields need price and statement currency to match.",
    "Sector and industry come from provider profiles where fetched; blank means not fetched, not 'none'.",
]
FUND_CAVEATS = [
    "Funds appear only where an issuer holdings file or factsheet is stored; absent fees are unknown, never zero.",
    "Asset class and concentration are StewardAI proxies from published holdings, not a category rating.",
]


class StockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    universe: Literal["covered", "watchlist", "holdings", "snapshot"] = "covered"
    snapshot_id: int | None = None
    filters: Annotated[list[DiscoverFilter], Field(max_length=MAX_FILTERS)] = Field(
        default_factory=list
    )
    missing: Missing = "exclude"
    sort_by: str | None = None
    descending: bool | None = None
    limit: Annotated[int, Field(ge=1, le=200)] = 50
    cursor: str | None = None
    as_of: dt.date | None = Field(
        default=None,
        description="Data date. Past dates use interval membership and facts known by then.",
    )
    known_at: dt.datetime | None = Field(
        default=None, description="Only facts known by this time are used."
    )
    reconciled_only: bool = False
    preset: Literal["idea_pipeline"] | None = Field(
        default=None, description="Apply the saved idea-pipeline rule on top of the filters."
    )


class FundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filters: Annotated[list[DiscoverFilter], Field(max_length=MAX_FILTERS)] = Field(
        default_factory=list
    )
    missing: Missing = "exclude"
    sort_by: str | None = None
    descending: bool | None = None
    limit: Annotated[int, Field(ge=1, le=200)] = 50
    cursor: str | None = None
    as_of: dt.date | None = None


class MetricInfo(BaseModel):
    id: str
    label: str
    group: str
    kind: str
    unit: str
    formula: str
    period: str
    definition_version: str
    enabled: bool
    disabled_reason: str | None
    known: int
    total: int


class CatalogueResponse(BaseModel):
    kind: Literal["stock", "fund"]
    as_of: dt.date
    universe: dict[str, object]
    metrics: list[MetricInfo]
    snapshots: list[dict[str, object]]
    warnings: list[str]
    method_version: str
    keyless_note: str


def _today() -> dt.date:
    return dt.datetime.now(dt.UTC).date()


def _422(problems: list[str]) -> HTTPException:
    return HTTPException(422, detail=problems)


def _coverage(records: list[Record], catalogue: dict[str, MetricDef]) -> dict[str, int]:
    return {
        m: sum(1 for r in records if (v := r.values.get(m)) is not None and v.value is not None)
        for m in catalogue
    }


async def _scope(
    pool: asyncpg.Pool, owner_id: object, req: StockRequest, as_of: dt.date
) -> tuple[list[UniverseMember], str, list[str], int | None]:
    members = await read.load_universe(pool)
    warnings: list[str] = []
    snapshot_id: int | None = None
    if req.universe == "snapshot":
        sid = req.snapshot_id or await read.snapshot_id_for(pool, as_of)
        if sid is None:
            raise HTTPException(
                404, "no universe snapshot exists on or before that date; freeze one first"
            )
        snap = await read.load_snapshot(pool, sid)
        if snap is None:
            raise HTTPException(404, "universe snapshot not found")
        by_ticker = {m.ticker: m for m in members}
        scoped = [by_ticker.get(m.ticker, m) for m in snap.members]
        snapshot_id = sid
        warnings.append(f"membership frozen on {snap.as_of}; facts are as known at the data date")
        return scoped, "snapshot", warnings, snapshot_id
    scoped, warnings = scope_for_date(members, as_of, _today())
    if req.universe == "watchlist":
        wanted = await read.watchlist_tickers(pool, owner_id)
        scoped = [m for m in scoped if m.ticker in wanted]
    elif req.universe == "holdings":
        wanted = await read.holding_tickers(pool)
        scoped = [m for m in scoped if m.ticker in wanted]
    return scoped, req.universe, warnings, snapshot_id


async def _stock_records(
    pool: asyncpg.Pool,
    members: list[UniverseMember],
    req_as_of: dt.date,
    known_at: dt.datetime | None,
    reconciled_only: bool,
    access: dict[tuple[str, str], str],
) -> tuple[list[Record], dict[str, composite.QvmScore]]:
    records = await read.stock_records(
        pool, members, req_as_of, cutoff=known_at, reconciled_only=reconciled_only, access=access
    )
    scores = composite.compute_qvm(records)
    build.apply_scores(records, scores)
    build.finalize_coverage(records)
    return records, scores


@router.get("/catalogue", response_model=CatalogueResponse)
async def catalogue(
    pool: Pool,
    owner: Owner,
    kind: Literal["stock", "fund"] = "stock",
    as_of: dt.date | None = None,
) -> CatalogueResponse:
    """Every metric with its definition, whether a filter on it is enabled, and how many members
    of the universe have a value, so coverage is visible before a filter is applied."""
    day = as_of or _today()
    access = await read.provider_states(pool)
    snaps = [
        {
            "id": r["id"],
            "as_of": r["as_of"],
            "known_at": r["known_at"],
            "members": r["member_count"],
            "active": r["active_count"],
        }
        for r in await pool.fetch(
            "select id, as_of, known_at, member_count, active_count from public.universe_snapshot order by as_of desc limit 50"
        )
    ]
    if kind == "fund":
        funds = await read.fund_inputs(pool, day)
        records = [build.fund_record(f, day) for f in funds]
        cat: dict[str, MetricDef] = FUND_CATALOGUE
        info: dict[str, object] = {
            "members": len(records),
            "kind": "fund",
            "with_holdings": sum(1 for f in funds if f.holdings),
            "with_factsheet": sum(1 for f in funds if f.factsheet),
        }
        warns: list[str] = []
    else:
        members, warns = scope_for_date(await read.load_universe(pool), day, _today())
        records, _ = await _stock_records(pool, members, day, None, False, access)
        cat = STOCK_CATALOGUE
        info = describe_universe(members)
    cover = _coverage(records, cat)
    items = []
    for m in cat.values():
        enabled, why = metric_state(m, access=access, coverage=cover, total=len(records))
        items.append(
            MetricInfo(
                id=m.id,
                label=m.label,
                group=m.group,
                kind=m.kind,
                unit=m.unit,
                formula=m.formula,
                period=m.period,
                definition_version=m.definition_version,
                enabled=enabled,
                disabled_reason=why,
                known=cover[m.id],
                total=len(records),
            )
        )
    return CatalogueResponse(
        kind=kind,
        as_of=day,
        universe=info,
        metrics=items,
        snapshots=snaps,
        warnings=warns,
        method_version=version("screen"),
        keyless_note=(
            "Statement, price, identity and fund metrics need no API key. Sector/industry need the FMP profile job; "
            "forward P/E needs an accessible FMP estimates endpoint. A metric whose source is missing shows as disabled, not as zero."
        ),
    )


@router.post("/stocks", response_model=DiscoveryResult)
async def discover_stocks(body: StockRequest, pool: Pool, owner: Owner) -> DiscoveryResult:
    return await _run_stock(pool, owner.user_id, body)


async def _run_stock(pool: asyncpg.Pool, owner_id: object, body: StockRequest) -> DiscoveryResult:
    as_of = body.as_of or _today()
    access = await read.provider_states(pool)
    members, key, warnings, _sid = await _scope(pool, owner_id, body, as_of)
    records, scores = await _stock_records(
        pool, members, as_of, body.known_at, body.reconciled_only, access
    )
    problems = validate_filters(
        body.filters,
        STOCK_CATALOGUE,
        access=access,
        coverage=_coverage(records, STOCK_CATALOGUE),
        total=len(records),
    )
    messages = [f"{p.metric}: {p.message}" for p in problems]
    if body.sort_by is not None and body.sort_by not in STOCK_CATALOGUE:
        messages.append(f"{body.sort_by}: unknown sort metric")
    if messages:
        raise _422(messages)
    gate = notes = None
    if body.preset == "idea_pipeline":
        verdicts = composite.idea_pipeline(records, scores)
        gate = {k: v.reasons for k, v in verdicts.items()}
    notes = {
        r.key: [
            f"composite {scores[r.key].score:.1f} ("
            + ", ".join(
                f"{n} {c:.2f} [{scores[r.key].bases.get(n, '')}]"
                for n, c in scores[r.key].components.items()
                if c is not None
            )
            + ")"
        ]
        for r in records
        if r.key in scores and scores[r.key].score is not None
    }
    label, definition = UNIVERSE_TEXT[key]
    result = run_discovery(
        records,
        STOCK_CATALOGUE,
        body.filters,
        kind="stock",
        as_of=as_of,
        universe_key=key,
        universe_label=label,
        universe_definition=definition,
        missing=body.missing,
        sort_by=body.sort_by,
        descending=body.descending,
        limit=body.limit,
        cursor=body.cursor,
        caveats=STOCK_CAVEATS,
        gate=gate,
        gate_label="Idea pipeline",
        notes=notes,
    )
    result.warnings.extend(warnings)
    return result


@router.post("/funds", response_model=DiscoveryResult)
async def discover_funds(body: FundRequest, pool: Pool, owner: Owner) -> DiscoveryResult:
    return await _run_fund(pool, body)


async def _run_fund(pool: asyncpg.Pool, body: FundRequest) -> DiscoveryResult:
    as_of = body.as_of or _today()
    funds = await read.fund_inputs(pool, as_of)
    records = [build.fund_record(f, as_of) for f in funds]
    access = await read.provider_states(pool)
    problems = validate_filters(
        body.filters,
        FUND_CATALOGUE,
        access=access,
        coverage=_coverage(records, FUND_CATALOGUE),
        total=len(records),
    )
    if problems:
        raise _422([f"{p.metric}: {p.message}" for p in problems])
    return run_discovery(
        records,
        FUND_CATALOGUE,
        body.filters,
        kind="fund",
        as_of=as_of,
        universe_key="funds",
        universe_label="Covered funds",
        universe_definition="Funds with a stored issuer holdings file or factsheet.",
        missing=body.missing,
        sort_by=body.sort_by,
        descending=body.descending,
        limit=body.limit,
        cursor=body.cursor,
        caveats=FUND_CAVEATS,
    )


class ExportRequest(BaseModel):
    kind: Literal["stock", "fund"] = "stock"
    stock: StockRequest | None = None
    fund: FundRequest | None = None
    columns: Annotated[list[str], Field(min_length=1, max_length=40)]


@router.post("/export", response_class=Response)
async def export_csv(body: ExportRequest, pool: Pool, owner: Owner) -> Response:
    """CSV of the whole result (up to 200 rows per call; a `# truncated` line says if more match),
    with method version, hashes, data date and filters as header comments."""
    stamp = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    if body.kind == "fund":
        result = await _run_fund(pool, body.fund or FundRequest(limit=200))
        cat: dict[str, MetricDef] = FUND_CATALOGUE
    else:
        req = (body.stock or StockRequest()).model_copy(update={"limit": 200, "cursor": None})
        result = await _run_stock(pool, owner.user_id, req)
        cat = STOCK_CATALOGUE
    bad = [c for c in body.columns if c not in cat]
    if bad:
        raise _422([f"unknown column: {c}" for c in bad])
    text = to_csv(result, cat, body.columns, generated_at=stamp)
    return Response(
        text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="discover.csv"'},
    )


@router.get("/baselines", response_model=list[bl.Baseline])
async def peer_baselines(
    pool: Pool,
    owner: Owner,
    scope: Literal["home_market", "global", "saved_set"] = "global",
    country: Annotated[str | None, Query(pattern=r"^[A-Za-z]{2}$")] = None,
    tickers: Annotated[
        str | None, Query(description="Comma-separated tickers for saved_set.")
    ] = None,
    peers_of: Annotated[
        str | None, Query(description="Use the confirmed peers of this ticker as the saved set.")
    ] = None,
    metrics: Annotated[
        str, Query(description="Comma-separated metric ids.")
    ] = "gross_margin,operating_margin,roic,net_debt_to_equity",
    basis: Literal["US_GAAP", "IFRS"] | None = None,
    as_of: dt.date | None = None,
) -> list[bl.Baseline]:
    """Median and range of covered securities for each metric, with count, geographic scope and
    every exclusion (stale, other accounting basis, no value)."""
    day = as_of or _today()
    wanted = [m.strip() for m in metrics.split(",") if m.strip()]
    unknown = [m for m in wanted if m not in STOCK_CATALOGUE]
    if unknown:
        raise _422([f"unknown metric: {m}" for m in unknown])
    saved: list[str] = [t.strip() for t in (tickers or "").split(",") if t.strip()]
    if peers_of:
        rows = await pool.fetch(
            "select peer_ticker from public.peer_set where ticker=$1 and status='confirmed'"
            " and (valid_to is null or valid_to >= $2)",
            peers_of,
            day,
        )
        saved += [r["peer_ticker"] for r in rows]
    if scope == "saved_set" and not saved:
        raise _422(["saved_set needs tickers or a ticker with confirmed peers"])
    if scope == "home_market" and not country:
        raise _422(["home_market needs a country"])
    access = await read.provider_states(pool)
    members, _ = scope_for_date(await read.load_universe(pool), day, _today())
    records, _scores = await _stock_records(pool, members, day, None, False, access)
    return bl.baselines(
        records,
        [STOCK_CATALOGUE[m] for m in wanted],
        scope=scope,
        as_of=day,
        home_country=country,
        saved_tickers=saved,
        basis=basis,
    )


# --------------------------------------------------------------------------------------
# Universe snapshots
# --------------------------------------------------------------------------------------


class SnapshotCreated(BaseModel):
    id: int
    as_of: dt.date
    members: int
    active: int


@router.post(
    "/universe/snapshots", response_model=SnapshotCreated, status_code=status.HTTP_201_CREATED
)
async def freeze_universe(pool: Pool, owner: Owner) -> SnapshotCreated:
    """Freeze today's covered universe (delisted members stay in later reads of past dates)."""
    today = _today()
    members = await read.load_universe(pool)
    snap: UniverseSnapshot = freeze(members, today, dt.datetime.now(dt.UTC))
    sid = await read.write_snapshot(pool, snap)
    return SnapshotCreated(
        id=sid,
        as_of=today,
        members=len(snap.members),
        active=sum(1 for m in snap.members if m.status == "active"),
    )


# --------------------------------------------------------------------------------------
# Saved screens
# --------------------------------------------------------------------------------------


class ScreenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=120)]
    kind: Literal["stock", "fund"] = "stock"
    stock: StockRequest | None = None
    fund: FundRequest | None = None


class ScreenSummary(BaseModel):
    id: int
    name: str
    kind: str
    latest_version: int
    updated_at: dt.datetime
    definition_hash: str | None = None


class ScreenVersion(BaseModel):
    version: int
    definition: dict[str, object]
    definition_hash: str
    method_version: str
    created_at: dt.datetime


class ScreenDetail(ScreenSummary):
    versions: list[ScreenVersion]
    runs: list[dict[str, object]]


def _definition(body: ScreenBody) -> dict[str, object]:
    req = body.stock if body.kind == "stock" else body.fund
    if req is None:
        raise _422([f"a {body.kind} definition is required"])
    data = req.model_dump(mode="json", exclude={"cursor", "limit"})
    if body.kind == "stock":  # a saved screen is reproduced as of the run date, not frozen to today
        data["as_of"] = None
    return data


def _screen_hash(kind: str, definition: dict[str, object]) -> str:
    return input_hash({"kind": kind, "definition": definition, "method": version("screen")})


@router.get("/screens", response_model=list[ScreenSummary])
async def list_screens(
    pool: Pool, owner: Owner, kind: Literal["stock", "fund"] | None = None
) -> list[ScreenSummary]:
    rows = await pool.fetch(
        """select d.id, d.name, d.kind, d.latest_version, d.updated_at, v.definition_hash
           from public.screen_definition d join public.screen_definition_version v
             on v.screen_id = d.id and v.version = d.latest_version
           where d.owner_id=$1 and not d.archived and ($2::text is null or d.kind=$2) order by d.name""",
        owner.user_id,
        kind,
    )
    return [ScreenSummary(**dict(r)) for r in rows]


@router.post("/screens", response_model=ScreenSummary, status_code=status.HTTP_201_CREATED)
async def save_screen(body: ScreenBody, pool: Pool, owner: Owner) -> ScreenSummary:
    definition = _definition(body)
    digest = _screen_hash(body.kind, definition)
    async with pool.acquire() as conn, conn.transaction():
        try:
            row = await conn.fetchrow(
                "insert into public.screen_definition (owner_id, name, kind) values ($1,$2,$3)"
                " returning id, name, kind, latest_version, updated_at",
                owner.user_id,
                body.name.strip(),
                body.kind,
            )
        except asyncpg.UniqueViolationError as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "A screen with this name already exists; save a new version instead",
            ) from exc
        await conn.execute(
            "insert into public.screen_definition_version (screen_id, version, definition, definition_hash, method_version)"
            " values ($1,1,$2::jsonb,$3,$4)",
            row["id"],
            _json(definition),
            digest,
            version("screen"),
        )
    return ScreenSummary(**dict(row), definition_hash=digest)


def _json(value: object) -> str:
    return json.dumps(value, default=str)


@router.put("/screens/{screen_id}", response_model=ScreenSummary)
async def save_screen_version(
    screen_id: Annotated[int, Path(ge=1)], body: ScreenBody, pool: Pool, owner: Owner
) -> ScreenSummary:
    """A changed definition becomes a new immutable version; an unchanged one is a no-op."""
    definition = _definition(body)
    digest = _screen_hash(body.kind, definition)
    async with pool.acquire() as conn, conn.transaction():
        cur = await conn.fetchrow(
            "select d.id, d.kind, d.latest_version, v.definition_hash from public.screen_definition d"
            " join public.screen_definition_version v on v.screen_id=d.id and v.version=d.latest_version"
            " where d.id=$1 and d.owner_id=$2 and not d.archived for update of d",
            screen_id,
            owner.user_id,
        )
        if cur is None:
            raise HTTPException(404, "Screen not found")
        if cur["kind"] != body.kind:
            raise _422(["a screen cannot change between stock and fund"])
        next_version = cur["latest_version"]
        if cur["definition_hash"] != digest:
            next_version += 1
            await conn.execute(
                "insert into public.screen_definition_version (screen_id, version, definition, definition_hash, method_version)"
                " values ($1,$2,$3::jsonb,$4,$5)",
                screen_id,
                next_version,
                _json(definition),
                digest,
                version("screen"),
            )
        row = await conn.fetchrow(
            "update public.screen_definition set latest_version=$2, name=$3 where id=$1"
            " returning id, name, kind, latest_version, updated_at",
            screen_id,
            next_version,
            body.name.strip(),
        )
    return ScreenSummary(**dict(row), definition_hash=digest)


@router.get("/screens/{screen_id}", response_model=ScreenDetail)
async def screen_detail(
    screen_id: Annotated[int, Path(ge=1)], pool: Pool, owner: Owner
) -> ScreenDetail:
    head = await pool.fetchrow(
        "select id, name, kind, latest_version, updated_at from public.screen_definition where id=$1 and owner_id=$2 and not archived",
        screen_id,
        owner.user_id,
    )
    if head is None:
        raise HTTPException(404, "Screen not found")
    versions = await pool.fetch(
        "select version, definition, definition_hash, method_version, created_at from public.screen_definition_version"
        " where screen_id=$1 order by version desc",
        screen_id,
    )
    runs = await pool.fetch(
        "select id, screen_version, run_at, as_of_cutoff, universe_key, universe_size, result_count, unknown_count,"
        " excluded_count, definition_hash, result_hash from public.screen_run where screen_id=$1 and owner_id=$2"
        " order by run_at desc limit 20",
        screen_id,
        owner.user_id,
    )
    parsed = [
        ScreenVersion(
            version=v["version"],
            definition=json.loads(v["definition"])
            if isinstance(v["definition"], str)
            else v["definition"],
            definition_hash=v["definition_hash"],
            method_version=v["method_version"],
            created_at=v["created_at"],
        )
        for v in versions
    ]
    return ScreenDetail(
        **dict(head),
        definition_hash=parsed[0].definition_hash if parsed else None,
        versions=parsed,
        runs=[dict(r) for r in runs],
    )


@router.delete("/screens/{screen_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_screen(screen_id: Annotated[int, Path(ge=1)], pool: Pool, owner: Owner) -> None:
    """Archive, never delete: saved runs keep their definition."""
    result = await pool.execute(
        "update public.screen_definition set archived=true where id=$1 and owner_id=$2 and not archived",
        screen_id,
        owner.user_id,
    )
    if result == "UPDATE 0":
        raise HTTPException(404, "Screen not found")


@router.post("/screens/{screen_id}/run", response_model=DiscoveryResult)
async def run_saved_screen(
    screen_id: Annotated[int, Path(ge=1)],
    pool: Pool,
    owner: Owner,
    screen_version: Annotated[int | None, Query(alias="version", ge=1)] = None,
    as_of: dt.date | None = None,
    cursor: str | None = None,
) -> DiscoveryResult:
    """Run a saved version (default: latest) and record what the run saw."""
    head = await pool.fetchrow(
        "select id, kind, latest_version from public.screen_definition where id=$1 and owner_id=$2 and not archived",
        screen_id,
        owner.user_id,
    )
    if head is None:
        raise HTTPException(404, "Screen not found")
    ver = screen_version or head["latest_version"]
    row = await pool.fetchrow(
        "select definition, definition_hash, method_version from public.screen_definition_version where screen_id=$1 and version=$2",
        screen_id,
        ver,
    )
    if row is None:
        raise HTTPException(404, "Version not found")
    raw = json.loads(row["definition"]) if isinstance(row["definition"], str) else row["definition"]
    raw = {**raw, "cursor": cursor}
    if as_of is not None:
        raw["as_of"] = as_of.isoformat()
    notes: list[str] = []
    if row["method_version"] != version("screen"):
        notes.append(
            f"saved under method {row['method_version']}; running under {version('screen')}"
        )
    if head["kind"] == "fund":
        result = await _run_fund(pool, FundRequest.model_validate(raw))
        sid = None
    else:
        req = StockRequest.model_validate(raw)
        result = await _run_stock(pool, owner.user_id, req)
        sid = req.snapshot_id
    result.warnings.extend(notes)
    keys = [r.key for r in result.rows]
    if result.next_cursor:  # a long result: the record holds the first page's matches only
        result.warnings.append("only the first page of matches is stored in the run record")
    excluded_total = result.failed_filter + result.unknown_excluded + result.no_data
    await pool.execute(
        "insert into public.screen_run (owner_id, screen_id, screen_version, kind, definition_hash, method_version, as_of_cutoff,"
        " universe_key, universe_snapshot_id, universe_size, result_count, unknown_count, excluded_count, matched_tickers, result_hash)"
        " values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)",
        owner.user_id,
        screen_id,
        ver,
        head["kind"],
        row["definition_hash"],
        version("screen"),
        dt.datetime.combine(result.as_of, dt.time.max, tzinfo=dt.UTC),
        result.universe_key,
        sid,
        result.universe_size,
        result.matched,
        result.unknown_excluded,
        excluded_total,
        keys,
        result.result_hash,
    )
    return result


class ColumnSet(BaseModel):
    id: int | None = None
    kind: Literal["stock", "fund"] = "stock"
    name: Annotated[str, Field(min_length=1, max_length=80)]
    columns: Annotated[list[str], Field(min_length=1, max_length=40)]


@router.get("/column-sets", response_model=list[ColumnSet])
async def list_column_sets(
    pool: Pool, owner: Owner, kind: Literal["stock", "fund"] | None = None
) -> list[ColumnSet]:
    rows = await pool.fetch(
        "select id, kind, name, columns from public.screen_column_set where owner_id=$1 and ($2::text is null or kind=$2) order by name",
        owner.user_id,
        kind,
    )
    return [ColumnSet(**dict(r)) for r in rows]


@router.post("/column-sets", response_model=ColumnSet, status_code=status.HTTP_201_CREATED)
async def save_column_set(body: ColumnSet, pool: Pool, owner: Owner) -> ColumnSet:
    cat = STOCK_CATALOGUE if body.kind == "stock" else FUND_CATALOGUE
    bad = [c for c in body.columns if c not in cat]
    if bad:
        raise _422([f"unknown column: {c}" for c in bad])
    row = await pool.fetchrow(
        "insert into public.screen_column_set (owner_id, kind, name, columns) values ($1,$2,$3,$4)"
        " on conflict (owner_id, kind, name) do update set columns=excluded.columns returning id, kind, name, columns",
        owner.user_id,
        body.kind,
        body.name.strip(),
        body.columns,
    )
    return ColumnSet(**dict(row))


@router.delete("/column-sets/{set_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_column_set(set_id: Annotated[int, Path(ge=1)], pool: Pool, owner: Owner) -> None:
    result = await pool.execute(
        "delete from public.screen_column_set where id=$1 and owner_id=$2", set_id, owner.user_id
    )
    if result == "DELETE 0":
        raise HTTPException(404, "Column set not found")
