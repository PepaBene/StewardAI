"""The follow-up work on the Q0 to Q4 gaps, on synthetic data with no database."""

import datetime as dt

import numpy as np
import pytest

from steward.config.strategy import StrategyConfig, StressEpisodeConfig
from steward.portfolio import exante as ex
from steward.portfolio import factor_shock as fs
from steward.portfolio import factors as pf
from steward.portfolio import replay as rp
from steward.quant import factors as fc
from steward.quant import proxy as px
from steward.quant import returns as rt
from steward.quant import stress as st
from tests.test_factors import START, WEEKS, _rate, _scenario
from tests.test_stress import EP, _book, _fx


def _exposures() -> pf.FactorExposures:
    book, daily = _scenario()
    return pf.build_exposures(book, daily, _rate, "developed")


# --- factor shocks (Q3.2) and the rates factor (G34) ------------------------------------------


def test_a_market_shock_moves_each_holding_by_its_loading_and_reconciles() -> None:
    report = _exposures()
    result = fs.apply_shock(report, fs.ShockSpec(market=-0.2), drawdown_limit=0.1)
    by = {p.ticker: p for p in result.positions}
    assert by["VAL"].basis == "measured" and by["VAL"].move == pytest.approx(-0.2, abs=0.04)
    assert result.after == pytest.approx(result.before + sum(p.pnl for p in result.positions))
    assert result.loss == pytest.approx(result.pnl / result.before)
    assert result.breaches_limit is True  # 800 of 1,000 invested at about -20 %
    assert result.worst[0].ticker == "VAL"
    assert by["VAL"].drivers[0].factor == "MKT_RF"


def test_an_owner_beta_replaces_only_the_market_loading_and_a_gap_uses_the_fallback() -> None:
    report = _exposures()
    owner = fs.apply_shock(report, fs.ShockSpec(market=-0.1, owner_betas={"MIX": 2.0}))
    mix = next(p for p in owner.positions if p.ticker == "MIX")
    assert mix.basis == "owner" and mix.move == pytest.approx(-0.2, abs=0.01)

    unfit = report.model_copy(deep=True)
    unfit.positions[1].fit = None
    fallback = fs.apply_shock(unfit, fs.ShockSpec(market=-0.1, unmeasured_beta=1.5))
    second = next(p for p in fallback.positions if p.ticker == unfit.positions[1].ticker)
    assert second.basis == "fallback" and second.move == pytest.approx(-0.15)
    flat = fs.apply_shock(unfit, fs.ShockSpec(market=-0.1, unmeasured_beta=None))
    skipped = next(p for p in flat.positions if p.ticker == unfit.positions[1].ticker)
    assert skipped.basis == "unshocked" and skipped.pnl == 0.0
    assert flat.unshocked_weight == pytest.approx(skipped.weight)


def test_a_shock_never_loses_more_than_the_whole_position_and_an_empty_one_moves_nothing() -> None:
    report = _exposures()
    huge = fs.apply_shock(report, fs.ShockSpec(market=-0.95, owner_betas={"VAL": 4.0}))
    assert next(p for p in huge.positions if p.ticker == "VAL").move == -1.0
    nothing = fs.apply_shock(report, fs.ShockSpec())
    assert nothing.pnl == pytest.approx(0.0) and nothing.worst == []


def test_a_shock_with_no_exposures_says_why() -> None:
    empty = pf.build_exposures(_scenario()[0], {}, _rate, "us")
    result = fs.apply_shock(empty, fs.ShockSpec(market=-0.1))
    assert result.after is None and result.reason is not None


def test_weekly_yield_changes_need_a_level_at_both_ends_of_the_week() -> None:
    dates = [dt.date(2025, 1, 8), dt.date(2025, 1, 15), dt.date(2025, 3, 5)]
    series = [(dt.date(2025, 1, 1), 4.0), (dt.date(2025, 1, 8), 4.25), (dt.date(2025, 1, 15), 4.0)]
    got = fc.weekly_level_changes(series, dates)
    assert got[0] == pytest.approx(0.25) and got[1] == pytest.approx(-0.25)
    assert np.isnan(got[2])  # no stored level near March


def test_the_rates_factor_is_regressed_when_a_yield_series_is_stored() -> None:
    book, daily = _scenario()
    yields = [
        (START - dt.timedelta(days=21) + dt.timedelta(days=i), 4.0 + 0.5 * np.sin(i / 9))
        for i in range(7 * WEEKS + 40)
    ]
    report = pf.build_exposures(book, daily, _rate, "developed", yields=yields)
    assert report.portfolio is not None
    assert "RATE10" in {b.factor for b in report.portfolio.betas}
    assert not any("No rates factor" in w for w in report.warnings)
    without = pf.build_exposures(book, daily, _rate, "developed")
    assert any("No rates factor" in w for w in without.warnings)


# --- proxy preference (G9) --------------------------------------------------------------------


