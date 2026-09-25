"""Instrument lifecycle events raised from reference data, in the news event taxonomy.

- a FIRDS termination date on the holding's home market -> `delisting`;
- a GLEIF successor LEI (the entity merged into another) -> `m_and_a`;
- a GLEIF entity status of INACTIVE, or a registration LAPSED or RETIRED -> `entity_status`.

Each becomes one tier-1 news item (from the `reference-data` source) whose URL points at the
source record, in a story of its own for the holding. The URL is unique, so a replayed change
never raises a second event. The next news run assesses the story (tier 1: confirmed) and
routes it by the event rules and notification rules like any other event.
"""

import datetime as dt
import json
from dataclasses import dataclass

import asyncpg

SOURCE_ID = "reference-data"


@dataclass(frozen=True)
class LifecycleEvent:
    ticker: str
    event_type: str  # delisting, m_and_a, entity_status
    headline: str
    summary: str
    url: str  # the source record, and the event's unique key
    event_date: dt.date | None


def gleif_url(lei: str) -> str:
    return f"https://search.gleif.org/#/record/{lei}"


def firds_url(isin: str, mic: str) -> str:
    return (
        "https://registers.esma.europa.eu/publication/searchRegister?core=esma_registers_firds"
        f"&isin={isin}&mic={mic}"
    )


# A termination dated this long before it is first seen is old regulator data (UK venues
# left FIRDS at Brexit with a termination date), not a delisting to alert on.
STALE_TERMINATION = dt.timedelta(days=30)


def listing_events(
    ticker: str,
    name: str,
    isin: str,
    home_mic: str | None,
    known: bool,
    previous_termination: dt.datetime | None,
    current_termination: dt.datetime | None,
    today: dt.date,
) -> list[LifecycleEvent]:
    """A termination date newly recorded for the home-market listing. `known` is False on the
    first read of that listing: a baseline, not a change."""
    if home_mic is None or current_termination is None or not known:
        return []
    if previous_termination is not None and previous_termination == current_termination:
        return []
    if current_termination.date() < today - STALE_TERMINATION:
        return []
    day = current_termination.date()
    return [
        LifecycleEvent(
            ticker=ticker,
            event_type="delisting",
            headline=f"{name}: trading on {home_mic} ends {day.isoformat()} (ESMA FIRDS)",
            summary=f"ESMA FIRDS records a termination date of {day.isoformat()} for {isin} "
            f"on its home market {home_mic}.",
            url=f"{firds_url(isin, home_mic)}#terminated-{day.isoformat()}",
            event_date=day,
        )
    ]


def entity_events(
    ticker: str,
    name: str,
    lei: str,
    before: dict[str, str | None] | None,
    after: dict[str, str | None],
    today: dt.date,
) -> list[LifecycleEvent]:
    """Changes between two GLEIF states ({status, successor_lei, registration_status}).
    No previous state (first read) raises nothing: a baseline is not a change."""
    if before is None:
        return []
    events = []
    successor = after.get("successor_lei")
    if successor and successor != before.get("successor_lei"):
        events.append(
            LifecycleEvent(
                ticker,
                "m_and_a",
                f"{name}: legal entity succeeded by LEI {successor} (GLEIF)",
                f"GLEIF records {successor} as the successor of {lei}: the entity was merged "
                "into or taken over by another.",
                f"{gleif_url(lei)}#successor-{successor}",
                today,
            )
        )
    status, registration = after.get("status"), after.get("registration_status")
    if status == "INACTIVE" and before.get("status") != "INACTIVE":
        events.append(
            LifecycleEvent(
                ticker,
                "entity_status",
                f"{name}: legal entity is now inactive (GLEIF)",
                f"GLEIF records the entity status of {lei} as INACTIVE.",
                f"{gleif_url(lei)}#status-inactive",
                today,
            )
        )
    if registration in ("LAPSED", "RETIRED") and registration != before.get("registration_status"):
        events.append(
            LifecycleEvent(
                ticker,
                "entity_status",
                f"{name}: LEI registration {registration.lower()} (GLEIF)",
                f"GLEIF records the registration of {lei} as {registration}.",
                f"{gleif_url(lei)}#registration-{registration.lower()}",
                today,
            )
        )
    return events


async def ensure_source(pool: asyncpg.Pool | asyncpg.Connection) -> None:
    await pool.execute(
        "insert into public.source (id, name, tier, type, enabled) values ($1,"
        " 'Reference data (ESMA FIRDS, GLEIF)', 1, 'reference', true)"
        " on conflict (id) do nothing",
        SOURCE_ID,
    )


async def raise_events(
    pool: asyncpg.Pool, events: list[LifecycleEvent], now: dt.datetime, job_id: int | None
) -> list[int]:
    """Store each event once (by URL). Returns the new news item ids."""
    created: list[int] = []
    if not events:
        return created
    await ensure_source(pool)
    for event in events:
        async with pool.acquire() as conn, conn.transaction():
            exists = await conn.fetchval(
                "select id from public.news_item where url = $1", event.url
            )
            if exists:
                continue
            cluster = await conn.fetchval(
                "insert into public.story_cluster (ticker, event_type, headline, headline_key,"
                " best_tier, first_seen, last_seen, event_date) values ($1, $2, $3, $4, 1, $5,"
                " $5, $6) returning id",
                event.ticker,
                event.event_type,
                event.headline,
                event.url,
                now,
                event.event_date,
            )
            item = await conn.fetchval(
                "insert into public.news_item (url, source_id, outlet, outlet_name, tier,"
                " published_at, first_seen_at, headline, summary, language, cluster_id, tickers,"
                " links, event_type, affected_ticker, rumour, relevant, event_date,"
                " classified_at, classified_by, job_id) values ($1, $2, $3, $4, 1, $5, $5, $6,"
                " $7, 'en', $8, $9, $10::jsonb, $11, $12, false, true, $13, $5,"
                " 'rules:reference', $14) returning id",
                event.url,
                SOURCE_ID,
                "gleif.org" if "gleif" in event.url else "esma.europa.eu",
                "GLEIF" if "gleif" in event.url else "ESMA FIRDS",
                now,
                event.headline,
                event.summary,
                cluster,
                [event.ticker],
                json.dumps([{"ticker": event.ticker, "via": "source", "term": event.ticker}]),
                event.event_type,
                event.ticker,
                event.event_date,
                job_id,
            )
            created.append(item)
    return created
