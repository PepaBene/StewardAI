# ruff: noqa: E501 - prompt and reason text stays on one line each
"""Plain-English screen requests (insight plan I10.1, I10.2).

A sentence becomes a `ScreenDraft`: filters on catalogue metrics, markets and industries, a sort,
and the phrases that could not be mapped. The model only proposes; every filter is checked
against the metric registry (the id exists, the operator suits the metric, the value is in the
metric's unit and plausible range) and anything that fails is reported back as unmatched, never
guessed. Requests for predictions are refused. Parsing never runs the screen.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, ConfigDict, Field

from steward.analytics.versions import version
from steward.discovery.metrics import STOCK_CATALOGUE, MetricDef

logger = logging.getLogger(__name__)

Op = Literal["gte", "lte", "in"]


# ----------------------------------------------------------------------------- the draft


class DraftFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric_id: str
    op: Op
    value: Decimal | list[str]
    note: str | None = Field(
        default=None, description="How the value was read, when it was converted."
    )


class DraftSort(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric_id: str
    descending: bool = True


class ScreenDraft(BaseModel):
    """What the sentence asks for, in the screener's own terms. Nothing here has been run."""

    kind: Literal["stock"] = "stock"
    markets: list[str] = Field(default_factory=list, description="ISO 3166 alpha-2 country codes.")
    industries: list[str] = Field(
        default_factory=list, description="Sector or industry names as stored."
    )
    keywords: list[str] = Field(
        default_factory=list, description="Name or theme words; shown, not applied as a filter."
    )
    filters: list[DraftFilter] = Field(default_factory=list)
    sort: DraftSort | None = None
    unmatched_phrases: list[str] = Field(default_factory=list)
    refused: bool = False
    refusal_reason: str | None = None


class ParseResult(BaseModel):
    text: str
    draft: ScreenDraft
    model: str | None
    cached: bool
    method_version: str
    registry_hash: str
    evaluation: str = Field(
        description="Whether the parser's evaluation set passed for this metric registry."
    )
    note: str = (
        "A draft only: nothing has been run. Check the filters, then run the screen yourself."
    )


# --------------------------------------------------------------- what the model returns


class ModelFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric_id: str
    op: Op
    number: float | None = Field(
        description="Threshold in the metric's unit (fractions as decimals: 4 % is 0.04). Null for 'in'."
    )
    values: list[str] = Field(description="Allowed categories for 'in'; empty otherwise.")
    phrase: str = Field(description="The words of the request this filter came from.")


