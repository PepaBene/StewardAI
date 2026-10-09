"""P3: institutional and major ownership as separate, dated layers, as pure functions.

The rules this module exists to enforce:
  * a 13F position is an institutional report, never a beneficial stake;
  * a later 13F amendment only counts once it was known, and a RESTATEMENT replaces the report
    while NEW HOLDINGS adds to it;
  * each ownership system (13F, 13D/G, N-PORT, national voting rights) is summed only within
    itself. There is deliberately no combined total, because the systems overlap (a fund's holding
    is also inside its manager's 13F);
  * what a lane does not account for is shown as a remainder against an explicit denominator, and
    a lane that exceeds its denominator is flagged, not clipped.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Literal

ZERO = Decimal(0)


# --------------------------------------------------------------------------- 13F amendments
@dataclass(frozen=True)
class Filing13F:
    accession: str
    cik: str
    period: dt.date
    filed: dt.date
    known_at: dt.datetime
    submission_type: Literal["13F-HR", "13F-HR/A"]
    amendment_type: Literal["RESTATEMENT", "NEW HOLDINGS"] | None = None
    manager_name: str | None = None
    # "unknown" until the source says otherwise; never assumed to be "none".
    confidential_treatment: Literal["none_indicated", "requested", "unknown"] = "unknown"
    # From the filing's summary page; None when never read, not zero.
    reported_line_count: int | None = None
    reported_total_value_usd: Decimal | None = None
    omitted_holdings_notice: bool | None = None


@dataclass(frozen=True)
class EffectiveReport:
    cik: str
    period: dt.date
    accessions: tuple[str, ...]  # the filings whose holdings make up the report at the cutoff
    known_at: dt.datetime  # when the report reached its current content
    amended: bool
    flags: tuple[str, ...] = field(default_factory=tuple)


def effective_reports(
    filings: Iterable[Filing13F], as_of: dt.datetime
) -> dict[tuple[str, dt.date], EffectiveReport]:
    """One report per (manager, period) as known at `as_of`. A filing known later is invisible."""
    groups: dict[tuple[str, dt.date], list[Filing13F]] = defaultdict(list)
    for f in filings:
        if f.known_at <= as_of:
            groups[(f.cik, f.period)].append(f)
    out: dict[tuple[str, dt.date], EffectiveReport] = {}
    for (cik, period), rows in groups.items():
        # An amendment never sorts before the original it amends, even when both are known at
        # the same moment (end of the filing day): the HR is applied first.
        rows.sort(key=lambda r: (r.known_at, r.submission_type != "13F-HR", r.accession))
        accessions: list[str] = []
        flags: list[str] = []
        known = rows[0].known_at
        amended = False
        for r in rows:
            if r.submission_type == "13F-HR":
                accessions, known = [r.accession], r.known_at
                continue
            amended = True
            if r.amendment_type == "RESTATEMENT":
                accessions, known = [r.accession], r.known_at
            elif r.amendment_type == "NEW HOLDINGS":
                if not accessions:
                    flags.append(f"{r.accession}: adds holdings but the base filing is not known")
                accessions.append(r.accession)
                known = r.known_at
            else:
                flags.append(f"{r.accession}: amendment type missing, not applied")
        out[(cik, period)] = EffectiveReport(
            cik, period, tuple(accessions), known, amended, tuple(flags)
        )
    return out


@dataclass(frozen=True)
class QuarterChoice:
    """Which reporting quarter the manager lane shows, and who has not filed it yet."""

    period: dt.date | None
    filed: tuple[str, ...]  # managers with a report for that quarter
    not_yet_filed: tuple[str, ...]  # managers known to hold the name whose report is not known yet
    share_filed: Decimal  # of all managers, the share that filed the chosen quarter
    fell_back: bool  # no quarter had enough filers: the best-covered one is shown, and said so


def choose_common_quarter(
    reports: Mapping[tuple[str, dt.date], EffectiveReport],
    *,
    min_share: Decimal = Decimal("0.8"),
) -> QuarterChoice:
    """The latest quarter that at least `min_share` of the managers have filed (13F filings arrive
    over 45 days, so the newest quarter is often only partly in). A manager that has not filed that
    quarter is named, not silently carried forward from an older one, so the lane never mixes
    report dates without saying so."""
    managers = sorted({cik for cik, _ in reports})
    if not managers:
        return QuarterChoice(None, (), (), ZERO, False)
    by_period: dict[dt.date, set[str]] = defaultdict(set)
    for cik, period in reports:
        by_period[period].add(cik)
    for period in sorted(by_period, reverse=True):
        share = Decimal(len(by_period[period])) / Decimal(len(managers))
        if share >= min_share:
            filed = tuple(sorted(by_period[period]))
            return QuarterChoice(
                period,
                filed,
                tuple(m for m in managers if m not in by_period[period]),
                share,
                False,
            )
    # No quarter reached the bar: show the best-covered one (the latest among ties) and say so.
    best = max(by_period, key=lambda p: (len(by_period[p]), p))
    return QuarterChoice(
        best,
        tuple(sorted(by_period[best])),
        tuple(m for m in managers if m not in by_period[best]),
        Decimal(len(by_period[best])) / Decimal(len(managers)),
        True,
    )


@dataclass(frozen=True)
class Holding13F:
    accession: str
    cusip: str
    shares: Decimal | None
    value_usd: Decimal | None
    put_call: str | None = None
    title_of_class: str | None = None


@dataclass(frozen=True)
class PositionLine:
    cusip: str
    title_of_class: str | None
    is_option: bool
    put_call: str | None
    shares: Decimal | None
    value_usd: Decimal | None


def effective_positions(
    report: EffectiveReport, holdings: Iterable[Holding13F]
) -> list[PositionLine]:
    """Positions of one effective report. Options stay separate lines and are not shares owned."""
    keep = set(report.accessions)
    acc: dict[tuple[str, str | None, str | None], list[Holding13F]] = defaultdict(list)
    for h in holdings:
        if h.accession in keep:
            acc[(h.cusip, h.title_of_class, h.put_call)].append(h)
    lines: list[PositionLine] = []
    for (cusip, title, pc), rows in sorted(acc.items(), key=lambda kv: (kv[0][0], str(kv[0]))):
        # a total with a missing part is not a total, so it is not published
        has_shares = all(r.shares is not None for r in rows)
        has_value = all(r.value_usd is not None for r in rows)
        lines.append(
            PositionLine(
                cusip,
                title,
                pc is not None,
                pc,
                sum((r.shares or ZERO for r in rows), ZERO) if has_shares else None,
                sum((r.value_usd or ZERO for r in rows), ZERO) if has_value else None,
            )
        )
    return lines


def manager_name_changes(filings: Iterable[Filing13F]) -> dict[str, list[tuple[dt.date, str]]]:
    """Managers whose reported name changed. Identity is the CIK, so a rename never splits a
    manager into two holders; the changes are listed so a reviewer can see them."""
    seen: dict[str, list[tuple[dt.date, str]]] = defaultdict(list)
    for f in sorted(filings, key=lambda r: (r.period, r.known_at)):
        if f.manager_name and (not seen[f.cik] or seen[f.cik][-1][1] != f.manager_name):
            seen[f.cik].append((f.period, f.manager_name))
    return {cik: names for cik, names in seen.items() if len(names) > 1}


# ------------------------------------------------------------------------------ CUSIP reuse
@dataclass(frozen=True)
class CusipMapping:
    cusip: str
    issuer_id: int
    valid_from: dt.date | None
    valid_to: dt.date | None
    share_class: str | None = None


def resolve_cusip(
    cusip: str, on: dt.date, mappings: Iterable[CusipMapping]
) -> tuple[CusipMapping | None, str | None]:
    """The issuer a CUSIP meant on a date. If two issuers both claim it on that date, or none
    does, the answer is None with the reason: a reused CUSIP is never joined by guess."""
    live = [
        m
        for m in mappings
        if m.cusip == cusip
        and (m.valid_from is None or m.valid_from <= on)
        and (m.valid_to is None or on <= m.valid_to)
    ]
    if not live:
        return None, "no issuer is mapped to this CUSIP on that date"
    if len({m.issuer_id for m in live}) > 1:
        return None, "more than one issuer holds this CUSIP on that date"
    return live[0], None


# ---------------------------------------------------------------------------- rank movement
@dataclass(frozen=True)
class RankMove:
    manager: str
    prev_rank: int | None
    cur_rank: int | None
    prev_shares: Decimal | None
    cur_shares: Decimal | None
    kind: Literal["new", "exited", "increased", "decreased", "unchanged"]


def _ranks(book: Mapping[str, Decimal]) -> dict[str, int]:
    ordered = sorted(book.items(), key=lambda kv: (-kv[1], kv[0]))
    return {m: i + 1 for i, (m, _) in enumerate(ordered)}


def rank_flow(
    prev: Mapping[str, Decimal], cur: Mapping[str, Decimal], top_n: int = 10
) -> list[RankMove]:
    """Quarter-to-quarter holder movement among the top holders of either quarter. This describes
    what was reported; it says nothing about why, and is not read as trading intent."""
    pr, cr = _ranks(prev), _ranks(cur)
    names = {m for m, r in pr.items() if r <= top_n} | {m for m, r in cr.items() if r <= top_n}
    moves: list[RankMove] = []
    for m in names:
        p, c = prev.get(m), cur.get(m)
        if p is None:
            kind = "new"
        elif c is None:
            kind = "exited"
        elif c > p:
            kind = "increased"
        elif c < p:
            kind = "decreased"
        else:
            kind = "unchanged"
        moves.append(RankMove(m, pr.get(m), cr.get(m), p, c, kind))  # type: ignore[arg-type]
    return sorted(moves, key=lambda x: (x.cur_rank is None, x.cur_rank or 0, x.manager))


# ------------------------------------------------------------------------------- the lanes
LaneKey = Literal["institutional_13f", "control_13dg", "fund_nport", "national_voting"]

LANE_TITLE: dict[LaneKey, str] = {
    "institutional_13f": "Institutional managers (13F)",
    "control_13dg": "Reported control stakes (13D/G)",
    "fund_nport": "Fund holdings (N-PORT)",
    "national_voting": "National voting-rights notices",
}
LANE_BASIS: dict[LaneKey, str] = {
    "institutional_13f": "Long US-listed shares reported quarterly by managers over $100m; "
    "options excluded. A report of holdings, not a beneficial stake.",
    "control_13dg": "Largest single reporting person per filing, so a group figure is a floor, "
    "never a sum. Reported percent of class, not recalculated.",
    "fund_nport": "Holdings of registered funds at their report date. Funds sit inside their "
    "manager's 13F, so this lane overlaps the first.",
    "national_voting": "Voting rights over the threshold, by jurisdiction. Voting rights are not "
    "economic interest.",
}
OVERLAPS: dict[LaneKey, tuple[LaneKey, ...]] = {
    "institutional_13f": ("fund_nport", "control_13dg"),
    "control_13dg": ("institutional_13f",),
    "fund_nport": ("institutional_13f",),
    "national_voting": ("control_13dg",),
}


@dataclass(frozen=True)
class LaneEntry:
    holder: str
    shares: Decimal | None
    reported_percent: Decimal | None
    report_date: dt.date | None  # what the figure is measured on
    filed: dt.date | None
    known_at: dt.datetime
    amended: bool = False

    @property
    def lag_days(self) -> int | None:
        if self.report_date is None or self.filed is None:
            return None
        return (self.filed - self.report_date).days


STALE_STAKE_DAYS = 548  # about 18 months: a 13G is refreshed at least yearly while it stands


def one_stake_per_position(
    rows: Sequence[tuple[str, Decimal | None, Decimal | None, dt.date | None, dt.datetime]],
) -> set[int]:
    """Indexes (into `rows`: filer, shares, percent, event date, known_at) to keep, so a parent
    and its subsidiary, or the members of a group, who each file the same stake are counted once.
    Two filers reporting the same share count, percent and event date are one stake; the
    latest-known filing of them is kept. A row with no share count is never merged."""
    best: dict[tuple[object, ...], int] = {}
    keep: set[int] = set()
    for i, (_filer, shares, percent, event, known) in enumerate(rows):
        if shares is None:
            keep.add(i)
            continue
        key = (shares, percent, event)
        if key not in best or known > rows[best[key]][4]:
            best[key] = i
    return keep | set(best.values())


@dataclass(frozen=True)
class Lane:
    key: LaneKey
    title: str
    basis: str
    status: Literal["covered", "no_data", "not_covered"]
    entries: tuple[LaneEntry, ...]
    total_shares: Decimal | None
    percent_of_denominator: Decimal | None
    remainder_shares: Decimal | None
    remainder_percent: Decimal | None
    overlaps_with: tuple[LaneKey, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class OwnershipView:
    denominator: Decimal | None
    denominator_as_of: dt.date | None
    lanes: tuple[Lane, ...]
    warnings: tuple[str, ...]
    # There is intentionally no `combined_total`: the lanes overlap.


def build_lane(
    key: LaneKey,
    entries: Sequence[LaneEntry],
    denominator: Decimal | None,
    *,
    covered: bool = True,
    as_of: dt.date | None = None,
) -> Lane:
    """One lane: its entries, its own total, and its remainder against the denominator."""
    notes: list[str] = []
    if not covered:
        return Lane(
            key,
            LANE_TITLE[key],
            LANE_BASIS[key],
            "not_covered",
            (),
            None,
            None,
            None,
            None,
            OVERLAPS[key],
            ("No source we can read covers this lane.",),
        )
    if not entries:
        return Lane(
            key,
            LANE_TITLE[key],
            LANE_BASIS[key],
            "no_data",
            (),
            None,
            None,
            None,
            None,
            OVERLAPS[key],
            ("Nothing was known in this lane at the chosen date.",),
        )
    known = [e.shares for e in entries if e.shares is not None]
    total = sum(known, ZERO) if known else None
    if len(known) < len(entries):
        notes.append(
            f"{len(entries) - len(known)} entries report no share count and are not in the total"
        )
    if key == "control_13dg":
        notes.append("The total is a floor: each filing shows its largest reporting person.")
        if as_of is not None:
            stale = sum(
                1
                for e in entries
                if e.report_date is not None and (as_of - e.report_date).days > STALE_STAKE_DAYS
            )
            if stale:
                notes.append(
                    f"{stale} stake(s) were last reported over 18 months before this date and "
                    "may no longer stand."
                )
    dates = {e.report_date for e in entries if e.report_date is not None}
    if len(dates) > 1:
        notes.append(f"Entries are measured on {len(dates)} different dates; the total mixes them.")
    pct = rem = rem_pct = None
    if total is not None and denominator:
        pct = total / denominator * 100
        if total > denominator:
            notes.append("The lane exceeds the denominator: the figures use different bases.")
        else:
            rem, rem_pct = denominator - total, (denominator - total) / denominator * 100
    elif denominator is None:
        notes.append("No share-count denominator is known, so no remainder is shown.")
    return Lane(
        key,
        LANE_TITLE[key],
        LANE_BASIS[key],
        "covered",
        tuple(entries),
        total,
        pct,
        rem,
        rem_pct,
        OVERLAPS[key],
        tuple(notes),
    )


def build_view(
    lanes: Mapping[LaneKey, Sequence[LaneEntry]],
    denominator: Decimal | None,
    denominator_as_of: dt.date | None = None,
    *,
    not_covered: Iterable[LaneKey] = (),
    as_of: dt.date | None = None,
) -> OwnershipView:
    missing = set(not_covered)
    order: tuple[LaneKey, ...] = (
        "institutional_13f",
        "control_13dg",
        "fund_nport",
        "national_voting",
    )
    built = tuple(
        build_lane(k, lanes.get(k, ()), denominator, covered=k not in missing, as_of=as_of)
        for k in order
    )
    warnings: list[str] = []
    live = {ln.key for ln in built if ln.status == "covered"}
    for ln in built:
        for other in ln.overlaps_with:
            if ln.key in live and other in live and ln.key < other:
                warnings.append(f"{LANE_TITLE[ln.key]} and {LANE_TITLE[other]} overlap; not added")
    if denominator is None:
        warnings.append("No denominator: percentages and remainders are unavailable")
    return OwnershipView(denominator, denominator_as_of, built, tuple(warnings))


def reported_stake_delta(
    prev_percent: Decimal | None,
    prev_class: str | None,
    cur_percent: Decimal | None,
    cur_class: str | None,
) -> tuple[Decimal | None, str | None]:
    """A change in reported stake, only when both are the same class and both are reported."""
    if prev_percent is None or cur_percent is None:
        return None, "one of the two filings reports no percentage"
    if (prev_class or "").strip().lower() != (cur_class or "").strip().lower():
        return None, "the filings report different classes of shares"
    return cur_percent - prev_percent, None


# ------------------------------------------------------------------ national adapter contracts
@dataclass(frozen=True)
class OwnershipAdapterContract:
    key: str
    jurisdiction: str
    regulator: str
    thresholds_percent: tuple[Decimal, ...]
    measures: Literal["voting_rights", "economic_interest", "both"]
    chain: Literal["direct", "indirect", "both"]
    denominator: str
    publication_lag: str
    reviewed_examples: int = 0
    correct_examples: int = 0


def adapter_precision(c: OwnershipAdapterContract) -> Decimal | None:
    return Decimal(c.correct_examples) / c.reviewed_examples if c.reviewed_examples else None


def may_enable(
    c: OwnershipAdapterContract, *, min_examples: int = 20, min_precision: Decimal = Decimal("0.98")
) -> tuple[bool, str]:
    """A jurisdiction is switched on only after 20 reviewed examples meet the P0 precision bar."""
    if c.reviewed_examples < min_examples:
        return False, f"{c.reviewed_examples} of {min_examples} reviewed examples"
    p = adapter_precision(c)
    if p is None or p < min_precision:
        return False, f"precision {p} is below {min_precision}"
    return True, "meets the reviewed-example bar"


def _p(*xs: str) -> tuple[Decimal, ...]:
    return tuple(Decimal(x) for x in xs)


NATIONAL_ADAPTERS: dict[str, OwnershipAdapterContract] = {
    "uk_tr1": OwnershipAdapterContract(
        "uk_tr1",
        "GB",
        "FCA (via the National Storage Mechanism)",
        _p("3", "5", "10", "15", "20", "25", "30", "50", "75"),
        "voting_rights",
        "both",
        "total voting rights announced by the issuer",
        "within 4 trading days of the crossing",
    ),
    "de_wphg": OwnershipAdapterContract(
        "de_wphg",
        "DE",
        "BaFin",
        _p("3", "5", "10", "15", "20", "25", "30", "50", "75"),
        "both",
        "both",
        "total voting rights published by the issuer",
        "within 4 trading days of the crossing",
    ),
    "fr_amf": OwnershipAdapterContract(
        "fr_amf",
        "FR",
        "AMF",
        _p("5", "10", "15", "20", "25", "30", "33.33", "50", "66.67", "90", "95"),
        "both",
        "both",
        "capital and voting rights as published by the issuer",
        "within 4 trading days",
    ),
    "jp_edinet": OwnershipAdapterContract(
        "jp_edinet",
        "JP",
        "FSA (EDINET large-shareholding report)",
        _p("5"),
        "economic_interest",
        "both",
        "issued shares as stated in the report",
        "within 5 business days",
    ),
    "ca_sedarplus": OwnershipAdapterContract(
        "ca_sedarplus",
        "CA",
        "CSA (early warning report)",
        _p("10"),
        "both",
        "both",
        "outstanding securities of the class",
        "promptly, press release at once",
    ),
    "au_asx": OwnershipAdapterContract(
        "au_asx",
        "AU",
        "ASX (substantial holder notice)",
        _p("5"),
        "voting_rights",
        "both",
        "voting power in the company",
        "within 2 business days",
    ),
}


def with_review(
    c: OwnershipAdapterContract, reviewed: int, correct: int
) -> OwnershipAdapterContract:
    return replace(c, reviewed_examples=reviewed, correct_examples=correct)


# ----------------------------------------------------------------------------- control chains
def hierarchy_conflict(beneficial_percent: Decimal | None, parent_consolidates: bool) -> str | None:
    """P3.5: control data from a corporate registry (PSC, GLEIF) and beneficial ownership from a
    filing are separate facts. If the registry says the holder consolidates a listed issuer but
    no stake is reported (or the reverse), the pair goes to review; neither overrides the other."""
    if parent_consolidates and (beneficial_percent is None or beneficial_percent < Decimal(25)):
        return "registry shows consolidation but no filing reports a stake of 25% or more"
    if (
        not parent_consolidates
        and beneficial_percent is not None
        and beneficial_percent > Decimal(50)
    ):
        return "a filing reports a majority stake but the registry shows no consolidation"
    return None
