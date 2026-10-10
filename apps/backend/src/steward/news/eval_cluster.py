"""Cross-language duplicate grouping evaluation: 54 English<->Japanese/Chinese/German pairs of
one event and 54 distractors (a different event about the same company or topic).

    uv run python -m steward.news.eval_cluster            # check the harness with a stand-in
    uv run python -m steward.news.eval_cluster --live     # embed with the configured model

For each threshold it reports pair recall (a pair's two reports grouped) and the false-merge
rate (a distractor grouped with its pair's report). N3 asks for JP/ZH/DE pairs that cluster
correctly with no wrong merges; the table shows the threshold that does, if one exists, so
`embedding_duplicate_threshold` is set from data. Without --live a hashed character-n-gram
embedder stands in: that only checks the harness (and shows what a lexical baseline does on
cross-language text, which is poorly), not the model. The set is synthetic.
"""

import argparse
import asyncio
import hashlib
import json
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from steward.config.jobs import NewsSettings
from steward.news.embeddings import Embedder, cosine, embed_text, normalise
from steward.settings import get_settings

SET_PATH = Path(__file__).resolve().parents[3] / "tests" / "news" / "crosslang_pairs.json"
THRESHOLDS = (0.70, 0.75, 0.80, 0.85, 0.88, 0.90, 0.92, 0.94)


class HashedEmbedder:
    """A stand-in: character 3-gram counts hashed to 256 buckets. Lexical, so it separates
    languages; useful only to exercise the harness."""

    model = "hashed-ngrams"
    dimensions = 256

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dimensions
            t = text.lower()
            for i in range(max(1, len(t) - 2)):
                h = int.from_bytes(hashlib.blake2b(t[i : i + 3].encode(), digest_size=4).digest())
                vec[h % self.dimensions] += 1.0
            out.append(normalise(vec) if any(vec) else vec)
        return out


@dataclass
class PairScore:
    language: str
    similarity: float


@dataclass
class Result:
    pairs: list[PairScore]
    distractors: list[float]  # best similarity of a distractor to its pair's two reports

    def at(self, threshold: float) -> tuple[float, float]:
        recall = sum(p.similarity >= threshold for p in self.pairs) / len(self.pairs)
        false = sum(d >= threshold for d in self.distractors) / len(self.distractors)
        return recall, false

    def report(self) -> str:
        lines = ["threshold  pair recall  false merges   per language recall (ja zh de)"]
        for t in THRESHOLDS:
            recall, false = self.at(t)
            per = []
            for lang in ("ja", "zh", "de"):
                group = [p for p in self.pairs if p.language == lang]
                per.append(f"{sum(p.similarity >= t for p in group) / len(group):.0%}")
            lines.append(f"  {t:.2f}       {recall:6.1%}       {false:6.1%}        {' '.join(per)}")
        best = self.best_threshold()
        lines.append("")
        lines.append(
            f"lowest threshold with no false merge: {best:.2f}"
            if best is not None
            else "no threshold separates the pairs from the distractors"
        )
        mean = sum(p.similarity for p in self.pairs) / len(self.pairs)
        lines.append(f"mean pair similarity {mean:.3f}; max distractor {max(self.distractors):.3f}")
        return "\n".join(lines)

    def best_threshold(self) -> float | None:
        """The lowest listed threshold at which no distractor groups (maximises recall)."""
        for t in THRESHOLDS:
            if self.at(t)[1] == 0:
                return t
        return None


def load(path: Path = SET_PATH) -> dict[str, list[dict[str, object]]]:
    data: dict[str, list[dict[str, object]]] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _text(item: dict[str, object]) -> str:
    return embed_text(str(item["headline"]), str(item.get("summary") or ""))


async def evaluate(embedder: Embedder, data: dict[str, list[dict[str, object]]]) -> Result:
    pairs, distractors = data["pairs"], data["distractors"]
    texts = (
        [_text(p["a"]) for p in pairs]  # type: ignore[arg-type]
        + [_text(p["b"]) for p in pairs]  # type: ignore[arg-type]
        + [_text(d) for d in distractors]
    )
    vectors: list[list[float]] = []
    for start in range(0, len(texts), 100):
        vectors.extend(await embedder.embed(texts[start : start + 100]))
    n = len(pairs)
    a_vec, b_vec, d_vec = vectors[:n], vectors[n : 2 * n], vectors[2 * n :]
    index = {str(p["id"]): i for i, p in enumerate(pairs)}
    scores = [
        PairScore(str(p["language_b"]), cosine(a_vec[i], b_vec[i])) for i, p in enumerate(pairs)
    ]
    far = []
    for j, d in enumerate(distractors):
        i = index[str(d["pair_id"])]
        far.append(max(cosine(d_vec[j], a_vec[i]), cosine(d_vec[j], b_vec[i])))
    return Result(scores, far)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true", help="embed with the configured model")
    args = parser.parse_args()
    data = load()

    async def run() -> Result:
        if not args.live:
            return await evaluate(HashedEmbedder(), data)
        settings = get_settings()
        if not settings.google_api_key:
            raise SystemExit("GOOGLE_API_KEY is not set")
        news = NewsSettings()
        async with httpx.AsyncClient() as http:
            from steward.news.embeddings import GeminiEmbedder

            embedder = GeminiEmbedder(
                http,
                settings.google_api_key.get_secret_value(),
                news.embedding_model,
                news.embedding_dimensions,
            )
            return await evaluate(embedder, data)

    result = asyncio.run(run())
    print(result.report())
    if not math.isfinite(result.at(0.9)[0]):
        sys.exit(2)


if __name__ == "__main__":
    main()
