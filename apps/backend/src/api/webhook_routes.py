"""Inbound webhooks. Not owner-authenticated: each is checked by the sender's signature."""

import datetime as dt

from fastapi import APIRouter, HTTPException, Request, status

from api.db import Pool
from steward.ops import resend_webhook
from steward.settings import get_settings

router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/resend", status_code=status.HTTP_204_NO_CONTENT)
async def resend_event(request: Request, pool: Pool) -> None:
    """A delivery event from Resend (delivered, bounced, complained...). Refused without a valid
    signature, and always refused while `RESEND_WEBHOOK_SECRET` is not set."""
    secret = get_settings().resend_webhook_secret
    if secret is None or not secret.get_secret_value():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not enabled")
    body = await request.body()
    now = dt.datetime.now(dt.UTC)
    if not resend_webhook.verify(
        secret.get_secret_value(),
        request.headers.get("svix-id", ""),
        request.headers.get("svix-timestamp", ""),
        body,
        request.headers.get("svix-signature", ""),
        now,
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad signature")
    event = resend_webhook.parse(body)
    if event is not None:
        await resend_webhook.apply(pool, event[0], event[1], now)
