"""Changes to a setting the app already reads: a rule, source, schedule or model (engine plan, L5).

Each is one edit of one field of one config domain, written as a target
`config:<domain>:<path>` (`config:jobs:digest.model`, `config:news_sources:sources[id=x].enabled`).
Promotion saves a new config version with no author (so the "what the engine did" view and the
config history both show it, and the owner can roll it back like any version). Nothing else has to
know about the pipeline: the consumers keep reading their config.

The engine may only touch the fields in `ALLOWED`; budget, credentials, strategy and the gates are
outside it twice over (`engine.change.FROZEN` and this list).
"""

import datetime as dt
import json
import re
from dataclasses import dataclass
from typing import Any, Literal

import asyncpg

from steward.config import CONFIG_MODELS
from steward.config.store import ConfigConflictError, load_active, parse_body, save_config
from steward.engine import change

PREFIX = "config:"
Kind = Literal["rule", "source_weight", "schedule", "model"]

# change kind -> (config domain, allowed path)
ALLOWED: dict[Kind, tuple[str, re.Pattern[str]]] = {
    "rule": ("event_rules", re.compile(r"^severities\.[a-z0-9_]+$")),
    "source_weight": (
        "news_sources",
        re.compile(r"^sources\[id=[a-z0-9]+(-[a-z0-9]+)*\]\.(enabled|tier)$"),
    ),
    "schedule": ("jobs", re.compile(r"^[a-z0-9_]+\.cron$")),
    "model": (
        "jobs",
        re.compile(
            r"^(news\.classification_model|digest\.model|chat\.model|thesis_settings\.model"
            r"|autopilot_settings\.model)$"
        ),
    ),
}
_STEP = re.compile(r"^(?P<name>[a-z0-9_]+)(\[(?P<key>[a-z_]+)=(?P<value>[a-z0-9-]+)\])?$")


class NotAllowed(Exception):
    """The edit is outside what the engine may change."""


class Stale(Exception):
    """The field no longer holds what the change started from."""


@dataclass(frozen=True)
class Edit:
    domain: str
    path: str

    @property
    def target(self) -> str:
        return f"{PREFIX}{self.domain}:{self.path}"


def parse_target(target: str) -> Edit:
    if not target.startswith(PREFIX):
        raise NotAllowed(f"{target!r} is not a config edit")
    domain, _, path = target.removeprefix(PREFIX).partition(":")
    if not domain or not path:
        raise NotAllowed(f"{target!r} has no field")
    return Edit(domain, path)


def check_allowed(kind: Kind, edit: Edit) -> None:
    domain, pattern = ALLOWED[kind]
    if edit.domain != domain or not pattern.match(edit.path):
        raise NotAllowed(f"the engine may not change {edit.target} as a {kind} change")
    change.ensure_changeable(edit.target)


def _walk(data: Any, path: str) -> tuple[Any, str | int]:
    """The container that holds the last step of `path`, and the key or index of that step."""
    steps = path.split(".")
    node = data
    for raw in steps[:-1]:
        node = _step(node, raw)
    last = _STEP.fullmatch(steps[-1])
    if last is None:
        raise NotAllowed(f"bad path {path!r}")
    if last["key"]:
        raise NotAllowed(f"bad path {path!r}")
    return node, last["name"]


def _step(node: Any, raw: str) -> Any:
    m = _STEP.fullmatch(raw)
    if m is None:
        raise NotAllowed(f"bad path step {raw!r}")
    child = node[m["name"]] if isinstance(node, dict) and m["name"] in node else None
    if child is None:
        raise Stale(f"{raw!r} is not in the config")
    if m["key"]:
        found = [x for x in child if isinstance(x, dict) and str(x.get(m["key"])) == m["value"]]
        if not found:
            raise Stale(f"{raw!r} is not in the config")
        return found[0]
    return child


def read(data: dict[str, Any], path: str) -> Any:
    node, key = _walk(data, path)
    if not isinstance(node, dict) or key not in node:
        raise Stale(f"{path!r} is not in the config")
    return node[key]


def write(data: dict[str, Any], path: str, value: Any) -> None:
    node, key = _walk(data, path)
    if not isinstance(node, dict):
        raise Stale(f"{path!r} is not in the config")
    node[key] = value


async def current(pool: asyncpg.Pool, edit: Edit) -> Any:
    """The value the field holds now (the default when no version of the domain is stored)."""
    stored = await load_active(pool, edit.domain, CONFIG_MODELS[edit.domain])
    body = stored.body if stored else parse_body(edit.domain, {})
    return read(json.loads(body.model_dump_json()), edit.path)


