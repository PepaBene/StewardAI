"""Macro/trade audit fixes (A4, A5, B10-B20, C): parsers on documented formats, dedupe and
revisions, per-series event raising, per-dimension exposure caps, point-in-time reads."""

import datetime as dt
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from pydantic import ValidationError

from steward.config.jobs import (
    ComtradeSlice,
    EcbSeries,
    FredSeries,
    JobsConfig,
    MacroSettings,
    ProviderJobSettings,
    PublicDataSettings,
    PublicSdmxSeries,
    WorldBankIndicator,
)
from steward.config.store import save_config
from steward.jobs.runner import JobContext, run_job
from steward.macro import exposure as exposure_mod
from steward.macro.collect import (
    MacroDeps,
    MacroError,
    Observation,
    fetch_ecb_series,
    parse_ecb_series,
    parse_fred,
    parse_jsonstat,
    parse_period,
    run_macro_collect,
    store_and_evaluate,
)
from steward.macro.geo import iso2_for_m49
from steward.macro.hs import chapter_comparable, normalize_hs_revision
from steward.macro.providers import (
    SdmxAmbiguousError,
    TradePoint,
    finite_decimal,
    frequency_of,
    parse_comtrade,
    parse_fred_vintages,
    parse_sdmx_json,
    parse_world_bank,
    trade_metrics,
)
from steward.macro.public_collect import PublicDataDeps, run_public_data_collect
from steward.macro.read import context_for_holding
from steward.macro.sdmx_keys import SdmxKeyError, pin_key
from steward.macro.store import MacroRow, store_macro_rows, store_trade_points

NOW = dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC)
TODAY = NOW.date()
ECB_SERIES = EcbSeries(key="m3", label="M3 growth", flow_key="BSI/M.U2.Y.V.M30.X.I.U2.2300.Z01.A")


# ---------------------------------------------------------------------------------------
# Periods, numbers (A4, B19, C)
# ---------------------------------------------------------------------------------------


def test_parse_period_accepts_every_sdmx_period_form() -> None:
    assert parse_period("2026") == dt.date(2026, 1, 1)
    assert parse_period("2026-07") == dt.date(2026, 7, 1)
    assert parse_period("202607") == dt.date(2026, 7, 1)
    assert parse_period("2026-Q2") == dt.date(2026, 4, 1)
    assert parse_period("2026-S2") == dt.date(2026, 7, 1)
    assert parse_period("2026-W05") == dt.date(2026, 1, 26)
    assert parse_period("2026-07-15") == dt.date(2026, 7, 15)
    assert parse_period("2026-07-15T10:00:00Z") == dt.date(2026, 7, 15)
    for bad in ("2026-13", "2026-Q5", "soon", "", "2026-W60"):
        with pytest.raises(ValueError):
            parse_period(bad)
    assert [frequency_of(x) for x in ("2026", "2026-Q2", "2026-S1", "2026-W05", "2026-07")] == [
        "annual",
        "quarterly",
        "semiannual",
        "weekly",
        "monthly",
    ]


def test_ecb_monthly_quarterly_and_annual_rows_parse() -> None:
    """A4: 8 of the 9 default ECB series are monthly/quarterly; date.fromisoformat rejected
    '2026-07' and '2026-Q2' so they parsed to nothing."""
    text = (
        "KEY,FREQ,TIME_PERIOD,OBS_VALUE,UNIT_MULT,ADJUSTMENT\n"
        "BSI.M,M,2026-07,3.2,6,Y\n"
        "BSI.M,M,2026-08,3.4,6,Y\n"
        "BLS.Q,Q,2026-Q2,-5.0,0,\n"
        "X.A,A,2025,1.5,0,\n"
    )
    out = parse_ecb_series(text, ECB_SERIES, TODAY)
    assert [o.period for o in out] == [
        dt.date(2026, 7, 1),
        dt.date(2026, 8, 1),
        dt.date(2026, 4, 1),
        dt.date(2025, 1, 1),
    ]
    assert out[0].unit_multiplier == Decimal(10) ** 6 and out[0].seasonal_adjustment == "Y"
    assert out[0].release_calendar and out[0].geography_definition


async def test_ecb_all_rows_skipped_is_an_error_not_silence(
    caplog: pytest.LogCaptureFixture,
) -> None:
    body = "KEY,TIME_PERIOD,OBS_VALUE\nA,Q-two,1\nA,never,2\n"
    transport = httpx.MockTransport(lambda _: httpx.Response(200, text=body))
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(MacroError, match="all 2 rows were skipped"):
            await fetch_ecb_series(http, ECB_SERIES, TODAY)
    assert "skipped 2 row" in caplog.text


async def test_ecb_backfill_has_no_invented_release_date() -> None:
    body = "KEY,TIME_PERIOD,OBS_VALUE\nA,2026-07,1\n"
    transport = httpx.MockTransport(lambda _: httpx.Response(200, text=body))
    async with httpx.AsyncClient(transport=transport) as http:
        backfill = await fetch_ecb_series(http, ECB_SERIES, TODAY, since=dt.date(2016, 1, 1))
        recent = await fetch_ecb_series(http, ECB_SERIES, TODAY)
    assert backfill[0].release_date is None  # B18: not "the day the job first ran"
    assert recent[0].release_date == TODAY


def test_nan_and_infinity_are_missing_not_numbers() -> None:
    for bad in (float("nan"), float("inf"), "NaN", "Infinity", "-Infinity", "sNaN", "..", ""):
        assert finite_decimal(bad) is None
    assert finite_decimal("1.5") == Decimal("1.5") and finite_decimal(0) == Decimal(0)
    fred = FredSeries(key="x", label="x", series_id="X")
    rows = {"observations": [{"date": "2026-01-01", "value": v} for v in ("NaN", "Infinity", "1")]}
    assert [o.value for o in parse_fred(rows, fred)] == [Decimal(1)]
    ecb = "TIME_PERIOD,OBS_VALUE\n2026-01,NaN\n2026-02,Infinity\n2026-03,2\n"
    assert [o.value for o in parse_ecb_series(ecb, ECB_SERIES, TODAY)] == [Decimal(2)]
    wb = parse_world_bank(
        [{}, [{"indicator": {"id": "I"}, "country": {"id": "US"}, "date": "2025", "value": "NaN"}]],
        "u",
    )
    assert wb[0].value is None
    vint = parse_fred_vintages(
        {"observations": [{"date": "2026-01-01", "realtime_start": "2026-02-01", "value": "inf"}]},
        series_id="x",
        unit="%",
        source_url="u",
    )
    assert vint[0].value is None


