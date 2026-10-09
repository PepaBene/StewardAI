"""Facts read straight from the filing: evidence for filings never copied to the statement table."""

import datetime as dt

import asyncpg
import pytest

from steward.disclosure.fact_evidence import mirror_document_facts
from steward.disclosure.parse import parse_html

pytestmark = pytest.mark.db

NOW = dt.datetime(2026, 3, 1, tzinfo=dt.UTC)
HEADER = (
    "<ix:header><ix:resources>"
    '<xbrli:context id="c1"><xbrli:entity><xbrli:identifier scheme="s">1</xbrli:identifier>'
    "</xbrli:entity><xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate>"
    "<xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>"
    '<xbrli:unit id="u1"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
    "</ix:resources></ix:header>"
)
BODY = (
    '<p>Revenue <ix:nonFraction name="us-gaap:Revenues" contextRef="c1" unitRef="u1" '
    'decimals="-6" scale="6">1,000</ix:nonFraction> million.</p>'
    '<p>Odd <ix:nonFraction name="acme:CustomThing" contextRef="c1" unitRef="u1" '
    'decimals="-6" scale="6">5</ix:nonFraction></p>'
)


async def version(pool: asyncpg.Pool) -> tuple[int, int]:
    issuer = await pool.fetchval(
        "insert into public.issuer (legal_name) values ('Acme') returning id"
    )
    doc = await pool.fetchval(
        """insert into public.source_document (regulator, external_id, stable_url, issuer_id)
           values ('SEC', 'A-1', 'https://sec.example/a', $1) returning id""",
        issuer,
    )
    vid = await pool.fetchval(
        """insert into public.source_document_version (document_id, content_hash, known_at)
           values ($1, 'h1', $2) returning id""",
        doc,
        NOW,
    )
    return issuer, vid


async def test_known_metrics_are_written_once_and_extensions_are_not_guessed(
    backend_pool: asyncpg.Pool,
) -> None:
    issuer, vid = await version(backend_pool)
    parsed = parse_html(f"<html><body>{HEADER}<h1>Item 8</h1>{BODY}</body></html>".encode())
    assert await mirror_document_facts(backend_pool, vid, issuer, NOW, parsed) == 1
    assert await mirror_document_facts(backend_pool, vid, issuer, NOW, parsed) == 0
    row = await backend_pool.fetchrow(
        "select metric, value, unit, currency, known_at, period_end"
        " from public.disclosure_observation where document_version_id=$1",
        vid,
    )
    assert row is not None
    assert row["metric"] == "revenue" or "revenue" in row["metric"].lower()
    assert row["value"] == 1_000_000_000 and row["unit"] == "USD" and row["currency"] == "USD"
    assert row["known_at"] == NOW and row["period_end"] == dt.date(2025, 12, 31)
    assert await backend_pool.fetchval("select count(*) from public.disclosure_observation") == 1
