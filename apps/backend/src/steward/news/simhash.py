"""64-bit SimHash of a news item's text, for near-duplicate detection before any model runs.

Character 3-grams of the normalised text, not words: the same code then works for German
compounds and for Japanese and Chinese, which have no spaces to split on. Two items whose
hashes differ in at most `NEAR_DUPLICATE_BITS` bits are copies of one text (a wire story
re-posted with a different suffix or a changed timestamp). Different wording of the same
event is the embedding stage's job, not this one's.

Hashes are stored in Postgres `bigint`, which is signed; `to_signed` / `to_unsigned` convert.
"""

import hashlib
import re
import unicodedata

NEAR_DUPLICATE_BITS = 3
BITS = 64
NGRAM = 3
# A text with fewer n-grams than this has too little signal for a hash to mean anything.
MIN_GRAMS = 8

_NOISE = re.compile(r"[^\w\s]|_", re.UNICODE)
_SPACE = re.compile(r"\s+")
# Wire prefixes and outlet suffixes that vary between copies of one story.
_EN_DASH = chr(0x2013)
_PREFIX = re.compile(
    r"^\s*(?:(?:update|updated|exclusive|breaking|analysis|factbox|corrected)\s*\d*\s*"
    rf"[-:{_EN_DASH}]\s*)+",
    re.IGNORECASE,
)
_SUFFIX = re.compile(rf"\s+[-{_EN_DASH}|]\s+[^-{_EN_DASH}|]{{2,40}}$")


def normalise(text: str) -> str:
    """Lower-cased, accent-folded where the base letter survives, punctuation and wire
    furniture removed, whitespace collapsed. CJK characters pass through unchanged."""
    text = unicodedata.normalize("NFKC", text)
    text = _PREFIX.sub("", text)
    text = _SUFFIX.sub("", text)
    text = _NOISE.sub(" ", text.lower())
    return _SPACE.sub(" ", text).strip()


def _grams(text: str) -> list[str]:
    if len(text) < NGRAM:
        return [text] if text else []
    return [text[i : i + NGRAM] for i in range(len(text) - NGRAM + 1)]


def simhash(text: str, *, normalised: bool = False) -> int | None:
    """The unsigned 64-bit SimHash of `text`, or None when it is too short to hash."""
    grams = _grams(text if normalised else normalise(text))
    if len(grams) < MIN_GRAMS:
        return None
    weights = [0] * BITS
    for gram in grams:
        h = int.from_bytes(hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest(), "big")
        for bit in range(BITS):
            weights[bit] += 1 if (h >> bit) & 1 else -1
    value = 0
    for bit, weight in enumerate(weights):
        if weight > 0:
            value |= 1 << bit
    return value


def distance(a: int, b: int) -> int:
    """Hamming distance between two unsigned hashes."""
    return (a ^ b).bit_count()


def near_duplicate(a: int | None, b: int | None, bits: int = NEAR_DUPLICATE_BITS) -> bool:
    return a is not None and b is not None and distance(a, b) <= bits


def to_signed(value: int) -> int:
    return value - (1 << BITS) if value >= 1 << (BITS - 1) else value


def to_unsigned(value: int) -> int:
    return value + (1 << BITS) if value < 0 else value


class SimhashIndex:
    """Near-duplicate lookup without comparing every pair. A 64-bit hash is cut into four
    16-bit bands; two hashes within 3 differing bits must agree on at least one whole band
    (pigeonhole), so candidates are the items sharing a band value, then checked exactly."""

    def __init__(self, bits: int = NEAR_DUPLICATE_BITS) -> None:
        if bits > 3:
            raise ValueError("four bands only guarantee a match up to 3 differing bits")
        self._bits = bits
        self._bands: list[dict[int, list[tuple[int, int]]]] = [{} for _ in range(4)]

    @staticmethod
    def _band(value: int, i: int) -> int:
        return (value >> (16 * i)) & 0xFFFF

    def add(self, key: int, value: int) -> None:
        for i in range(4):
            self._bands[i].setdefault(self._band(value, i), []).append((key, value))

    def matches(self, value: int) -> list[tuple[int, int]]:
        """(key, distance) of every indexed hash within the bit limit, nearest first."""
        seen: dict[int, int] = {}
        for i in range(4):
            for key, other in self._bands[i].get(self._band(value, i), []):
                if key not in seen:
                    d = distance(value, other)
                    if d <= self._bits:
                        seen[key] = d
        return sorted(seen.items(), key=lambda kv: (kv[1], kv[0]))


def item_hash(headline: str, summary: str | None) -> int | None:
    """The hash of an item: its headline plus the lead of its summary (the first 300
    characters; later text is where outlets differ)."""
    # Each part is normalised alone: a wire prefix or outlet suffix belongs to the headline,
    # and would not be recognised once the summary follows it.
    return simhash(f"{normalise(headline)} {normalise((summary or '')[:300])}", normalised=True)