class ModelSort(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric_id: str
    descending: bool


class ModelDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refused: bool = Field(
        description="True when the request asks for a prediction or something no metric can measure."
    )
    refusal_reason: str | None
    markets: list[str]
    industries: list[str]
    keywords: list[str]
    filters: list[ModelFilter]
    sort: ModelSort | None
    unmatched_phrases: list[str] = Field(
        description="Every phrase of the request that maps to no metric, market or industry."
    )


SYSTEM = """You turn one plain-English stock screen request into a structured draft for a stock screener.

Rules:
- Use only metric ids from the registry below. Each metric lists its unit, aliases and its usual range.
- Units: "fraction" values are decimals (4 % is 0.04, 25 % is 0.25); "multiple" is a plain number (a P/E of 15 is 15); "money" is in the listing currency (2 billion is 2000000000); "count" and "years" are plain numbers; "ratio" check scores run 0 to 6.
- Operators: "gte" (at least / over / above), "lte" (at most / under / below), "in" (only for category metrics: country, sector, industry).
- Vague words map to fixed thresholds: "low debt" is net_debt_to_equity lte 0.5; "high yield" or "high dividend" is dividend_yield gte 0.04; "dividend payers" alone is dividend_yield gte 0.0001; "profitable" is net_margin gte 0; "positive earnings growth" is forward_eps_growth gte 0; "cheap" alone is pe_ratio lte 15; "large cap" is market_cap gte 10000000000; "small cap" is market_cap lte 2000000000; "mid cap" is market_cap gte 2000000000 and market_cap lte 10000000000; "insider buying" alone is insider_buyers_90d gte 1; "financially healthy" is checks_health_score gte 4; "rising margins" or "growing" alone is revenue_growth gte 0; "buying back shares" is share_change_1y lte 0; "low volatility" is volatility_1y lte 0.2; "quality payers" is checks_dividend_score gte 4.
- Countries go in "markets" as ISO 3166 alpha-2 codes (US, GB, DE, ...); "European" means the European countries listed, "UK" is GB. Sectors and industries go in "industries", using a name from the list below exactly; a sector not in the list is unmatched.
- Company names, themes or products ("AI", "electric cars") go in "keywords".
- A sort only when the request asks for an order ("highest yield first", "cheapest").
- Never invent a metric. A phrase you cannot map goes in unmatched_phrases, word for word.
- Refuse (refused true, no filters) a request for a forecast of price or return ("stocks that will double", "next big winner", "guaranteed"), or for advice; say why in refusal_reason. A request that is partly mappable is not refused: map what you can and list the rest as unmatched.
"""


def registry_lines(catalogue: dict[str, MetricDef] | None = None) -> list[str]:
    cat = catalogue or STOCK_CATALOGUE
    out = []
    for m in cat.values():
        if m.id in ("data_coverage", "asset_type", "mic", "currency"):
            continue
        rng = f"; range {m.ticks[0]} to {m.ticks[-1]}" if m.ticks else ""
        alias = f"; aliases: {', '.join(m.aliases)}" if m.aliases else ""
        out.append(f"- {m.id} ({m.label}; {m.kind}; unit {m.unit}{rng}{alias})")
    return out


def registry_hash(catalogue: dict[str, MetricDef] | None = None) -> str:
    """Changes whenever a metric, its unit, range or aliases change (re-run the evaluation)."""
    return hashlib.sha256("\n".join(registry_lines(catalogue)).encode()).hexdigest()[:16]


def prompt(text: str, sectors: list[str], countries: list[str]) -> str:
    return (
        "<registry>\n" + "\n".join(registry_lines()) + "\n</registry>\n"
        f"<sectors_and_industries>\n{', '.join(sectors) or '(none stored yet)'}\n</sectors_and_industries>\n"
        f"<countries_in_universe>\n{', '.join(countries) or '(none)'}\n</countries_in_universe>\n"
        f"<request>\n{text}\n</request>"
    )


# ---------------------------------------------------------------------------- validation


def _plausible(m: MetricDef, v: Decimal) -> bool:
    if not m.ticks:
        return True
    lo, hi = m.ticks[0], m.ticks[-1]
    span = max(hi - lo, Decimal(1) if m.unit != "fraction" else Decimal("0.5"))
    return lo - span <= v <= hi + span * 2


def validate_draft(
    raw: ModelDraft, sectors: list[str], catalogue: dict[str, MetricDef] | None = None
) -> ScreenDraft:
    """Keep only what the registry accepts; everything else becomes an unmatched phrase."""
    cat = catalogue or STOCK_CATALOGUE
    unmatched = [p for p in raw.unmatched_phrases if p.strip()]
    if raw.refused:
        return ScreenDraft(
            refused=True,
            refusal_reason=raw.refusal_reason or "The request cannot be measured by any metric.",
            unmatched_phrases=unmatched,
            keywords=raw.keywords,
        )
    filters: list[DraftFilter] = []
    seen: set[tuple[str, str]] = set()
    markets = sorted(
        {c.strip().upper() for c in raw.markets if len(c.strip()) == 2 and c.strip().isalpha()}
    )
    known = {s.lower(): s for s in sectors}
    industries: list[str] = []
    for name in raw.industries:
        hit = known.get(name.strip().lower())
        if hit:
            industries.append(hit)
        else:
            unmatched.append(f"{name} (no stored sector or industry by that name)")
    for f in raw.filters:
        m = cat.get(f.metric_id)
        where = f.phrase or f.metric_id
        if m is None:
            unmatched.append(f"{where} (no metric '{f.metric_id}')")
            continue
        if m.id in ("country", "sector", "industry"):
            vals = [v for v in f.values if v.strip()]
            if m.id == "country":
                markets = sorted(
                    {*markets, *(v.strip().upper() for v in vals if len(v.strip()) == 2)}
                )
            else:
                for v in vals:
                    hit = known.get(v.strip().lower())
                    if hit:
                        industries.append(hit)
                    else:
                        unmatched.append(f"{v} (no stored sector or industry by that name)")
            continue
        if m.kind == "category":
            if f.op != "in" or not f.values:
                unmatched.append(f"{where} ({m.label} takes a list of values)")
                continue
            filters.append(
                DraftFilter(
                    metric_id=m.id, op="in", value=[v.strip() for v in f.values if v.strip()]
                )
            )
            continue
        if f.op == "in" or f.number is None:
            unmatched.append(f"{where} ({m.label} needs a number and at least or at most)")
            continue
        num = Decimal(str(f.number))
        note = None
        if m.unit == "fraction" and abs(num) > 1 and m.ticks and m.ticks[-1] <= 1:
            # "over 4" for a yield means 4 %: the registry's range says so.
            num, note = num / 100, f"read {f.number:g} as {f.number:g} %"
        if not _plausible(m, num):
            unmatched.append(f"{where} ({num} is outside the plausible range of {m.label})")
            continue
        if (m.id, f.op) in seen:
            unmatched.append(f"{where} (a second '{f.op}' limit on {m.label})")
            continue
        seen.add((m.id, f.op))
        filters.append(DraftFilter(metric_id=m.id, op=f.op, value=num, note=note))
    sort = None
    if raw.sort is not None:
        sm = cat.get(raw.sort.metric_id)
        if sm is not None and sm.kind == "numeric":
            sort = DraftSort(metric_id=sm.id, descending=raw.sort.descending)
        else:
            unmatched.append(f"sort by {raw.sort.metric_id} (not a numeric metric)")
    return ScreenDraft(
        markets=markets,
        industries=list(dict.fromkeys(industries)),
        keywords=[k for k in raw.keywords if k.strip()],
        filters=filters[:12],
        sort=sort,
        unmatched_phrases=list(dict.fromkeys(unmatched)),
    )


def to_stock_filters(draft: ScreenDraft) -> list[dict[str, Any]]:
    """The draft as the screener's own filter list (what the UI fills in and the owner runs)."""
    out: list[dict[str, Any]] = []
    if draft.markets:
        out.append({"metric": "country", "op": "in", "value": draft.markets})
    if draft.industries:
        out.append({"metric": "sector", "op": "in", "value": draft.industries})
    for f in draft.filters:
        out.append(
            {
                "metric": f.metric_id,
                "op": f.op,
                "value": f.value if isinstance(f.value, list) else str(f.value),
            }
        )
    return out


# --------------------------------------------------------------------------- the call


class ParseUnavailable(Exception):
    """No model is configured, or the call failed; the message says which."""


class ParseBudgetRefused(Exception):
    """A Budget config cap stopped the call."""


@dataclass
class Vocabulary:
    sectors: list[str]
    countries: list[str]


async def vocabulary(pool: asyncpg.Pool) -> Vocabulary:
    rows = await pool.fetch(
        """select distinct text_value from public.company_research_observation
           where provider='fmp' and dataset='profile' and metric in ('sector','industry') and text_value is not null
           order by 1 limit 300"""
    )
    from steward.discovery.read import load_universe

    countries = sorted({m.country for m in await load_universe(pool) if m.country})
    return Vocabulary([r["text_value"] for r in rows], countries)


async def parse_screen(
    pool: asyncpg.Pool, owner_id: Any, text: str, *, llm: Any, model: str
) -> ParseResult:
    """Parse once (cached by request), inside a short `screen_parse` job so the Budget caps
    apply and the cost is recorded; every request is logged in `screen_parse_log`."""
    from steward.budget_guard import BudgetExceeded
    from steward.jobs.runner import JobContext, run_job
    from steward.llm.cache import ResultCache, request_key
    from steward.llm.types import LLMError, Message, Request

    text = " ".join(text.split())
    vocab = await vocabulary(pool)
    request = Request(
        model=model,
        system=SYSTEM,
        messages=(Message(role="user", text=prompt(text, vocab.sectors, vocab.countries)),),
        schema=ModelDraft,
        max_tokens=1500,
        cache_key="screen_parse",
    )
    reg = registry_hash()
    cache = ResultCache(pool, "screen_parse", ttl_days=30)
    key = request_key(
        request.model, request.system, [m.text for m in request.messages], ModelDraft, None
    )
    cached = await cache.get(key)
    raw: ModelDraft | None = None
    used_model: str | None = model
    if cached is not None:
        try:
            raw = ModelDraft.model_validate(cached)
        except ValueError:
            raw = None
    hit = raw is not None
    if raw is None:
        if not llm.configured(model):
            raise ParseUnavailable("The model for screen requests is not configured.")
        box: dict[str, Any] = {}

        async def handler(ctx: JobContext) -> None:
            async with ctx.step("parse"):
                try:
                    box["done"] = await llm.complete(request)
                except Exception as exc:  # kept for the caller; re-raised so the job shows failed
                    box["error"] = exc
                    raise

        outcome = await run_job(
            pool, "screen_parse", handler, trigger="manual", params={"chars": len(text)}, wait=False
        )
        if outcome == "busy":
            raise ParseUnavailable("Another screen request is being read; try again in a moment.")
        err: BaseException | None = box.get("error")
        while err is not None:
            if isinstance(err, BudgetExceeded):
                raise ParseBudgetRefused(str(err)) from err
            err = err.__cause__
        if isinstance(box.get("error"), LLMError):
            raise ParseUnavailable(f"The model call failed: {box['error']}"[:300])
        if "error" in box:
            raise box["error"]
        done = box.get("done")
        if done is None or not isinstance(done.parsed, ModelDraft):
            raise ParseUnavailable("The model returned no usable draft.")
        raw = done.parsed
        used_model = done.model
        await cache.put(key, done.model, raw.model_dump(mode="json"))
    draft = validate_draft(raw, vocab.sectors)
    try:
        await pool.execute(
            """insert into public.screen_parse_log (owner_id, text, draft, unmatched, refused, model, method_version, registry_hash)
               values ($1, $2, $3::jsonb, $4, $5, $6, $7, $8)""",
            owner_id, text[:1000], json.dumps(draft.model_dump(mode="json")), draft.unmatched_phrases,
            draft.refused, used_model, version("screen_parse"), reg,
        )  # fmt: skip
    except Exception:
        logger.exception("screen_parse_log write failed")
    from steward.discovery.parse_eval import gate_status

    return ParseResult(
        text=text, draft=draft, model=used_model, cached=hit, method_version=version("screen_parse"),
        registry_hash=reg, evaluation=gate_status(),
    )  # fmt: skip
