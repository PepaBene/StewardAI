"""The news classification evaluation set: 100 labelled items.

    uv run python -m steward.news.eval            # check the set and score the labels
    uv run python -m steward.news.eval --live     # classify with the configured model

Phase 3 AC: event-type classification matches the label on at least 85% of items. The live
run calls the model API and needs OPENAI_API_KEY or GOOGLE_API_KEY. Without
--live the labels are scored against themselves, which checks the harness, not the model.
"""

import argparse
import asyncio
import datetime as dt
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from steward.config.jobs import NewsSettings
from steward.llm.client import create_llm
from steward.news.classify import (
    Candidate,
    Classifier,
    ClassifyItem,
    ClassifyResult,
    HoldingContext,
    Label,
    LLMClassifier,
)
from steward.news.gate import check, load_baseline, make_baseline, save_baseline, wilson_interval
from steward.settings import get_settings

SET_PATH = Path(__file__).resolve().parents[3] / "tests" / "news" / "news_classification.json"
BENCHMARK_PATH = SET_PATH.with_name("news_benchmark.json")
REAL_PATH = SET_PATH.with_name("real_benchmark.json")
SETS = {"classification": SET_PATH, "benchmark": BENCHMARK_PATH, "real": REAL_PATH}
TARGET = 0.85

HOLDINGS = [
    HoldingContext("AAPL_US_EQ", "Apple", relations=("Samsung", "Google", "TSMC", "Foxconn")),
    HoldingContext(
        "MSFT_US_EQ", "Microsoft", relations=("Amazon Web Services", "Google Cloud", "OpenAI")
    ),
    HoldingContext("NVDA_US_EQ", "NVIDIA", relations=("AMD", "Intel", "TSMC")),
    HoldingContext("SAPd_EQ", "SAP", relations=("Oracle", "Workday")),
    HoldingContext("NOVOb_EQ", "Novo Nordisk", relations=("Eli Lilly",)),
]


@dataclass(frozen=True)
class LabelledItem:
    id: str
    item: ClassifyItem
    expected: dict[str, Any]
    # Segments the score is broken down by (the plan's release gates are per language, region
    # and event type, not one pooled number). Set in the file, else inferred from the outlet.
    region: str | None = None
    language: str | None = None


_TLD_REGIONS = {
    "jp": "JP", "cn": "CN", "hk": "HK", "kr": "KR", "sg": "SG", "in": "IN", "de": "DE",
    "uk": "GB", "ca": "CA", "cz": "CZ", "fr": "FR", "nl": "NL", "ch": "CH", "au": "AU",
}  # fmt: skip
_REGION_LANGUAGES = {"JP": "ja", "CN": "zh", "DE": "de", "CZ": "cs", "KR": "ko", "FR": "fr"}


def infer_region(outlet: str | None) -> str | None:
    """A country from an outlet domain's last label (`.co.jp` -> JP), else None."""
    if not outlet:
        return None
    return _TLD_REGIONS.get(outlet.lower().rstrip(".").rsplit(".", 1)[-1])


def load_holdings(path: Path = SET_PATH) -> list[HoldingContext]:
    """The holdings an evaluation set was written for: its own `holdings` list when it has
    one (the 150-item benchmark spans six countries), else the default five."""
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("holdings")
    if not rows:
        return HOLDINGS
    return [
        HoldingContext(r["ticker"], r["name"], relations=tuple(r.get("relations", ())))
        for r in rows
    ]


def load_set(path: Path = SET_PATH) -> list[LabelledItem]:
    data = json.loads(path.read_text(encoding="utf-8"))
    labelled = []
    for index, row in enumerate(data["items"]):
        item = ClassifyItem(
            index=index,
            headline=row["headline"],
            summary=row["summary"],
            outlet=row["outlet"],
            tier=2,
            published_at=dt.datetime.fromisoformat(row["published_at"].replace("Z", "+00:00")),
            candidates=tuple(Candidate(t, "name", t) for t in row["candidates"]),
        )
        region = row.get("region") or infer_region(row["outlet"])
        language = row.get("language") or _REGION_LANGUAGES.get(region or "", "en")
        labelled.append(LabelledItem(row["id"], item, row["expected"], region, language))
    return labelled


class OracleClassifier:
    """Returns the expected labels: checks the harness end to end without a model."""

    def __init__(self, labelled: list[LabelledItem]) -> None:
        self._by_headline = {x.item.headline: x.expected for x in labelled}

    async def classify(
        self, items: Any, holdings: Any, today: dt.date
    ) -> tuple[ClassifyResult, Any]:
        result = ClassifyResult()
        for item in items:
            expected = self._by_headline[item.headline]
            result.labels[item.index] = Label(
                event_type=expected["event_type"],
                affected_ticker=expected["affected_ticker"],
                relevant=expected["relevant"],
                thesis_link=None,
                rumour=expected["rumour"],
                event_date=None,
            )
        return result, None


