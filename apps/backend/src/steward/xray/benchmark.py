"""Benchmark-relative X-Ray (plan M5.6).

The benchmark is a *labelled ETF proxy*: the holdings of the fund `look_through.benchmark_proxies`
names for an index, equity lines renormalised to 100%. It is today's holdings of that fund, not
the index's historical membership, and nothing here claims otherwise. Comparison rules:

- both sides are equity only and renormalised, so cash, funds not looked through and unknown fund
  weight sit outside and are stated;
- a classification is compared only when both sides use the same one: sectors only when every
  label on both sides is a GICS sector; countries only when the portfolio's country labels all
  have the proxy's own basis (country of risk against country of risk). Otherwise the dimension is
  withheld with the reason, not compared across meanings;
- issuers are matched by canonical issuer, so an ADR and its ordinary share are one name.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal, get_args

from pydantic import BaseModel, Field

from steward.config.jobs import GicsSector
from steward.funds.aggregate import IssuerRef, issuer_for
from steward.lookthrough.classify import country_name
from steward.lookthrough.read import UNCLASSIFIED, SnapshotIn
from steward.xray.leaves import LeafSet
from steward.xray.metrics import XrayActive
from steward.xray.views import COUNTRY_KIND_LABELS

GICS = set(get_args(GicsSector))
MATERIAL = 0.01  # a basis or scheme below this share of equity does not decide comparability
TOP_ACTIVE = 5


class XrayBenchmarkDim(BaseModel):
    dimension: Literal["sector", "country", "currency", "issuer"]
    like_for_like: bool
    reason: str | None = Field(description="Why the comparison is withheld, when it is.")
    lines: list[XrayActive]
    portfolio_unlabelled: float = Field(
        description="Share of the portfolio's equity with no label here."
    )
    benchmark_unlabelled: float
    notes: list[str]


class XrayMissing(BaseModel):
    key: str
    label: str
    weight: float


class XrayBenchmark(BaseModel):
    available: bool
    reason: str | None
    label: str | None
    symbol: str | None
    proxy_fund: str | None
    proxy_isin: str | None
    as_of: dt.date | None
    age_days: int | None
    definition: str
    dimensions: list[XrayBenchmarkDim]
    overlap: float | None = Field(
        description="Sum of the smaller weight per shared issuer, portfolio equity against "
        "benchmark."
    )
    portfolio_only: list[XrayMissing] = Field(
        description="Portfolio issuers absent from the proxy, as a share of portfolio equity."
    )
    portfolio_only_weight: float
    benchmark_only: list[XrayMissing] = Field(
        description="Proxy constituents the portfolio does not hold, as a share of the proxy."
    )
    benchmark_only_weight: float
    proxy_rows_weight: float | None = Field(
        description="The proxy's published rows as a share of its net assets."
    )
    proxy_unidentified_weight: float = Field(
        description="Proxy equity with neither ISIN nor name: cannot be matched to an issuer."
    )
    equity_weight: float = Field(description="Portfolio equity as a share of total value.")
    outside_weight: float = Field(
        description="Portfolio weight outside the comparison: cash, other assets, unknown."
    )
    notes: list[str]


@dataclass
class BenchmarkWeights:
    label: str
    symbol: str | None
    fund_label: str
    isin: str
    as_of: dt.date
    country_basis: str | None
    currency_derived: bool
    issuer: dict[str, tuple[str, float]] = field(default_factory=dict)
    sector: dict[str, float] = field(default_factory=dict)
    country: dict[str, float] = field(default_factory=dict)
    currency: dict[str, float] = field(default_factory=dict)
    rows_weight: float = 0.0
    unidentified: float = 0.0


def benchmark_weights(
    snap: SnapshotIn,
    *,
    label: str,
    symbol: str | None,
    fund_label: str,
    groups: dict[str, IssuerRef],
    register: dict[str, IssuerRef],
) -> BenchmarkWeights:
    equity = [r for r in snap.rows if r.asset_class == "equity" and r.weight > 0]
    total = sum(r.weight for r in equity)
    out = BenchmarkWeights(
        label=label,
        symbol=symbol,
        fund_label=fund_label,
        isin=snap.isin,
        as_of=snap.as_of,
        country_basis=snap.country_basis,
        currency_derived=snap.currency_basis == "derived",
        rows_weight=sum(r.weight for r in snap.rows),
    )
    if total <= 0:
        return out
    sector: dict[str, float] = defaultdict(float)
    country: dict[str, float] = defaultdict(float)
    currency: dict[str, float] = defaultdict(float)
    for r in equity:
        w = r.weight / total
        ref = issuer_for(r.isin, r.name, groups, register)
        if ref is None:
            out.unidentified += w
        else:
            lab, cur = out.issuer.get(ref.key, (ref.label, 0.0))
            out.issuer[ref.key] = (lab, cur + w)
        sector[r.sector or UNCLASSIFIED] += w
        country[r.country or UNCLASSIFIED] += w
        currency[r.currency or UNCLASSIFIED] += w
    out.sector, out.country, out.currency = dict(sector), dict(country), dict(currency)
    return out


def _lines(
    portfolio: dict[str, float],
    bench: dict[str, float],
    labels: dict[str, str],
) -> list[XrayActive]:
    keys = set(portfolio) | set(bench)
    rows = [
        XrayActive(
            key=k,
            label=labels.get(k, k),
            portfolio_weight=portfolio.get(k, 0.0),
            benchmark_weight=bench.get(k, 0.0),
            active=portfolio.get(k, 0.0) - bench.get(k, 0.0),
        )
        for k in keys
    ]
    rows.sort(key=lambda a: -abs(a.active))
    return rows


def _split(lines: list[XrayActive]) -> tuple[list[XrayActive], list[XrayActive]]:
    over = sorted((x for x in lines if x.active > 0), key=lambda x: -x.active)[:TOP_ACTIVE]
    under = sorted((x for x in lines if x.active < 0), key=lambda x: x.active)[:TOP_ACTIVE]
    return over, under


def compare(
    leafset: LeafSet,
    bench: BenchmarkWeights,
    *,
    today: dt.date,
) -> tuple[XrayBenchmark, dict[str, tuple[list[XrayActive], list[XrayActive], str | None]]]:
    equity_leaves = [x for x in leafset.leaves if x.is_equity]
    eq = sum(x.weight for x in equity_leaves)
    outside = sum(x.weight for x in leafset.leaves if not x.is_equity)
    definition = (
        f"{bench.label} proxied by {bench.fund_label}'s equity holdings as of {bench.as_of}, "
        "renormalised to 100%. This is the proxy fund's holdings today, not the index's "
        "membership on any past date."
    )
    notes = [
        "Both sides are equity only and renormalised to 100%; the portfolio's cash, other assets "
        "and unknown fund weight sit outside the comparison.",
    ]
    age = (today - bench.as_of).days
    if age > 45:
        notes.append(f"The proxy's holdings are {age} days old.")
    if eq <= 0:
        return (
            XrayBenchmark(
                available=False,
                reason="The portfolio has no identified equity to compare.",
                label=bench.label,
                symbol=bench.symbol,
                proxy_fund=bench.fund_label,
                proxy_isin=bench.isin,
                as_of=bench.as_of,
                age_days=age,
                definition=definition,
                dimensions=[],
                overlap=None,
                portfolio_only=[],
                portfolio_only_weight=0.0,
                benchmark_only=[],
                benchmark_only_weight=0.0,
                proxy_rows_weight=bench.rows_weight,
                proxy_unidentified_weight=bench.unidentified,
                equity_weight=0.0,
                outside_weight=outside,
                notes=notes,
            ),
            {},
        )

    p_issuer: dict[str, float] = defaultdict(float)
    p_label: dict[str, str] = {}
    p_sector: dict[str, float] = defaultdict(float)
    p_country: dict[str, float] = defaultdict(float)
    p_currency: dict[str, float] = defaultdict(float)
    kinds: dict[str, float] = defaultdict(float)
    for x in equity_leaves:
        w = x.weight / eq
        ik = x.issuer_key or f"unidentified:{x.name}"
        p_issuer[ik] += w
        p_label.setdefault(ik, x.issuer_label or x.name or ik)
        p_sector[x.sector or UNCLASSIFIED] += w
        p_country[x.country or UNCLASSIFIED] += w
        p_currency[x.currency or UNCLASSIFIED] += w
        if x.country:
            kinds[x.country_kind] += w

    dims: list[XrayBenchmarkDim] = []
    actives: dict[str, tuple[list[XrayActive], list[XrayActive], str | None]] = {}

    def add(
        name: Literal["sector", "country", "currency", "issuer"],
        portfolio: dict[str, float],
        bench_w: dict[str, float],
        labels: dict[str, str],
        reason: str | None,
        extra: list[str] | None = None,
    ) -> None:
        ok = reason is None
        lines = _lines(portfolio, bench_w, labels)[:15] if ok else []
        dims.append(
            XrayBenchmarkDim(
                dimension=name,
                like_for_like=ok,
                reason=reason,
                lines=lines,
                portfolio_unlabelled=portfolio.get(UNCLASSIFIED, 0.0),
                benchmark_unlabelled=bench_w.get(UNCLASSIFIED, 0.0),
                notes=extra or [],
            )
        )
        if ok and name in ("sector", "country", "issuer"):
            over, under = _split(_lines(portfolio, bench_w, labels))
            actives[name] = (over, under, f"Against {bench.label} proxy ({bench.fund_label}).")

    # sector: only when everything on both sides is a GICS label
    odd_p = {k: v for k, v in p_sector.items() if k != UNCLASSIFIED and k not in GICS}
    odd_b = {k: v for k, v in bench.sector.items() if k != UNCLASSIFIED and k not in GICS}
    reason = None
    if sum(odd_p.values()) >= MATERIAL:
        reason = (
            "Some of the portfolio's sector labels are not GICS sectors "
            f"({', '.join(sorted(odd_p)[:4])}); comparing them to the proxy would mix schemes."
        )
    elif sum(odd_b.values()) >= MATERIAL:
        reason = (
            "The proxy fund publishes a sector scheme other than GICS "
            f"({', '.join(sorted(odd_b)[:4])}); it is not compared to the portfolio's GICS sectors."
        )
    add(
        "sector",
        dict(p_sector),
        bench.sector,
        {k: k for k in set(p_sector) | set(bench.sector)},
        reason,
    )

    # country: only against the proxy's own country basis
    differing = {k: v for k, v in kinds.items() if k != bench.country_basis and v >= MATERIAL}
    if bench.country_basis is None:
        reason = "The proxy fund does not say what its country label means."
    elif differing:
        parts = ", ".join(
            f"{COUNTRY_KIND_LABELS.get(k, k)} {v:.0%}" for k, v in sorted(differing.items())
        )
        reason = (
            "The proxy's countries are "
            f"{COUNTRY_KIND_LABELS.get(bench.country_basis, bench.country_basis)}, "
            f"but part of the portfolio's equity is labelled differently ({parts}). They are not "
            "compared, because the same company can sit in different countries under each."
        )
    else:
        reason = None
    c_labels = {k: (country_name(k) or k) for k in set(p_country) | set(bench.country)}
    c_labels[UNCLASSIFIED] = "No country label"
    add("country", dict(p_country), bench.country, c_labels, reason)

    cur_notes = []
    if bench.currency_derived:
        cur_notes.append("The proxy's currencies are derived from its countries, not published.")
    add(
        "currency",
        dict(p_currency),
        bench.currency,
        {k: k for k in set(p_currency) | set(bench.currency)},
        None,
        cur_notes,
    )

    i_labels = dict(p_label) | {k: v[0] for k, v in bench.issuer.items()}
    bench_issuer = {k: v[1] for k, v in bench.issuer.items()}
    add("issuer", dict(p_issuer), bench_issuer, i_labels, None)

    shared = set(p_issuer) & set(bench_issuer)
    overlap = sum(min(p_issuer[k], bench_issuer[k]) for k in shared)
    only_p = sorted(
        ((k, w) for k, w in p_issuer.items() if k not in bench_issuer), key=lambda kv: -kv[1]
    )
    only_b = sorted(
        ((k, w) for k, w in bench_issuer.items() if k not in p_issuer), key=lambda kv: -kv[1]
    )
    return (
        XrayBenchmark(
            available=True,
            reason=None,
            label=bench.label,
            symbol=bench.symbol,
            proxy_fund=bench.fund_label,
            proxy_isin=bench.isin,
            as_of=bench.as_of,
            age_days=age,
            definition=definition,
            dimensions=dims,
            overlap=overlap,
            portfolio_only=[
                XrayMissing(key=k, label=i_labels.get(k, k), weight=w) for k, w in only_p[:10]
            ],
            portfolio_only_weight=sum(w for _, w in only_p),
            benchmark_only=[
                XrayMissing(key=k, label=i_labels.get(k, k), weight=w) for k, w in only_b[:10]
            ],
            benchmark_only_weight=sum(w for _, w in only_b),
            proxy_rows_weight=bench.rows_weight,
            proxy_unidentified_weight=bench.unidentified,
            equity_weight=eq,
            outside_weight=outside,
            notes=notes,
        ),
        actives,
    )


def unavailable(reason: str, today: dt.date | None = None) -> XrayBenchmark:
    return XrayBenchmark(
        available=False,
        reason=reason,
        label=None,
        symbol=None,
        proxy_fund=None,
        proxy_isin=None,
        as_of=None,
        age_days=None,
        definition="No benchmark proxy is configured or its holdings are not stored yet.",
        dimensions=[],
        overlap=None,
        portfolio_only=[],
        portfolio_only_weight=0.0,
        benchmark_only=[],
        benchmark_only_weight=0.0,
        proxy_rows_weight=None,
        proxy_unidentified_weight=0.0,
        equity_weight=0.0,
        outside_weight=0.0,
        notes=[],
    )
