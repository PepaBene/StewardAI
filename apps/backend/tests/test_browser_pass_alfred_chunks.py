"""Browser-pass item 10: ALFRED refuses a real-time range of more than 2000 vintage dates (DFF).

Verified live 2026-10-03 with the real key: CPIAUCSL answers in one request (682 windows) but DFF
gets HTTP 400 "There are 5138 vintage dates in the specified real-time period"; the chunked fetch
returned 3928 windows for the last ten years with no duplicate (observation, vintage)."""

import datetime as dt
from typing import Any

import httpx
import pytest

from steward.macro import collect
from steward.macro.collect import MacroError, _alfred_observations, merge_realtime_chunks

D = [dt.date(2020, 1, 1) + dt.timedelta(days=i * 10) for i in range(6)]  # six vintage dates
TODAY = dt.date(2020, 4, 1)


def row(date: str, start: dt.date, end: dt.date, value: str) -> dict[str, str]:
    return {
        "date": date,
        "realtime_start": start.isoformat(),
        "realtime_end": end.isoformat(),
        "value": value,
    }


def day(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=1)


def test_a_window_clipped_at_a_chunk_boundary_is_one_window_and_a_revision_is_kept() -> None:
    chunks = [
        (dt.date(1776, 7, 4), [row("X", D[0], day(D[2]), "1"), row("Y", D[1], day(D[2]), "5")]),
        (
            D[2],
            [
                row("X", D[2], day(D[4]), "1"),
                row("Y", D[2], day(D[3]), "5"),
                row("Y", D[3], day(D[4]), "6"),
            ],
        ),
        (D[4], [row("X", D[4], TODAY, "1"), row("Y", D[4], TODAY, "6")]),
    ]
    merged = merge_realtime_chunks(chunks)
    assert [(r["date"], r["realtime_start"], r["realtime_end"], r["value"]) for r in merged] == [
        ("X", D[0].isoformat(), TODAY.isoformat(), "1"),
        ("Y", D[1].isoformat(), day(D[3]).isoformat(), "5"),
        ("Y", D[3].isoformat(), TODAY.isoformat(), "6"),
    ]


def test_a_new_value_that_starts_exactly_on_the_boundary_is_a_real_revision() -> None:
    chunks = [
        (dt.date(1776, 7, 4), [row("X", D[0], day(D[2]), "1")]),
        (D[2], [row("X", D[2], TODAY, "2")]),
    ]
    assert [r["value"] for r in merge_realtime_chunks(chunks)] == ["1", "2"]


def _transport(too_many: bool = True) -> tuple[httpx.MockTransport, list[dict[str, str]]]:
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        calls.append({"path": request.url.path, **params})
        if request.url.path.endswith("vintagedates"):
            body = {"count": len(D), "vintage_dates": [d.isoformat() for d in D]}
            return httpx.Response(200, json=body)
        start = dt.date.fromisoformat(params["realtime_start"])
        end = dt.date.fromisoformat(params["realtime_end"])
        if too_many and start.year < 2000 and end >= D[-1]:
            return httpx.Response(
                400,
                json={
                    "error_message": "Bad Request.  There are 5138 vintage dates in the specified "
                    "real-time period. This exceeds the maximum number of vintage dates allowed"
                },
            )
        rows = [row("X", max(start, D[0]), end, "1")] if end >= D[0] else []
        return httpx.Response(200, json={"count": len(rows), "observations": rows})

    return httpx.MockTransport(handler), calls


async def test_a_range_over_the_vintage_limit_is_fetched_in_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(collect, "FRED_CHUNK_VINTAGE_DATES", 2)
    transport, calls = _transport()
    async with httpx.AsyncClient(transport=transport) as http:
        body: Any = await _alfred_observations(http, "DFF", "k", dt.date(2020, 1, 1), TODAY)
    # One refused request, one vintage-date listing, then a chunk per two vintage dates; no
    # chunk spans more than the limit and the three chunks tile the whole real-time range.
    assert [c["path"].rsplit("/", 1)[-1] for c in calls] == [
        "observations", "vintagedates", "observations", "observations", "observations",
    ]  # fmt: skip
    spans = [(c["realtime_start"], c["realtime_end"]) for c in calls[2:]]
    assert spans == [
        ("1776-07-04", day(D[2]).isoformat()),
        (D[2].isoformat(), day(D[4]).isoformat()),
        (D[4].isoformat(), TODAY.isoformat()),
    ]
    assert [(r["date"], r["realtime_start"], r["realtime_end"]) for r in body["observations"]] == [
        ("X", D[0].isoformat(), TODAY.isoformat())
    ]


async def test_other_failures_still_raise_and_the_normal_case_is_one_request() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500, text="boom"))
    ) as http:
        with pytest.raises(MacroError, match="HTTP 500"):
            await _alfred_observations(http, "DFF", "k", dt.date(2020, 1, 1), TODAY)
    transport, calls = _transport(too_many=False)
    async with httpx.AsyncClient(transport=transport) as http:
        await _alfred_observations(http, "CPIAUCSL", "k", dt.date(2020, 1, 1), TODAY)
    assert len(calls) == 1
