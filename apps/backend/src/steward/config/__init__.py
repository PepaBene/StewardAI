"""Pydantic schemas for every config domain.

These models are the single source of truth for config validation: the API validates
every config write against them, and the OpenAPI spec built from them generates the
frontend's TypeScript types.
"""

from pydantic import BaseModel

from steward.config.holding_profiles import HoldingProfile, HoldingProfilesConfig
from steward.config.jobs import JobSchedule, JobsConfig
from steward.config.strategy import StrategyConfig
from steward.config.thesis import ThesesConfig, ThesisConfig, ThesisKPI
from steward.config.verification import VerificationConfig

# Domains implemented so far. The `config_version.domain` check constraint lists every
# domain from the spec, so later phases only add a model here.
CONFIG_MODELS: dict[str, type[BaseModel]] = {
    "strategy": StrategyConfig,
    "theses": ThesesConfig,
    "jobs": JobsConfig,
    "holding_profiles": HoldingProfilesConfig,
    "verification": VerificationConfig,
}

__all__ = [
    "CONFIG_MODELS",
    "HoldingProfile",
    "HoldingProfilesConfig",
    "JobSchedule",
    "JobsConfig",
    "StrategyConfig",
    "ThesesConfig",
    "ThesisConfig",
    "ThesisKPI",
    "VerificationConfig",
]
