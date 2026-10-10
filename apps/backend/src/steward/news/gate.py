"""Release gate for the news classifier: a change to sources, prompts, models, clustering or
severity must not make any segment (language, region, event type) clearly worse.

    python -m steward.news.eval --set benchmark --live --save-baseline baseline.json
    python -m steward.news.eval --set benchmark --live --gate baseline.json

A segment fails when the top of its 95 % Wilson interval is below the baseline rate: the drop
is larger than the noise a sample that size allows. Segments under `MIN_ITEMS` items in
either run are listed but never gate, because their interval says nothing.
"""

import datetime as dt
import json
import math
from dataclasses import dataclass
from pathlib import Path

MIN_ITEMS = 10
TARGET = 0.85


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return (0.0, 1.0)
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass(frozen=True)
class Baseline:
    model: str
    recorded: str
    pooled: tuple[int, int]
    segments: dict[str, tuple[int, int]]


def make_baseline(
    model: str, pooled: tuple[int, int], segments: dict[str, list[int]], now: dt.date
) -> Baseline:
    return Baseline(model, now.isoformat(), pooled, {k: (v[0], v[1]) for k, v in segments.items()})


def save_baseline(path: Path, baseline: Baseline) -> None:
    path.write_text(
        json.dumps(
            {
                "model": baseline.model,
                "recorded": baseline.recorded,
                "pooled": list(baseline.pooled),
                "segments": {k: list(v) for k, v in sorted(baseline.segments.items())},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def load_baseline(path: Path) -> Baseline:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Baseline(
        raw["model"],
        raw["recorded"],
        (raw["pooled"][0], raw["pooled"][1]),
        {k: (v[0], v[1]) for k, v in raw["segments"].items()},
    )


def check(
    pooled: tuple[int, int],
    segments: dict[str, list[int]],
    baseline: Baseline | None,
    *,
    min_items: int = MIN_ITEMS,
    target: float = TARGET,
) -> list[str]:
    """Reasons the run must not ship; an empty list means it may. Without a baseline only the
    pooled target is checked, and that is said in the output rather than passed silently."""
    failures: list[str] = []
    hits, total = pooled
    if total == 0 or hits / total < target:
        failures.append(f"pooled accuracy {hits}/{total} is below the {target:.0%} target")
    if baseline is None:
        return failures
    for name, (b_hits, b_total) in sorted(baseline.segments.items()):
        if b_total < min_items:
            continue
        now = segments.get(name)
        if now is None or now[1] == 0:
            failures.append(f"{name}: in the baseline ({b_total} items) but missing from this run")
            continue
        if now[1] < min_items:
            continue
        _, high = wilson_interval(now[0], now[1])
        base_rate = b_hits / b_total
        if high < base_rate:
            failures.append(
                f"{name}: {now[0]}/{now[1]} = {now[0] / now[1]:.0%} (up to {high:.0%}) "
                f"is below the baseline {b_hits}/{b_total} = {base_rate:.0%}"
            )
    return failures
