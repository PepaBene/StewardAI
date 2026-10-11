"""Every read-only chat tool runs under a database role that can only SELECT.

The catalog marks `propose_job` as the one write tool. Every other tool must work (or fail for
a data reason) on a connection that cannot insert, update or delete, so a tool that quietly
writes a cache or a log row is caught here instead of in production.
"""

import secrets
import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest

from steward.chat import catalog
from steward.chat.tools import TOOL_DEFINITIONS, ChatTools
from steward.research.sources import Registry

OWNER = str(uuid.uuid4())
FORBIDDEN = ("read-only", "permission denied", "must be owner", "insufficientprivilege")

NAMED: dict[str, Any] = {
    "ticker": "AAPL_US_EQ",
    "symbol": "TSM",
    "sell": "AAPL_US_EQ",
    "buy": "MSFT_US_EQ",
    "currency": "USD",
    "fund_isin": "IE00B4L5Y983",
    "query": "supply chain",
    "symbols": ["AAPL"],
    "trades": [{"side": "buy", "ticker": "AAPL_US_EQ", "value_base": 1000.0}],
    "value": "Technology",
}


def args_for(definition: dict[str, Any]) -> dict[str, Any]:
    """Minimal arguments that satisfy a tool's schema."""
    schema = definition["input_schema"]
    out: dict[str, Any] = {}
    for key in schema.get("required", []):
        spec = schema["properties"][key]
        if "enum" in spec:
            out[key] = spec["enum"][0]
        elif key in NAMED:
            out[key] = NAMED[key]
        elif spec.get("type") == "integer":
            out[key] = 1
        elif spec.get("type") == "number":
            out[key] = 1000.0
        else:
            out[key] = "x"
    return out


@pytest.fixture
async def read_only_pool(
    migrated_db: Any, backend_pool: asyncpg.Pool
) -> AsyncIterator[asyncpg.Pool]:
    role = f"ai_reader_{secrets.token_hex(4)}"  # roles are cluster-wide, databases are not
    admin = await asyncpg.connect(migrated_db.admin_url)
    try:
        await admin.execute(f"create role {role} nologin")
        await admin.execute(f"grant usage on schema public to {role}")
        await admin.execute(f"grant select on all tables in schema public to {role}")
        await admin.execute(f"grant {role} to app_backend")

        async def use_role(conn: asyncpg.Connection) -> None:
            await conn.execute(f"set role {role}")

        pool = await asyncpg.create_pool(
            migrated_db.backend_url, min_size=1, max_size=4, init=use_role
        )
        try:
            yield pool
        finally:
            await pool.close()
            await admin.execute(f"drop owned by {role}")
            await admin.execute(f"drop role {role}")
    finally:
        await admin.close()


@pytest.mark.db
async def test_every_read_only_tool_runs_without_writing(read_only_pool: asyncpg.Pool) -> None:
    # The role must really be unable to write, or the check below proves nothing.
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await read_only_pool.execute(
            "insert into public.fx_rate (source, rate_date, currency, home_currency, rate,"
            " published_amount, published_rate) values ('ecb', now(), 'USD', 'EUR', 1, 1, 1)"
        )
    tools = ChatTools(read_only_pool, Registry(), None, None, owner_id=OWNER)
    definitions = {d["name"]: d for d in TOOL_DEFINITIONS}
    problems: list[str] = []
    for name in sorted(catalog.TOOLS):
        if catalog.TOOLS[name].writes:
            continue
        try:
            result = await tools.call(name, args_for(definitions[name]))
        except asyncpg.PostgresError as exc:  # a data error is fine; a privilege error is not
            result = {"error": f"{type(exc).__name__}: {exc}"}
        text = str(result.get("error", "")).lower()
        if any(word in text for word in FORBIDDEN):
            problems.append(f"{name}: {result['error']}")
    assert not problems, "\n".join(problems)
    assert len(tools.trace) == sum(not t.writes for t in catalog.TOOLS.values())
