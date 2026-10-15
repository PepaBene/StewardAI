"""P7 against a real database: series immutability, guidance never analyst data, version dedupe, a
corrected IR file at three as-of cutoffs, and a surprise scored with what was known before
release."""

import datetime as dt
from decimal import Decimal as D
from typing import Any

import asyncpg
import pytest

from steward.disclosure import estimate_read

pytestmark = pytest.mark.db

UTC = dt.UTC
RELEASE = dt.datetime(2026, 7, 28, 12, tzinfo=UTC)
BEFORE_ALL = dt.datetime(2026, 6, 1, tzinfo=UTC)
BETWEEN = dt.datetime(2026, 7, 20, tzinfo=UTC)
AFTER = dt.datetime(2026, 8, 20, tzinfo=UTC)


async def setup(pool: asyncpg.Pool) -> tuple[int, int]:
    issuer = await pool.fetchval(
        "insert into public.issuer (legal_name, cik) values ('Acme Instruments', '0000011111') "
        "returning id"
    )
    await pool.execute(
        "insert into public.instrument (t212_ticker, currency, cik) values "
        "('ACME','USD','0000011111')"
    )
    source = await pool.fetchval(
        """insert into public.consensus_source
             (issuer_id, source_key, url, discovery_method, publisher, origin, format)
           values ($1,'acme.ir','https://ir.acme.example/consensus','IR page link','Acme IR',
                   'issuer_compiled','html') returning id""",
        issuer,
    )
    return int(issuer), int(source)


async def snapshot(
    pool: asyncpg.Pool,
    issuer: int,
    source: int | None,
    known: dt.datetime,
    *,
    series: str = "published_analyst_consensus",
    digest: str | None = None,
    supersedes: int | None = None,
    **kw: object,
) -> int:
    return int(
        await pool.fetchval(
            """insert into public.public_estimate_snapshot
                 (issuer_id, source_id, content_hash, series, published_at, known_at,
                 panel_description,
                  analyst_count, panel_method_version, supersedes_id, analyst_name, broker_name)
               values ($1,$2,$3,$4,$5,$5,$6,$7,'issuer panel',$8,$9,$10) returning id""",
            issuer,
            source,
            digest,
            series,
            known,
            None if series == "company_guidance" else "Consensus of analysts",
            None if series == "company_guidance" else 14,
            supersedes,
            kw.get("analyst"),
            kw.get("broker"),
        )
    )


async def value(pool: asyncpg.Pool, snap: int, mean: str | None, **kw: object) -> int:
    return int(
        await pool.fetchval(
            """insert into public.estimate_value
                 (snapshot_id, metric, fiscal_period, period_start, period_end, currency, unit,
                 scale,
                  basis, mean, low, high, analyst_count)
               values ($1,'Revenue','FY2026Q3','2026-04-01','2026-06-30','USD','amount',6,
                       'GAAP',$2,$3,$4,$5)
               returning id""",
            snap,
            None if mean is None else D(mean),
            kw.get("low"),
            kw.get("high"),
            kw.get("n", 14),
        )
    )


async def test_series_is_immutable_and_guidance_is_never_analyst_data(
    backend_pool: asyncpg.Pool,
) -> None:
    issuer, source = await setup(backend_pool)
    snap = await snapshot(backend_pool, issuer, source, BEFORE_ALL, digest="a")
    with pytest.raises(asyncpg.RestrictViolationError):
        await backend_pool.execute(
            "update public.public_estimate_snapshot set series='company_guidance' where id=$1", snap
        )
    guide = await snapshot(
        backend_pool, issuer, source, BEFORE_ALL, series="company_guidance", digest="g"
    )
    with pytest.raises(asyncpg.CheckViolationError):
        await value(backend_pool, guide, "100")  # a mean on a management target
    await value(backend_pool, guide, None, low=D("95"), high=D("105"), n=None)
    with pytest.raises(asyncpg.CheckViolationError):  # a named estimate needs analyst and broker
        await snapshot(
            backend_pool, issuer, source, BEFORE_ALL, series="named_individual_estimate", digest="n"
        )


async def test_identical_bytes_do_not_create_a_second_version(backend_pool: asyncpg.Pool) -> None:
    issuer, source = await setup(backend_pool)
    await snapshot(backend_pool, issuer, source, BEFORE_ALL, digest="same")
    with pytest.raises(asyncpg.UniqueViolationError):
        await snapshot(backend_pool, issuer, source, BETWEEN, digest="same")


