"""Reading an SEC filing's full XBRL instance (`*_htm.xml`) and its label linkbase (`*_lab.xml`).

companyfacts drops every dimensional fact, so two things live only in the instance:

- revenue by region, business segment and product (`srt:StatementGeographicalAxis`,
  `us-gaap:StatementBusinessSegmentsAxis`, `srt:ProductOrServiceAxis`, and the IFRS
  `GeographicalAreasAxis`, `SegmentsAxis`, `ProductsAndServicesAxis`);
- cover-page shares outstanding tagged per share class (Alphabet's classes A/B/C, Novo
  Nordisk's A/B shares).

Parsing is pure (bytes in, dataclasses out) so it is tested on trimmed real instances.
Member names are company-specific, so labels are kept as the filing reports them.

Security: an XBRL instance never needs a DTD, so a document declaring one (or an entity) is
refused before it reaches the XML parser; no entity can be expanded or fetched.
"""

import datetime as dt
import itertools
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Literal
from xml.sax.saxutils import escape

XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"
LINK = "http://www.xbrl.org/2003/linkbase"
XLINK = "http://www.w3.org/1999/xlink"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

# Bump when the parse changes, so stored filings are parsed again on the next run.
PARSER_VERSION = 2

# Standard taxonomies by namespace URI prefix -> the prefix used in stored names, whatever
# prefix a filing declares for them.
STANDARD_PREFIXES: tuple[tuple[str, str], ...] = (
    ("http://fasb.org/us-gaap/", "us-gaap"),
    ("http://fasb.org/srt/", "srt"),
    ("http://xbrl.sec.gov/dei/", "dei"),
    ("http://xbrl.sec.gov/country/", "country"),
    ("http://xbrl.sec.gov/currency/", "currency"),
    ("http://xbrl.sec.gov/exch/", "exch"),
    ("http://xbrl.sec.gov/stpr/", "stpr"),
    ("http://xbrl.ifrs.org/taxonomy/", "ifrs-full"),
    ("https://xbrl.ifrs.org/taxonomy/", "ifrs-full"),
    ("http://www.xbrl.org/2003/iso4217", "iso4217"),
)

AxisKind = Literal["geographic", "segment", "product"]

AXES: dict[str, AxisKind] = {
    "srt:StatementGeographicalAxis": "geographic",
    "ifrs-full:GeographicalAreasAxis": "geographic",
    "us-gaap:StatementBusinessSegmentsAxis": "segment",
    "ifrs-full:SegmentsAxis": "segment",
    "srt:ProductOrServiceAxis": "product",
    "ifrs-full:ProductsAndServicesAxis": "product",
}

# A second dimension that only says "this is the operating segments' own figure" (ASU
# 2023-07 / IFRS 8 tagging). A segment fact carrying it is the same number as without it;
# every other second dimension makes a cross-tab, which is skipped.
NEUTRAL_MEMBERS = {
    ("us-gaap:ConsolidationItemsAxis", "us-gaap:OperatingSegmentsMember"),
    ("ifrs-full:SegmentConsolidationItemsAxis", "ifrs-full:OperatingSegmentsMember"),
}

# Revenue concepts, most preferred first. Within one axis and period the concept used by
# the most members wins (a filing tags a breakdown with one concept); ties go by this order.
REVENUE_CONCEPTS: tuple[str, ...] = (
    "us-gaap:Revenues",
    "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
    "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",
    "us-gaap:SalesRevenueNet",
    "ifrs-full:Revenue",
    "ifrs-full:RevenueFromContractsWithCustomers",
)

SHARE_CONCEPTS: tuple[str, ...] = (
    "dei:EntityCommonStockSharesOutstanding",
    "us-gaap:CommonStockSharesOutstanding",
    "ifrs-full:NumberOfSharesOutstanding",
)