def _short_history_matrix() -> rt.ReturnMatrix:
    rng = np.random.default_rng(4)
    n = 80
    best = rng.normal(0, 0.02, n)
    sector = 0.8 * best + rng.normal(0, 0.003, n)
    own = 1.0 * best + rng.normal(0, 0.004, n)
    own[:40] = np.nan
    cols = {"OWN": own, "proxy:best": best, "proxy:sector": sector}
    dates = [START + dt.timedelta(weeks=i) for i in range(n)]
    data = np.column_stack(list(cols.values()))
    return rt.ReturnMatrix(
        "weekly", dates, list(cols), data, rt.coverage_of(list(cols), dates, data)
    )


def test_a_sector_proxy_wins_when_it_fits_nearly_as_well_and_not_when_it_fits_much_worse() -> None:
    matrix = _short_history_matrix()
    candidates = ["proxy:best", "proxy:sector"]
    plain = px.backfill(matrix, candidates)[1]["OWN"]
    assert plain.fit is not None and plain.fit.proxy == "proxy:best"
    preferred = px.backfill(matrix, candidates, preferred={"OWN": ["proxy:sector"]})[1]["OWN"]
    assert preferred.fit is not None and preferred.fit.proxy == "proxy:sector"
    strict = px.backfill(matrix, candidates, preferred={"OWN": ["proxy:sector"]})
    assert strict[1]["OWN"].fit is not None
    # a preferred candidate that fits far worse is passed over
    rng = np.random.default_rng(9)
    noisy = matrix.values.copy()
    noisy[:, 2] = rng.normal(0, 0.02, noisy.shape[0])
    worse = rt.ReturnMatrix(
        "weekly", matrix.dates, matrix.keys, noisy, rt.coverage_of(matrix.keys, matrix.dates, noisy)
    )
    skipped = px.backfill(worse, candidates, preferred={"OWN": ["proxy:sector"]})[1]["OWN"]
    assert skipped.fit is not None and skipped.fit.proxy == "proxy:best"


def test_a_holdings_sector_label_selects_its_preferred_proxies() -> None:
    book = ex.LoadedBook(
        "CZK", 100.0, [ex.BookPosition("AAA", "A", "CZK", 100.0)],
        rt.ReturnMatrix("weekly", [], [], np.empty((0, 0)), {}), {}, {}, {},
        proxy_matches={"proxy:xlk.us": ["tech", "software"], "proxy:xle.us": ["energy"]},
    )  # fmt: skip
    strategy = StrategyConfig(
        base_currency="CZK",
        categories=["sector"],
        classifications={"AAA": {"sector": "Software & services"}},
    )
    assert ex.proxy_preferences(book, strategy) == {"AAA": ["proxy:xlk.us"]}


# --- daily matrix (G12), stale feed (G11), scaled forecast (G15), weight basis (G16) ----------


def test_the_book_builds_a_daily_matrix_and_backfills_short_daily_histories() -> None:
    start, end = dt.date(2024, 1, 1), dt.date(2025, 12, 31)
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    rng = np.random.default_rng(2)
    market = np.cumprod(1 + rng.normal(0, 0.01, len(days))) * 100
    short_from = 400
    own = (
        np.cumprod(
            1 + 1.2 * np.diff(market, prepend=market[0]) / market + rng.normal(0, 0.003, len(days))
        )
        * 50
    )
    inputs = [
        rt.SeriesInput(
            "NEW",
            [(d, float(v)) for d, v in zip(days[short_from:], own[short_from:], strict=True)],
            "CZK",
        ),
        rt.SeriesInput(
            ex.PROXY_PREFIX + "xlk.us",
            [(d, float(v)) for d, v in zip(days, market, strict=True)],
            "CZK",
        ),
    ]
    book = ex.LoadedBook(
        "CZK", 100.0, [ex.BookPosition("NEW", "New", "CZK", 100.0)],
        rt.ReturnMatrix("weekly", [], [], np.empty((0, 0)), {}), {}, {}, {},
        inputs=inputs, fx=_fx, start=start, end=end,
    )  # fmt: skip
    raw = book.daily(500)
    assert raw.frequency == "daily" and len(raw.dates) == 500
    assert np.isnan(raw.column("NEW")[:5]).all() or raw.coverage["NEW"].observations == 500
    filled, status = book.daily_filled(900)
    assert status["NEW"].status == "proxied" and not np.isnan(filled.column("NEW")).any()
    assert ex.LoadedBook("CZK", 1.0, [], raw, {}, {}, {}).daily().dates == []


def test_a_stalled_price_feed_is_called_out_and_the_forecast_is_scaled_for_missing_weight() -> None:
    rng = np.random.default_rng(6)
    cols = {
        "AAA": rng.normal(0.001, 0.02, 156),
        "BBB": rng.normal(0.001, 0.02, 156),
        "NEW": np.where(np.arange(156) < 140, np.nan, rng.normal(0, 0.03, 156)),
    }
    cols["AAA"][-8:] = np.nan  # the feed for AAA stopped eight weeks ago
    cols[ex.BENCHMARK_KEY] = 0.9 * cols["AAA"] + rng.normal(0, 0.01, 156)
    cols[ex.BENCHMARK_KEY][-8:] = rng.normal(0, 0.02, 8)
    book = _book_of(cols, {"AAA": 400.0, "BBB": 400.0, "NEW": 200.0})
    strategy = StrategyConfig(base_currency="CZK", weight_basis="invested")
    report = ex.build_report(book, strategy)
    assert report.volatility_forecast is not None
    assert any("price feed may have stalled" in w and "AAA" in w for w in report.warnings)
    assert report.coverage is not None and report.coverage.covered_share == pytest.approx(0.8)
    assert report.volatility_forecast_scaled == pytest.approx(report.volatility_forecast / 0.8)
    assert report.weight_basis == "invested"
    row = next(r for r in report.positions if r.ticker == "BBB")
    assert row.weight_of_invested == pytest.approx(0.4)  # 400 of the 1,000 invested


