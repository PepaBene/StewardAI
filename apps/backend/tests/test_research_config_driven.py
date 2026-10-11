"""Settings that were fixed constants: freshness limits, trace retention."""

import pytest
from pydantic import ValidationError

from steward.chat.research_tools import FRESHNESS_PROBES
from steward.config.jobs import DEFAULT_FRESHNESS_DAYS, JobsConfig
from steward.jobs.registry import JOB_HANDLERS


def test_freshness_defaults_match_the_probed_datasets_and_todays_limits() -> None:
    assert set(DEFAULT_FRESHNESS_DAYS) == {name for name, _, _ in FRESHNESS_PROBES}
    assert JobsConfig().chat.freshness_days == DEFAULT_FRESHNESS_DAYS
    assert DEFAULT_FRESHNESS_DAYS["news"] == 3 and DEFAULT_FRESHNESS_DAYS["fundamentals"] == 120


def test_freshness_overrides_are_validated_and_stored_configs_still_load() -> None:
    assert JobsConfig.model_validate({"chat": {}}).chat.freshness_days == DEFAULT_FRESHNESS_DAYS
    assert JobsConfig.model_validate(
        {"chat": {"freshness_days": {"news": 10}}}
    ).chat.freshness_days == {"news": 10}
    for bad in ({"colour": 3}, {"news": 0}):
        with pytest.raises(ValidationError):
            JobsConfig.model_validate({"chat": {"freshness_days": bad}})


def test_trace_retention_is_a_daily_enabled_job_with_a_30_day_default() -> None:
    cfg = JobsConfig()
    schedule = cfg.schedules()["research_trace_retention"]
    assert schedule.enabled and schedule.cron == "40 3 * * *"
    assert cfg.research.trace_retention_days == 30
    assert "research_trace_retention" in JOB_HANDLERS
    with pytest.raises(ValidationError):
        JobsConfig.model_validate({"research": {"trace_retention_days": 0}})