DOCUMENT_CONCEPTS: tuple[str, ...] = (
    "dei:DocumentType",
    "dei:DocumentPeriodEndDate",
    "dei:DocumentFiscalYearFocus",
    "dei:DocumentFiscalPeriodFocus",
    "dei:EntityCentralIndexKey",
    "dei:Security12bTitle",
    "dei:TradingSymbol",
)

WANTED = {*REVENUE_CONCEPTS, *SHARE_CONCEPTS, *DOCUMENT_CONCEPTS}


class XbrlError(Exception):
    pass


@dataclass(frozen=True)
class Context:
    id: str
    start: dt.date | None
    end: dt.date | None
    instant: dt.date | None
    dims: tuple[tuple[str, str], ...]
    typed: bool = False

    @property
    def is_duration(self) -> bool:
        return self.start is not None and self.end is not None


@dataclass(frozen=True)
class Fact:
    concept: str
    context: Context
    unit: str | None
    decimals: str | None
    text: str
    id: str | None
    xml: str

    @property
    def number(self) -> Decimal | None:
        try:
            value = Decimal(self.text.strip())
        except InvalidOperation:
            return None
        return value if value.is_finite() else None

    @property
    def precision(self) -> int:
        """decimals as a number for ranking duplicates: INF highest, missing lowest."""
        if self.decimals is None:
            return -(10**6)
        if self.decimals.upper() == "INF":
            return 10**6
        try:
            return int(self.decimals)
        except ValueError:
            return -(10**6)


@dataclass
class Instance:
    facts: list[Fact] = field(default_factory=list)
    contexts: dict[str, Context] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)

    def text(self, concept: str) -> str | None:
        """The first undimensioned value of a document concept (dei:DocumentType, ...)."""
        for f in self.facts:
            if f.concept == concept and not f.context.dims:
                return f.text.strip()
        return None

    def texts(self, concept: str) -> list[str]:
        return [f.text.strip() for f in self.facts if f.concept == concept]


def _guard(data: bytes) -> None:
    if re.search(rb"<!DOCTYPE|<!ENTITY", data, re.IGNORECASE):
        raise XbrlError("refusing an XML document with a DTD or entity declaration")


def _canonical_prefix(uri: str, declared: str) -> str:
    for start, prefix in STANDARD_PREFIXES:
        if uri.startswith(start):
            return prefix
    return declared


class _Names:
    """Resolves element tags ({uri}local) and QName text values (prefix:local) to names with
    the canonical prefix of a standard taxonomy, or the filing's own prefix."""

    def __init__(self, declared: dict[str, str]) -> None:
        # prefix -> uri as declared in the document; uri -> canonical prefix.
        self._uri_of = declared
        self._prefix_of: dict[str, str] = {}
        for prefix, uri in declared.items():
            self._prefix_of.setdefault(uri, _canonical_prefix(uri, prefix))

    def tag(self, tag: str) -> str | None:
        if not tag.startswith("{"):
            return None
        uri, local = tag[1:].split("}", 1)
        prefix = self._prefix_of.get(uri)
        return f"{prefix}:{local}" if prefix else None

    def qname(self, text: str) -> str:
        text = text.strip()
        if ":" not in text:
            return text
        prefix, local = text.split(":", 1)
        uri = self._uri_of.get(prefix)
        return f"{_canonical_prefix(uri, prefix) if uri else prefix}:{local}"


def _date(text: str | None) -> dt.date | None:
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text.strip()[:10])
    except ValueError:
        return None


def _context(el: ET.Element, names: _Names) -> Context:
    period = el.find(f"{{{XBRLI}}}period")
    start = end = instant = None
    if period is not None:
        start = _date(period.findtext(f"{{{XBRLI}}}startDate"))
        end = _date(period.findtext(f"{{{XBRLI}}}endDate"))
        instant = _date(period.findtext(f"{{{XBRLI}}}instant"))
    dims = tuple(
        (names.qname(m.get("dimension", "")), names.qname(m.text or ""))
        for m in el.iter(f"{{{XBRLDI}}}explicitMember")
    )
    typed = any(True for _ in el.iter(f"{{{XBRLDI}}}typedMember"))
    return Context(el.get("id", ""), start, end, instant, dims, typed)


