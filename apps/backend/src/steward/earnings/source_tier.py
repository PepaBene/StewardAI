"""The trust tier of the page a fact was read from, by the rules news corroboration uses.

A fact's source host is tier 1 when it is one of the holding's own IR or results domains
(its holding profile); otherwise it takes the tier the news_sources config lists for the
domain (longest match, subdomains included). The owner's feedback then applies as it does
in corroboration: a host whose trust score is below the verification config's
`demote_below` counts one tier lower (at most 3). A host that is neither is unknown (null):
no tier is guessed.
"""

from dataclasses import dataclass, field

import asyncpg

from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.news_sources import NewsSourcesConfig
from steward.config.store import load_active
from steward.config.verification import VerificationConfig
from steward.news.normalize import host_of
from steward.verify.trust import TrustTable, load_trust


@dataclass
class SourceTiers:
    registry: NewsSourcesConfig
    profiles: HoldingProfilesConfig | None
    trust: TrustTable
    _ir: dict[str, list[str]] = field(default_factory=dict)

    def _ir_domains(self, ticker: str) -> list[str]:
        if ticker not in self._ir:
            profile = self.profiles.for_ticker(ticker) if self.profiles else None
            self._ir[ticker] = (
                [d.lower().removeprefix("www.") for d in profile.allowed_domains()]
                if profile
                else []
            )
        return self._ir[ticker]

    def tier(self, ticker: str, url: str | None) -> int | None:
        host = host_of(url)
        if not host:
            return None
        if any(host == d or host.endswith(f".{d}") for d in self._ir_domains(ticker)):
            base: int | None = 1
        else:
            base = self.registry.domain_tier(host)
        return None if base is None else self.trust.effective_tier(base, host)


async def load_source_tiers(pool: asyncpg.Pool) -> SourceTiers:
    """The active news_sources, holding_profiles and verification configs (defaults when
    none is saved) with the stored trust scores."""
    registry = await load_active(pool, "news_sources", NewsSourcesConfig)
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    verification = await load_active(pool, "verification", VerificationConfig)
    settings = (verification.body if verification else VerificationConfig()).trust
    return SourceTiers(
        registry=registry.body if registry else NewsSourcesConfig(),
        profiles=profiles.body if profiles else None,
        trust=await load_trust(pool, settings),
    )
