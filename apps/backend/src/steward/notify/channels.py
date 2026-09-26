"""Delivery channels: Telegram (Bot API) and email (Resend's HTTP API), both over httpx.

Messages are plain text composed in code; no model writes an alert. Errors never include the
request URL, because the Telegram bot token is part of it.
"""

import html
from typing import Protocol

import httpx

from steward.settings import Settings

TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_LIMIT = 4096
RESEND_URL = "https://api.resend.com/emails"


class ChannelError(Exception):
    pass


class Channel(Protocol):
    async def send(self, subject: str, text: str) -> None: ...


def _truncate(text: str, limit: int, tail: str = "…") -> str:
    return text if len(text) <= limit else text[: limit - len(tail)].rstrip() + tail


def _api_error(response: httpx.Response, field: str, secrets: tuple[str, ...] = ()) -> str:
    """The provider's own explanation from a JSON error body (Resend's "message",
    Telegram's "description"), shortened and with any secret blanked; "" when none."""
    try:
        body = response.json()
    except ValueError:
        return ""
    detail = body.get(field) if isinstance(body, dict) else None
    if not isinstance(detail, str):
        return ""
    for secret in secrets:
        if secret:
            detail = detail.replace(secret, "***")
    return _truncate(" ".join(detail.split()), 200)


class TelegramChannel:
    def __init__(self, http: httpx.AsyncClient, token: str, chat_id: str) -> None:
        self._http = http
        self._token = token
        self._chat_id = chat_id

    async def send(self, subject: str, text: str) -> None:
        body = text if text.startswith(subject) else f"{subject}\n\n{text}"
        try:
            response = await self._http.post(
                TELEGRAM_URL.format(token=self._token),
                json={
                    "chat_id": self._chat_id,
                    "text": _truncate(body, TELEGRAM_LIMIT),
                    "disable_web_page_preview": True,
                },
                timeout=20.0,
            )
        except httpx.HTTPError as exc:
            raise ChannelError(f"Telegram unreachable ({type(exc).__name__})") from None
        if response.status_code != 200:
            description = _api_error(response, "description", (self._token,))
            raise ChannelError(
                f"Telegram returned HTTP {response.status_code}"
                + (f": {description}" if description else "")
            )


def text_to_html(text: str) -> str:
    """Plain text to minimal HTML: escaped, line breaks kept, bare URLs linked."""
    lines = []
    for line in text.splitlines():
        escaped = html.escape(line)
        words = [
            f'<a href="{w}">{w}</a>' if w.startswith(("http://", "https://")) else w
            for w in escaped.split(" ")
        ]
        lines.append(" ".join(words))
    return '<div style="font-family:sans-serif;line-height:1.5">' + "<br>".join(lines) + "</div>"


class EmailChannel:
    def __init__(self, http: httpx.AsyncClient, api_key: str, sender: str, to: str) -> None:
        self._http = http
        self._api_key = api_key
        self._sender = sender
        self._to = [a.strip() for a in to.split(",") if a.strip()]

    async def send(self, subject: str, text: str, idempotency_key: str | None = None) -> None:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if idempotency_key:
            # Resend drops a repeat of the same key (24 h), so a retry after a timeout
            # whose first attempt did go through does not send a second email.
            headers["Idempotency-Key"] = idempotency_key
        try:
            response = await self._http.post(
                RESEND_URL,
                headers=headers,
                json={
                    "from": self._sender,
                    "to": self._to,
                    "subject": _truncate(subject, 200),
                    "text": text,
                    "html": text_to_html(text),
                },
                timeout=20.0,
            )
        except httpx.HTTPError as exc:
            raise ChannelError(f"email API unreachable ({type(exc).__name__})") from None
        if response.status_code not in (200, 201, 202):
            # e.g. 403 "The gmail.com domain is not verified": ALERT_EMAIL_FROM must be on
            # a domain verified in Resend.
            message = _api_error(response, "message", (self._api_key,))
            raise ChannelError(
                f"email API returned HTTP {response.status_code}"
                + (f": {message}" if message else "")
            )


def configured_channels(settings: Settings) -> frozenset[str]:
    """Names of the channels whose credentials are set (the keys `channels_from_settings`
    returns)."""
    names = set()
    if settings.telegram_bot_token and settings.telegram_chat_id:
        names.add("telegram")
    if settings.resend_api_key and settings.alert_email_from and settings.alert_email_to:
        names.add("email")
    return frozenset(names)


def channels_from_settings(http: httpx.AsyncClient, settings: Settings) -> dict[str, Channel]:
    """The channels whose credentials are set. A channel a rule names but that is missing
    here fails the alert with a clear error instead of dropping it silently."""
    channels: dict[str, Channel] = {}
    if settings.telegram_bot_token and settings.telegram_chat_id:
        channels["telegram"] = TelegramChannel(
            http, settings.telegram_bot_token.get_secret_value(), settings.telegram_chat_id
        )
    if settings.resend_api_key and settings.alert_email_from and settings.alert_email_to:
        channels["email"] = EmailChannel(
            http,
            settings.resend_api_key.get_secret_value(),
            settings.alert_email_from,
            settings.alert_email_to,
        )
    return channels


MISSING_CHANNEL_HELP = {
    "telegram": "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are not set",
    "email": "RESEND_API_KEY, ALERT_EMAIL_FROM and ALERT_EMAIL_TO are not set",
}
