"""The immutable snapshot of what a research run was made from (Phase 3).

A saved conclusion is only readable if you know the template, profile, prompts, strategy and
thesis it was written under, and those change. The snapshot copies them into the report at
the time of the run and carries a digest of its own content, so a later edit to the saved copy
shows up as a digest that no longer matches. `config_version_ids` on the report points at the
stored versions; the snapshot keeps the text the model actually saw, which survives even if a
config row were ever pruned.
"""

import datetime as dt
import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field

SNAPSHOT_VERSION = 1


class PromptCopy(BaseModel):
    sha256: str
    text: str


class RunSnapshot(BaseModel):
    schema_version: int = SNAPSHOT_VERSION
    taken_at: str
    profile: dict[str, Any]
    template: dict[str, Any] | None
    layout: list[dict[str, Any]]
    prompts: dict[str, PromptCopy]
    versions: dict[str, int | None] = Field(
        description="Active config version id per domain when the run started."
    )
    thesis: dict[str, Any] | None = Field(description="The ticker's thesis as it stood.")
    plan_sha256: str
    digest: str = ""


def _canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_of(data: Any) -> str:
    text = data if isinstance(data, str) else _canonical(data)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _digest(snapshot: RunSnapshot) -> str:
    return sha256_of(snapshot.model_dump(mode="json", exclude={"digest"}))


def take_snapshot(
    *,
    now: dt.datetime,
    profile: dict[str, Any],
    template: dict[str, Any] | None,
    layout: list[dict[str, Any]],
    prompts: dict[str, str],
    versions: dict[str, int | None],
    thesis: dict[str, Any] | None,
    plan: dict[str, Any],
) -> RunSnapshot:
    snapshot = RunSnapshot(
        taken_at=now.isoformat(timespec="seconds"),
        profile=profile,
        template=template,
        layout=layout,
        prompts={k: PromptCopy(sha256=sha256_of(v), text=v) for k, v in prompts.items()},
        versions=versions,
        thesis=thesis,
        plan_sha256=sha256_of(plan),
    )
    snapshot.digest = _digest(snapshot)
    return snapshot


def verify_snapshot(snapshot: RunSnapshot) -> bool:
    """True when the snapshot is exactly as it was taken."""
    return snapshot.digest == _digest(snapshot) and all(
        p.sha256 == sha256_of(p.text) for p in snapshot.prompts.values()
    )
