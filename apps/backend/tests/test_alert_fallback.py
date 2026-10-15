"""A critical alert whose channel fails goes out by the other channel; others do not."""

import datetime as dt

import asyncpg
import pytest

from steward.notify.alerts import dispatch_due
from steward.notify.channels import ChannelError
from tests.conftest import MigratedDatabase

NOW = dt.datetime(2026, 10, 14, 12, 0, tzinfo=dt.UTC)


class Dead:
    async def send(self, subject: str, text: str) -> str | None:
        raise ChannelError("down")


class Working:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, subject: str, text: str) -> str | None:
        self.sent.append(text)
        return "id-1"


async def add(admin_url: str, severity: int) -> None:
    admin = await asyncpg.connect(admin_url)
    try:
        await admin.execute(
            "insert into public.alert (kind, severity, channel, message, status, send_after,"
            " created_at) values ('news', $1, 'telegram', 'Big news', 'pending', $2, $2)",
            severity,
            NOW - dt.timedelta(minutes=1),
        )
    finally:
        await admin.close()


@pytest.mark.db
async def test_a_critical_alert_that_telegram_cannot_send_goes_by_email(
    backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> None:
    await add(migrated_db.admin_url, 3)
    email = Working()
    channels = {"telegram": Dead(), "email": email}
    for _ in range(3):  # three attempts, each retried two minutes later
        await dispatch_due(backend_pool, channels, now=NOW + dt.timedelta(hours=_))  # type: ignore[arg-type]
    assert len(email.sent) == 1
    assert email.sent[0].startswith("Telegram did not deliver this alert")
    assert "Big news" in email.sent[0]


@pytest.mark.db
async def test_a_less_urgent_alert_is_not_repeated_on_another_channel(
    backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> None:
    await add(migrated_db.admin_url, 2)
    email = Working()
    channels = {"telegram": Dead(), "email": email}
    for _ in range(3):
        await dispatch_due(backend_pool, channels, now=NOW + dt.timedelta(hours=_))  # type: ignore[arg-type]
    assert email.sent == []
