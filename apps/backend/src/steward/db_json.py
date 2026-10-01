"""Decoding of jsonb values at the read sites that return them to clients.

asyncpg returns `jsonb` as text unless a codec is registered, and this codebase deliberately
registers none on the shared pool (a global codec would change every query that passes
`json.dumps(...)::jsonb` strings). A route that handed such a value on to FastAPI therefore
sent a JSON *string* ("{\\"members\\": []}") where clients expect an object. Every function
here is explicit about which columns it decodes, so a non-jsonb text column is never parsed.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any


def decode_jsonb(value: object, default: Any = None) -> Any:
    """The JSON value of a jsonb column however the driver delivered it.

    Text and bytes are parsed (an unparsable value yields `default`); a value that is already
    a dict, list or number is returned unchanged. A *string* that was stored as a JSON string
    scalar is parsed to that scalar, then returned as is.
    """
    if isinstance(value, (str, bytes, bytearray)):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return value


def decode_jsonb_object(value: object) -> dict[str, Any]:
    """A jsonb *object*; anything else (null, array, scalar, bad text) is an empty dict."""
    loaded = decode_jsonb(value, {})
    return loaded if isinstance(loaded, dict) else {}


def decode_row(
    row: Mapping[str, Any], columns: Iterable[str], *, default: Any = None
) -> dict[str, Any]:
    """A plain dict of the record with the named jsonb columns decoded."""
    out = dict(row)
    for column in columns:
        if column in out:
            out[column] = decode_jsonb(out[column], default)
    return out


def decode_rows(
    rows: Iterable[Mapping[str, Any]], columns: Iterable[str], *, default: Any = None
) -> list[dict[str, Any]]:
    names = tuple(columns)
    return [decode_row(row, names, default=default) for row in rows]


def decode_structured(value: object) -> Any:
    """Decode a jsonb value delivered as text, but only when it really is a JSON object or
    array; every other value (including a string that merely looks like a scalar) is returned
    as it is. Safe to apply twice and to a value a codec already decoded."""
    if isinstance(value, (str, bytes, bytearray)):
        try:
            loaded = json.loads(value)
        except ValueError:
            return value
        return loaded if isinstance(loaded, (dict, list)) else value
    return value


def decode_structured_rows(
    rows: Iterable[Mapping[str, Any]], columns: Iterable[str]
) -> list[dict[str, Any]]:
    names = tuple(columns)
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        for column in names:
            if column in item:
                item[column] = decode_structured(item[column])
        out.append(item)
    return out
