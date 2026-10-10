"""The alert e-mail: sent once per alert, marked mailed only after the provider accepted it."""

import datetime as dt

import asyncpg
import pytest

from steward.disclosure import alerts
from steward.disclosure.jobs import digest_text, mail_alert_digest
from steward.notify.channels import ChannelError

pytestmark = pytest.mark.db

NOW = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)


class Sink:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[tuple[str, str]] = []

    async def send(self, subject: str, text: str, idempotency_key: str | None = None) -> None:
        if self.fail:
            raise ChannelError("email API unreachable")
        self.sent.append((subject, text))


async def raise_alert(pool: asyncpg.Pool, key: str, ticker: str) -> None:
    await alerts.record(
        pool,
        kind="insider_trade",
        ticker=ticker,
        dedupe_key=key,
        headline=f"{ticker} insider bought",
        detail={},
        known_at=NOW,
        source_url=None,
    )


async def test_alerts_are_mailed_once_and_only_after_acceptance(
    backend_pool: asyncpg.Pool,
) -> None:
    await raise_alert(backend_pool, "k1", "AAA")
    await raise_alert(backend_pool, "k2", "BBB")
    failing = Sink(fail=True)
    with pytest.raises(ChannelError):
        await mail_alert_digest(backend_pool, failing, now=NOW)  # type: ignore[arg-type]
    assert (
        await backend_pool.fetchval(
            "select count(*) from public.disclosure_alert where emailed_at is null"
        )
        == 2
    )
    sink = Sink()
    assert await mail_alert_digest(backend_pool, sink, now=NOW) == 2  # type: ignore[arg-type]
    assert "AAA" in sink.sent[0][1] and "BBB" in sink.sent[0][1]
    assert await mail_alert_digest(backend_pool, sink, now=NOW) == 0  # type: ignore[arg-type]
    assert len(sink.sent) == 1


def test_digest_text_states_the_headlines_unchanged() -> None:
    groups = [
        {"ticker": "AAA", "count": 2, "headlines": ["h1", "h2"]},
        {"ticker": "BBB", "count": 1, "headlines": ["h3"]},
    ]
    text = digest_text(groups, "https://app.example/")  # type: ignore[arg-type]
    assert text.startswith("3 new disclosure alerts")
    assert "AAA (2)\n  - h1\n  - h2" in text
    assert text.rstrip().endswith("https://app.example/disclosures/alerts")
