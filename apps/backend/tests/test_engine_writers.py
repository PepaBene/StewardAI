"""Every place the engine writes on its own is listed in the "what the engine did" view.

A module that starts writing autonomously (a self-written config version, an autopilot decision, a
notice, an engine change, an auto-logged trade, an override) is not in the lists below, so this
test fails until its author maps it to a source of `steward.engine.timeline`. That is the check
the plan asks for: a write cannot skip the ledger without someone noticing.
"""

import re
from pathlib import Path

from steward.engine import timeline

SRC = Path(__file__).resolve().parents[1] / "src"

# source -> (pattern that marks an autonomous write, the modules that contain it)
WRITERS: dict[str, tuple[str, set[str]]] = {
    "config": (
        r"created_by=None",
        {
            "steward/thesis/job.py",
            "steward/ops/tuning.py",
            "steward/autopilot/thesis_upkeep.py",
            "steward/autopilot/kinds/thesis.py",
            "steward/autopilot/kinds/graph.py",
            "steward/exposures/ai_steps.py",
            "steward/fundamentals/ir_discovery.py",
            "steward/engine/config_change.py",
        },
    ),
    "autopilot": (
        r"\bledger\.record\(|\bledger\.set_mode\(",
        {
            "steward/actions/registry.py",
            "steward/autopilot/judge.py",
            "steward/autopilot/calibration.py",
            "steward/engine/learning.py",
        },
    ),
    "watchdog": (
        r"\btasks\.see\(|\bops_tasks\.see\(",
        {
            "steward/engine/override_watch.py",
            "steward/autopilot/reverify.py",
            "steward/ops/delivery.py",
            "steward/ops/watchdog.py",
            "steward/ops/anticipate.py",
            "steward/engine/audit.py",
            "steward/engine/challenger.py",
            "steward/ops/liveness.py",
            "steward/thesis/review.py",
            "steward/thesis/job.py",
            "steward/ops/tuning.py",
            "steward/ops/release_gate.py",
            "steward/ops/journal_gaps.py",
            "steward/ops/opportunities.py",
            "steward/ops/suggestions.py",
            "steward/autopilot/thesis_upkeep.py",
            "steward/engine/learning.py",
            "steward/engine/triggers.py",
            "steward/engine/data_audit.py",
            "steward/engine/prompt_change.py",
            "steward/engine/watchers.py",
            "steward/engine/instructions.py",
            "steward/engine/tuners.py",
            "steward/engine/outcomes.py",
            "steward/engine/second_look.py",
            "steward/engine/domain_learning.py",
        },
    ),
    "engine_change": (
        r"\bchange\.propose\(|engine_change\.propose\(",
        {
            "steward/engine/override_watch.py",
            "steward/engine/prompt_change.py",
            "steward/engine/instructions.py",
            "steward/engine/config_change.py",
        },
    ),
    "trigger": (
        r"\btriggers\.(fire|log)\(",
        {
            "steward/autopilot/research_trigger.py",
            "steward/autopilot/thesis_upkeep.py",
            "steward/ops/anticipate.py",
            "steward/earnings/calendar.py",
            "steward/engine/learning.py",
            "steward/engine/watchers.py",
        },
    ),
    "journal": (r'origin="auto"', {"steward/ops/journal_gaps.py"}),
    "override": (
        r"\boverrides\.record(_config_edit)?\(",
        {
            "steward/actions/registry.py",
            "api/action_routes.py",
            "steward/news/read_analysis.py",
            "api/autopilot_routes.py",
            "api/journal_routes.py",
            "api/chat_routes.py",
            "api/routes.py",
            "api/config_history_routes.py",
        },
    ),
}


def modules_with(pattern: str) -> set[str]:
    rx = re.compile(pattern)
    return {
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if rx.search(p.read_text(encoding="utf-8"))
    }


def test_every_writer_maps_to_a_source_of_the_view() -> None:
    assert set(WRITERS) <= set(timeline.SOURCES)


def test_no_module_writes_autonomously_without_being_listed() -> None:
    for source, (pattern, expected) in WRITERS.items():
        found = modules_with(pattern) - {"steward/engine/timeline.py"}
        assert found == expected, (
            f"{source}: modules that write on their own changed. New: {sorted(found - expected)}; "
            f"gone: {sorted(expected - found)}. Map them to a source in steward/engine/timeline.py "
            "and update WRITERS."
        )
