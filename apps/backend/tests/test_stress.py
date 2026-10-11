"""Historical stress replay on today's weights (quant plan Q3.1), on synthetic prices."""

import datetime as dt

import numpy as np
import pytest

from steward.portfolio import exante as ex
from steward.portfolio import replay as rp
from steward.quant import returns as rt
from steward.quant import stress as st

EP = st.Episode("test", "Test episode", dt.date(2020, 2, 19), dt.date(2020, 3, 23), "")
PROXY = ex.PROXY_PREFIX + "xlk.us"


def _fx(currency: str, day: dt.date) -> float | None:
    return 1.0


def _closes(start: dt.date, end: dt.date, first: float, last: float) -> list[tuple[dt.date, float]]:
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    return [(d, first + (last - first) * i / (len(days) - 1)) for i, d in enumerate(days)]


def test_move_is_the_base_currency_change_between_the_two_dates() -> None:
    series = rt.SeriesInput("A", _closes(dt.date(2020, 1, 1), dt.date(2020, 4, 1), 100, 70), "USD")
    got = st.move(series, lambda c, d: 25.0 if d < dt.date(2020, 3, 1) else 20.0, EP.start, EP.end)
    assert got is not None
    start_close = dict(series.closes)[EP.start]
    end_close = dict(series.closes)[EP.end]
    assert got == pytest.approx(end_close * 20.0 / (start_close * 25.0) - 1)
    # a series that starts after the episode has no move
    late = rt.SeriesInput("B", _closes(dt.date(2020, 6, 1), dt.date(2020, 7, 1), 10, 11), "CZK")
    assert st.move(late, _fx, EP.start, EP.end) is None


def test_rank_proxies_orders_by_r_squared_and_skips_short_overlap() -> None:
    rng = np.random.default_rng(1)
    market = rng.normal(0, 0.02, 80)
    own = 1.2 * market + rng.normal(0, 0.002, 80)
    noise = rng.normal(0, 0.02, 80)
    short = np.full(80, np.nan)
    short[:10] = market[:10]
    ranked = st.rank_proxies(own, {"noise": noise, "market": market, "short": short})
    assert [c.proxy for c in ranked] == ["market", "noise"]
    assert ranked[0].beta == pytest.approx(1.2, abs=0.05)


def test_replay_reconciles_labels_the_basis_and_flags_the_limit() -> None:
    holdings = [st.Holding("OLD", 500.0), st.Holding("NEW", 300.0), st.Holding("GONE", 100.0)]
    ranked = {
        "NEW": [st.ProxyChoice("p_late", 1.0, 0.9, 100), st.ProxyChoice("p_ok", 1.5, 0.5, 100)],
    }
    res = st.replay(
        EP,
        holdings,
        1000.0,  # 100 of cash
        {"OLD": -0.30, "NEW": None, "GONE": None},
        {"p_late": None, "p_ok": -0.20},
        ranked,
        drawdown_limit=0.20,
    )
    by = {r.ticker: r for r in res.positions}
    assert by["OLD"].basis == "real" and by["OLD"].pnl == pytest.approx(-150.0)
    # the best-fitting proxy has no data in the episode, so the next one is used
    assert by["NEW"].basis == "proxied" and by["NEW"].proxy == "p_ok"
    assert by["NEW"].move == pytest.approx(np.expm1(1.5 * np.log(0.8)))
    assert by["GONE"].basis == "unavailable" and by["GONE"].pnl == 0.0
    assert res.after == pytest.approx(res.before + sum(r.pnl for r in res.positions))
    assert res.real_weight == pytest.approx(0.5)
    assert res.proxied_weight == pytest.approx(0.3)
    assert res.unavailable_weight == pytest.approx(0.1)
    assert res.loss is not None and res.loss == pytest.approx(res.pnl / 1000.0)
    assert res.breaches_limit is True
    assert res.worst[0].ticker == "OLD"


def test_an_episode_nothing_covers_has_no_result_instead_of_a_zero_loss() -> None:
    res = st.replay(EP, [st.Holding("A", 100.0)], 100.0, {"A": None}, {}, {}, drawdown_limit=0.2)
    assert res.loss is None and res.breaches_limit is None
    assert res.unavailable_weight == pytest.approx(1.0)


def _book() -> tuple[ex.LoadedBook, list[rt.SeriesInput]]:
    rng = np.random.default_rng(5)
    market = rng.normal(0.0, 0.02, 156)
    cols = {
        "OLD": market + rng.normal(0, 0.005, 156),
        "NEW": 0.8 * market + rng.normal(0, 0.005, 156),
        PROXY: market,
    }
    start = dt.date(2017, 1, 4)  # a Wednesday
    dates = [start + dt.timedelta(weeks=i) for i in range(156)]
    data = np.column_stack(list(cols.values()))
    matrix = rt.ReturnMatrix(
        "weekly", dates, list(cols), data, rt.coverage_of(list(cols), dates, data)
    )
    held = [
        ex.BookPosition("OLD", "Old", "CZK", 400.0),
        ex.BookPosition("NEW", "New", "CZK", 300.0),
    ]
    book = ex.LoadedBook("CZK", 1000.0, held, matrix, {}, {PROXY: "US technology"}, {})
    inputs = [
        rt.SeriesInput("OLD", _closes(dt.date(2019, 1, 1), dt.date(2021, 1, 1), 100, 80), "CZK"),
        rt.SeriesInput("NEW", _closes(dt.date(2020, 9, 1), dt.date(2021, 1, 1), 50, 60), "CZK"),
        rt.SeriesInput(PROXY, _closes(dt.date(2019, 1, 1), dt.date(2021, 1, 1), 100, 120), "CZK"),
    ]
    return book, inputs


def test_the_report_replays_what_the_prices_cover_and_says_why_it_cannot_replay_the_rest() -> None:
    book, inputs = _book()
    old_episode = st.Episode(
        "old", "Before the data", dt.date(2008, 9, 12), dt.date(2009, 3, 9), ""
    )
    report = rp.build_replay(book, inputs, {PROXY: "US technology"}, _fx, 0.2, [old_episode, EP])
    first, second = report.episodes
    assert first.status == "no_data" and first.loss is None and first.after is None
    assert first.reason is not None and "stored prices" in first.reason
    assert second.status == "replayed" and second.loss is not None
    bases = {p.ticker: p.basis for p in second.positions}
    assert bases == {"OLD": "real", "NEW": "proxied"}
    proxied = next(p for p in second.positions if p.ticker == "NEW")
    assert proxied.proxy == "US technology" and proxied.beta == pytest.approx(0.8, abs=0.1)
    assert second.unavailable_weight == pytest.approx(0.0)
    assert second.after == pytest.approx(1000.0 + second.pnl)
    assert report.warnings and "price history" in report.warnings[0]
    assert report.history_start == dt.date(2019, 1, 1)
