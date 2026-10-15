"""Grow the evaluation sets from feedback (Defence layer 7).

    uv run python -m steward.verify.export_evals [--dry-run]

Writes every eval candidate not yet exported into the repository's eval files, then marks
it exported; commit the files and the next CI run evaluates them:

- a fact marked wrong becomes a golden case in tests/golden/earnings/fb-<...>/: the
  archived source document (from the evidence bucket) and one label, the corrected figure.
  The case is checked like CI checks it before it is written (the label's number must be in
  its snippet, the snippet in the document, and the figure must pass validation); a
  candidate that fails says why and is left for the owner to fix;
- a news item marked wrong becomes a labelled item in tests/news/news_classification.json
  with the corrected label.

Feedback cases are partial (one figure, not every metric of the release), marked
`"origin": "feedback"`; the golden checker skips the completeness rule for them.
"""

import argparse
import asyncio
import datetime as dt
import gzip
import json
import re
import sys
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import asyncpg
import httpx

from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.store import load_active
from steward.config.verification import VerificationConfig
from steward.db import create_pool
from steward.documents.fetch import Document
from steward.earnings.golden import DEFAULT_DIR as GOLDEN_DIR
from steward.earnings.golden import label_to_figure
from steward.earnings.metrics import BASE_METRICS
from steward.earnings.validate import ValidationContext, validate
from steward.news.eval import SET_PATH as NEWS_SET
from steward.settings import get_settings
from steward.storage import EvidenceStore, StorageError, SupabaseStorage

GZIP_MTIME = 0  # reproducible archives


@dataclass
class ExportReport:
    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _plain(written: str) -> Decimal | None:
    try:
        return Decimal(written.strip().strip("()").replace(",", ""))
    except InvalidOperation:
        return None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


async def golden_case(
    pool: asyncpg.Pool,
    storage: EvidenceStore,
    candidate: asyncpg.Record,
    profiles: HoldingProfilesConfig,
) -> tuple[str, dict[str, Any], bytes] | str:
    """(case id, case.json, document bytes), or why the candidate cannot become a case."""
    fact = await pool.fetchrow(
        "select f.*, e.content_type, e.storage_path, i.name as company, i.cik,"
        " j.params ->> 'report_date' as report_date"
        " from public.fact f join public.evidence e on e.sha256 = f.evidence_sha256"
        " left join public.instrument i on i.t212_ticker = f.ticker"
        " left join public.job j on j.id = f.job_id where f.id = $1",
        candidate["fact_id"],
    )
    if fact is None:
        return "the fact no longer exists"
    if fact["metric"] not in BASE_METRICS:
        return f"metric {fact['metric']} is not in the golden set's metrics"
    corrected = json.loads(candidate["corrected"])
    value = Decimal(corrected["value"])
    written = corrected.get("value_as_written") or fact["value_as_written"]
    snippet = corrected.get("snippet") or fact["snippet"]
    if _plain(written) is None or abs(_plain(written) or 0) != abs(value):
        return (
            f"the number as printed ({written!r}) does not read as the corrected value {value}; "
            "give corrected_value_as_written"
        )
    if written not in snippet:
        return f"{written!r} is not in the snippet; give corrected_snippet from the document"
    try:
        raw = await storage.get(fact["storage_path"])
    except StorageError as exc:
        return f"the archived document could not be read: {exc}"
    document = Document(fact["source_url"], fact["content_type"], raw)
    report_date = (
        dt.date.fromisoformat(fact["report_date"])
        if fact["report_date"]
        else fact["extracted_at"].date()
    )
    profile = profiles.for_ticker(fact["ticker"])
    fye = profile.fiscal_year_end_month if profile else 12
    label = {
        "metric": fact["metric"],
        "basis": fact["basis"],
        "fiscal_year": fact["fiscal_year"],
        "fiscal_period": fact["fiscal_period"],
        "value_as_written": written,
        "value": float(value) if value != value.to_integral() else int(value),
        "scale": fact["scale"],
        "unit": fact["unit"],
        "currency": fact["currency"],
        "snippet": snippet,
    }
    if fact["unit_snippet"]:
        label["unit_snippet"] = fact["unit_snippet"]
    case_id = _slug(
        f"fb-{fact['ticker']}-fy{fact['fiscal_year']}-{fact['fiscal_period']}-{fact['id']}"
    )
    data: dict[str, Any] = {
        "id": case_id,
        "origin": "feedback",
        "fact_id": fact["id"],
        "ticker": fact["ticker"],
        "company": fact["company"] or fact["ticker"],
        "cik": fact["cik"],
        "form": None,
        "source_url": fact["source_url"],
        "filing_index_url": None,
        "content_type": fact["content_type"],
        "document": "document.html.gz" if "html" in fact["content_type"] else "document.gz",
        "report_date": report_date.isoformat(),
        "fiscal_year_end_month": fye,
        "reporting_currency": fact["currency"],
        "period": {
            "fiscal_year": fact["fiscal_year"],
            "fiscal_period": fact["fiscal_period"],
            "period_end": fact["period_end"].isoformat() if fact["period_end"] else None,
        },
        "figures": [label],
        "absent_metrics": [],
        "labelled_by": "owner feedback",
        "notes": (
            f"Fact {fact['id']} was marked wrong (stored {fact['value_as_written']}, corrected "
            f"to {written})." + (f" {candidate['note']}" if candidate["note"] else "")
        ),
    }
    # The same checks CI applies: the label must pass validation against the document.
    text = document.text()
    validated = validate(
        [label_to_figure(label, data["period"]["period_end"])],
        ValidationContext(
            document_text=text,
            report_date=report_date,
            fye_month=fye,
            reporting_currency=fact["currency"],
            config=VerificationConfig(),
        ),
    )
    if not validated or validated[0].status == "rejected":
        reason = validated[0].reason if validated else "no result"
        return f"the corrected figure fails validation: {reason}"
    return case_id, data, raw