def test_eurostat_jsonstat_attributes_and_vintage() -> None:
    body = {
        "id": ["geo", "time"],
        "size": [1, 2],
        "dimension": {
            "geo": {"category": {"index": {"DE": 0}}},
            "time": {"category": {"index": {"2026-06": 0, "2026-07": 1}}},
        },
        "value": {"0": 2.1, "1": float("nan")},
        "updated": "2026-08-18T11:00:00+0200",
    }
    from steward.config.jobs import EurostatSeries

    series = EurostatSeries(
        key="hicp",
        label="HICP",
        dataset="prc_hicp_minr",
        filters={"unit": "RCH_A", "s_adj": "NSA"},
    )
    out = parse_jsonstat(body, series)
    assert [o.period_label for o in out] == ["2026-06"]
    assert out[0].vintage == "2026-08-18T11:00:00+0200"
    assert out[0].transformation == "annual rate of change"
    assert out[0].seasonal_adjustment == "not seasonally adjusted"


# ---------------------------------------------------------------------------------------
# SDMX-JSON (A5) and key pinning
# ---------------------------------------------------------------------------------------

SERIES_SHAPED = {
    "meta": {"id": "x"},
    "data": {
        "dataSets": [
            {
                "series": {
                    "0:0": {
                        "attributes": [0, 0],
                        "observations": {"0": [1.5, 0], "1": [1.7, 0]},
                    },
                    "0:1": {
                        "attributes": [0, 0],
                        "observations": {"0": [9.5, 0], "1": [None, 0]},
                    },
                }
            }
        ],
        "structures": [
            {
                "dimensions": {
                    "series": [
                        {"id": "FREQ", "values": [{"id": "Q"}]},
                        {"id": "REF_AREA", "values": [{"id": "USA"}, {"id": "CAN"}]},
                    ],
                    "observation": [
                        {"id": "TIME_PERIOD", "values": [{"id": "2025-Q1"}, {"id": "2025-Q2"}]}
                    ],
                },
                "attributes": {
                    "series": [
                        {"id": "UNIT_MULT", "values": [{"id": "6"}]},
                        {"id": "ADJUSTMENT", "values": [{"id": "S"}]},
                    ],
                    "observation": [{"id": "OBS_STATUS", "values": [{"id": "A"}]}],
                },
            }
        ],
    },
}
FLAT_ALL_DIMENSIONS = {
    "data": {
        "dataSets": [{"observations": {"0:0:0": [2.0], "0:0:1": [2.5], "0:1:0": [7.0]}}],
        "structures": [
            {
                "dimensions": {
                    "series": [],
                    "observation": [
                        {"id": "FREQ", "values": [{"id": "M"}]},
                        {"id": "REF_AREA", "values": [{"id": "USA"}, {"id": "CAN"}]},
                        {"id": "TIME_PERIOD", "values": [{"id": "2026-05"}, {"id": "2026-06"}]},
                    ],
                }
            }
        ],
    }
}


def _sdmx(body: dict[str, Any], **extra: Any) -> list[Any]:
    return parse_sdmx_json(
        body,
        source="oecd",
        series_id="cpi",
        geography="XX",
        unit="%",
        frequency="monthly",
        source_url="u",
        vintage="v",
        **extra,
    )


def test_sdmx_series_shaped_response_is_parsed_with_geography_from_the_response() -> None:
    points = _sdmx(SERIES_SHAPED, geography_map={"USA": "US", "CAN": "CA"})
    by_geo = {(p.geography, p.period): p.value for p in points}
    assert by_geo[("US", dt.date(2025, 1, 1))] == Decimal("1.5")
    assert by_geo[("US", dt.date(2025, 4, 1))] == Decimal("1.7")
    assert by_geo[("CA", dt.date(2025, 1, 1))] == Decimal("9.5")
    assert by_geo[("CA", dt.date(2025, 4, 1))] is None  # a null observation is a gap
    first = points[0]
    assert first.unit_multiplier == Decimal(10) ** 6 and first.seasonal_adjustment == "S"
    assert first.frequency == "quarterly" and first.metadata["dimensions"]["FREQ"] == "Q"


def test_sdmx_flat_all_dimensions_response_reads_the_time_dimension_by_name() -> None:
    points = _sdmx(FLAT_ALL_DIMENSIONS)
    assert {(p.geography, p.period, p.value) for p in points} == {
        ("USA", dt.date(2026, 5, 1), Decimal(2)),
        ("USA", dt.date(2026, 6, 1), Decimal("2.5")),
        ("CAN", dt.date(2026, 5, 1), Decimal(7)),
    }
    # Pinned: the other country is dropped, not merged under the configured geography.
    pinned = _sdmx(FLAT_ALL_DIMENSIONS, expect_geography="USA")
    assert {p.geography for p in pinned} == {"USA"}
    skipped: list[str] = []
    _sdmx(FLAT_ALL_DIMENSIONS, expect_geography="USA", skipped=skipped)
    assert any("CAN" in s for s in skipped)


def test_sdmx_unpinned_series_for_one_geography_is_ambiguous() -> None:
    body = json.loads(json.dumps(FLAT_ALL_DIMENSIONS))
    dims = body["data"]["structures"][0]["dimensions"]["observation"]
    dims.insert(1, {"id": "MEASURE", "values": [{"id": "CPI"}, {"id": "HICP"}]})
    body["data"]["dataSets"][0]["observations"] = {"0:0:0:0": [1.0], "0:1:0:0": [2.0]}
    with pytest.raises(SdmxAmbiguousError, match="pin every dimension"):
        _sdmx(body)


def test_sdmx_1_0_layout_and_legacy_fixture() -> None:
    legacy = json.loads(
        (Path(__file__).parent / "fixtures" / "contracts" / "sdmx.json").read_text()
    )
    points = _sdmx(legacy)
    assert [p.value for p in points] == [Decimal("2.5"), Decimal("2.7")]
    assert {p.geography for p in points} == {"XX"}  # no area dimension: the fallback applies
    v1 = {
        "dataSets": [{"series": {"0": {"observations": {"0": [3.0]}}}}],
        "structure": {
            "dimensions": {
                "series": [{"id": "REF_AREA", "values": [{"id": "DE"}]}],
                "observation": [{"id": "TIME_PERIOD", "values": [{"id": "2026-03"}]}],
            }
        },
    }
    only = _sdmx(v1)
    assert (only[0].geography, only[0].period) == ("DE", dt.date(2026, 3, 1))


