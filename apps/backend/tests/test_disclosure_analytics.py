"""P8.1, P8.3, P8.4, P9.7 pure logic: coverage cells, quality rates, look-through and the screen.

The rules under test: a missing record is uncovered (never a zero), a share with no denominator is
null, an unknown weight stays out of every total, and a screen excludes a holding with missing data
instead of scoring it."""

import datetime as dt
from decimal import Decimal as D

from api.main import create_app
from steward.disclosure import coverage_report as cr
from steward.disclosure import look_through as lt
from steward.disclosure import screening as sc

AS_OF = dt.datetime(2026, 10, 7, 12, tzinfo=dt.UTC)


def at(month: int, day: int, year: int = 2026) -> dt.datetime:
    return dt.datetime(year, month, day, 12, tzinfo=dt.UTC)


HOLD = [
    cr.Holding("AAA", D(600), "US", 1),
    cr.Holding("BBB", D(300), "US", None),
    cr.Holding("CCC", D(100), "GB", 3),
]


def test_routes_are_get_only_and_models_are_prefixed() -> None:
    schema = create_app().openapi()
    for path in (
        "/disclosures/data/coverage",
        "/disclosures/company/{ticker}/evidence",
        "/disclosures/portfolio/look-through",
        "/disclosures/screen",
    ):
        assert list(schema["paths"][path]) == ["get"]
    mine = [n for n in schema["components"]["schemas"] if n.startswith("Analytics")]
    assert "AnalyticsCoverageOut" in mine and "AnalyticsScreenOut" in mine
    assert not any(n.startswith("Analytics") and n.endswith("Out_") for n in mine)


def test_jurisdiction_prefers_isin_then_country_then_unknown() -> None:
    assert cr.jurisdiction_of("GB00B03MLX29", "NL") == "GB"
    assert cr.jurisdiction_of(None, "NL") == "NL"
    assert cr.jurisdiction_of("", None) == "XX"


def test_state_never_available_without_a_record_and_flags_stale() -> None:
    spec = cr.BY_KEY["estimates"]
    ev = cr.Evidence(2, at(1, 1), at(2, 1))
    assert cr.state_for(spec, HOLD[0], ev, None, AS_OF) == "stale"
    fresh = cr.Evidence(1, at(9, 1), at(9, 20))
    assert cr.state_for(spec, HOLD[0], fresh, None, AS_OF) == "available"
    assert cr.state_for(spec, HOLD[1], cr.NO_EVIDENCE, None, AS_OF) == "unresolved_identity"
    assert cr.state_for(spec, HOLD[0], cr.NO_EVIDENCE, "paid_only", AS_OF) == "paid_only"
    assert cr.state_for(spec, HOLD[0], cr.NO_EVIDENCE, None, AS_OF) == "none_known"


def test_coverage_cells_keep_denominators_and_count_uncovered() -> None:
    evidence = {("AAA", "insiders"): cr.Evidence(3, at(8, 1), at(9, 1))}
    cells = cr.coverage_cells(HOLD, evidence, {}, AS_OF)
    us = next(c for c in cells if c["dataset"] == "insiders" and c["jurisdiction"] == "US")
    assert (us["tickers"], us["covered"], us["uncovered"]) == (2, 1, 1)
    assert us["denominator_value"] == "900" and us["covered_value"] == "600"
    assert us["uncovered_value"] == "300" and us["covered_share"] == str(D(600) / D(900))
    assert us["first_known_at"] == at(8, 1) and us["last_known_at"] == at(9, 1)
    assert us["blind_spots"][0].startswith("1 held with no record: BBB")
    gb = next(c for c in cells if c["dataset"] == "insiders" and c["jurisdiction"] == "GB")
    assert gb["covered"] == 0 and gb["uncovered"] == 1
    assert gb["first_known_at"] is None  # no date is invented for an uncovered cell


def test_zero_denominator_gives_null_share() -> None:
    cells = cr.coverage_cells([cr.Holding("ZZZ", D(0), "US", 9)], {}, {}, AS_OF)
    assert all(c["covered_share"] is None for c in cells)


def test_company_evidence_links_and_unresolved_identity() -> None:
    ev = {"insiders": cr.Evidence(4, at(8, 1), at(9, 1), "2026-08-01", "2026-09-01")}
    out = cr.company_evidence("AAA", HOLD[0], ev, {}, AS_OF, D(1000))
    by = {d["dataset"]: d for d in out["datasets"]}
    assert out["weight"] == "0.6" and out["covered_datasets"] == 1
    assert by["insiders"]["api_path"] == "/disclosures/holdings/AAA/insiders"
    assert by["financials"]["api_path"] == "/disclosures/issuers/1/statements"
    assert by["financials"]["coverage_state"] == "none_known" and by["financials"]["count"] == 0
    blank = cr.company_evidence("BBB", HOLD[1], {}, {}, AS_OF, D(1000))
    fin = next(d for d in blank["datasets"] if d["dataset"] == "financials")
    assert fin["coverage_state"] == "unresolved_identity" and fin["api_path"] is None
    unheld = cr.company_evidence("NOPE", None, {}, {}, AS_OF, D(1000))
    assert unheld["held"] is False and unheld["weight"] is None


