"""Resend webhook: only a correctly signed, fresh request counts, and a weaker later event never
hides a bounce."""

import base64
import datetime as dt
import hashlib
import hmac
import json

import asyncpg

from steward.ops import resend_webhook as hook

NOW = dt.datetime.now(dt.UTC)
KEY = b"super-secret-key"
SECRET = "whsec_" + base64.b64encode(KEY).decode()


def sign(msg_id: str, ts: str, body: bytes) -> str:
    mac = hmac.new(KEY, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode()


def test_a_correct_signature_is_accepted() -> None:
    body = b'{"type":"email.bounced"}'
    ts = str(int(NOW.timestamp()))
    assert hook.verify(SECRET, "m1", ts, body, sign("m1", ts, body), NOW)


def test_a_wrong_body_or_old_timestamp_or_secret_is_refused() -> None:
    body = b'{"type":"email.bounced"}'
    ts = str(int(NOW.timestamp()))
    good = sign("m1", ts, body)
    assert not hook.verify(SECRET, "m1", ts, body + b" ", good, NOW)
    assert not hook.verify(SECRET, "m2", ts, body, good, NOW)
    assert not hook.verify(
        "whsec_" + base64.b64encode(b"other").decode(), "m1", ts, body, good, NOW
    )
    old = str(int((NOW - dt.timedelta(hours=1)).timestamp()))
    assert not hook.verify(SECRET, "m1", old, body, sign("m1", old, body), NOW)
    assert not hook.verify(SECRET, "m1", "not-a-number", body, good, NOW)
    assert not hook.verify(SECRET, "m1", ts, body, "", NOW)


def test_only_tracked_events_with_an_email_id_are_parsed() -> None:
    event = json.dumps({"type": "email.bounced", "data": {"email_id": "e1"}}).encode()
    assert hook.parse(event) == ("e1", "bounced")
    assert hook.parse(b'{"type":"domain.created","data":{"email_id":"e1"}}') is None
    assert hook.parse(b'{"type":"email.sent","data":{}}') is None
    assert hook.parse(b"not json") is None


async def alert(pool: asyncpg.Pool, provider_id: str, status: str | None) -> int:
    return int(
        await pool.fetchval(
            "insert into public.alert (kind, severity, channel, message, status, provider_id,"
            " provider_status, sent_at) values ('test', 3, 'email', 'm', 'sent', $1, $2, now())"
            " returning id",
            provider_id,
            status,
        )
    )


async def test_an_event_is_stored_and_a_weaker_later_one_does_not_hide_a_bounce(
    backend_pool: asyncpg.Pool,
) -> None:
    aid = await alert(backend_pool, "e1", None)
    assert await hook.apply(backend_pool, "e1", "bounced", NOW)
    assert await hook.apply(backend_pool, "e1", "delivered", NOW)
    status = await backend_pool.fetchval(
        "select provider_status from public.alert where id = $1", aid
    )
    assert status == "bounced"


async def test_an_event_for_an_unknown_email_is_ignored(backend_pool: asyncpg.Pool) -> None:
    assert not await hook.apply(backend_pool, "nobody", "delivered", NOW)