def test_bis_and_oecd_keys_are_pinned_and_validated() -> None:
    # The shipped OECD inflation key had 4 of the 8 dimensions of DF_PRICES_ALL (verified live
    # 2026-10-03): it is corrected to the full key and validated against the data structure.
    oecd = pin_key("oecd", "OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0", ".M.CPI.PC_CHGPP", "US")
    assert oecd.key == "USA.M.N.CPI.PA._T.N.GY" and oecd.validated and oecd.corrected
    # a flow outside the documented table is still pinned when the area slot comes first
    other = pin_key("oecd", "OECD.X,DSD_X@DF_X,1.0", ".A.B", "US")
    assert other.key == "USA.A.B" and not other.validated
    # The shipped default ".Q.P.A.M.770.A" started with the area slot; BIS WS_TC starts with FREQ.
    tc = pin_key("bis", "BIS,WS_TC,2.0", ".Q.P.A.M.770.A", "US")
    assert tc.key == "Q.US.P.A.M.770.A" and tc.validated and tc.corrected
    assert pin_key("bis", "BIS,WS_DSR,1.0", ".Q.P.N", "US").key == "Q.US.P"
    spp = pin_key("bis", "BIS,WS_LONG_CPI,1.0", ".Q.R", "US")
    assert (spp.flow, spp.key) == ("BIS,WS_SPP,1.0", "Q.US.R.628") and spp.unit
    with pytest.raises(SdmxKeyError, match="has 3 dimensions"):
        pin_key("bis", "BIS,WS_TC,2.0", "Q.US.P", "US")
    with pytest.raises(SdmxKeyError, match="area slot"):
        pin_key("bis", "BIS,WS_TC,2.0", "Q.DE.P.A.M.770.A", "US")
    with pytest.raises(SdmxKeyError, match="alpha-3"):
        pin_key("oecd", "F", ".M", "XK")


# ---------------------------------------------------------------------------------------
# Comtrade parsing, HS revisions, trade metrics (B14, C)
# ---------------------------------------------------------------------------------------


def _row(**kw: Any) -> dict[str, Any]:
    base = {
        "reporterCode": 842,
        "partnerCode": 251,
        "flowCode": "X",
        "classificationCode": "H6",
        "cmdCode": "85",
        "period": "2024",
        "primaryValue": 100,
        "qty": 5,
        "qtyUnitAbbr": "u",
        "netWgt": 0,
    }
    return base | kw


def test_comtrade_flows_weight_quantity_and_iso_codes() -> None:
    body = {
        "data": [
            _row(flowCode="X"),
            _row(flowCode="M", primaryValue=70),
            _row(flowCode="RX", primaryValue=5),
            _row(flowCode="RM", primaryValue=6),
            _row(flowCode="X", flowDesc="garbage", cmdCode="86", qty=0, qtyUnitAbbr="N/A"),
            _row(flowCode="Z", flowDesc="Unknown"),
        ]
    }
    skipped: list[str] = []
    points = parse_comtrade(body, skipped)
    assert [p.flow for p in points] == ["export", "import", "re_export", "re_import", "export"]
    assert len(skipped) == 1
    first = points[0]
    assert first.hs_revision == "HS2022"  # H6
    assert first.net_weight == Decimal(0)  # a reported zero weight is not a missing one
    assert (first.quantity, first.quantity_unit) == (Decimal(5), "u")  # not the weight
    assert (first.reporter_iso, first.partner_iso) == ("US", "FR")  # 842 / 251 are Comtrade's
    assert points[-1].quantity is None and points[-1].quantity_unit is None
    assert parse_comtrade([]) == [] and parse_comtrade({"data": None}) == []
    assert iso2_for_m49(0) == "W00" and iso2_for_m49(999999) is None and iso2_for_m49("x") is None
    assert iso2_for_m49(124) == "CA" and iso2_for_m49(276) == "DE" and iso2_for_m49(699) == "IN"


def test_migration_m49_backfill_matches_the_python_map() -> None:
    sql = next(
        Path(__file__).parents[3].joinpath("supabase", "migrations").glob("20261005110000_*.sql")
    ).read_text()
    pairs = dict(re.findall(r"\('(\d+)','([A-Z0-9]{2,3})'\)", sql))
    for code in (124, 276, 842, 251, 381, 699, 156):
        assert pairs[str(code)] == iso2_for_m49(code)
    assert pairs["0"] == "W00"


def test_hs_revision_labels_and_concordance() -> None:
    assert [normalize_hs_revision(x) for x in ("H6", "hs 2022", "HS2022", "2022", " h5 ")] == [
        "HS2022",
        "HS2022",
        "HS2022",
        "HS2022",
        "HS2017",
    ]
    assert normalize_hs_revision("") is None and normalize_hs_revision("S3") == "S3"
    assert chapter_comparable("85", "H5", "HS2022")
    assert not chapter_comparable("8542", "HS2017", "HS2022")  # needs the WCO table
    assert chapter_comparable("8542", "HS2022", "H6")  # same revision


def _tp(flow: str, partner: str, year: int, value: str | None, code: str = "85") -> TradePoint:
    return TradePoint(
        "842",
        partner,
        flow,
        "HS2022",
        code,
        dt.date(year, 1, 1),
        None if value is None else Decimal(value),
        None,
        None,
        partner_iso={"0": "W00", "124": "CA", "484": "MX"}.get(partner),
    )


def test_trade_metrics_do_not_blend_flows_or_count_world_or_zero_missing() -> None:
    rows = [
        _tp("export", "0", 2024, "200"),
        _tp("export", "124", 2024, "100"),
        _tp("export", "484", 2024, "100"),
        _tp("export", "0", 2025, "300"),
        _tp("export", "124", 2025, "150"),
        _tp("export", "484", 2025, "150"),
        _tp("import", "124", 2024, "50"),
        _tp("import", "124", 2025, "25"),
        _tp("re_export", "124", 2025, "9999"),
    ]
    metrics = trade_metrics(rows)
    assert metrics["export_growth"] == Decimal("0.5")  # World row is the total: 300 / 200
    assert metrics["import_growth"] == Decimal("-0.5")
    # HHI over CA and MX only (World excluded): 0.5^2 + 0.5^2.
    assert metrics["export_partner_concentration"] == Decimal("0.5")
    assert metrics["import_partner_concentration"] == Decimal(1)
    assert metrics["export_share_of_two_way_trade"] == Decimal(300) / Decimal(325)
    # A missing value is not zero: the latest period has no export value at all.
    assert (
        trade_metrics([_tp("export", "124", 2024, "100"), _tp("export", "124", 2025, None)])[
            "export_growth"
        ]
        is None
    )
    # Growth only against the adjacent period: 2023 -> 2025 is not growth.
    assert (
        trade_metrics([_tp("export", "124", 2023, "100"), _tp("export", "124", 2025, "300")])[
            "export_growth"
        ]
        is None
    )
    only_world = [_tp("export", "0", 2025, "10")]
    assert trade_metrics(only_world)["export_partner_concentration"] is None


# ---------------------------------------------------------------------------------------
# Exposure maths (B20)
# ---------------------------------------------------------------------------------------


