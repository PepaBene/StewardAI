"""Prompts config: owner overrides of the system prompts the AI steps send.

Each model-facing step has a built-in system prompt in its own module (the default). An
override here replaces that text for the next run; removing it restores the default. Parts
that code fills in at run time (the event types a classifier chooses from, the output schema
enforced through structured outputs) are not in the prompt text and stay code-owned, so an
override can change instructions and tone but cannot break the output contract.

The CI evals gate fingerprints the built-in defaults only: an override is not covered by the
live evals, which the editor says next to every overridden prompt.
"""

import importlib
from dataclasses import dataclass
from typing import Annotated, Literal

import asyncpg
from pydantic import Field

from steward.config.common import ConfigModel

PromptKey = Literal[
    "earnings_extraction",
    "earnings_locate",
    "earnings_calendar",
    "news_classification",
    "news_corroboration",
    "news_escalation",
    "news_digest",
    "research_writer",
    "monthly_review",
    "chat_assistant",
    "claim_verifier",
    "ir_discovery_search",
    "ir_discovery_approve",
    "web_gap_search",
    "web_fact_approve",
    "web_domain_tier",
    "exposure_inputs",
    "exposure_hs_mapping",
    "dependency_extract",
    "dependency_approve",
    "listing_approval",
    "peer_approval",
]

PromptText = Annotated[str, Field(min_length=20, max_length=50_000)]


@dataclass(frozen=True)
class PromptInfo:
    title: str
    description: str
    # "module:attribute" of the built-in default, imported on demand (no import cycles).
    default_ref: str


PROMPTS: dict[PromptKey, PromptInfo] = {
    "earnings_extraction": PromptInfo(
        "Earnings extraction",
        "Reads figures, snippets and units out of a results document.",
        "steward.earnings.extract:SYSTEM_PROMPT",
    ),
    "earnings_locate": PromptInfo(
        "Results document search",
        "Finds the results release for a holding and period on the web.",
        "steward.earnings.locate:SEARCH_SYSTEM",
    ),
    "earnings_calendar": PromptInfo(
        "Report date reading",
        "Reads the next results date off an investor-relations calendar page.",
        "steward.earnings.calendar:CALENDAR_SYSTEM",
    ),
    "news_classification": PromptInfo(
        "News classification",
        "Labels news items with event type, severity, rumour and relevance. The list of event "
        "types is appended by code from the event rules.",
        "steward.news.classify:SYSTEM_PROMPT",
    ),
    "news_corroboration": PromptInfo(
        "News corroboration search",
        "Searches for independent sources that confirm or contradict a story.",
        "steward.news.corroborate:SEARCH_SYSTEM",
    ),
    "news_escalation": PromptInfo(
        "Impact note",
        "Writes the impact note for a severe confirmed story.",
        "steward.news.escalation:SYSTEM",
    ),
    "news_digest": PromptInfo(
        "Daily and weekly digest",
        "Writes the digest from the period's stories, facts and macro figures.",
        "steward.news.digest:SYSTEM_PROMPT",
    ),
    "research_writer": PromptInfo(
        "Deep research writer",
        "Writes each research section from the gathered sources.",
        "steward.research.writer:SYSTEM",
    ),
    "monthly_review": PromptInfo(
        "Monthly review",
        "Writes the monthly review and its proposed actions.",
        "steward.review.monthly:SYSTEM",
    ),
    "chat_assistant": PromptInfo(
        "Chat assistant",
        "Answers questions in the chat panel with sources.",
        "steward.chat.assistant:SYSTEM",
    ),
    "claim_verifier": PromptInfo(
        "Claim verifier",
        "Checks each written sentence against the snippets it cites.",
        "steward.verify.claims:SYSTEM",
    ),
    "ir_discovery_search": PromptInfo(
        "Investor page search",
        "Searches the web for a holding's investor-relations, results and calendar pages.",
        "steward.fundamentals.ir_discovery:SEARCH_SYSTEM",
    ),
    "ir_discovery_approve": PromptInfo(
        "Investor page approval",
        "Judges whether the fetched pages are the company's own investor pages.",
        "steward.fundamentals.ir_discovery:APPROVE_SYSTEM",
    ),
    "web_gap_search": PromptInfo(
        "Gap research search",
        "Searches reliable web pages for a fact the data sources left empty.",
        "steward.research.web_facts:SEARCH_SYSTEM",
    ),
    "web_fact_approve": PromptInfo(
        "Web fact approval",
        "Judges whether a quoted passage supports the proposed fact for this company.",
        "steward.research.web_facts:APPROVE_SYSTEM",
    ),
    "web_domain_tier": PromptInfo(
        "Web source tier",
        "Places a web domain the source lists do not name in tier 2, tier 3 or rejected.",
        "steward.research.domains:DOMAIN_SYSTEM",
    ),
    "exposure_inputs": PromptInfo(
        "Currency and input exposures",
        "Reads an annual report for the currencies and commodities a holding depends on.",
        "steward.exposures.inputs:INPUTS_SYSTEM",
    ),
    "exposure_hs_mapping": PromptInfo(
        "Product lines to trade codes",
        "Maps reported product lines to HS headings (always an estimate).",
        "steward.exposures.hs:HS_SYSTEM",
    ),
    "dependency_extract": PromptInfo(
        "Customers and suppliers",
        "Reads an annual report for named customers, suppliers and the shares it prints.",
        "steward.graph.dependencies:DEPENDENCY_SYSTEM",
    ),
    "dependency_approve": PromptInfo(
        "Customer and supplier approval",
        "Judges whether a quoted passage supports the claimed customer or supplier.",
        "steward.graph.dependencies:APPROVE_SYSTEM",
    ),
    "listing_approval": PromptInfo(
        "Listing approval",
        "Decides whether a derived exchange listing is the right one for a held security.",
        "steward.approvals.ai:LISTING_SYSTEM",
    ),
    "peer_approval": PromptInfo(
        "Peer approval",
        "Decides which vendor-suggested peers belong in a holding's peer set.",
        "steward.approvals.ai:PEER_SYSTEM",
    ),
}


def default_text(key: PromptKey) -> str:
    module, attribute = PROMPTS[key].default_ref.split(":")
    value = getattr(importlib.import_module(module), attribute)
    assert isinstance(value, str)
    return value


class PromptsConfig(ConfigModel):
    """Stored as `config_version` rows, domain `prompts`. No override: the default is used."""

    schema_version: Literal[1] = 1
    overrides: dict[PromptKey, PromptText] = Field(
        default_factory=dict,
        description="System prompt text per AI step, replacing the built-in default.",
    )

    def text(self, key: PromptKey, default: str) -> str:
        """The prompt to send: the override when one is set, else the caller's default."""
        return self.overrides.get(key) or default


async def load_prompts(conn: asyncpg.Connection | asyncpg.Pool) -> PromptsConfig:
    """The active prompts config, or an empty one (every default) when none is saved."""
    from steward.config.store import load_active

    stored = await load_active(conn, "prompts", PromptsConfig)
    return stored.body if stored is not None else PromptsConfig()