def _unit(el: ET.Element, names: _Names) -> str:
    measures = [names.qname(m.text or "") for m in el.iter(f"{{{XBRLI}}}measure")]
    if len(measures) == 1:
        prefix, _, local = measures[0].partition(":")
        return local if prefix == "iso4217" else measures[0]
    return "/".join(measures)


def _leaf_xml(concept: str, el: ET.Element) -> str:
    """The fact element as written in the instance (a leaf: attributes and text), with the
    canonical prefix, for storing the source fact next to the figure read from it."""
    attrs = "".join(f' {k}="{escape(v, {chr(34): "&quot;"})}"' for k, v in el.attrib.items())
    return f"<{concept}{attrs}>{escape(el.text or '')}</{concept}>"


def parse_instance(data: bytes, wanted: set[str] | None = None) -> Instance:
    """Contexts, units and the facts of the `wanted` concepts (default: revenue, shares and
    document facts) of an XBRL 2.1 instance. Facts of other concepts are not kept."""
    _guard(data)
    wanted = WANTED if wanted is None else wanted
    declared: dict[str, str] = {}
    raw_facts: list[tuple[str, dict[str, str], str, str]] = []
    context_els: list[ET.Element] = []
    unit_els: list[ET.Element] = []
    names: _Names | None = None
    depth = 0
    try:
        for event, item in ET.iterparse(BytesIO(data), events=("start-ns", "start", "end")):  # noqa: S314 - DTDs refused in _guard
            if event == "start-ns":
                prefix, uri = item
                declared.setdefault(prefix, uri)
                continue
            el: ET.Element = item
            if event == "start":
                depth += 1
                continue
            depth -= 1
            if depth != 1:  # only the root's children are contexts, units and facts
                continue
            if names is None:
                names = _Names(declared)
            if el.tag == f"{{{XBRLI}}}context":
                context_els.append(el)
            elif el.tag == f"{{{XBRLI}}}unit":
                unit_els.append(el)
            else:
                concept = names.tag(el.tag)
                if concept in wanted and el.get("contextRef"):
                    raw_facts.append(
                        (concept, dict(el.attrib), el.text or "", _leaf_xml(concept, el))
                    )
                    el.clear()
                    continue
                el.clear()
    except ET.ParseError as exc:
        raise XbrlError(f"not a well-formed XBRL instance: {exc}") from exc
    names = names or _Names(declared)
    contexts = {c.id: c for c in (_context(el, names) for el in context_els)}
    units = {el.get("id", ""): _unit(el, names) for el in unit_els}
    instance = Instance(contexts=contexts, units=units)
    for concept, attrib, text, xml in raw_facts:
        context = contexts.get(attrib.get("contextRef", ""))
        if context is None:
            continue
        unit_ref = attrib.get("unitRef")
        instance.facts.append(
            Fact(
                concept=concept,
                context=context,
                unit=units.get(unit_ref, unit_ref) if unit_ref else None,
                decimals=attrib.get("decimals"),
                text=text,
                id=attrib.get("id"),
                xml=xml,
            )
        )
    return instance


# ---------------------------------------------------------------------------------- labels

LABEL_ROLE_ORDER = (
    "http://www.xbrl.org/2003/role/terseLabel",
    "http://www.xbrl.org/2003/role/label",
    "http://www.xbrl.org/2003/role/verboseLabel",
)


def _element_name(href: str) -> str | None:
    """`https://.../srt-2026.xsd#srt_AsiaPacificMember` -> `srt:AsiaPacificMember`; the
    fragment is the element id, `<prefix>_<LocalName>` by the SEC's and IFRS's conventions."""
    fragment = href.rsplit("#", 1)[-1]
    if "_" not in fragment:
        return None
    prefix, local = fragment.split("_", 1)
    if prefix == "ifrs-full" or prefix.startswith("ifrs"):
        prefix = "ifrs-full"
    return f"{prefix}:{local}"