def test_exposure_cap_is_per_dimension_and_effect_is_weighted() -> None:
    E = exposure_mod.Exposure
    rows = [
        E("regions", Decimal("0.6"), "reported", geography="DE"),
        E("regions", Decimal("0.4"), "reported", geography="US"),
        E("chips", Decimal("1"), "estimated", hs_code="8542"),
    ]
    summary = exposure_mod.exposure_summary(rows)
    assert summary["mapped"] == Decimal(1) and summary["unknown"] == Decimal(0)
    assert summary["by_dimension"]["hs_product"]["mapped"] == Decimal(1)  # type: ignore[index]
    with pytest.raises(ValueError, match="geography"):
        exposure_mod.exposure_summary(
            [*rows, E("more", Decimal("0.1"), "reported", geography="FR")]
        )
    changes = {"DE": Decimal("1.0"), "US": Decimal("-0.5"), "8542": Decimal("100")}
    assert exposure_mod.weighted_effect(rows, changes) == Decimal("0.4")  # not the HS key
    assert exposure_mod.weighted_effect(rows, changes, "hs_product") == Decimal(100)


def test_exposure_input_needs_a_revision_with_an_hs_code() -> None:
    from api.macro_routes import ExposureInput

    base = {
        "issuer_id": 1,
        "segment": "s",
        "basis": "reported",
        "weight": "0.5",
        "valid_from": "2026-01-01",
    }
    with pytest.raises(ValidationError, match="hs_revision is required"):
        ExposureInput.model_validate({**base, "hs_code": "8542"})
    ok = ExposureInput.model_validate({**base, "hs_code": "8542", "hs_revision": "H6"})
    assert ok.hs_revision == "HS2022"
    assert ExposureInput.model_validate({**base, "geography": "DE"}).hs_revision is None


# ---------------------------------------------------------------------------------------
# Storage: dedupe, revisions, events (B10, B13, late-filled periods)
# ---------------------------------------------------------------------------------------


def _row_for(value: str, *, vintage: str = "v1", known: dt.datetime = NOW) -> MacroRow:
    return MacroRow(
        "cpi", "oecd", "US", dt.date(2026, 6, 1), Decimal(value), "%", "monthly", None, known,
        vintage, "u", {},
    )  # fmt: skip


@pytest.mark.db
async def test_repeated_fetches_do_not_grow_the_table_and_changes_are_revisions(
    backend_pool: asyncpg.Pool,
) -> None:
    first = await store_macro_rows(backend_pool, [_row_for("2.0")])
    again = await store_macro_rows(
        backend_pool, [_row_for("2.0", vintage="other", known=NOW + dt.timedelta(days=1))]
    )
    assert (first.inserted, again.inserted, again.unchanged) == (1, 0, 1)
    revised = await store_macro_rows(
        backend_pool, [_row_for("2.4", vintage="v2", known=NOW + dt.timedelta(days=2))]
    )
    assert (revised.inserted, revised.revised) == (0, 1)
    rows = await backend_pool.fetch(
        "select value, vintage, revision, metadata from macro_observation order by revision"
    )
    assert [(r["value"], r["vintage"], r["revision"]) for r in rows] == [
        (Decimal("2.0"), "v1", 0),
        (Decimal("2.4"), "v2", 1),  # the provider's own vintage label is kept
    ]
    assert json.loads(rows[1]["metadata"])["revised_from"] == "2.0"


@pytest.mark.db
async def test_alfred_vintages_are_keyed_by_window_start_not_by_run(
    backend_pool: asyncpg.Pool,
) -> None:
    body = {
        "observations": [
            {"date": "2026-01-01", "realtime_start": "2026-02-10", "realtime_end": "2026-03-01",
             "value": "100.0"},
            {"date": "2026-01-01", "realtime_start": "2026-03-02", "realtime_end": "2026-09-30",
             "value": "101.0"},
        ]
    }  # fmt: skip

    async def run(end: str) -> int:
        body["observations"][1]["realtime_end"] = end
        points = parse_fred_vintages(body, series_id="gdp", unit="USD", source_url="u")
        result = await store_macro_rows(
            backend_pool,
            [MacroRow.from_point(p, p.release_at or NOW) for p in points],
            vintage_keyed=True,
        )
        return result.inserted

    assert await run("2026-09-30") == 2
    assert await run("2026-10-01") == 0  # the open window's end moves daily; no new vintage
    vintages = await backend_pool.fetch(
        "select vintage, revision from macro_observation order by 1"
    )
    assert [(v["vintage"], v["revision"]) for v in vintages] == [
        ("2026-02-10", 0),
        ("2026-03-02", 1),
    ]


def _hicp(period: dt.date, value: str) -> Observation:
    return Observation(
        "hicp:EA21", "hicp", "eurostat", period, period.strftime("%Y-%m"), Decimal(value),
        "HICP", "%", dt.date(2026, 10, 1), "https://example.test", False,
    )  # fmt: skip


@pytest.mark.db
async def test_a_late_filled_older_period_is_stored_but_not_a_surprise(
    backend_pool: asyncpg.Pool,
) -> None:
    bands = {"hicp": 0.3}
    d = dt.date
    await store_and_evaluate(backend_pool, [_hicp(d(2026, 5, 1), "2.0")], bands, known_at=NOW)
    await store_and_evaluate(backend_pool, [_hicp(d(2026, 7, 1), "2.1")], bands, known_at=NOW)
    # June appears late, a big jump against May - but July is already out: not news.
    count, events = await store_and_evaluate(
        backend_pool, [_hicp(d(2026, 6, 1), "5.0")], bands, known_at=NOW
    )
    assert (count, events) == (1, [])
    count, events = await store_and_evaluate(
        backend_pool, [_hicp(d(2026, 8, 1), "2.9")], bands, known_at=NOW
    )
    assert count == 1 and len(events) == 1  # August against the real previous period (June)
    # Re-running the same fetch writes nothing new.
    n = await backend_pool.fetchval("select count(*) from macro_observation")
    await store_and_evaluate(backend_pool, [_hicp(d(2026, 8, 1), "2.9")], bands, known_at=NOW)
    assert await backend_pool.fetchval("select count(*) from macro_observation") == n
    meta = await backend_pool.fetchrow(
        "select * from macro_series_metadata where source='eurostat'"
    )
    assert meta is not None and meta["series_id"] == "hicp"


def _transport(routes: dict[str, Any]) -> httpx.MockTransport:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        for prefix, answer in routes.items():
            if str(request.url).startswith(prefix):
                return answer(request) if callable(answer) else answer
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    transport.seen = seen  # type: ignore[attr-defined]
    return transport


FRED = "https://api.stlouisfed.org/fred/series/observations"


