"""The news classification evaluation set: 100 labelled items.

    uv run python -m steward.news.eval            # check the set and score the labels
    uv run python -m steward.news.eval --live     # classify with the configured model

Phase 3 AC: event-type classification matches the label on at least 85% of items. The live
run calls the Claude API (about $0.02 with Haiku 4.5) and needs ANTHROPIC_API_KEY. Without
--live the labels are scored against themselves, which checks the harness, not the model.
"""

import argparse
import asyncio
import datetime as dt
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from steward.ai import create_client
from steward.config.jobs import NewsSettings
from steward.news.classify import (
    Candidate,
    Classifier,
    ClassifyItem,
    ClassifyResult,
    ClaudeClassifier,
    HoldingContext,
    Label,
)
from steward.settings import get_settings

SET_PATH = Path(__file__).resolve().parents[3] / "tests" / "news" / "news_classification.json"
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


def load_set(path: Path = SET_PATH) -> list[LabelledItem]:
    data = json.loads(path.read_text())
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
        labelled.append(LabelledItem(row["id"], item, row["expected"]))
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

    @property
    def accuracy(self) -> float:
        return self.event_type / self.total if self.total else 0.0

    def report(self) -> str:
        lines = [
            f"event type: {self.event_type}/{self.total} = {self.accuracy:.1%} "
            f"(target {TARGET:.0%})",
            f"rumour flag: {self.rumour}/{self.total}",
            f"relevance: {self.relevant}/{self.total}",
            f"affected holding: {self.affected}/{self.total}",
            f"no label returned: {self.missing}",
        ]
        if self.mistakes:
            lines += ["", "event-type mistakes:", *self.mistakes]
        return "\n".join(lines)


async def evaluate(classifier: Classifier, labelled: list[LabelledItem], batch: int = 50) -> Score:
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
        result, _ = await classifier.classify(items, HOLDINGS, dt.date(2026, 9, 25))
        for i, x in enumerate(chunk):
            label = result.labels.get(i)
            if label is None:
                score.missing += 1
                continue
            expected = x.expected
            if label.event_type == expected["event_type"]:
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
    parser.add_argument("--live", action="store_true", help="classify with the Claude API")
    parser.add_argument("--model", default=NewsSettings().classification_model)
    args = parser.parse_args()
    labelled = load_set()
    classifier: Classifier
    if args.live:
        classifier = ClaudeClassifier(create_client(get_settings()), args.model)
    else:
        classifier = OracleClassifier(labelled)
    score = asyncio.run(evaluate(classifier, labelled))
    print(score.report())
    sys.exit(0 if score.accuracy >= TARGET else 1)


if __name__ == "__main__":
    main()