STANDARD_ROLE_ORDER = (LABEL_ROLE_ORDER[1], LABEL_ROLE_ORDER[0], LABEL_ROLE_ORDER[2])


class Labels:
    """English labels of a filing's label linkbase, by element name."""

    def __init__(self, by_name: dict[str, dict[str, str]] | None = None) -> None:
        self._by_name = by_name or {}

    def __len__(self) -> int:
        return len(self._by_name)

    def get(self, name: str, *, standard: bool = False) -> str | None:
        """The terse label when the filing gives one (the label it shows in its tables),
        else the standard label; `standard` puts the standard label first (share classes,
        whose terse labels are often table-specific). Without " [Member]" / " [Axis]"."""
        by_role = self._by_name.get(name)
        if not by_role:
            return None
        order = (
            (LABEL_ROLE_ORDER[1], LABEL_ROLE_ORDER[0], LABEL_ROLE_ORDER[2])
            if standard
            else LABEL_ROLE_ORDER
        )
        text = next((by_role[r] for r in order if r in by_role), None)
        if text is None:
            text = next(iter(by_role.values()))
        return re.sub(r"\s*\[(Member|Axis|Domain|Line Items|Table)\]\s*$", "", text).strip() or None


def parse_labels(data: bytes) -> Labels:
    """The English labels of a label linkbase (`*_lab.xml`), by element name and role."""
    _guard(data)
    try:
        root = ET.fromstring(data)  # noqa: S314 - DTDs refused in _guard
    except ET.ParseError as exc:
        raise XbrlError(f"not a well-formed label linkbase: {exc}") from exc
    found: dict[str, dict[str, str]] = defaultdict(dict)
    for link in root.iter(f"{{{LINK}}}labelLink"):
        locs: dict[str, str] = {}
        labels: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        arcs: list[tuple[str, str]] = []
        for el in link:
            kind = el.get(f"{{{XLINK}}}type")
            key = el.get(f"{{{XLINK}}}label", "")
            if kind == "locator":
                name = _element_name(el.get(f"{{{XLINK}}}href", ""))
                if name:
                    locs[key] = name
            elif kind == "resource":
                labels[key].append(
                    (el.get(f"{{{XLINK}}}role", ""), el.get(XML_LANG, ""), (el.text or "").strip())
                )
            elif kind == "arc":
                arcs.append((el.get(f"{{{XLINK}}}from", ""), el.get(f"{{{XLINK}}}to", "")))
        for source, target in arcs:
            name = locs.get(source)
            if name is None:
                continue
            for role, lang, text in labels.get(target, []):
                if "documentation" in role:
                    continue
                if text and (not lang or lang.lower().startswith("en")):
                    found[name].setdefault(role, text)
    return Labels(dict(found))


COUNTRY_NAMES = {
    "US": "United States",
    "CN": "China",
    "TW": "Taiwan",
    "KR": "South Korea",
    "JP": "Japan",
    "NL": "Netherlands",
    "SG": "Singapore",
    "DE": "Germany",
    "GB": "United Kingdom",
    "FR": "France",
    "IE": "Ireland",
    "IN": "India",
    "CA": "Canada",
    "CH": "Switzerland",
    "DK": "Denmark",
    "CZ": "Czechia",
    "HK": "Hong Kong",
    "IL": "Israel",
}


def derived_label(member: str) -> str:
    """A readable name from the element name, for members the filing gives no label for:
    `country:US` -> "United States", `srt:AsiaPacificMember` -> "Asia Pacific"."""
    prefix, _, local = member.partition(":")
    if prefix == "country":
        return COUNTRY_NAMES.get(local, local)
    local = re.sub(r"(Member|Domain)$", "", local)
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", local)
    return words.strip() or member