def test_quality_rates_have_numerator_denominator_and_null_when_unsampled() -> None:
    labels = [
        {"dataset": "insider_class", "sample_key": "s", "item_ref": f"i{n}", "correct": n != 0}
        for n in range(50)
    ]
    labels.append(
        {"dataset": "insider_class", "sample_key": "s", "item_ref": "i0", "correct": True}
    )
    labels.append(
        {
            "dataset": "relationship_claim",
            "sample_key": "s",
            "item_ref": "r",
            "correct": False,
            "human_value": "unrelated_third_party",
        }
    )
    q = {r["dataset"]: r for r in cr.quality_rows(labels)}
    assert (q["insider_class"]["errors"], q["insider_class"]["labelled"]) == (
        0,
        50,
    )  # latest label wins
    assert (
        q["insider_class"]["state"] == "meets_bar" and q["insider_class"]["error_rate"] == "0.0000"
    )
    assert q["relationship_claim"]["state"] == "sample_too_small"
    assert q["relationship_claim"]["error_rate"] == "1.0000"
    assert (
        q["ownership_match"]["error_rate"] is None
        and q["ownership_match"]["state"] == "not_sampled"
    )
    stages = cr.release_order(list(q.values()))
    assert [s["order"] for s in stages] == [1, 2, 3, 4, 5]
    assert stages[0]["state"] == "gates_open" and stages[-1]["state"] == "not_started"


# ----------------------------------------------------------------------------------- look-through
def line(fund: str, held: str | None, pct: str | None, period: dt.date) -> lt.FundLine:
    return lt.FundLine(fund, held, None, None if pct is None else D(pct), period, at(8, 1))


def rel(ticker: str, kind: str, share: str | None, name: str | None = "Big Co") -> lt.Relationship:
    return lt.Relationship(
        ticker,
        kind,
        "k",
        name,
        name is None,
        None if share is None else D(share),
        "issuer_revenue",
        "FY2025",
        at(8, 1),
    )


def test_look_through_totals_use_known_weights_and_show_denominator() -> None:
    holdings = [
        cr.Holding("AAA", D(500), "US", 1),
        cr.Holding("BBB", D(300), "US", 2),
        cr.Holding("FUND", D(200), "US", None),
    ]
    when = at(10, 7)
    out = lt.build(
        holdings,
        [
            line("FUND", "AAA", "0.5", dt.date(2026, 6, 30)),
            line("FUND", None, "0.2", dt.date(2026, 6, 30)),
            line("FUND", "BBB", None, dt.date(2026, 6, 30)),
        ],
        [
            rel("AAA", "customer", "0.25"),
            rel("BBB", "customer", None),
            rel("BBB", "supplier", None, None),
        ],
        [
            {
                "ticker": "BBB",
                "deal_id": 7,
                "title": "BBB buys X",
                "deal_type": "acquisition",
                "state": "agreed",
                "weight": D("0.3"),
            }
        ],
        when,
        dt.date(2026, 10, 6),
    )
    assert out["denominator_value"] == "1000"
    ex = {e["ticker"]: e for e in out["exposures"]}
    assert ex["AAA"]["direct_weight"] == "0.5" and D(ex["AAA"]["fund_weight"]) == D("0.1")
    assert D(ex["AAA"]["total_weight"]) == D("0.6")
    totals = out["totals"]
    assert totals["unknown_weight_holdings"] == [
        "BBB"
    ]  # no stated share: not in any weighted total
    assert D(totals["concentrated_customer_weight"]) == D("0.6")
    assert D(totals["largest_customer_weighted"]) == D("0.6") * D("0.25")
    assert totals["customer_known_weight"] == totals["concentrated_customer_weight"]
    assert D(totals["deal_exposed_weight"]) == D("0.3")
    assert D(totals["unmapped_fund_weight"]) == D("0.2") * D("0.2")
    unknown_rows = [r for r in out["relationships"] if r["weight_state"] == "unknown"]
    assert len(unknown_rows) == 2 and all(r["weighted_share"] is None for r in unknown_rows)
    assert next(r for r in out["relationships"] if r["anonymous"])["counterparty_name"] is None
    fund = out["funds"][0]
    assert (
        fund["lag_days"] == (when.date() - dt.date(2026, 6, 30)).days and fund["unknown_lines"] == 1
    )
    assert out["mixed_as_of"] is True and out["max_fund_lag_days"] == fund["lag_days"]