@pytest.mark.db
async def test_events_survive_an_alfred_failure_and_alfred_asks_for_revisions(
    backend_pool: asyncpg.Pool,
) -> None:
    fred = FredSeries(
        key="fed_funds", label="Fed funds", series_id="DFF", policy_rate=True, units="lin"
    )
    cpi = FredSeries(key="us_cpi", label="US CPI", series_id="CPIAUCSL", units="pc1")
    async with backend_pool.acquire() as conn:
        await save_config(
            conn,
            "jobs",
            JobsConfig(
                macro=MacroSettings(eurostat=[], fred=[fred, cpi], ecb=[], cnb=[], history_years=1)
            ),
            created_by=None,
        )

    def answer(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if params.get("output_type") == "1":
            return httpx.Response(500)  # ALFRED is down
        series = params["series_id"]
        today = params.get("realtime_start", "2026-10-01")
        if series == "DFF":
            rows = [("2026-09-29", "4.25")] if "observation_start" in params else []
            if "observation_start" not in params:
                rows = [("2026-09-29", "4.25"), ("2026-09-30", "4.00")]
        else:
            rows = [("2026-09-01", "3.0")]
        return httpx.Response(
            200,
            json={
                "count": len(rows),
                "observations": [
                    {"date": d, "value": v, "realtime_start": today, "realtime_end": today}
                    for d, v in rows
                ],
            },
        )

    transport = _transport({FRED: answer})

    async def collect(now: dt.datetime) -> str:
        async with httpx.AsyncClient(transport=transport) as http:

            async def handler(ctx: JobContext) -> None:
                await run_macro_collect(
                    ctx, MacroDeps(http=http, fred_key="k", today=now.date(), now=now)
                )

            return await run_job(backend_pool, "macro_collect", handler, trigger="manual")

    assert await collect(NOW - dt.timedelta(days=2)) == "succeeded"  # backfill: baseline only
    # Second run: DFF 4.25 -> 4.00 is a decision; ALFRED answers 500 for both series.
    assert await collect(NOW) == "succeeded"
    created = await backend_pool.fetchval(
        "select count(*) from news_item where event_type='central_bank'"
    )
    assert created == 1  # raised right after DFF was stored, though ALFRED failed afterwards
    steps = await backend_pool.fetch("select name, detail from job_step order by id")
    detail = {s["name"]: json.loads(s["detail"]) for s in steps}
    assert "vintage_error" in detail["fred:fed_funds"]
    alfred = [r for r in transport.seen if r.url.params.get("output_type")]  # type: ignore[attr-defined]
    assert {r.url.params["output_type"] for r in alfred} == {"1"}  # not 4 (initial release only)
    cpi_call = next(r for r in alfred if r.url.params["series_id"] == "CPIAUCSL")
    assert cpi_call.url.params["units"] == "pc1"
    # The current-vintage run twice stored each period once.
    assert (
        await backend_pool.fetchval(
            "select count(*) from macro_observation where series_id='fed_funds'"
            " and period='2026-09-30'"
        )
        == 1
    )


# ---------------------------------------------------------------------------------------
# The public-data job (A5, B12, B14, B19, D2)
# ---------------------------------------------------------------------------------------

WB = "https://api.worldbank.org/v2/country/"
OECD = "https://sdmx.oecd.org/public/rest/v1/data/"
COMTRADE = "https://comtradeapi.un.org/data/v1/get/C/A/HS"


def _wb_body(pages: int = 1, page: int = 1) -> list[Any]:
    def row(year: str, value: float | None) -> dict[str, Any]:
        return {
            "indicator": {"id": "NY.GDP.MKTP.CD", "value": "GDP"},
            "country": {"id": "US", "value": "United States"},
            "countryiso3code": "USA",
            "date": year,
            "value": value,
        }

    rows = [row(str(y), float(y)) for y in range(2017, 2026) if y != 2020]
    rows += [row("2020", None), row("2026", None), row("2016", 1.0)]
    return [{"page": page, "pages": pages, "total": len(rows), "lastupdated": "2026-07-01"}, rows]


def _public_config(
    *,
    sdmx: list[PublicSdmxSeries] | None = None,
    comtrade: list[ComtradeSlice] | None = None,
    quota: int | None = None,
) -> JobsConfig:
    config = JobsConfig(
        public_data=PublicDataSettings(
            countries=["US"],
            world_bank=[
                WorldBankIndicator(
                    key="NY.GDP.MKTP.CD", label="GDP", unit="USD", consumer="country context"
                )
            ],
            sdmx=sdmx or [],
            comtrade=comtrade or [],
        )
    )
    for name in ("world_bank", "oecd", "un_comtrade"):
        config.providers[name] = ProviderJobSettings(
            enabled=True, daily_quota=quota, reserve_credits=0 if quota is None else 1
        )
    return config


async def _run_public(
    pool: asyncpg.Pool,
    config: JobsConfig,
    transport: httpx.MockTransport,
    key: str | None = None,
    now: dt.datetime = NOW,
) -> str:
    async with pool.acquire() as conn:
        await save_config(conn, "jobs", config, created_by=None)
    await pool.execute("update provider_access set state='available'")
    async with httpx.AsyncClient(transport=transport) as http:

        async def handler(ctx: JobContext) -> None:
            await run_public_data_collect(ctx, PublicDataDeps(http, now=now, comtrade_key=key))

        return await run_job(pool, "public_data_collect", handler, trigger="manual")


@pytest.mark.db
async def test_world_bank_surfaces_missing_years_and_writes_metadata_without_sdmx(
    backend_pool: asyncpg.Pool,
) -> None:
    transport = _transport({WB: lambda _: httpx.Response(200, json=_wb_body())})
    assert await _run_public(backend_pool, _public_config(), transport) == "succeeded"
    coverage = await backend_pool.fetchrow(
        "select * from coverage_status where provider='world_bank' and entity_key='US'"
    )
    assert coverage is not None and coverage["state"] == "available"
    assert coverage["available_periods"] == 9 and coverage["expected_periods"] == 11
    # 2020 is a null, 2026 is null: both are surfaced, as is 2025 present.
    assert "2020" in coverage["reason"] and "2026" in coverage["reason"]
    assert coverage["earliest_period"] == dt.date(2016, 1, 1)
    # B12: the semantic record is written by the World Bank step itself (no SDMX series here).
    meta = await backend_pool.fetchrow(
        "select * from macro_series_metadata where source='world_bank'"
    )
    assert meta is not None and meta["geography_definition"] and meta["release_calendar"]
    # null observations are not stored; the header's lastupdated is the vintage.
    rows = await backend_pool.fetch("select period, vintage from macro_observation")
    assert len(rows) == 9 and {r["vintage"] for r in rows} == {"2026-07-01"}
    again = await _run_public(backend_pool, _public_config(), transport)
    assert again == "succeeded"
    assert await backend_pool.fetchval("select count(*) from macro_observation") == 9


@pytest.mark.db
async def test_world_bank_quota_exhaustion_skips_the_step_instead_of_failing(
    backend_pool: asyncpg.Pool,
) -> None:
    transport = _transport({WB: lambda _: httpx.Response(200, json=_wb_body())})
    config = _public_config(quota=1)  # one credit, one reserved: nothing may be spent
    assert await _run_public(backend_pool, config, transport) == "succeeded"
    detail = await backend_pool.fetchval(
        "select detail from job_step where name='world_bank:indicators'"
    )
    assert "skipped" in json.loads(detail)
    assert not transport.seen  # type: ignore[attr-defined]


def _oecd_series() -> PublicSdmxSeries:
    return PublicSdmxSeries(
        provider="oecd",
        key="inflation",
        label="Inflation",
        base_url="https://sdmx.oecd.org/public/rest/v1",  # type: ignore[arg-type]
        flow="OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0",
        series_key=".M.CPI.PC_CHGPP",
        geography="US",
        unit="%",
        frequency="monthly",
        definition="CPI inflation",
        consumer="holding country context",
    )


@pytest.mark.db
async def test_oecd_request_is_pinned_and_rows_carry_the_response_geography(
    backend_pool: asyncpg.Pool,
) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=FLAT_ALL_DIMENSIONS)

    transport = _transport({OECD: answer})
    config = _public_config(sdmx=[_oecd_series()])
    assert await _run_public(backend_pool, config, transport) == "succeeded"
    request = next(r for r in transport.seen if "/data/" in str(r.url))  # type: ignore[attr-defined]
    # the stored key (4 of 8 dimensions) is corrected to the verified full key
    assert request.url.path.endswith("/USA.M.N.CPI.PA._T.N.GY")
    assert request.url.params["dimensionAtObservation"] == "AllDimensions"
    rows = await backend_pool.fetch("select geography, period, value from macro_observation")
    # The response also held CAN: it is not stored under the configured US.
    assert {(r["geography"], r["period"], r["value"]) for r in rows} == {
        ("US", dt.date(2026, 5, 1), Decimal(2)),
        ("US", dt.date(2026, 6, 1), Decimal("2.5")),
    }
    cov = await backend_pool.fetchrow(
        "select state, available_periods from coverage_status where provider='oecd'"
    )
    assert (cov["state"], cov["available_periods"]) == ("available", 2)
    assert await backend_pool.fetchval("select count(*) from source_observation") == 2
    await _run_public(backend_pool, config, transport)  # unchanged values: nothing re-stored
    assert await backend_pool.fetchval("select count(*) from macro_observation") == 2
    assert await backend_pool.fetchval("select count(*) from source_observation") == 2


