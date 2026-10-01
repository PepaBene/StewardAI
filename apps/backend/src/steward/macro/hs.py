"""HS revision labels and the HS2017 -> HS2022 concordance (B14).

Comtrade labels the classification of a row `H0`..`H6` (and `S1`..`S4` for SITC); users type
`HS2022`, `HS 2022` or `2022`. One canonical label (`HS2022`) is stored everywhere, so a
trade row and an exposure mapping for the same revision always match.

Concordance: the 2-digit HS chapters are the same in HS2017 and HS2022 (the 2022 amendment
re-cut headings and subheadings inside chapters, it added no chapter), so a chapter code is
comparable across the two. 4- and 6-digit codes are comparable only when the WCO
correlation table row says 1:1; `hs_concordance` (migration 20261005110000) holds those rows,
loaded with `concordance_rows` from the official WCO table. With no row the answer is
"unknown", never "same".
"""

from __future__ import annotations

import re

# Comtrade classificationCode -> canonical revision label.
COMTRADE_HS = {
    "H0": "HS1992",
    "H1": "HS1996",
    "H2": "HS2002",
    "H3": "HS2007",
    "H4": "HS2012",
    "H5": "HS2017",
    "H6": "HS2022",
}
HS_REVISION_ORDER = ("HS1992", "HS1996", "HS2002", "HS2007", "HS2012", "HS2017", "HS2022")


def normalize_hs_revision(text: object) -> str | None:
    """'H6' / 'hs 2022' / 'HS2022' / '2022' -> 'HS2022'; SITC labels and unknown labels are
    upper-cased as given; empty -> None."""
    raw = str(text or "").strip().upper().replace(" ", "").replace("-", "")
    if not raw:
        return None
    if raw in COMTRADE_HS:
        return COMTRADE_HS[raw]
    match = re.fullmatch(r"(?:HS)?(19\d\d|20\d\d)", raw)
    if match and f"HS{match.group(1)}" in HS_REVISION_ORDER:
        return f"HS{match.group(1)}"
    return raw


# Revision pairs whose 2-digit chapters are identical.
_CHAPTER_STABLE = {("HS2017", "HS2022"), ("HS2022", "HS2017")}


def chapter_comparable(code: str, from_revision: str, to_revision: str) -> bool:
    """True when `code` is a 2-digit chapter and the two revisions share chapter structure."""
    a, b = normalize_hs_revision(from_revision), normalize_hs_revision(to_revision)
    if a == b:
        return True
    return len(code) == 2 and (a, b) in _CHAPTER_STABLE


def concordance_rows(
    pairs: list[tuple[str, str, str]], from_revision: str, to_revision: str
) -> list[tuple[str, str, str, str, str]]:
    """Rows for `public.hs_concordance` from WCO correlation pairs (from_code, to_code,
    relation) where relation is one of '1:1', '1:n', 'n:1', 'n:n'."""
    a, b = normalize_hs_revision(from_revision), normalize_hs_revision(to_revision)
    assert a is not None and b is not None
    return [(a, code, b, to_code, relation) for code, to_code, relation in pairs]
