"""Golden-set evaluation of the earnings pipeline: `uv run python -m steward.earnings.golden`.

Each case in tests/golden/earnings is a real results release with labelled figures. A run
feeds every document through an extractor, the validator and the verdict logic, then
scores extracted figures against the labels: a label counts as matched when a figure with
the same metric, basis and fiscal period passed validation with the same value (scale
applied) and currency.

Extractors:
- `--oracle` (default, used in CI): returns the labels themselves. Scores the validator,
  not the model: every correct, real-world figure must pass validation, and every KPI must
  get a verdict.
- `--live`: calls Claude (ANTHROPIC_API_KEY) and measures extraction accuracy. With
  `--record DIR` it also saves each case's model output, so the run can be replayed.
- `--replay DIR`: re-scores saved model output without any API call.

Exit status 1 when accuracy is below `--min-accuracy` (default 0.95, the Phase 2 target).
"""

import argparse
import asyncio
import datetime as dt
import gzip
import json
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from steward.ai import Usage
from steward.config.thesis import ThesisConfig, ThesisKPI
from steward.config.verification import VerificationConfig
from steward.documents.fetch import Document
from steward.earnings.extract import ClaudeExtractor, ExtractionRequest, Extractor
from steward.earnings.metrics import BASE_METRICS
from steward.earnings.periods import likely_reported_period
from steward.earnings.schema import ExtractedFigure, ExtractionResult
from steward.earnings.units import SCALE_FACTOR
from steward.earnings.validate import ValidatedFigure, ValidationContext, validate
from steward.earnings.verdicts import FactRow, VerdictDraft, judge_thesis

DEFAULT_DIR = Path(__file__).resolve().parents[3] / "tests" / "golden" / "earnings"


@dataclass(frozen=True)
class GoldenCase:
    id: str
    data: dict[str, Any]
    document: Document

    @property
    def figures(self) -> list[dict[str, Any]]:
        return list(self.data["figures"])

    @property
    def report_date(self) -> dt.date:
        return dt.date.fromisoformat(self.data["report_date"])

    @property
    def fye_month(self) -> int:
        return int(self.data["fiscal_year_end_month"])

    def request(self) -> ExtractionRequest:
        return ExtractionRequest(
            ticker=self.data["ticker"],
            company_name=self.data["company"],
            report_date=self.report_date,
            fye_month=self.fye_month,
            expected_period=likely_reported_period(self.report_date, self.fye_month),
            metrics=list(BASE_METRICS.items()),
            reporting_currency=self.data.get("reporting_currency"),
        )


def load_cases(directory: Path = DEFAULT_DIR) -> list[GoldenCase]:
    cases = []
    for case_file in sorted(directory.glob("*/case.json")):
        data = json.loads(case_file.read_text(encoding="utf-8"))
        raw = (case_file.parent / data["document"]).read_bytes()
        if data["document"].endswith(".gz"):
            raw = gzip.decompress(raw)
        cases.append(
            GoldenCase(data["id"], data, Document(data["source_url"], data["content_type"], raw))
        )
    return cases


def label_to_figure(label: dict[str, Any], period_end: str | None) -> ExtractedFigure:
    return ExtractedFigure(
        metric=label["metric"],
        basis=label["basis"],
        fiscal_year=label["fiscal_year"],
        fiscal_period=label["fiscal_period"],
        period_end=period_end,
        value_as_written=label["value_as_written"],
        value=label["value"],
        scale=label["scale"],
        unit=label["unit"],
        currency=label.get("currency"),
        source_snippet=label["snippet"],
        unit_snippet=label.get("unit_snippet"),
    )