def news_row(item: asyncpg.Record, corrected: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"fb-{item['id']}",
        "origin": "feedback",
        "headline": item["headline"],
        "summary": item["summary"] or "",
        "outlet": item["outlet"] or "",
        "published_at": item["published_at"].astimezone(dt.UTC).isoformat().replace("+00:00", "Z"),
        "candidates": sorted(set(item["tickers"] or []))
        or ([corrected["affected_ticker"]] if corrected.get("affected_ticker") else []),
        "expected": {
            "event_type": corrected["event_type"],
            "affected_ticker": corrected["affected_ticker"],
            "rumour": corrected["rumour"],
            "relevant": corrected["relevant"],
        },
    }


async def export_candidates(
    pool: asyncpg.Pool,
    storage: EvidenceStore,
    golden_dir: Path = GOLDEN_DIR,
    news_set: Path = NEWS_SET,
    dry_run: bool = False,
) -> ExportReport:
    report = ExportReport()
    stored = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    profiles = stored.body if stored else HoldingProfilesConfig()
    candidates = await pool.fetch(
        "select * from public.eval_candidate where exported_at is null order by id"
    )
    news_data = await asyncio.to_thread(_read_json, news_set)
    news_ids = {row["id"] for row in news_data["items"]} if news_data else set()
    cases: list[tuple[Path, dict[str, Any], bytes]] = []
    done: list[tuple[int, str]] = []
    for candidate in candidates:
        if candidate["kind"] == "earnings_figure":
            built = await golden_case(pool, storage, candidate, profiles)
            if isinstance(built, str):
                report.skipped.append(
                    f"candidate {candidate['id']} (fact {candidate['fact_id']}): {built}"
                )
                continue
            case_id, data, raw = built
            cases.append((golden_dir / case_id, data, raw))
            where = str(golden_dir / case_id)
        else:
            item = await pool.fetchrow(
                "select id, headline, summary, outlet, published_at, tickers"
                " from public.news_item where id = $1",
                candidate["news_item_id"],
            )
            if item is None or news_data is None:
                report.skipped.append(f"candidate {candidate['id']}: item or set missing")
                continue
            row = news_row(item, json.loads(candidate["corrected"]))
            if row["id"] not in news_ids:
                news_data["items"].append(row)
                news_ids.add(row["id"])
            where = f"{news_set}#{row['id']}"
        report.written.append(where)
        done.append((candidate["id"], where))
    if dry_run:
        return report
    # Files first: a candidate is marked exported only once its file is written.
    await asyncio.to_thread(_write_files, cases, news_set, news_data)
    for candidate_id, where in done:
        await pool.execute(
            "update public.eval_candidate set exported_at = now(), exported_to = $2 where id = $1",
            candidate_id,
            where,
        )
    return report


def _read_json(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _write_files(
    cases: list[tuple[Path, dict[str, Any], bytes]],
    news_set: Path,
    news_data: dict[str, Any] | None,
) -> None:
    for target, data, raw in cases:
        target.mkdir(parents=True, exist_ok=True)
        (target / "case.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (target / data["document"]).write_bytes(gzip.compress(raw, mtime=GZIP_MTIME))
    if news_data is not None:
        news_set.write_text(
            json.dumps(news_data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )


async def _main(dry_run: bool) -> int:
    settings = get_settings()
    if settings.supabase_url is None or settings.supabase_secret_key is None:
        print("SUPABASE_URL and SUPABASE_SECRET_KEY are needed to read archived documents")
        return 1
    pool = await create_pool(settings)
    try:
        async with httpx.AsyncClient(timeout=60.0) as http:
            storage = SupabaseStorage(
                http,
                settings.supabase_url,
                settings.supabase_secret_key.get_secret_value(),
                public_api_url=settings.public_api_url,
            )
            report = await export_candidates(pool, storage, dry_run=dry_run)
    finally:
        await pool.close()
    for line in report.written:
        print(f"written: {line}")
    for line in report.skipped:
        print(f"skipped: {line}")
    print(f"{len(report.written)} written, {len(report.skipped)} skipped")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="check, write nothing")
    args = parser.parse_args(argv)
    return asyncio.run(_main(args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
