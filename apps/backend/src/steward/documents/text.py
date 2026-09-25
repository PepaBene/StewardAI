"""Plain text from fetched documents, and the text matching the validator relies on.

The same text a document is turned into here is what the extraction call reads and what
snippet containment is checked against, so a snippet the model quotes can be found again
in the archived source byte for byte (after `normalize_for_match`).

HTML tables become one line per row with cells joined by " | ". Cells holding only a
currency symbol are glued to the next cell and cells holding only ")" or "%" to the
previous one, so an EDGAR row like `$ | 94,036` reads `$94,036`.
"""

import html
import re
import unicodedata
from html.parser import HTMLParser

_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
    "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "title", "ul",
}  # fmt: skip
_SKIP_TAGS = {"script", "style", "noscript", "template", "head", "svg"}
_PREFIX_ONLY = re.compile(r"^[$€£¥]$|^(US\$|C\$|A\$|CHF|kr\.?)$")
_SUFFIX_ONLY = re.compile(r"^(\)|%|\)%|%\))$")


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._buffer: list[str] = []
        self._skip_depth = 0
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    # -- text accumulation -------------------------------------------------
    def _flush_line(self) -> None:
        text = _collapse("".join(self._buffer))
        self._buffer = []
        if text:
            self.lines.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif self._skip_depth:
            return
        elif tag == "tr":
            self._flush_line()
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")
        elif tag in _BLOCK_TAGS and self._cell is None:
            self._flush_line()

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif self._skip_depth:
            return
        elif tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(_collapse("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            line = _join_cells(self._row)
            if line:
                self.lines.append(line)
            self._row = None
            self._cell = None
        elif tag in _BLOCK_TAGS and self._cell is None:
            self._flush_line()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._cell is not None:
            self._cell.append(data)
        else:
            self._buffer.append(data)

    def close(self) -> None:
        super().close()
        self._flush_line()


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ").replace("​", "")).strip()


def _join_cells(cells: list[str]) -> str:
    merged: list[str] = []
    pending_prefix = ""
    for cell in cells:
        if not cell:
            continue
        if _PREFIX_ONLY.match(cell):
            pending_prefix += cell
            continue
        if _SUFFIX_ONLY.match(cell) and merged:
            merged[-1] += cell
            continue
        merged.append(pending_prefix + cell)
        pending_prefix = ""
    if pending_prefix:
        merged.append(pending_prefix)
    return " | ".join(merged)


def html_to_text(markup: str) -> str:
    parser = _TextParser()
    parser.feed(markup)
    parser.close()
    return "\n".join(parser.lines)


def plain_to_text(text: str) -> str:
    """Plain text (or PDF text) with each line's whitespace collapsed, empty lines dropped."""
    lines = (_collapse(line) for line in html.unescape(text).splitlines())
    return "\n".join(line for line in lines if line)


_TRANSLATE = str.maketrans(
    {
        "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u2032": "'",
        "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2033": '"',
        "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",
        "\u2212": "-", "\u00ad": "",
    }
)  # fmt: skip


def normalize_for_match(text: str) -> str:
    """Canonical form for containment checks: NFKC, straight quotes and dashes, one space
    for any run of whitespace (line breaks included), and table separators ignored."""
    text = unicodedata.normalize("NFKC", text).translate(_TRANSLATE)
    text = text.replace(" | ", " ").replace("|", " ")
    return re.sub(r"\s+", " ", text).strip().casefold()


def contains(haystack: str, needle: str) -> bool:
    """Does `needle` occur in `haystack`, up to whitespace, quote/dash style and case?"""
    needle_n = normalize_for_match(needle)
    return bool(needle_n) and needle_n in normalize_for_match(haystack)
