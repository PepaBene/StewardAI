"""Language detection for news items, in code (no model call, no dependency).

Two signals, in this order:

1. Script. Kana means Japanese, Hangul Korean, Han without kana Chinese, Cyrillic Russian
   (or the closest Cyrillic language), Arabic script Arabic. These are near certain on a
   headline of a few words.
2. For Latin script, stopwords and diacritics: the language whose function words cover most of
   the text wins, if it clearly beats the runner-up. Too short or too ambiguous a text returns
   None rather than a guess, so a caller keeps the feed's own language tag.

This is a pre-filter for translation and clustering, not a classifier to trust on its own:
the feed's declared language is used when this is unsure.
"""

import re
from dataclasses import dataclass

# Function words that are frequent in the language and rare in the others in the list.
_STOPWORDS: dict[str, frozenset[str]] = {
    "en": frozenset(
        "the of and to in is for on with that as at by from it its has have will said says "
        "after over into than but are was were be this their more new".split()
    ),
    "de": frozenset(
        "der die das und ist von mit den dem des für auf nicht ein eine einen im zu sich auch "
        "als bei nach über wie aus wird werden hat haben dass noch oder um zum zur".split()
    ),
    "fr": frozenset(
        "le la les des du de et est un une pour dans que qui sur avec au aux par pas plus "
        "ses son sa ont sont été cette ce ou mais comme après selon".split()
    ),
    "es": frozenset(
        "el la los las de del y es un una para en que por con su sus al lo como más pero "
        "fue han ha se este esta tras según sobre entre".split()
    ),
    "it": frozenset(
        "il lo la gli le di del della dei delle e è un una per in che con su sono non più "
        "ha hanno come dopo secondo tra fra nel nella".split()
    ),
    "pt": frozenset(
        "o a os as de do da dos das e é um uma para em que por com no na nos nas não mais "
        "foi são como após segundo entre ao aos".split()
    ),
    "nl": frozenset(
        "de het een en van in is dat op te voor met zijn niet aan er ook als bij naar om "
        "door over uit worden wordt heeft hebben".split()
    ),
    "cs": frozenset(
        "a je se na v ve že s z do pro to o k jako by byl byla bylo jsou má mají který která "
        "které podle po od při ale také již".split()
    ),
    "pl": frozenset(
        "i w na z do nie się to jest że o dla po przez jak od ale lub czy który która które "
        "oraz przy po jego jej ich".split()
    ),
    "sv": frozenset(
        "och i att det som en på är av för med till den har de inte om ett men var sig från "
        "efter vid också".split()
    ),
    "tr": frozenset(
        "ve bir bu için ile de da çok olarak daha ancak gibi ise en olan sonra kadar".split()
    ),
}
# Letters that mark a language among the Latin-script ones.
_DIACRITICS: dict[str, str] = {
    "de": "äöüß",
    "fr": "àâçéèêëîïôùûœ",
    "es": "ñ¿¡áíóú",
    "pt": "ãõç",
    "cs": "ěščřžýůňťď",
    "pl": "ąćęłńśźż",
    "sv": "åäö",
    "tr": "ğışçö",
    "it": "àèìòù",
}

_KANA = re.compile(r"[぀-ヿㇰ-ㇿｦ-ﾟ]")
_HANGUL = re.compile(r"[가-힯ᄀ-ᇿ]")
_HAN = re.compile(r"[㐀-䶿一-鿿]")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_ARABIC = re.compile(r"[؀-ۿ]")
_HEBREW = re.compile(r"[֐-׿]")
_THAI = re.compile(r"[฀-๿]")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)

# Fewer letters than this is too little text to name a Latin-script language.
MIN_LATIN_WORDS = 4
# The winner must cover this many times the runner-up's stopwords.
MARGIN = 1.5


@dataclass(frozen=True)
class Detected:
    language: str
    confidence: float  # 0..1, a heuristic; script matches are 0.95+


def _share(pattern: re.Pattern[str], text: str, letters: int) -> float:
    return len(pattern.findall(text)) / letters if letters else 0.0


def detect_language(text: str | None) -> Detected | None:
    """The language of `text`, or None when it cannot be told apart reliably."""
    if not text:
        return None
    text = text.strip()
    letters = sum(ch.isalpha() for ch in text)
    if letters == 0:
        return None
    kana = _share(_KANA, text, letters)
    han = _share(_HAN, text, letters)
    hangul = _share(_HANGUL, text, letters)
    if kana >= 0.05 or (kana > 0 and han > 0):
        return Detected("ja", 0.97)
    if hangul >= 0.2:
        return Detected("ko", 0.97)
    if han >= 0.3:
        return Detected("zh", 0.95)
    for pattern, code in (
        (_CYRILLIC, "ru"),
        (_ARABIC, "ar"),
        (_HEBREW, "he"),
        (_THAI, "th"),
        (_DEVANAGARI, "hi"),
    ):
        if _share(pattern, text, letters) >= 0.5:
            return Detected(code, 0.9)
    return _detect_latin(text)


def _detect_latin(text: str) -> Detected | None:
    words = [w.lower() for w in _WORD.findall(text)]
    if len(words) < MIN_LATIN_WORDS:
        return None
    scores: dict[str, float] = {}
    for code, stop in _STOPWORDS.items():
        hits = sum(w in stop for w in words)
        marks = _DIACRITICS.get(code, "")
        accents = sum(ch in marks for ch in text.lower()) if marks else 0
        scores[code] = hits + 0.5 * min(accents, 4)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (best, top), (_, second) = ranked[0], ranked[1]
    if top < 2 or top < MARGIN * max(second, 0.5):
        return None
    return Detected(best, round(min(0.95, 0.5 + top / (len(words) + 1)), 2))


def resolve_language(declared: str | None, headline: str, summary: str | None) -> str | None:
    """The language to store for an item: the detected one when confident (a feed's tag is
    often just the site's default, e.g. a Czech outlet's English-tagged feed), else the
    feed's own two-letter code, else None."""
    detected = detect_language(f"{headline}. {summary or ''}")
    if detected is not None and detected.confidence >= 0.6:
        return detected.language
    return (declared or "")[:2].lower() or None