@pytest.mark.db
async def test_comtrade_rows_get_iso_codes_revisions_and_list_bodies_do_not_crash(
    backend_pool: asyncpg.Pool,
) -> None:
    bodies = [
        {"data": [_row(), _row(flowCode="M", partnerCode=0, primaryValue=50)]},
        {"data": [_row(primaryValue=100), _row(flowCode="M", partnerCode=0, primaryValue=50)]},
        {"data": [_row(primaryValue=120), _row(flowCode="RX", primaryValue=7)]},
        [],  # an HTML/JSON list answer must not crash `body.get`
    ]
    calls = iter(bodies)
    transport = _transport({COMTRADE: lambda _: httpx.Response(200, json=next(calls))})
    config = _public_config(
        comtrade=[
            ComtradeSlice(reporter=842, partner=0, hs_code="85", flow_code="X,M,RX", consumer="x")
        ]
    )
    for day in range(4):
        when = NOW + dt.timedelta(days=day)
        assert await _run_public(backend_pool, config, transport, key="k", now=when) == "succeeded"
    rows = await backend_pool.fetch(
        "select flow, partner, reporter_iso, partner_iso, hs_revision, trade_value, revision"
        " from trade_observation order by flow, revision"
    )
    assert [(r["flow"], r["trade_value"], r["revision"]) for r in rows] == [
        ("export", Decimal(100), 0),
        ("export", Decimal(120), 1),  # changed value = next revision; the unchanged rerun: none
        ("import", Decimal(50), 0),
        ("re_export", Decimal(7), 0),
    ]
    assert {(r["reporter_iso"], r["hs_revision"]) for r in rows} == {("US", "HS2022")}
    assert {r["partner_iso"] for r in rows if r["flow"] == "import"} == {"W00"}
    request = next(r for r in transport.seen if str(r.url).startswith(COMTRADE))  # type: ignore[attr-defined]
    assert request.url.params["customsCode"] == "C00" and request.url.params["motCode"] == "0"
    assert "Ocp-Apim-Subscription-Key" in request.headers


@pytest.mark.db
async def test_trade_store_dedupes_and_keeps_weight_apart_from_quantity(
    backend_pool: asyncpg.Pool,
) -> None:
    point = parse_comtrade({"data": [_row(netWgt=300, qty=5, primaryValue=10)]})
    first = await store_trade_points(backend_pool, point, NOW, "u")
    second = await store_trade_points(backend_pool, point, NOW + dt.timedelta(days=1), "u")
    assert (first.inserted, second.inserted, second.unchanged) == (1, 0, 1)
    row = await backend_pool.fetchrow(
        "select quantity, net_weight, quantity_unit from trade_observation"
    )
    assert (row["quantity"], row["net_weight"], row["quantity_unit"]) == (
        Decimal(5),
        Decimal(300),
        "u",
    )


# ---------------------------------------------------------------------------------------
# Schema: per-dimension cap (B16), HS revision requirement
# ---------------------------------------------------------------------------------------


async def _issuer(pool: asyncpg.Pool) -> int:
    issuer = await pool.fetchval(
        "insert into issuer (legal_name, country) values ('Example', 'US') returning id"
    )
    assert isinstance(issuer, int)
    return issuer


async def _map(pool: asyncpg.Pool, issuer: int, weight: str, **kw: Any) -> None:
    await pool.execute(
        "insert into exposure_mapping (issuer_id, segment, geography, hs_revision, hs_code, basis,"
        " weight, valid_from) values ($1, 's', $2, $3, $4, 'reported', $5, '2026-01-01')",
        issuer,
        kw.get("geography"),
        kw.get("hs_revision"),
        kw.get("hs_code"),
        Decimal(weight),
    )


@pytest.mark.db
async def test_geography_and_product_cuts_can_both_be_entered(backend_pool: asyncpg.Pool) -> None:
    issuer = await _issuer(backend_pool)
    await _map(backend_pool, issuer, "0.6", geography="DE")
    await _map(backend_pool, issuer, "0.4", geography="US")  # geography cut: 100%
    await _map(backend_pool, issuer, "1", hs_code="8542", hs_revision="HS2022")  # product: 100%
    with pytest.raises(asyncpg.RaiseError, match="geography exposure exceeds 100%"):
        await _map(backend_pool, issuer, "0.1", geography="FR")
    with pytest.raises(asyncpg.RaiseError, match="hs_product exposure exceeds"):
        await _map(backend_pool, issuer, "0.1", hs_code="8541", hs_revision="HS2022")
    with pytest.raises(asyncpg.CheckViolationError):  # an HS code needs its revision
        await _map(backend_pool, issuer, "0", hs_code="8541")
    await _map(backend_pool, issuer, "0.5", geography="CN", hs_code="8542", hs_revision="HS2022")