def test_look_through_with_no_holdings_is_empty_not_zero_weighted() -> None:
    out = lt.build([], [], [], [], AS_OF, None)
    assert out["totals"]["denominator_weight"] is None and out["exposures"] == []
    assert out["mixed_as_of"] is False and out["max_fund_lag_days"] is None


# ------------------------------------------------------------------------------------------ screen
def fv(value: object, period: str = "FY2025") -> sc.FieldValue:
    return sc.FieldValue(value, period, at(8, 1), "test")  # type: ignore[arg-type]


UNIVERSE = [
    sc.Universe(
        "AAA",
        D("0.5"),
        D(500),
        {"cash_conversion": fv(D("1.4")), "customer_concentration": fv(D("0.3"))},
    ),
    sc.Universe("BBB", D("0.3"), D(300), {"cash_conversion": fv(D("0.6"))}),
    sc.Universe("CCC", D("0.2"), D(200), {}),
]


def test_screen_excludes_missing_and_failed_separately_and_never_scores_zero() -> None:
    out = sc.screen(UNIVERSE, {"min_cash_conversion": D("1")})
    assert out["universe_size"] == 3 and out["denominator_value"] == "1000"
    assert [m["ticker"] for m in out["matched"]] == ["AAA"]
    kinds = {e["ticker"]: e["kind"] for e in out["excluded"]}
    assert kinds == {"BBB": "failed", "CCC": "missing"}
    assert out["excluded_missing"] == 1 and out["excluded_failed"] == 1
    assert (
        out["field_coverage"][0]["with_data"] == 2 and out["field_coverage"][0]["without_data"] == 1
    )
    assert out["matched_weight"] == "0.5"


def test_screen_needs_every_active_criterion_and_a_zero_threshold_does_not_admit_missing() -> None:
    out = sc.screen(
        UNIVERSE, {"min_cash_conversion": D("1"), "min_customer_concentration": D("0.2")}
    )
    assert [m["ticker"] for m in out["matched"]] == ["AAA"]
    zero = sc.screen(UNIVERSE, {"min_customer_concentration": D(0)})
    assert [m["ticker"] for m in zero["matched"]] == ["AAA"]  # BBB and CCC have no value: excluded
    assert {e["ticker"] for e in zero["excluded"]} == {"BBB", "CCC"}


def test_screen_flag_and_absolute_criteria() -> None:
    u = [
        sc.Universe(
            "UP", D("0.5"), D(5), {"stake_change_pct": fv(D("2.5")), "deal_exposure": fv(True)}
        ),
        sc.Universe("DOWN", D("0.5"), D(5), {"stake_change_pct": fv(D("-3"))}),
    ]
    assert [m["ticker"] for m in sc.screen(u, {"min_stake_change_pct": D(2)})["matched"]] == [
        "DOWN",
        "UP",
    ]
    flagged = sc.screen(u, {"deal_exposure": True})
    assert [m["ticker"] for m in flagged["matched"]] == ["UP"]
    assert flagged["excluded"][0]["kind"] == "missing"  # no deal known is not proof of none


def test_screen_without_criteria_matches_nothing_and_says_so() -> None:
    out = sc.screen(UNIVERSE, {"deal_exposure": False})
    assert out["matched"] == [] and out["active_criteria"] == [] and out["universe_size"] == 3


def test_ratio_field_uses_latest_period_with_both_inputs_and_positive_denominator() -> None:
    def row(metric: str, end: dt.date, value: str, known: dt.datetime) -> dict[str, object]:
        return {
            "metric": metric,
            "period_start": dt.date(end.year, 1, 1),
            "period_end": end,
            "value": D(value),
            "known_at": known,
        }

    rows = [
        row("operating_cash_flow", dt.date(2025, 12, 31), "120", at(2, 1)),
        row("net_income", dt.date(2025, 12, 31), "100", at(2, 1)),
        row("operating_cash_flow", dt.date(2025, 12, 31), "150", at(5, 1)),  # restated, known later
        row("operating_cash_flow", dt.date(2026, 6, 30), "50", at(8, 1)),
        row("net_income", dt.date(2026, 6, 30), "-10", at(8, 1)),  # a loss: ratio meaningless
    ]
    # The latest period has a loss: Missing with the reason, never the older period instead.
    latest = sc.ratio_field(rows, "operating_cash_flow", "net_income", "obs")
    assert isinstance(latest, sc.Missing) and "undefined" in latest.reason
    got = sc.ratio_field(rows[:3], "operating_cash_flow", "net_income", "obs")
    assert isinstance(got, sc.FieldValue)
    assert got.value == D("1.5000") and got.period == "2025-12-31"
    alone = sc.ratio_field(rows[:1], "operating_cash_flow", "net_income", "obs")
    assert isinstance(alone, sc.Missing)
