"""Shares outstanding per holding: the denominator of 13F "% of shares outstanding".

The fallback chain (first that yields a figure wins):

1. `dei:EntityCommonStockSharesOutstanding` from companyfacts (the cover-page figure),
   unless companyfacts has not caught up with the latest 10-K/10-Q/20-F (TSM's FY2025
   20-F reached it late) - then the instance of that filing comes first;
2. the same dei fact from the latest filing's XBRL instance: undimensioned, or tagged per
   share class and summed over the counted classes (Alphabet A/B/C, Novo Nordisk A/B;
   companyfacts drops these dimensional facts);
3. the balance-sheet `us-gaap:CommonStockSharesOutstanding` or
   `ifrs-full:NumberOfSharesOutstanding` (companyfacts, else the instance);
4. a figure entered by hand in the holding profile (issuers outside EDGAR).

The ADS basis (home-market shares per US-listed share) and the counted share classes come
from the holding profile's `share_basis`, else DEFAULT_BASIS_BY_CIK below.
"""

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from steward.config.holding_profiles import HoldingProfile
from steward.sources.edgar import Filing
from steward.sources.xbrl import Instance, InstanceShares, balance_sheet_shares

Method = Literal[
    "dei_companyfacts",
    "dei_instance",
    "dei_instance_classes",
    "us_gaap_common_stock",
    "ifrs_number_of_shares",
    "manual",
]

METHOD_LABELS: dict[str, str] = {
    "dei_companyfacts": "cover page (dei:EntityCommonStockSharesOutstanding), SEC companyfacts",
    "dei_instance": "cover page (dei:EntityCommonStockSharesOutstanding), filing XBRL",
    "dei_instance_classes": "cover page per share class (dei), filing XBRL",
    "us_gaap_common_stock": "balance sheet (us-gaap:CommonStockSharesOutstanding)",
    "ifrs_number_of_shares": "balance sheet (ifrs-full:NumberOfSharesOutstanding)",
    "manual": "entered manually in the holding profile",
}


@dataclass(frozen=True)
class BasisDefault:
    ads_ratio: Decimal | None = None
    share_classes: tuple[str, ...] = ()
    note: str = ""


# Documented defaults by SEC CIK; a holding profile's share_basis overrides them. Every
# other issuer: 1 US share = 1 home-market share (ASML New York registry shares, SAP and
# Novo Nordisk ADRs - "each representing one B Share").
DEFAULT_BASIS_BY_CIK: dict[str, BasisDefault] = {
    # "each ADS represents five (5) common shares" - TSMC 20-F for 2025, accession
    # 0001628280-26-025362 (the job re-reads the ratio from each new 20-F).
    "0001046179": BasisDefault(ads_ratio=Decimal(5), note="TSMC 20-F: 1 ADS = 5 common shares"),
}
# Share classes: every class tagged is counted (Alphabet A+B+C, Novo Nordisk A+B), as the
# figure is "% of all shares outstanding"; a profile can narrow it (e.g. GOOGL to
# us-gaap:CommonClassAMember, NVO to nvo:BShareCapitalMember, the class its US line is).


@dataclass(frozen=True)
class Basis:
    ads_ratio: Decimal
    ads_ratio_source: Literal["profile", "default"]
    share_classes: tuple[str, ...]
    share_classes_source: Literal["profile", "default", "all"]


def basis_for(profile: HoldingProfile, cik: str | None) -> Basis:
    default = DEFAULT_BASIS_BY_CIK.get(cik or "") or BasisDefault()
    configured = profile.share_basis
    if configured.ads_ratio is not None:
        ratio, ratio_source = Decimal(str(configured.ads_ratio)), "profile"
    else:
        ratio, ratio_source = default.ads_ratio or Decimal(1), "default"
    classes: tuple[str, ...]
    if configured.share_classes:
        classes, class_source = tuple(configured.share_classes), "profile"
    elif default.share_classes:
        classes, class_source = default.share_classes, "default"
    else:
        classes, class_source = (), "all"
    return Basis(ratio, ratio_source, classes, class_source)  # type: ignore[arg-type]


@dataclass
class SharesFigure:
    shares: Decimal
    as_of: dt.date
    method: Method
    accession: str | None
    form: str | None
    filed: dt.date | None
    source_url: str | None
    raw: dict[str, Any]
    classes: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _points(company_facts: dict[str, Any], taxonomy: str, concept: str) -> list[dict[str, Any]]:
    units = (company_facts.get("facts") or {}).get(taxonomy, {}).get(concept, {}).get("units") or {}
    points = units.get("shares") or []
    return [p for p in points if isinstance(p, dict) and p.get("val") and p.get("end")]