class OracleExtractor:
    """Returns a case's labels as the extraction: isolates the validator and verdicts."""

    def __init__(self, cases: list[GoldenCase]) -> None:
        self._by_url = {c.document.url: c for c in cases}

    async def extract(
        self, document: Document, text: str, request: ExtractionRequest
    ) -> tuple[ExtractionResult, Usage]:
        case = self._by_url[document.url]
        main = case.data["period"]
        figures = [
            label_to_figure(
                f,
                main.get("period_end")
                if (f["fiscal_year"], f["fiscal_period"])
                == (main["fiscal_year"], main["fiscal_period"])
                else None,
            )
            for f in case.figures
        ]
        return (
            ExtractionResult(
                document_kind="earnings_release",
                company_name=case.data["company"],
                reported_fiscal_year=main["fiscal_year"],
                reported_fiscal_period=main["fiscal_period"],
                figures=figures,
                not_found=list(case.data.get("absent_metrics", [])),
            ),
            Usage(model="oracle"),
        )


class ReplayExtractor:
    def __init__(self, directory: Path, cases: list[GoldenCase]) -> None:
        self._directory = directory
        self._ids = {c.document.url: c.id for c in cases}

    async def extract(
        self, document: Document, text: str, request: ExtractionRequest
    ) -> tuple[ExtractionResult, Usage]:
        path = self._directory / f"{self._ids[document.url]}.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        return ExtractionResult.model_validate(saved["result"]), Usage(model=saved["model"])


def default_kpis(currency: str) -> list[ThesisKPI]:
    """KPIs every case is judged on: one per derivation kind, plus a TTM KPI that must come
    out `unknown` (a single release never holds four quarters)."""
    rows = [
        ("revenue-growth", "revenue_growth_yoy", ">=", 0, "%", "quarter"),
        ("gross-margin", "gross_margin", ">=", 40, "%", "quarter"),
        ("operating-margin", "operating_margin", ">=", 15, "%", "quarter"),
        ("net-income", "net_income", ">", 0, f"{currency} m", "quarter"),
        ("eps", "eps_diluted", ">=", 0, f"{currency} per share", "quarter"),
        ("revenue-ttm", "revenue", ">=", 1, f"{currency} bn", "ttm"),
    ]
    return [
        ThesisKPI(id=i, metric=m, operator=o, threshold=t, unit=u, period=p)
        for i, m, o, t, u, p in rows
    ]


@dataclass
class CaseScore:
    id: str
    expected: int = 0
    matched: int = 0
    missing: list[str] = field(default_factory=list)
    wrong: list[str] = field(default_factory=list)
    rejected_labels: list[str] = field(default_factory=list)
    extra: int = 0
    verdicts: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    @property
    def accuracy(self) -> float:
        return self.matched / self.expected if self.expected else 0.0


def score_case(case: GoldenCase, validated: list[ValidatedFigure]) -> CaseScore:
    score = CaseScore(case.id)

    def as_tuple(v: ValidatedFigure) -> tuple[str, str, int, str]:
        return (v.key.metric, v.key.basis, v.key.fiscal_year, v.key.fiscal_period)

    accepted = {as_tuple(v): v for v in validated if v.status != "rejected"}
    rejected = {as_tuple(v): v for v in validated if v.status == "rejected"}
    labelled = set()
    for label in case.figures:
        score.expected += 1
        key = (label["metric"], label["basis"], label["fiscal_year"], label["fiscal_period"])
        labelled.add(key)
        name = f"{key[0]}/{key[1]} FY{key[2]} {key[3]}"
        want = Decimal(str(label["value"])) * SCALE_FACTOR[label["scale"]]
        got = accepted.get(key)
        if got is None:
            bad = rejected.get(key)
            if bad is not None:
                score.rejected_labels.append(f"{name}: {bad.reason}")
            else:
                score.missing.append(name)
            continue
        if got.value_units == want and got.figure.currency == label.get("currency"):
            score.matched += 1
        else:
            score.wrong.append(f"{name}: got {got.value_units} {got.figure.currency}, want {want}")
    score.extra = sum(
        1
        for k in accepted
        if k not in labelled and k[0] in BASE_METRICS and k[3] not in ("FY", "H1", "H2")
    )
    return score