# ---------------------------------------------------------------------------------------
# Point-in-time context (B17, B20)
# ---------------------------------------------------------------------------------------


async def _holding(pool: asyncpg.Pool, ticker: str, issuer: int | None) -> None:
    await pool.execute(
        "insert into instrument (t212_ticker,name,currency,price_symbol) values ($1,$1,'USD',$1)",
        ticker,
    )
    if issuer is None:
        await pool.execute(
            "insert into instrument_security_link (ticker,status) values ($1,'unresolved')", ticker
        )
        return
    security = await pool.fetchval(
        "insert into security (issuer_id,kind,name,currency) values ($1,'common','x','USD')"
        " returning id",
        issuer,
    )
    await pool.execute(
        "insert into instrument_security_link (ticker,security_id,status)"
        " values ($1,$2,'confirmed')",
        ticker,
        security,
    )


async def _macro(
    pool: asyncpg.Pool, geo: str, period: str, value: str, rev: int, known: str
) -> None:
    await pool.execute(
        "insert into macro_observation (series_id,source,geography,period,value,unit,frequency,"
        " known_at,vintage,revision,source_url) values ('cpi','oecd',$1,$2,$3,'%','monthly',$4,$5,"
        " $6,'u')",
        geo,
        dt.date.fromisoformat(period),
        Decimal(value),
        dt.datetime.fromisoformat(known).replace(tzinfo=dt.UTC),
        f"v{rev}",
        rev,
    )


@pytest.mark.db
async def test_macro_context_is_empty_without_a_mapping_and_ranges_use_latest_vintage(
    backend_pool: asyncpg.Pool,
) -> None:
    issuer = await _issuer(backend_pool)
    await _holding(backend_pool, "MAPPED", issuer)
    await _holding(backend_pool, "UNMAPPED", None)
    await _map(backend_pool, issuer, "0.5", geography="DE")
    await _map(backend_pool, issuer, "0.25", geography="US")
    await backend_pool.execute("update exposure_mapping set created_at='2026-02-01'")
    # DE: May 100 -> 120 (revised), June 110.  A superseded 100 must not set the range low.
    await _macro(backend_pool, "DE", "2026-05-01", "100", 0, "2026-06-10")
    await _macro(backend_pool, "DE", "2026-05-01", "120", 1, "2026-07-10")
    await _macro(backend_pool, "DE", "2026-06-01", "110", 0, "2026-07-10")
    await _macro(backend_pool, "US", "2026-05-01", "10", 0, "2026-06-10")
    await _macro(backend_pool, "US", "2026-06-01", "12", 0, "2026-07-10")
    await _macro(backend_pool, "FR", "2026-06-01", "99", 0, "2026-07-10")  # not mapped
    as_of = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)

    unmapped = await context_for_holding(backend_pool, "UNMAPPED", as_of)
    assert unmapped.exposure_unknown and unmapped.observations == [] and unmapped.effects == []

    context = await context_for_holding(backend_pool, "MAPPED", as_of)
    assert not context.exposure_unknown
    assert {o.geography for o in context.observations} == {"DE", "US"}  # FR is not attributed
    de = next(o for o in context.observations if o.geography == "DE")
    assert (de.five_year_low, de.five_year_high) == (Decimal(110), Decimal(120))
    assert (de.previous_period, de.previous_value) == (dt.date(2026, 5, 1), Decimal(120))
    # Weighted effect: 0.5 x (110 - 120) + 0.25 x (12 - 10) = -4.5, covering 75% of the issuer.
    (effect,) = context.series_effects
    assert effect.weighted_change == Decimal("-4.5") and effect.covered_weight == Decimal("0.75")
    assert context.exposure["mapped"] == Decimal("0.75")
    # A mapping entered after as_of is invisible (created_at, not valid_from).
    early = await context_for_holding(
        backend_pool, "MAPPED", dt.datetime(2026, 1, 15, tzinfo=dt.UTC)
    )
    assert early.exposure_unknown and early.observations == []


# ---------------------------------------------------------------------------------------
# Routes (B16, B14, A18)
# ---------------------------------------------------------------------------------------