async def test_corrected_file_at_three_cutoffs_and_surprise_before_release(
    backend_pool: asyncpg.Pool,
) -> None:
    issuer, source = await setup(backend_pool)
    first = dt.datetime(2026, 7, 15, tzinfo=UTC)
    original = await snapshot(backend_pool, issuer, source, first, digest="v1")
    await value(backend_pool, original, "1250.5", low=D("1230"), high=D("1270"))
    corrected = await snapshot(
        backend_pool,
        issuer,
        source,
        dt.datetime(2026, 8, 12, tzinfo=UTC),
        digest="v2",
        supersedes=original,
    )
    await value(backend_pool, corrected, "1262.0", low=D("1230"), high=D("1270"))

    def means(view: dict[str, Any]) -> list[str]:
        return [x["mean"] for x in view["lanes"]["published_analyst_consensus"]]

    assert (
        means(
            await estimate_read.estimate_view(
                backend_pool, "ACME", dt.datetime(2026, 7, 1, tzinfo=UTC)
            )
        )
        == []
    )
    assert means(await estimate_read.estimate_view(backend_pool, "ACME", BETWEEN)) == ["1250.5"]
    after = await estimate_read.estimate_view(backend_pool, "ACME", AFTER)
    assert (
        means(after) == ["1262.0"]
        and after["lanes"]["published_analyst_consensus"][0]["corrected"] is False
    )
    # no actual yet: explicit, not zero
    assert (
        after["events"][0]["status"] == "actual_not_loaded"
        and after["events"][0]["surprise"] is None
    )

    doc = await backend_pool.fetchval(
        "insert into public.source_document (regulator, external_id, stable_url, document_type) "
        "values ('issuer_ir','q3-release','https://ir.acme.example/q3','earnings') returning id"
    )
    ver = await backend_pool.fetchval(
        "insert into public.source_document_version (document_id, content_hash, published_at, "
        "known_at) "
        "values ($1,'h1',$2,$2) returning id",
        doc,
        RELEASE,
    )
    ev = await backend_pool.fetchval(
        "insert into public.source_evidence (document_version_id, quote) values ($1,'Revenue "
        "1,262') returning id",
        ver,
    )
    await backend_pool.execute(
        """insert into public.disclosure_observation
             (metric, issuer_id, period_end, value, unit, scale, currency, basis,
             document_version_id,
              evidence_id, parser_version, known_at)
           values ('Revenue',$1,'2026-06-30',1262,'amount',6,'USD','GAAP',$2,$3,'t',$4)""",
        issuer,
        ver,
        ev,
        RELEASE,
    )
    scored = (await estimate_read.estimate_view(backend_pool, "ACME", AFTER))["events"][0]
    # the 12 August correction was published after the release, so the 15 July file scores it
    assert scored["status"] == "scored" and scored["statistic"] == "mean"
    assert (
        D(scored["estimate"]) == D("1250.5") * 10**6 and D(scored["surprise"]) == D("11.5") * 10**6
    )
    assert scored["formula_version"] == "surprise.v1"


async def test_no_named_source_is_an_explicit_gap_and_the_gate_counts_pairs(
    backend_pool: asyncpg.Pool,
) -> None:
    issuer, source = await setup(backend_pool)
    a = await snapshot(backend_pool, issuer, source, BEFORE_ALL, digest="p1")
    b = await snapshot(backend_pool, issuer, source, BETWEEN, digest="p2")
    va, vb = await value(backend_pool, a, "100"), await value(backend_pool, b, "101")
    view = await estimate_read.estimate_view(backend_pool, "ACME", AFTER)
    assert view["lanes"]["named_individual_estimate"] == []
    assert any(
        c["kind"] == "named_estimates" and "never derived" in c["detail"] for c in view["coverage"]
    )
    assert (
        view["revision_gate"]["reason"] == "0 of 20 reviewed pairs"
        and view["revision_gate"]["met"] is False
    )
    await backend_pool.execute(
        "insert into public.estimate_pair_review (earlier_value_id, later_value_id, correct, "
        "reviewer) values ($1,$2,true,'owner')",
        va,
        vb,
    )
    assert (await estimate_read.estimate_view(backend_pool, "ACME", AFTER))["revision_gate"][
        "reason"
    ] == "1 of 20 reviewed pairs"
