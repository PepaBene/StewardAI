"""Point-in-time selection of versioned observations (restated filings, revised macro releases).

An observation is identified by (`key`, `period`); each revision has its own `known_at`. "As
known at T" returns, per identity, the latest revision published on or before T, so a result
saved at T is not rewritten when a later revision arrives (plan engineering rule 4).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Observation:
    key: str
    period: dt.date
    value: Decimal
    known_at: dt.datetime
    revision: int = 0


def as_known_at(
    observations: Iterable[Observation], cutoff: dt.datetime | None
) -> dict[tuple[str, dt.date], Observation]:
    """Latest revision per (key, period) with `known_at <= cutoff` (no cutoff: the latest)."""
    chosen: dict[tuple[str, dt.date], Observation] = {}
    for obs in observations:
        if cutoff is not None and obs.known_at > cutoff:
            continue
        ident = (obs.key, obs.period)
        current = chosen.get(ident)
        if current is None or (obs.known_at, obs.revision) > (current.known_at, current.revision):
            chosen[ident] = obs
    return chosen
