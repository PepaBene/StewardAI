"""Resend delivery events pushed to us (autonomous engine plan, L6), so a bounce is known at once
and not at the next poll. Resend signs webhooks the Svix way; a request is accepted only with a
valid signature made with `RESEND_WEBHOOK_SECRET`, and is refused outright when none is set. The
poll in `delivery.poll_email` stays as the fallback for a missed event.
"""

import base64
import datetime as dt
import hashlib
import hmac
import json
from typing import Any

import asyncpg

TOLERANCE = dt.timedelta(minutes=5)
EVENT_PREFIX = "email."
# The status words the poll stores (Resend's `last_event`), so both paths agree.
STATUS = {
    "email.sent": "sent",
    "email.delivered": "delivered",
    "email.delivery_delayed": "delivery_delayed",
    "email.bounced": "bounced",
    "email.complained": "complained",
    "email.opened": "opened",
    "email.clicked": "clicked",
}
# A later, weaker event must not hide a bounce or a delivery.
RANK = {
    "sent": 0,
    "delivery_delayed": 1,
    "opened": 2,
    "clicked": 2,
    "delivered": 3,
    "bounced": 4,
    "complained": 5,
}


def verify(
    secret: str, msg_id: str, timestamp: str, body: bytes, signature: str, now: dt.datetime
) -> bool:
    """Svix scheme: HMAC-SHA256 over `id.timestamp.body` with the base64 part of `whsec_...`."""
    try:
        sent = dt.datetime.fromtimestamp(int(timestamp), dt.UTC)
        key = base64.b64decode(secret.removeprefix("whsec_"))
    except ValueError:
        return False
    if abs(now - sent) > TOLERANCE:
        return False
    signed = f"{msg_id}.{timestamp}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return any(
        hmac.compare_digest(expected, part.split(",", 1)[1])
        for part in signature.split()
        if part.startswith("v1,")
    )


def parse(body: bytes) -> tuple[str, str] | None:
    """(email id, status word) of an event we track, or None."""
    try:
        data: Any = json.loads(body)
        state = STATUS.get(str(data.get("type")))
        email_id = data.get("data", {}).get("email_id")
    except (ValueError, AttributeError):
        return None
    if state is None or not isinstance(email_id, str) or not email_id:
        return None
    return email_id, state


async def apply(pool: asyncpg.Pool, email_id: str, state: str, now: dt.datetime) -> bool:
    """Store the event on the alert it belongs to unless the alert already holds a stronger one.
    False when no alert has this provider id."""
    row = await pool.fetchrow(
        "select id, provider_status from public.alert where channel = 'email' and provider_id = $1",
        email_id,
    )
    if row is None:
        return False
    if RANK.get(row["provider_status"] or "", -1) > RANK[state]:
        return True
    await pool.execute(
        "update public.alert set provider_status = $2, provider_checked_at = $3 where id = $1",
        row["id"],
        state,
        now,
    )
    return True
