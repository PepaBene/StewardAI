"""One module per queue the autopilot settles. A module exports `KIND` (one `JudgeKind`) or
`KINDS` (a list of them).

Add a module's name to `KIND_MODULES` to switch it on; the job, the API and calibration pick it
up from here.
"""

import importlib
from functools import cache

from steward.autopilot.judge import JudgeKind

KIND_MODULES: tuple[str, ...] = (
    "facts",
    "graph",
    "inbox",
    "disclosure",
    "disclosure_data",
    "news",
    "approvals",
    "thesis",
)


@cache
def all_kinds() -> dict[str, JudgeKind]:
    kinds: dict[str, JudgeKind] = {}
    for name in KIND_MODULES:
        module = importlib.import_module(f"steward.autopilot.kinds.{name}")
        found: list[JudgeKind] = list(getattr(module, "KINDS", [])) or [module.KIND]
        for kind in found:
            if kind.name in kinds:
                raise ValueError(f"two autopilot kinds are named {kind.name!r}")
            kinds[kind.name] = kind
    return kinds