# ------------------------------------------------------------------------ segment revenue


@dataclass(frozen=True)
class RevenueRow:
    kind: Literal["geographic", "segment", "product", "total"]
    axis: str | None
    member: str | None
    label: str
    label_source: Literal["filing", "derived"]
    concept: str
    start: dt.date
    end: dt.date
    value: Decimal
    unit: str
    decimals: str | None
    context_id: str
    fact_id: str | None
    subtotal: bool = False


@dataclass
class SegmentParse:
    rows: list[RevenueRow]
    facts_seen: int
    skipped_multi: int


def _is_currency(unit: str | None) -> bool:
    return unit is not None and bool(re.fullmatch(r"[A-Z]{3}", unit))


def _breakdown_member(context: Context) -> tuple[str, str] | Literal[False] | None:
    """(axis, member) of a context with exactly one explicit member on a breakdown axis
    (a neutral operating-segments member alongside is allowed); None for an undimensioned
    context; False for anything else (cross-tabs, other axes, typed members)."""
    if context.typed:
        return False
    dims = [d for d in context.dims if d not in NEUTRAL_MEMBERS]
    if not context.dims:
        return None
    if len(dims) != 1 or dims[0][0] not in AXES:
        return False
    return dims[0]


def _tolerance(rows: list[RevenueRow]) -> Decimal:
    """Rounding room for sums of reported values: half a unit of the coarsest precision per
    value added."""
    worst = Decimal(0)
    for r in rows:
        try:
            d = int(r.decimals) if r.decimals and r.decimals.upper() != "INF" else None
        except ValueError:
            d = None
        if d is not None:
            worst = max(worst, Decimal(5) * Decimal(10) ** (-d - 1))
    return worst * max(1, len(rows))