async def _set(pool: asyncpg.Pool, edit: Edit, expect: Any, value: Any, note: str) -> int | None:
    model = CONFIG_MODELS[edit.domain]
    stored = await load_active(pool, edit.domain, model)
    body = stored.body if stored else parse_body(edit.domain, {})
    data = json.loads(body.model_dump_json())
    if read(data, edit.path) != expect:
        raise Stale(f"{edit.path} no longer holds {expect!r}")
    write(data, edit.path, value)
    new = parse_body(edit.domain, data)  # validated by the config model itself
    try:
        async with pool.acquire() as conn:
            saved = await save_config(
                conn,
                edit.domain,
                new,
                created_by=None,
                note=note[:500],
                expected_active_id=stored.id if stored else None,
            )
    except ConfigConflictError as exc:
        raise Stale("the config changed while the edit was being made") from exc
    return saved.id


async def apply(pool: asyncpg.Pool, c: change.Change) -> int | None:
    """Make a promoted change real: save the new value as a config version."""
    edit = parse_target(c.target)
    check_allowed(_kind(c), edit)
    return await _set(
        pool,
        edit,
        _value(c.before),
        _value(c.after),
        f"Automatic ({c.change_kind}): {c.hypothesis}",
    )


async def revert(pool: asyncpg.Pool, c: change.Change) -> bool:
    """Put the old value back. False when the owner (or anything else) changed the field since:
    their value stands."""
    edit = parse_target(c.target)
    try:
        await _set(pool, edit, _value(c.after), _value(c.before), f"Rolled back: {c.hypothesis}")
    except Stale:
        return False
    return True


def _value(side: Any) -> Any:
    return side.get("value") if isinstance(side, dict) else side


def _kind(c: change.Change) -> Kind:
    if c.change_kind not in ALLOWED:
        raise NotAllowed(f"{c.change_kind} is not a config change")
    return c.change_kind


BLOCK_AFTER = 2  # rollbacks of one target before the engine stops proposing it
BLOCK_DAYS = 180


async def blocked(pool: asyncpg.Pool, target: str, now: dt.datetime) -> bool:
    """A field whose changes were rolled back twice recently is left to the owner: the engine has
    shown it does not judge that one well."""
    n = await pool.fetchval(
        "select count(*) from public.engine_change where target = $1 and status = 'rolled_back'"
        " and decided_at >= $2",
        target,
        now - dt.timedelta(days=BLOCK_DAYS),
    )
    return int(n or 0) >= BLOCK_AFTER


async def propose(
    pool: asyncpg.Pool,
    kind: Kind,
    edit: Edit,
    new_value: Any,
    hypothesis: str,
    metric: str,
    gates: change.Gates,
) -> change.Change:
    """Store a shadow change for one field, reading its present value now."""
    check_allowed(kind, edit)
    old = await current(pool, edit)
    if old == new_value:
        raise NotAllowed(f"{edit.target} already holds {new_value!r}")
    return await change.propose(
        pool,
        edit.target,
        kind,
        hypothesis,
        metric,
        before={"value": old},
        after={"value": new_value},
        gates=gates,
    )


async def promote(
    pool: asyncpg.Pool, c: change.Change, *, golden_passed: bool | None
) -> change.Change:
    """Apply the gates to a shadow change's stored numbers and, when it passes, save the config.
    A save that cannot be made (the field moved) turns the verdict into a rejection."""
    settled = await change.settle(pool, c.id, golden_passed=golden_passed)
    if settled.status != "promoted":
        return settled
    try:
        await apply(pool, settled)
    except (Stale, NotAllowed, ValueError) as exc:
        await pool.execute(
            "update public.engine_change set status = 'rejected', verdict = $2 where id = $1",
            c.id,
            f"passed its test but could not be applied: {exc}",
        )
        return settled.model_copy(
            update={"status": "rejected", "verdict": f"could not be applied: {exc}"}
        )
    return settled


async def undo(
    pool: asyncpg.Pool, c: change.Change, reasons: list[str]
) -> Literal["restored", "kept"] | None:
    """Roll a promoted config change back and restore the old value. None when the change was not
    live. "kept" when the owner changed the field since (or the old value no longer fits): their
    value stands, and the row is closed as rejected so it never counts as an engine rollback."""
    if not await change.roll_back(pool, c.id, reasons):
        return None
    try:
        restored = await revert(pool, c)
    except ValueError as exc:
        restored = False
        why = f"the old value could not be put back: {exc}"
    else:
        why = "the field was changed since, so that value stands"
    if restored:
        return "restored"
    await pool.execute(
        "update public.engine_change set status = 'rejected', verdict = $2 where id = $1",
        c.id,
        f"not rolled back: {why}"[:500],
    )
    return "kept"
