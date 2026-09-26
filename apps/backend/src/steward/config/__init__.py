"""Pydantic schemas for every config domain.

These models are the single source of truth for config validation: the API validates
every config write against them, and the OpenAPI spec built from them generates the
frontend's TypeScript types.
"""

from pydantic import BaseModel

from steward.config.budget import BudgetConfig
from steward.config.event_rules import EventRulesConfig
from steward.config.holding_profiles import HoldingProfile, HoldingProfilesConfig
from steward.config.jobs import JobSchedule, JobsConfig
from steward.config.news_sources import NewsSourcesConfig
from steward.config.notifications import NotificationsConfig
from steward.config.research import ResearchProfile, ResearchProfilesConfig
from steward.config.strategy import StrategyConfig
from steward.config.thesis import ThesesConfig, ThesisConfig, ThesisKPI
from steward.config.verification import VerificationConfig

# Every domain of the `config_version.domain` check constraint.
CONFIG_MODELS: dict[str, type[BaseModel]] = {
    "strategy": StrategyConfig,
    "theses": ThesesConfig,
    "jobs": JobsConfig,
    "holding_profiles": HoldingProfilesConfig,
    "verification": VerificationConfig,
    "news_sources": NewsSourcesConfig,
    "event_rules": EventRulesConfig,
    "notifications": NotificationsConfig,
    "budget": BudgetConfig,
    "research_profiles": ResearchProfilesConfig,
}

__all__ = [
    "CONFIG_MODELS",
    "BudgetConfig",
    "EventRulesConfig",
    "HoldingProfile",
    "HoldingProfilesConfig",
    "JobSchedule",
    "JobsConfig",
    "NewsSourcesConfig",
    "NotificationsConfig",
    "ResearchProfile",
    "ResearchProfilesConfig",
    "StrategyConfig",
    "ThesesConfig",
    "ThesisConfig",
    "ThesisKPI",
    "VerificationConfig",
]
