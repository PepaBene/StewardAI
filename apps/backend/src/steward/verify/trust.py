"""Source trust (Defence layer 1).

Every source host starts at 1. The owner's feedback moves it: *wrong* lowers it by the
Verification config's penalty, *useful* raises it by the reward (capped at 1). A source
whose score is below `demote_below` counts one tier lower wherever corroboration counts
tiers: a tier-1 source no longer confirms alone, and a tier-2 outlet no longer counts as an
independent source.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import asyncpg

from steward.config.verification import TrustSettings
from steward.news.normalize import host_of

Rating = str  # "wrong" | "irrelevant" | "useful"


@dataclass(frozen=True)
class TrustTable:
    scores: Mapping[str, float]
    settings: TrustSettings

    def score(self, host: str | None) -> float:
        return self.scores.get(host or "", 1.0)

    def effective_tier(self, tier: int, host: str | None) -> int:
        """The tier a source counts as: one lower (max 3) while its trust is low."""
        if self.score(host) < self.settings.demote_below:
            return min(3, tier + 1)
        return tier


async def load_trust(
    conn: asyncpg.Pool | asyncpg.Connection, settings: TrustSettings
) -> TrustTable:
    rows = await conn.fetch("select host, score from public.source_trust")
    return TrustTable({r["host"]: float(r["score"]) for r in rows}, settings)


def hosts_of(urls: Iterable[str | None]) -> list[str]:
    return sorted({h for h in (host_of(u) for u in urls if u) if h})


async def apply_rating(
    conn: asyncpg.Connection, hosts: Iterable[str], rating: Rating, settings: TrustSettings
) -> dict[str, float]:
    """Move each host's score for one piece of feedback; returns the new scores. Irrelevant
    says nothing about accuracy and leaves trust alone."""
    changed: dict[str, float] = {}
    if rating not in ("wrong", "useful"):
        return changed
    delta = -settings.wrong_penalty if rating == "wrong" else settings.useful_reward
    for host in hosts:
        score = await conn.fetchval(
            "insert into public.source_trust as t (host, score, wrong_count, useful_count)"
            " values ($1, greatest(0, least(1, 1 + $2::numeric)), $3, $4)"
            " on conflict (host) do update set"
            " score = greatest(0, least(1, t.score + $2::numeric)),"
            " wrong_count = t.wrong_count + $3, useful_count = t.useful_count + $4,"
            " updated_at = now() returning score",
            host,
            delta,
            1 if rating == "wrong" else 0,
            1 if rating == "useful" else 0,
        )
        changed[host] = float(score)
    return changed
