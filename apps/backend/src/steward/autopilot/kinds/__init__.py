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


# A kind with no `verify` must be listed here with the reason (autonomous engine plan 1.2);
# `check_verifiers` fails for a kind that is in neither place, and for a waiver left behind after
# the kind got a check.
VERIFY_WAIVERS: dict[str, str] = {}


def check_verifiers(kinds: dict[str, JudgeKind] | None = None) -> None:
    for name, kind in (kinds or all_kinds()).items():
        if kind.verify is None and name not in VERIFY_WAIVERS:
            raise ValueError(f"autopilot kind {name!r} has no verify hook and no waiver")
        if kind.verify is not None and name in VERIFY_WAIVERS:
            raise ValueError(f"autopilot kind {name!r} has a verify hook; remove its waiver")


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