def _book_of(columns: dict[str, np.ndarray], values: dict[str, float]) -> ex.LoadedBook:
    keys = list(columns)
    data = np.column_stack([columns[k] for k in keys])
    dates = [START + dt.timedelta(weeks=i) for i in range(data.shape[0])]
    matrix = rt.ReturnMatrix("weekly", dates, keys, data, rt.coverage_of(keys, dates, data))
    held = [ex.BookPosition(t, t.title(), "CZK", v) for t, v in values.items()]
    return ex.LoadedBook("CZK", sum(values.values()), held, matrix, {}, {}, {})


# --- owner episodes (G37) and the proxy caution (G36) -----------------------------------------


def test_owner_episodes_are_added_and_replace_a_built_in_with_the_same_key() -> None:
    strategy = StrategyConfig(base_currency="CZK")
    strategy.risk.stress_episodes = [
        StressEpisodeConfig(
            key="brexit", label="Brexit vote", start=dt.date(2016, 6, 22), end=dt.date(2016, 6, 27)
        ),
        StressEpisodeConfig(
            key="covid", label="My Covid", start=dt.date(2020, 2, 1), end=dt.date(2020, 3, 31)
        ),
    ]
    episodes = rp.episodes_for(strategy)
    keys = [e.key for e in episodes]
    assert keys == sorted(keys, key=lambda k: next(e.start for e in episodes if e.key == k))
    assert "brexit" in keys and next(e for e in episodes if e.key == "covid").label == "My Covid"
    assert len(episodes) == len(st.EPISODES) + 1
    with pytest.raises(ValueError):
        StressEpisodeConfig(
            key="bad", label="x", start=dt.date(2020, 2, 2), end=dt.date(2020, 2, 1)
        )


def test_a_replay_that_leans_on_a_proxy_carries_a_caution() -> None:
    book, inputs = _book()
    report = rp.build_replay(book, inputs, {}, _fx, 0.2, [EP], custom_keys=frozenset({"test"}))
    episode = report.episodes[0]
    assert episode.proxied_weight > 0 and episode.caution is not None
    assert "understated" in episode.caution and episode.custom is True


# ---- derived risk tiers: the strategy sets rules, never holdings ----


def _tier(vol, facts):
    from steward.config.strategy import RiskLimits
    from steward.portfolio.risk_tier import derive_tier

    return derive_tier("X", vol, facts, RiskLimits(auto_tiers=True))


def test_a_stock_is_tiered_by_volatility_size_and_profit() -> None:
    from steward.portfolio.risk_tier import Fundamentals

    calm = Fundamentals(True, 3e11, True)
    assert _tier(0.20, calm).tier == "core"
    assert _tier(0.30, calm).tier == "growth"  # too volatile for core, not for speculative
    assert _tier(0.40, calm).tier == "speculative"
    assert _tier(0.20, Fundamentals(True, 1e9, True)).tier == "speculative"  # small
    assert _tier(0.20, Fundamentals(True, 3e11, False)).tier == "speculative"  # loss-making
    assert _tier(0.20, Fundamentals(True, 3e11, None)).tier == "growth"  # profit unknown: not core
    history = _tier(0.20, Fundamentals(True, 3e11, True, loss_years=1))
    assert history.tier == "growth" and "loss in 1" in history.reason  # a loss year: not core
    assert _tier(0.20, Fundamentals(True, 3e11, True, loss_years=0)).tier == "core"


def test_a_fund_is_tiered_by_volatility_alone_and_none_without_prices() -> None:
    assert _tier(0.15, None).tier == "core" and _tier(0.15, None).kind == "fund"
    assert _tier(0.30, None).tier == "growth"
    assert _tier(0.50, None).tier == "speculative"
    assert _tier(None, None) is None


def test_an_owner_entered_tier_wins_over_a_derived_one() -> None:
    from steward.config.strategy import StrategyConfig
    from steward.portfolio.risk_tier import apply_tiers

    strategy = StrategyConfig(
        base_currency="CZK",
        categories=["sector", "risk"],
        classifications={"A": {"risk": "growth"}, "B": {"sector": "Tech"}},
    )
    out = apply_tiers(strategy, {"A": "core", "B": "core", "C": "speculative"})
    assert out.classifications["A"] == {"risk": "growth"}
    assert out.classifications["B"] == {"sector": "Tech", "risk": "core"}
    assert out.classifications["C"] == {"risk": "speculative"}
    assert strategy.classifications.get("C") is None  # the stored strategy is untouched