def _latest(points: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not points:
        return None
    return max(points, key=lambda p: (str(p["end"]), str(p.get("filed") or "")))


def _date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _from_point(
    point: dict[str, Any], method: Method, concept: str, cik: str, url: str
) -> SharesFigure:
    return SharesFigure(
        shares=Decimal(str(point["val"])),
        as_of=_date(point["end"]) or dt.date.min,
        method=method,
        accession=point.get("accn"),
        form=point.get("form"),
        filed=_date(point.get("filed")),
        source_url=url,
        raw={"source": "companyfacts", "cik": cik, "concept": concept, "fact": point},
    )


def companyfacts_is_current(point: dict[str, Any], latest: Filing | None) -> bool:
    """Whether companyfacts' latest cover-page point is from the latest periodic filing
    (or later), i.e. companyfacts has caught up with it."""
    if latest is None:
        return True
    if point.get("accn") == latest.accession:
        return True
    filed = _date(point.get("filed"))
    return filed is not None and filed >= latest.filing_date


def from_instance(
    shares: InstanceShares | None,
    instance: Instance | None,
    filing: Filing | None,
    instance_url: str | None,
    basis: Basis,
) -> SharesFigure | None:
    """Step 2: the dei cover-page facts of the filing's instance."""
    if shares is None or filing is None:
        return None
    meta = {
        "source": "instance",
        "instance_url": instance_url,
        "accession": filing.accession,
    }
    if shares.total is not None and not shares.classes:
        fact = next(
            f
            for f in (instance.facts if instance else [])
            if f.concept == "dei:EntityCommonStockSharesOutstanding"
            and not f.context.dims
            and f.context.instant == shares.as_of
        )
        return SharesFigure(
            shares=shares.total,
            as_of=shares.as_of,
            method="dei_instance",
            accession=filing.accession,
            form=filing.form,
            filed=filing.filing_date,
            source_url=instance_url,
            raw={**meta, "facts": [{"xml": fact.xml, "context": fact.context.id}]},
        )
    if not shares.classes:
        return None
    wanted = set(basis.share_classes)
    present = {c.member for c in shares.classes}
    notes: list[str] = []
    if wanted and not wanted & present:
        notes.append(
            "The configured share classes ("
            + ", ".join(sorted(wanted))
            + ") are not tagged in this filing; all classes are counted."
        )
        wanted = set()
    counted = [c for c in shares.classes if not wanted or c.member in wanted]
    return SharesFigure(
        shares=sum((c.shares for c in counted), Decimal(0)),
        as_of=shares.as_of,
        method="dei_instance_classes",
        accession=filing.accession,
        form=filing.form,
        filed=filing.filing_date,
        source_url=instance_url,
        raw={
            **meta,
            "facts": [
                {
                    "xml": c.fact.xml,
                    "context": c.fact.context.id,
                    "dimensions": [list(d) for d in c.fact.context.dims],
                }
                for c in shares.classes
            ],
        },
        classes=[
            {
                "member": c.member,
                "label": c.label,
                "shares": str(c.shares),
                "counted": not wanted or c.member in wanted,
            }
            for c in shares.classes
        ],
        notes=notes,
    )


def shares_chain(
    *,
    cik: str,
    company_facts: dict[str, Any],
    latest: Filing | None,
    instance: Instance | None,
    instance_shares: InstanceShares | None,
    instance_url: str | None,
    basis: Basis,
    companyfacts_url: str,
) -> SharesFigure | None:
    """Steps 1-3 of the chain for an SEC filer (step 4, manual, is the caller's)."""
    dei = _latest(_points(company_facts, "dei", "EntityCommonStockSharesOutstanding"))
    if dei is not None and companyfacts_is_current(dei, latest):
        return _from_point(
            dei,
            "dei_companyfacts",
            "dei:EntityCommonStockSharesOutstanding",
            cik,
            companyfacts_url,
        )
    figure = from_instance(instance_shares, instance, latest, instance_url, basis)
    if figure is not None:
        if dei is not None:
            figure.notes.append(
                "companyfacts has not caught up with the latest filing; read from its XBRL."
            )
        return figure
    if dei is not None:
        figure = _from_point(
            dei,
            "dei_companyfacts",
            "dei:EntityCommonStockSharesOutstanding",
            cik,
            companyfacts_url,
        )
        figure.notes.append("companyfacts lags the latest filing, which has no cover figure.")
        return figure
    for taxonomy, concept, method in (
        ("us-gaap", "CommonStockSharesOutstanding", "us_gaap_common_stock"),
        ("ifrs-full", "NumberOfSharesOutstanding", "ifrs_number_of_shares"),
    ):
        point = _latest(_points(company_facts, taxonomy, concept))
        if point is not None:
            return _from_point(
                point,
                method,  # type: ignore[arg-type]
                f"{taxonomy}:{concept}",
                cik,
                companyfacts_url,
            )
    fact = balance_sheet_shares(instance) if instance is not None else None
    if fact is not None and fact.number is not None and latest is not None:
        method = (
            "us_gaap_common_stock"
            if fact.concept.startswith("us-gaap")
            else "ifrs_number_of_shares"
        )
        return SharesFigure(
            shares=fact.number,
            as_of=fact.context.instant or latest.filing_date,
            method=method,  # type: ignore[arg-type]
            accession=latest.accession,
            form=latest.form,
            filed=latest.filing_date,
            source_url=instance_url,
            raw={
                "source": "instance",
                "instance_url": instance_url,
                "facts": [{"xml": fact.xml, "context": fact.context.id}],
            },
        )
    return None


def manual_figure(profile: HoldingProfile) -> SharesFigure | None:
    basis = profile.share_basis
    if basis.manual_shares_outstanding is None or basis.manual_as_of is None:
        return None
    return SharesFigure(
        shares=Decimal(basis.manual_shares_outstanding),
        as_of=basis.manual_as_of,
        method="manual",
        accession=None,
        form=None,
        filed=None,
        source_url=None,
        raw={
            "source": "holding_profile",
            "manual_shares_outstanding": basis.manual_shares_outstanding,
            "manual_as_of": basis.manual_as_of.isoformat(),
            "manual_source": basis.manual_source,
        },
    )
