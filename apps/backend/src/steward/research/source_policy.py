"""Which web sources a research run may search and cite, and what each one is worth.

The order of trust, for the model's brief and for the report's labels:

1. primary: the issuer's own pages, regulators, exchanges (tier 1), for facts;
2. quality news: established financial press and wires (tier 2), for context;
3. secondary: reference and specialist sites (tier 3), for interpretation;
4. owner-trusted: domains the owner added to the profile. Searched, never counted as proof;
5. forum: forums and social media, only when the profile enables them, always labelled.

A denied domain is neither searched nor cited. Nothing here decides whether a claim is true;
that stays with the claim verifier.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from steward.config.research import ResearchProfile

Role = Literal["primary", "quality_news", "secondary", "owner_trusted", "forum"]

ROLE_BY_TIER: dict[int, Role] = {1: "primary", 2: "quality_news", 3: "secondary"}

ROLE_CAVEATS: dict[str, str] = {
    "owner_trusted": "On a domain the owner trusts; not independent proof.",
    "forum": "Forum or social media: opinion, not a source of fact.",
}

FORUM_HOSTS: tuple[str, ...] = (
    "reddit.com",
    "x.com",
    "twitter.com",
    "stocktwits.com",
    "facebook.com",
    "youtube.com",
    "tiktok.com",
    "discord.com",
    "t.me",
    "quora.com",
    "medium.com",
    "substack.com",
)

# Sites that often show a search snippet but not the article to a crawler.
PAYWALLED_HOSTS: tuple[str, ...] = (
    "wsj.com",
    "ft.com",
    "bloomberg.com",
    "barrons.com",
    "economist.com",
    "thetimes.co.uk",
    "nytimes.com",
)

PRIORITY_TEXT = (
    "Source order: for a fact (a figure, a date, a filing), prefer the issuer's own pages, "
    "regulators and exchanges; use established financial press for context and reporting; "
    "treat reference and specialist sites as interpretation. A fact that only a secondary "
    "source gives should say so in the sentence."
)


def _matches(host: str, domains: tuple[str, ...] | list[str]) -> bool:
    return any(host == d or host.endswith(f".{d}") for d in domains)


def normalise_host(host: str | None) -> str:
    return (host or "").lower().removeprefix("www.")


@dataclass(frozen=True)
class Decision:
    allowed: bool
    tier: int | None
    role: Role | None
    reason: str | None  # why it is excluded, or a caveat when allowed


@dataclass
class SourcePolicy:
    tiers: tuple[int, ...]
    tier_of: Callable[[str | None], int | None]
    deny: tuple[str, ...] = ()
    trusted: tuple[str, ...] = ()
    allow_forums: bool = False

    @classmethod
    def for_profile(
        cls,
        profile: ResearchProfile,
        tier_of: Callable[[str | None], int | None],
        blocked: tuple[str, ...] = (),
    ) -> "SourcePolicy":
        return cls(
            tiers=tuple(profile.source_tiers),
            tier_of=tier_of,
            deny=tuple(dict.fromkeys([*profile.deny_domains, *blocked])),
            trusted=tuple(profile.allow_domains),
            allow_forums=profile.allow_forums,
        )

    def search_domains(self, tiered: tuple[str, ...]) -> tuple[str, ...]:
        """The allow-list handed to the search provider: the tier lists minus denied domains,
        plus the owner's domains, plus forums when enabled."""
        wanted = [d for d in tiered if not _matches(normalise_host(d), self.deny)]
        wanted += [d for d in self.trusted if not _matches(d, self.deny)]
        if self.allow_forums:
            wanted += [d for d in FORUM_HOSTS if not _matches(d, self.deny)]
        return tuple(dict.fromkeys(wanted))[:80]

    def decide(self, host: str | None) -> Decision:
        host = normalise_host(host)
        if not host:
            return Decision(False, None, None, "no host")
        if _matches(host, self.deny):
            return Decision(False, None, None, "domain is denied")
        tier = self.tier_of(host)
        if tier is not None and tier in self.tiers:
            return Decision(True, tier, ROLE_BY_TIER[tier], None)
        if _matches(host, self.trusted):
            return Decision(True, None, "owner_trusted", ROLE_CAVEATS["owner_trusted"])
        if _matches(host, FORUM_HOSTS):
            if self.allow_forums:
                return Decision(True, None, "forum", ROLE_CAVEATS["forum"])
            return Decision(False, None, None, "forums are not enabled in this profile")
        if tier is not None:
            return Decision(False, tier, ROLE_BY_TIER[tier], f"tier {tier} is not allowed here")
        return Decision(False, None, None, "domain is not on any allowed list")


def paywall_limit(host: str | None) -> str | None:
    if _matches(normalise_host(host), PAYWALLED_HOSTS):
        return "likely paywalled: the model may have seen only a snippet"
    return None