def verdicts_for(case: GoldenCase, validated: list[ValidatedFigure]) -> list[VerdictDraft]:
    facts = [
        FactRow(
            id=i,
            metric=v.key.metric,
            basis=v.key.basis,
            fiscal_year=v.key.fiscal_year,
            fiscal_period=v.key.fiscal_period,
            value_units=v.value_units,
            unit=v.figure.unit,
            currency=v.figure.currency,
        )
        for i, v in enumerate(validated, start=1)
        if v.status == "verified" and v.value_units is not None
    ]
    thesis = ThesisConfig(
        ticker=case.data["ticker"],
        why_owned="golden-set evaluation",
        kpis=default_kpis(case.data.get("reporting_currency") or "USD"),
    )
    return judge_thesis(thesis, facts)


def _save(directory: Path, case_id: str, model: str, result: ExtractionResult) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"model": model, "result": result.model_dump(mode="json")}
    (directory / f"{case_id}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


async def evaluate(
    cases: list[GoldenCase],
    extractor: Extractor,
    record: Path | None = None,
    concurrency: int = 4,
) -> list[CaseScore]:
    """Scores in case order; the cases run `concurrency` at a time (the calls are independent)."""
    gate = asyncio.Semaphore(concurrency)

    async def one(case: GoldenCase) -> CaseScore:
        text = case.document.text()
        try:
            async with gate:
                result, usage = await extractor.extract(case.document, text, case.request())
        except Exception as exc:  # one bad case must not end the run
            return CaseScore(case.id, expected=len(case.figures), error=str(exc))
        if record is not None:
            _save(record, case.id, usage.model, result)
        validated = validate(
            [f for f in result.figures if f.metric in BASE_METRICS],
            ValidationContext(
                document_text=text,
                report_date=case.report_date,
                fye_month=case.fye_month,
                reporting_currency=case.data.get("reporting_currency"),
                config=VerificationConfig(),
            ),
        )
        score = score_case(case, validated)
        score.verdicts = {
            f"{d.kpi_id} {d.period or '-'}": d.status for d in verdicts_for(case, validated)
        }
        return score

    return list(await asyncio.gather(*(one(case) for case in cases)))


def summarise(scores: list[CaseScore]) -> dict[str, Any]:
    expected = sum(s.expected for s in scores)
    matched = sum(s.matched for s in scores)
    return {
        "cases": len(scores),
        "fields": expected,
        "matched": matched,
        "accuracy": matched / expected if expected else 0.0,
        "errors": {s.id: s.error for s in scores if s.error},
        "per_case": {
            s.id: {
                "accuracy": round(s.accuracy, 4),
                "missing": s.missing,
                "wrong": s.wrong,
                "rejected_labels": s.rejected_labels,
                "extra_figures": s.extra,
                "verdicts": s.verdicts,
            }
            for s in scores
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--oracle", action="store_true", help="labels as extraction (default)")
    mode.add_argument("--live", action="store_true", help="call Claude")
    mode.add_argument("--replay", type=Path, help="re-score saved model output")
    parser.add_argument("--record", type=Path, help="with --live: save model output here")
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--effort", default="high")
    parser.add_argument("--cases", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--min-accuracy", type=float, default=0.95)
    parser.add_argument("--json", type=Path, help="write the full report here")
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c.id in wanted]
    extractor: Extractor
    if args.live:
        from steward.ai import create_client
        from steward.settings import get_settings

        extractor = ClaudeExtractor(create_client(get_settings()), args.model, args.effort)
    elif args.replay:
        extractor = ReplayExtractor(args.replay, cases)
    else:
        extractor = OracleExtractor(cases)

    scores = asyncio.run(evaluate(cases, extractor, args.record if args.live else None))
    report = summarise(scores)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    for s in scores:
        flag = "ok  " if s.accuracy >= args.min_accuracy else "FAIL"
        problems = s.missing + s.wrong + s.rejected_labels + ([s.error] if s.error else [])
        print(f"{flag} {s.id:<18} {s.matched}/{s.expected}  {'; '.join(problems)[:300]}")
    print(
        f"{report['cases']} cases, {report['matched']}/{report['fields']} fields matched "
        f"({report['accuracy']:.1%}); target {args.min_accuracy:.0%}"
    )
    return 0 if report["accuracy"] >= args.min_accuracy else 1


if __name__ == "__main__":
    sys.exit(main())