def mark_subtotals(members: list[RevenueRow], total: Decimal | None) -> list[RevenueRow]:
    """Flag members that are the sum of other members (Novo's "International Operations" =
    EUCAN + Emerging Markets + APAC + China; ASML's "Product" = its system types), so a
    chart does not count them twice. Only done when the members add up to more than the
    reported total, largest candidate first, until they no longer do."""
    if total is None or total <= 0 or len(members) < 3:
        return members
    flagged: set[int] = set()

    def excess() -> bool:
        kept = sum((m.value for i, m in enumerate(members) if i not in flagged), Decimal(0))
        return kept > total * Decimal("1.005")

    order = sorted(range(len(members)), key=lambda i: members[i].value, reverse=True)
    while excess():
        found = False
        for i in order:
            if i in flagged:
                continue
            others = [j for j in range(len(members)) if j != i and j not in flagged]
            if len(others) > 16:
                break
            target = members[i].value
            for size in range(2, len(others) + 1):
                for combo in itertools.combinations(others, size):
                    parts = [members[j] for j in combo]
                    if abs(sum((p.value for p in parts), Decimal(0)) - target) <= _tolerance(
                        [*parts, members[i]]
                    ):
                        flagged.add(i)
                        found = True
                        break
                if found:
                    break
            if found:
                break
        if not found:
            break
    if excess():
        # Two partitions of the same total side by side (Microsoft: "Product" + "Service
        # and other" = total = its twelve product lines): the coarser one is flagged.
        rest = [i for i in range(len(members)) if i not in flagged]
        if len(rest) <= 16:
            done = False
            for size in range(1, len(rest) // 2 + 1):
                for combo in itertools.combinations(rest, size):
                    inside = [members[j] for j in combo]
                    outside = [members[j] for j in rest if j not in combo]
                    tol_in = _tolerance(inside) + total * Decimal("0.0005")
                    tol_out = _tolerance(outside) + total * Decimal("0.0005")
                    if (
                        abs(sum((m.value for m in inside), Decimal(0)) - total) <= tol_in
                        and abs(sum((m.value for m in outside), Decimal(0)) - total) <= tol_out
                    ):
                        flagged.update(combo)
                        done = True
                        break
                if done:
                    break
    return [
        RevenueRow(**{**m.__dict__, "subtotal": True}) if i in flagged else m
        for i, m in enumerate(members)
    ]


def segment_revenue(instance: Instance, labels: Labels) -> SegmentParse:
    """Revenue by region, segment and product, plus the undimensioned total, per period.

    Kept: duration facts of a revenue concept in a currency unit whose context has exactly
    one explicit member on a breakdown axis (see AXES). Skipped and counted: cross-tabs
    (product x region, segment x product) and facts on other axes. Duplicates (the same
    fact tagged twice) keep the most precise value; one concept is used per axis and period.
    """
    concept_rank = {c: i for i, c in enumerate(REVENUE_CONCEPTS)}
    best: dict[tuple[str, str | None, str | None, str, dt.date, dt.date, str], Fact] = {}
    seen = skipped = 0
    for fact in instance.facts:
        if fact.concept not in concept_rank or not fact.context.is_duration:
            continue
        if not _is_currency(fact.unit) or fact.number is None:
            continue
        seen += 1
        found = _breakdown_member(fact.context)
        if found is False:
            skipped += 1
            continue
        axis, name = found if found else (None, None)
        assert fact.context.start is not None and fact.context.end is not None
        key = (
            fact.concept,
            axis,
            name,
            fact.unit or "",
            fact.context.start,
            fact.context.end,
            AXES[axis] if axis else "total",
        )
        held = best.get(key)
        if held is None or fact.precision > held.precision:
            best[key] = fact
    # Group by (kind, period, unit) and keep one concept per group.
    groups: dict[tuple[str, dt.date, dt.date, str], dict[str, list[Fact]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for (concept, _axis, _member, unit, start, end, kind), fact in best.items():
        groups[(kind, start, end, unit)][concept].append(fact)
    rows: list[RevenueRow] = []
    totals: dict[tuple[dt.date, dt.date, str], Decimal] = {}
    for (kind, start, end, unit), by_concept in groups.items():
        concept = min(by_concept, key=lambda c: (-len(by_concept[c]), concept_rank[c]))
        for fact in by_concept[concept]:
            value = fact.number
            assert value is not None
            axis, member = _axis_member(fact.context)
            if kind == "total":
                label, source = "Total revenue", "derived"
                totals[(start, end, unit)] = value
            else:
                assert member is not None
                filed = labels.get(member)
                label = filed or derived_label(member)
                source = "filing" if filed else "derived"
            rows.append(
                RevenueRow(
                    kind=kind,  # type: ignore[arg-type]
                    axis=axis,
                    member=member,
                    label=label,
                    label_source=source,  # type: ignore[arg-type]
                    concept=concept,
                    start=start,
                    end=end,
                    value=value,
                    unit=unit,
                    decimals=fact.decimals,
                    context_id=fact.context.id,
                    fact_id=fact.id,
                )
            )
    out: list[RevenueRow] = [r for r in rows if r.kind == "total"]
    breakdowns: dict[tuple[str, dt.date, dt.date, str], list[RevenueRow]] = defaultdict(list)
    for r in rows:
        if r.kind != "total":
            breakdowns[(r.kind, r.start, r.end, r.unit)].append(r)
    for (_kind, start, end, unit), members in breakdowns.items():
        members.sort(key=lambda r: (-r.value, r.label))
        out.extend(mark_subtotals(members, totals.get((start, end, unit))))
    out.sort(key=lambda r: (r.end, r.start, r.kind, -r.value))
    return SegmentParse(rows=out, facts_seen=seen, skipped_multi=skipped)


def _axis_member(context: Context) -> tuple[str | None, str | None]:
    member = _breakdown_member(context)
    if not member:
        return None, None
    return member


# ------------------------------------------------------------------------ shares outstanding


@dataclass(frozen=True)
class ShareClass:
    member: str
    label: str
    shares: Decimal
    fact: Fact


@dataclass(frozen=True)
class InstanceShares:
    """Cover-page shares outstanding from an instance: one undimensioned figure, or one per
    share class (any single-axis context, e.g. us-gaap:StatementClassOfStockAxis)."""

    as_of: dt.date
    total: Decimal | None
    classes: list[ShareClass]


def instance_shares(instance: Instance, labels: Labels) -> InstanceShares | None:
    facts = [
        f
        for f in instance.facts
        if f.concept == "dei:EntityCommonStockSharesOutstanding"
        and f.context.instant is not None
        and f.number is not None
        and not f.context.typed
        and len(f.context.dims) <= 1
    ]
    if not facts:
        return None
    as_of = max(f.context.instant for f in facts if f.context.instant is not None)
    latest = [f for f in facts if f.context.instant == as_of]
    total = next((f.number for f in latest if not f.context.dims), None)
    classes: dict[str, ShareClass] = {}
    for f in latest:
        if not f.context.dims or f.number is None:
            continue
        member = f.context.dims[0][1]
        held = classes.get(member)
        if held is None or f.precision > held.fact.precision:
            classes[member] = ShareClass(
                member, labels.get(member, standard=True) or derived_label(member), f.number, f
            )
    return InstanceShares(as_of, total, sorted(classes.values(), key=lambda c: c.member))


def balance_sheet_shares(instance: Instance) -> Fact | None:
    """The latest undimensioned us-gaap CommonStockSharesOutstanding or ifrs-full
    NumberOfSharesOutstanding of the instance."""
    for concept in ("us-gaap:CommonStockSharesOutstanding", "ifrs-full:NumberOfSharesOutstanding"):
        facts = [
            f
            for f in instance.facts
            if f.concept == concept
            and not f.context.dims
            and f.context.instant is not None
            and f.number is not None
        ]
        if facts:
            return max(facts, key=lambda f: (f.context.instant, f.precision))
    return None


# ----------------------------------------------------------------------------- ADS ratio

_NUMBER_WORDS: dict[str, Decimal] = {
    word: Decimal(n)
    for word, n in (
        ("one", 1),
        ("two", 2),
        ("three", 3),
        ("four", 4),
        ("five", 5),
        ("six", 6),
        ("seven", 7),
        ("eight", 8),
        ("nine", 9),
        ("ten", 10),
        ("twenty", 20),
        ("a half", "0.5"),
        ("one-half", "0.5"),
    )
}

# "ADSs (each ADS represents five (5) common shares)", "American Depositary Receipts, each
# representing one B Share", "each American Depositary Share represents 10 ordinary shares".
_ADS_PATTERN = re.compile(
    r"(?:ADSs?|ADRs?|American\s+Depositary\s+(?:Shares?|Receipts?))\b[^.;]{0,60}?"
    r"\brepresent(?:s|ing)?\s+(?P<n>a\s+half|[a-z-]+|\d+(?:\.\d+)?)(?:\s*\(\s*\d+\s*\))?\s+"
    r"(?:of\s+(?:a|an|one)\s+)?(?:[A-Z]\s+)?(?:common|ordinary|registered)?\s*shares?\b",
    re.IGNORECASE,
)


def ads_ratio(text: str) -> tuple[Decimal, str] | None:
    """Home-market shares per ADS as the filing states it, and the sentence it is read from:
    "each ADS represents five (5) common shares" -> 5; "American Depositary Receipts, each
    representing one B Share" -> 1."""
    flat = re.sub(r"\s+", " ", text)
    for match in _ADS_PATTERN.finditer(flat):
        word = match.group("n").lower()
        if re.fullmatch(r"\d+(?:\.\d+)?", word):
            value = Decimal(word)
        elif word in _NUMBER_WORDS:
            value = _NUMBER_WORDS[word]
        else:
            continue
        if value <= 0:
            continue
        start = max(0, match.start() - 80)
        return value, flat[start : match.end() + 20].strip()
    return None