@dataclass
class Score:
    total: int
    event_type: int
    rumour: int
    relevant: int
    affected: int
    missing: int
    mistakes: list[str]
    # Event-type hits and items per segment ("region:JP", "language:de", "event:m_and_a").
    segments: dict[str, list[int]] = field(default_factory=dict)

    @property
    def accuracy(self) -> float:
        return self.event_type / self.total if self.total else 0.0

    def segment_report(self) -> list[str]:
        """Event-type accuracy per segment with its 95 % interval; a segment whose interval
        is wide says "too few items" instead of hiding behind the pooled number."""
        lines = []
        for name, (hits, total) in sorted(self.segments.items()):
            low, high = wilson_interval(hits, total)
            note = "  (too few items to gate on)" if total < 10 else ""
            lines.append(
                f"  {name}: {hits}/{total} = {hits / total:.0%} [{low:.0%}-{high:.0%}]{note}"
            )
        return lines

    def report(self) -> str:
        lines = [
            f"event type: {self.event_type}/{self.total} = {self.accuracy:.1%} "
            f"(target {TARGET:.0%})",
            f"rumour flag: {self.rumour}/{self.total}",
            f"relevance: {self.relevant}/{self.total}",
            f"affected holding: {self.affected}/{self.total}",
            f"no label returned: {self.missing}",
        ]
        if self.segments:
            lines += ["", "event type by segment (95 % Wilson interval):", *self.segment_report()]
        if self.mistakes:
            lines += ["", "event-type mistakes:", *self.mistakes]
        return "\n".join(lines)


async def evaluate(
    classifier: Classifier,
    labelled: list[LabelledItem],
    batch: int = 50,
    holdings: list[HoldingContext] | None = None,
) -> Score:
    score = Score(len(labelled), 0, 0, 0, 0, 0, [])
    for start in range(0, len(labelled), batch):
        chunk = labelled[start : start + batch]
        items = [
            ClassifyItem(
                index=i,
                headline=x.item.headline,
                summary=x.item.summary,
                outlet=x.item.outlet,
                tier=x.item.tier,
                published_at=x.item.published_at,
                candidates=x.item.candidates,
            )
            for i, x in enumerate(chunk)
        ]
        result, _ = await classifier.classify(items, holdings or HOLDINGS, dt.date(2026, 9, 25))
        for i, x in enumerate(chunk):
            label = result.labels.get(i)
            if label is None:
                score.missing += 1
                continue
            expected = x.expected
            hit = label.event_type == expected["event_type"]
            for key in (
                f"region:{x.region or 'unknown'}",
                f"language:{x.language or 'unknown'}",
                f"event:{expected['event_type']}",
            ):
                tally = score.segments.setdefault(key, [0, 0])
                tally[0] += hit
                tally[1] += 1
            if hit:
                score.event_type += 1
            else:
                score.mistakes.append(
                    f"  {x.id}: {label.event_type} (expected {expected['event_type']}): "
                    f"{x.item.headline}"
                )
            score.rumour += label.rumour == expected["rumour"]
            score.relevant += label.relevant == expected["relevant"]
            score.affected += label.affected_ticker == expected["affected_ticker"]
    return score


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true", help="classify with the model API")
    parser.add_argument("--model", default=NewsSettings().classification_model)
    parser.add_argument(
        "--set",
        choices=sorted(SETS),
        default="classification",
        help="classification: the 100-item set; benchmark: the 150-item multi-region set; "
        "real: 253 real headlines, labelled by three AI models (no human review)",
    )
    parser.add_argument("--gate", type=Path, help="fail if a segment is worse than this baseline")
    parser.add_argument("--save-baseline", type=Path, help="write this run as the baseline")
    args = parser.parse_args()
    if args.gate and not args.live:
        # Without a model the oracle classifier is scored against its own labels and always passes.
        sys.exit("--gate needs --live: the oracle run would pass against its own labels.")
    # The report names Japanese and Chinese items; a Windows console code page cannot print them.
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    path = SETS[args.set]
    labelled = load_set(path)
    holdings = load_holdings(path)
    classifier: Classifier
    if args.live:
        classifier = LLMClassifier(create_llm(get_settings()), args.model)
    else:
        classifier = OracleClassifier(labelled)
    score = asyncio.run(evaluate(classifier, labelled, holdings=holdings))
    print(score.report())
    pooled = (score.event_type, score.total)
    if args.save_baseline:
        if not args.live:
            sys.exit("A baseline from the oracle run would record the labels, not a model.")
        save_baseline(
            args.save_baseline,
            make_baseline(args.model, pooled, score.segments, dt.datetime.now(dt.UTC).date()),
        )
        print(f"\nbaseline written to {args.save_baseline}")
    baseline = load_baseline(args.gate) if args.gate else None
    failures = check(pooled, score.segments, baseline)
    if score.total and score.relevant / score.total < TARGET:
        failures.append(
            f"relevance {score.relevant}/{score.total} = {score.relevant / score.total:.1%}"
            f" is below the target {TARGET:.0%}"
        )
    if args.gate is None:
        print("\nno baseline given: only the pooled target was checked")
    for failure in failures:
        print(f"GATE FAIL: {failure}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