@pytest.fixture
async def macro_api(backend_pool: asyncpg.Pool) -> Any:
    from api.auth import AuthenticatedUser, require_owner
    from api.db import get_pool, get_pool_or_error
    from api.main import create_app

    app = create_app()

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[get_pool_or_error] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(
        "7f0c3d4e-0000-4000-8000-000000000001", None, "aal2"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.db
async def test_exposure_routes_normalize_revision_and_report_the_cap(
    backend_pool: asyncpg.Pool, macro_api: httpx.AsyncClient
) -> None:
    issuer = await _issuer(backend_pool)
    base = {"issuer_id": issuer, "segment": "s", "basis": "reported", "valid_from": "2026-01-01"}
    missing = await macro_api.post(
        "/macro/exposures", json={**base, "hs_code": "8542", "weight": "0.5"}
    )
    assert missing.status_code == 422
    ok = await macro_api.post(
        "/macro/exposures", json={**base, "hs_code": "8542", "hs_revision": "H6", "weight": "1"}
    )
    assert ok.status_code == 201 and ok.json()["hs_revision"] == "HS2022"
    geo = await macro_api.post("/macro/exposures", json={**base, "geography": "DE", "weight": "1"})
    assert geo.status_code == 201  # a geography cut next to a full product cut
    over = await macro_api.post(
        "/macro/exposures", json={**base, "geography": "US", "weight": "0.2"}
    )
    assert over.status_code == 422 and "geography exposure exceeds 100%" in over.json()["detail"]


@pytest.mark.db
async def test_trade_route_takes_iso_reporters_and_reports_flows_separately(
    backend_pool: asyncpg.Pool, macro_api: httpx.AsyncClient
) -> None:
    points = parse_comtrade(
        {
            "data": [
                _row(period="2024", primaryValue=100),
                _row(period="2025", primaryValue=150),
                _row(period="2025", partnerCode=0, primaryValue=400),
                _row(period="2025", flowCode="M", partnerCode=0, primaryValue=90),
                _row(period="2025", flowCode="RX", partnerCode=0, primaryValue=5),
            ]
        }
    )
    await store_trade_points(backend_pool, points, NOW, "https://example.test/comtrade")
    by_iso = await macro_api.get("/macro/trade/US/85")  # reporter 842 is stored as M49
    assert by_iso.status_code == 200
    body = by_iso.json()
    assert body["hs_revision"] == "HS2022" and body["periods"] == 2
    assert Decimal(body["latest_value"]) == 400  # World aggregate, not World plus partners
    assert {k: Decimal(v) for k, v in body["latest_value_by_flow"].items()} == {
        "export": 400,
        "import": 90,
        "re_export": 5,
    }
    assert Decimal(body["metrics"]["import_growth"] or 0) == 0
    assert (await macro_api.get("/macro/trade/842/85")).status_code == 200
    assert (await macro_api.get("/macro/trade/US/99")).status_code == 404


@pytest.mark.db
async def test_portfolio_coverage_does_not_add_geography_and_product_cuts(
    backend_pool: asyncpg.Pool, macro_api: httpx.AsyncClient
) -> None:
    issuer = await _issuer(backend_pool)
    await _holding(backend_pool, "HOLD", issuer)
    await _map(backend_pool, issuer, "0.6", geography="DE")
    await _map(backend_pool, issuer, "0.5", hs_code="8542", hs_revision="HS2022")
    job = await backend_pool.fetchval(
        "insert into job (job_type, trigger, status) values ('portfolio_sync', 'manual',"
        " 'succeeded') returning id"
    )
    await backend_pool.execute(
        "insert into position_snapshot (snapshot_date, ticker, job_id, captured_at, quantity,"
        " avg_price, current_price, instrument_currency, value, account_currency,"
        " value_account, cost_account, ppl, base_currency, fx_source, fx_rate, value_base)"
        " values ('2026-09-25', 'HOLD', $1, now(), 1, 1, 1, 'USD', 100, 'USD', 100, 100, 0,"
        " 'USD', 'ecb', 1, 100)",
        job,
    )
    await backend_pool.execute("update exposure_mapping set created_at='2026-01-02'")
    body = (await macro_api.get("/macro/coverage/portfolio")).json()
    # 0.6 geography and 0.5 product are two views of the issuer: mapped is 0.6, not 1.1 -> 1.
    assert Decimal(body["mapped_weight"]) == Decimal("0.6")
    assert Decimal(body["unknown_weight"]) == Decimal("0.4")


@pytest.mark.db
@pytest.mark.parametrize("status", ["suggested", "rejected"])
async def test_unconfirmed_identity_links_attribute_no_exposure(
    backend_pool: asyncpg.Pool, macro_api: httpx.AsyncClient, status: str
) -> None:
    """Audit N3: a suggested (or owner-rejected) ticker-to-security link that still carries a
    security_id must not hand that issuer's exposure mapping to the holding."""
    issuer = await _issuer(backend_pool)
    await _holding(backend_pool, "HOLD", issuer)
    await _map(backend_pool, issuer, "0.6", geography="DE")
    await backend_pool.execute("update exposure_mapping set created_at='2026-01-02'")
    job = await backend_pool.fetchval(
        "insert into job (job_type, trigger, status) values ('portfolio_sync', 'manual',"
        " 'succeeded') returning id"
    )
    await backend_pool.execute(
        "insert into position_snapshot (snapshot_date, ticker, job_id, captured_at, quantity,"
        " avg_price, current_price, instrument_currency, value, account_currency,"
        " value_account, cost_account, ppl, base_currency, fx_source, fx_rate, value_base)"
        " values ('2026-09-25', 'HOLD', $1, now(), 1, 1, 1, 'USD', 100, 'USD', 100, 100, 0,"
        " 'USD', 'ecb', 1, 100)",
        job,
    )
    as_of = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    confirmed = await context_for_holding(backend_pool, "HOLD", as_of)
    assert not confirmed.exposure_unknown
    body = (await macro_api.get("/macro/coverage/portfolio")).json()
    assert Decimal(body["mapped_weight"]) == Decimal("0.6")

    await backend_pool.execute(
        "update instrument_security_link set status=$1 where ticker='HOLD'", status
    )
    context = await context_for_holding(backend_pool, "HOLD", as_of)
    assert context.exposure_unknown and context.observations == []
    body = (await macro_api.get("/macro/coverage/portfolio")).json()
    assert Decimal(body["mapped_weight"]) == 0
    assert Decimal(body["unknown_weight"]) == 1
    assert body["by_exposure"] == []


@pytest.mark.db
async def test_sdmx_attributes_reach_the_observation_columns(backend_pool: asyncpg.Pool) -> None:
    text = "KEY,TIME_PERIOD,OBS_VALUE,UNIT_MULT,ADJUSTMENT,TRANSFORMATION\nA,2026-07,3.2,6,Y,GY\n"
    obs = parse_ecb_series(text, ECB_SERIES, None)
    await store_and_evaluate(backend_pool, obs, {}, known_at=NOW)
    row = await backend_pool.fetchrow(
        "select unit_multiplier, seasonal_adjustment, transformation, frequency, release_at"
        " from macro_observation"
    )
    assert (row["unit_multiplier"], row["seasonal_adjustment"], row["transformation"]) == (
        Decimal(10) ** 6,
        "Y",
        "GY",
    )
    assert row["frequency"] == "monthly" and row["release_at"] is None
    meta = await backend_pool.fetchrow("select * from macro_series_metadata where source='ecb'")
    assert meta["unit_multiplier"] == Decimal(10) ** 6 and meta["release_calendar"]


@pytest.mark.db
async def test_the_rollback_script_restores_the_previous_schema(migrated_db: Any) -> None:
    root = Path(__file__).parents[3] / "supabase" / "rollback"
    script = (root / "20261005110000_macro_trade_correctness.down.sql").read_text()
    conn = await asyncpg.connect(migrated_db.admin_url)  # the owner role: rollbacks drop objects
    try:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute(script)
            columns = {
                r["column_name"]
                for r in await conn.fetch(
                    "select column_name from information_schema.columns"
                    " where table_name='trade_observation'"
                )
            }
            assert "reporter_iso" not in columns and "revision" not in columns
            assert await conn.fetchval("select to_regclass('public.hs_concordance')") is None
            # The one-total cap is back: a geography cut and a product cut now collide.
            issuer = await conn.fetchval(
                "insert into issuer (legal_name, country) values ('R', 'US') returning id"
            )
            await conn.execute(
                "insert into exposure_mapping (issuer_id,segment,geography,basis,weight,valid_from)"
                " values ($1,'s','DE','reported',1,'2026-01-01')",
                issuer,
            )
            with pytest.raises(asyncpg.RaiseError):
                await conn.execute(
                    "insert into exposure_mapping (issuer_id,segment,hs_code,hs_revision,basis,"
                    "weight,valid_from) values ($1,'s','8542','HS2022','reported',0.1,"
                    "'2026-01-01')",
                    issuer,
                )
        finally:
            await tx.rollback()
    finally:
        await conn.close()
