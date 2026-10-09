"""Filing parser (plan P1.4): sections, tables, inline-XBRL facts, text blocks and footnotes with
stable evidence offsets.

`parse_html(data)` is a pure function of the document bytes and `PARSER_VERSION`. It builds one
canonical plain text; every section, table row, fact and text block carries `[start, end)`
character offsets into that text, so an evidence span can be re-found by anyone running the same
parser version on the archived bytes. Changing the parser means bumping `PARSER_VERSION`, which
creates new observations/claims instead of silently rewriting earlier ones.

Hidden `ix:header` content (contexts, units, footnotes, relationships) is read into structures but
is not part of the text. A malformed or empty document raises `ParseError`; the caller quarantines
the document version rather than guessing.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any

PARSER_VERSION = "disclosure-html-2"

_BLOCK = {
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt", "figcaption",
    "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li",
    "main", "nav", "ol", "p", "pre", "section", "title", "ul",
}  # fmt: skip
_SKIP = {"script", "style", "noscript", "template", "head", "svg"}
_HEADING = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_SECTION_LINE = re.compile(
    r"^(?:PART\s+[IV]+\b|ITEM\s+\d{1,2}[A-C]?\b\.?|NOTE\s+\d{1,3}\b[.:\s-])", re.IGNORECASE
)
_SPACE = re.compile(r"\s+")
TOC_MAX_CHARS = 300


class ParseError(ValueError):
    """The document cannot be parsed into a usable text; quarantine it."""


@dataclass(frozen=True, slots=True)
class Section:
    title: str
    level: int
    start: int
    end: int
    toc: bool  # a table-of-contents line, not the section body


@dataclass(frozen=True, slots=True)
class TableRow:
    index: int
    cells: tuple[str, ...]
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Table:
    index: int
    start: int
    end: int
    rows: tuple[TableRow, ...]


@dataclass(frozen=True, slots=True)
class IxContext:
    id: str
    start: dt.date | None
    end: dt.date | None
    instant: dt.date | None
    dimensions: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class IxFact:
    name: str
    context_ref: str | None
    unit_ref: str | None
    decimals: str | None
    scale: int
    sign: str
    raw_text: str
    value: Decimal | None
    error: str | None
    fact_id: str | None
    nil: bool
    start: int | None
    end: int | None
    footnote_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TextBlock:
    name: str
    context_ref: str | None
    start: int
    end: int
    # Later spans of the same block: a filing often tags only the heading and carries the rest in
    # `ix:continuation` elements chained by `continuedAt`. In chain order after `start:end`.
    continuations: tuple[tuple[int, int], ...] = ()

    @property
    def segments(self) -> tuple[tuple[int, int], ...]:
        return ((self.start, self.end), *self.continuations)


@dataclass(frozen=True, slots=True)
class Footnote:
    footnote_id: str
    text: str


@dataclass(slots=True)
class ParsedDocument:
    text: str
    sections: list[Section] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    facts: list[IxFact] = field(default_factory=list)
    text_blocks: list[TextBlock] = field(default_factory=list)
    footnotes: list[Footnote] = field(default_factory=list)
    contexts: dict[str, IxContext] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)
    parser_version: str = PARSER_VERSION

    def span(self, start: int, end: int) -> str:
        return self.text[start:end]

    def dimensions_of(self, fact: IxFact) -> dict[str, str]:
        ctx = self.contexts.get(fact.context_ref or "")
        return dict(ctx.dimensions) if ctx else {}


def _date(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def parse_fact_value(
    raw: str, fmt: str | None, scale: int, sign: str
) -> tuple[Decimal | None, str | None]:
    """The reported number of an `ix:nonFraction`, or (None, why). Never guesses a format."""
    # Transformation registry 4 and later spell the names with hyphens (num-dot-decimal); earlier
    # registries run them together (numdotdecimal). Both mean the same format.
    lowered = (fmt or "").lower().replace("-", "")
    text = raw.strip()
    if "zerodash" in lowered or "fixedzero" in lowered:
        number = Decimal(0)
    else:
        if "numcommadecimal" in lowered:
            cleaned = text.replace(".", "").replace(" ", "").replace("\xa0", "").replace(",", ".")
        elif not lowered or "numdotdecimal" in lowered:
            cleaned = text.replace(",", "").replace(" ", "").replace("\xa0", "")
        else:
            return None, f"unsupported number format {fmt!r}"
        try:
            number = Decimal(cleaned)
        except InvalidOperation:
            return None, f"not a number: {text[:40]!r}"
        if not number.is_finite():
            return None, f"not a finite number: {text[:40]!r}"
    value = number.scaleb(scale)
    return (-value if sign == "-" else value), None


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.pos = 0
        self.skip = 0
        self.header = 0
        self.headings: list[tuple[str, int, int]] = []  # title, level, start
        self._heading: tuple[int, int] | None = None  # level, start
        self._heading_parts: list[str] | None = None
        self._cell_parts: list[str] | None = None
        self._hidden: list[tuple[str, dict[str, str], list[str]]] = []
        self.tables: list[Table] = []
        self._table_stack: list[tuple[int, list[TableRow]]] = []
        self._row: tuple[int, list[str], int] | None = None  # start, cells, row index
        self._cell_start: int | None = None
        self.facts: list[IxFact] = []
        self._fact_stack: list[tuple[dict[str, str], int, list[str]]] = []
        self.blocks: list[TextBlock] = []
        self._block_stack: list[tuple[str, str | None, int, str]] = []
        self._cont_stack: list[tuple[str, str, int]] = []  # id, continuedAt, start
        self.conts: dict[str, tuple[str, int, int]] = {}  # id -> continuedAt, start, end
        self.block_links: list[str] = []  # continuedAt of each block, parallel to self.blocks
        self.contexts: dict[str, IxContext] = {}
        self.units: dict[str, str] = {}
        self.footnotes: list[Footnote] = []
        self.relationships: list[tuple[list[str], list[str]]] = []
        self._ctx: dict[str, Any] | None = None
        self._unit: dict[str, Any] | None = None
        self._capture: tuple[str, list[str]] | None = None
        self._footnote: tuple[str, list[str]] | None = None

    # -- text building -------------------------------------------------------------
    def _at_line_start(self) -> bool:
        return not self.chunks or self.chunks[-1].endswith("\n")

    def _emit(self, text: str) -> None:
        if text:
            self.chunks.append(text)
            self.pos += len(text)
            if self._cell_parts is not None:
                self._cell_parts.append(text)
            if self._heading_parts is not None:
                self._heading_parts.append(text)

    def _newline(self) -> None:
        if not self._at_line_start():
            self._emit("\n")

    def handle_data(self, data: str) -> None:
        if self.header:
            self._header_data(data)
            return
        if self.skip:
            return
        text = _SPACE.sub(" ", data)
        if self._at_line_start() or (self.chunks and self.chunks[-1].endswith(" ")):
            text = text.lstrip(" ")
        self._emit(text)
        for _attrs, _start, buffer in self._fact_stack:
            buffer.append(text)

    # -- header (contexts, units, footnotes) ----------------------------------------
    def _header_data(self, data: str) -> None:
        if self._capture is not None:
            self._capture[1].append(data)
        if self._footnote is not None:
            self._footnote[1].append(data)
        for _tag, _attrs, parts in self._hidden:
            parts.append(data)

    def _start_header_tag(self, tag: str, attrs: dict[str, str]) -> None:
        if tag in ("ix:nonfraction", "ix:nonnumeric"):
            self._hidden.append((tag, attrs, []))
        elif tag == "xbrli:context":
            self._ctx = {"id": attrs.get("id", ""), "dims": []}
        elif tag in ("xbrli:startdate", "xbrli:enddate", "xbrli:instant", "xbrli:measure"):
            self._capture = (tag, [])
        elif tag in ("xbrldi:explicitmember", "xbrldi:typedmember") and self._ctx is not None:
            self._capture = (tag + "|" + attrs.get("dimension", ""), [])
        elif tag == "xbrli:unit":
            self._unit = {"id": attrs.get("id", ""), "measures": []}
        elif tag == "ix:footnote":
            self._footnote = (attrs.get("id", ""), [])
        elif tag == "ix:relationship":
            self.relationships.append(
                (attrs.get("fromrefs", "").split(), attrs.get("torefs", "").split())
            )

    def _end_header_tag(self, tag: str) -> None:
        if tag in ("ix:nonfraction", "ix:nonnumeric") and self._hidden:
            _t, attrs, parts = self._hidden.pop()
            self._store_fact(attrs, None, None, "".join(parts), numeric=(tag == "ix:nonfraction"))
        elif self._capture is not None and tag == self._capture[0].split("|")[0]:
            name, parts = self._capture
            text = "".join(parts).strip()
            self._capture = None
            if self._ctx is not None:
                if name.startswith(("xbrldi:explicitmember", "xbrldi:typedmember")):
                    self._ctx["dims"].append((name.split("|", 1)[1], text))
                else:
                    self._ctx[name] = text
            elif self._unit is not None and name == "xbrli:measure":
                self._unit["measures"].append(text)
        elif tag == "xbrli:context" and self._ctx is not None:
            c = self._ctx
            self._ctx = None
            if c["id"]:
                self.contexts[str(c["id"])] = IxContext(
                    str(c["id"]),
                    _date(str(c.get("xbrli:startdate", ""))),
                    _date(str(c.get("xbrli:enddate", ""))),
                    _date(str(c.get("xbrli:instant", ""))),
                    tuple(c["dims"]),
                )
        elif tag == "xbrli:unit" and self._unit is not None:
            u = self._unit
            self._unit = None
            measures = [m.split(":")[-1] for m in u["measures"]]
            if u["id"]:
                self.units[str(u["id"])] = "/".join(measures)
        elif tag == "ix:footnote" and self._footnote is not None:
            fid, parts = self._footnote
            self._footnote = None
            self.footnotes.append(Footnote(fid, _SPACE.sub(" ", "".join(parts)).strip()))
        elif tag == "ix:header":
            self.header = max(0, self.header - 1)

    # -- tags ----------------------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "ix:header":
            self.header += 1
            return
        if self.header:
            self._start_header_tag(tag, a)
            return
        if tag in _SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        if tag == "ix:nonfraction":
            self._fact_stack.append((a, self.pos, []))
        elif tag == "ix:nonnumeric":
            self._block_stack.append(
                (a.get("name", ""), a.get("contextref"), self.pos, a.get("continuedat", ""))
            )
        elif tag == "ix:continuation":
            self._cont_stack.append((a.get("id", ""), a.get("continuedat", ""), self.pos))
        elif tag == "table":
            self._newline()
            self._table_stack.append((self.pos, []))
        elif tag == "tr" and self._table_stack:
            self._newline()
            self._row = (self.pos, [], len(self._table_stack[-1][1]))
        elif tag in ("td", "th") and self._row is not None:
            if self._row[1]:
                self._emit(" | ")
            self._cell_start = self.pos
            self._cell_parts = []
        elif tag == "br" and self._cell_start is not None:
            self._emit(" ")
        elif tag in _BLOCK and self._cell_start is None:
            self._newline()
            if tag in _HEADING:
                self._heading = (_HEADING[tag], self.pos)
                self._heading_parts = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if self.header:
            self._start_header_tag(tag, a)
            self._end_header_tag(tag)
            return
        if tag == "ix:nonfraction" and not self.skip:  # nil facts are self-closing
            self._fact_stack.append((a, self.pos, []))
            self._close_fact()
            return
        if tag in _SKIP:
            return
        self.handle_starttag(tag, attrs)
        if tag in _BLOCK or tag in ("td", "th"):
            self.handle_endtag(tag)

    def _close_fact(self) -> None:
        attrs, start, buffer = self._fact_stack.pop()
        self._store_fact(attrs, start, self.pos, "".join(buffer))

    def _store_fact(
        self,
        attrs: dict[str, str],
        start: int | None,
        end: int | None,
        text: str,
        *,
        numeric: bool = True,
    ) -> None:
        raw = _SPACE.sub(" ", text).strip()
        nil = attrs.get("xsi:nil", "").lower() == "true"
        scale = int(attrs["scale"]) if re.fullmatch(r"-?\d{1,2}", attrs.get("scale", "")) else 0
        sign = attrs.get("sign", "")
        value, error = (
            (None, None)
            if nil or not numeric
            else parse_fact_value(raw, attrs.get("format"), scale, sign)
        )
        self.facts.append(
            IxFact(
                name=attrs.get("name", ""),
                context_ref=attrs.get("contextref"),
                unit_ref=attrs.get("unitref"),
                decimals=attrs.get("decimals"),
                scale=scale,
                sign=sign,
                raw_text=raw,
                value=value,
                error=error,
                fact_id=attrs.get("id") or None,
                nil=nil,
                start=start,
                end=end,
            )
        )

    def handle_endtag(self, tag: str) -> None:
        if self.header:
            self._end_header_tag(tag)
            return
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag == "ix:nonfraction" and self._fact_stack:
            self._close_fact()
        elif tag == "ix:nonnumeric" and self._block_stack:
            name, ctx, start, link = self._block_stack.pop()
            self.blocks.append(TextBlock(name, ctx, start, self.pos))
            self.block_links.append(link)
        elif tag == "ix:continuation" and self._cont_stack:
            cid, link, start = self._cont_stack.pop()
            if cid:
                self.conts[cid] = (link, start, self.pos)
        elif tag in ("td", "th") and self._row is not None and self._cell_start is not None:
            self._row[1].append("".join(self._cell_parts or ()).strip())
            self._cell_start = None
            self._cell_parts = None
        elif tag == "tr" and self._row is not None and self._table_stack:
            start, cells, index = self._row
            self._row = None
            if cells:
                self._table_stack[-1][1].append(TableRow(index, tuple(cells), start, self.pos))
            self._newline()
        elif tag == "table" and self._table_stack:
            start, rows = self._table_stack.pop()
            if rows:
                self.tables.append(Table(len(self.tables), start, self.pos, tuple(rows)))
            self._newline()
        elif tag in _BLOCK and self._cell_start is None:
            if tag in _HEADING and self._heading is not None:
                level, start = self._heading
                self._heading = None
                title = "".join(self._heading_parts or ()).strip()
                self._heading_parts = None
                if title:
                    self.headings.append((title, level, start))
            self._newline()


def _sections(text: str, headings: list[tuple[str, int, int]]) -> list[Section]:
    marks: dict[int, tuple[str, int]] = {
        start: (title[:200], level) for title, level, start in headings
    }
    offset = 0
    for line in text.split("\n"):
        stripped = line.strip()
        if (
            stripped
            and len(stripped) <= 200
            and _SECTION_LINE.match(stripped)
            and offset not in marks
        ):
            marks[offset] = (stripped, 2)
        offset += len(line) + 1
    ordered = sorted(marks.items())
    out = []
    for i, (start, (title, level)) in enumerate(ordered):
        end = ordered[i + 1][0] if i + 1 < len(ordered) else len(text)
        out.append(Section(title, level, start, end, toc=(end - start) < TOC_MAX_CHARS))
    return out


def _with_continuations(
    blocks: list[TextBlock], links: list[str], conts: dict[str, tuple[str, int, int]]
) -> list[TextBlock]:
    """Attach each block's `continuedAt` chain. An unknown id or a cycle ends the chain there."""
    out: list[TextBlock] = []
    for block, link in zip(blocks, links, strict=True):
        spans: list[tuple[int, int]] = []
        seen: set[str] = set()
        while link and link not in seen and link in conts:
            seen.add(link)
            link, start, end = conts[link]
            spans.append((start, end))
        out.append(replace(block, continuations=tuple(spans)) if spans else block)
    return out


def parse_html(data: bytes) -> ParsedDocument:
    """Parse an (inline-XBRL) HTML filing. Raises ParseError when nothing readable remains."""
    if not data or not data.strip():
        raise ParseError("empty document")
    decoded = _decode(data)
    parser = _Parser()
    try:
        parser.feed(decoded)
        parser.close()
    except (AssertionError, ValueError, RecursionError) as exc:
        raise ParseError(f"HTML could not be parsed: {type(exc).__name__}") from exc
    text = "".join(parser.chunks)
    if not text.strip():
        raise ParseError("document has no readable text")
    notes: dict[str, list[str]] = {}
    ids = {f.fact_id for f in parser.facts if f.fact_id}
    for from_refs, to_refs in parser.relationships:
        for ref in from_refs:
            if ref in ids:
                notes.setdefault(ref, []).extend(to_refs)
    facts = [replace(f, footnote_ids=tuple(notes.get(f.fact_id or "", ()))) for f in parser.facts]
    return ParsedDocument(
        text=text,
        sections=_sections(text, parser.headings),
        tables=parser.tables,
        facts=facts,
        text_blocks=_with_continuations(parser.blocks, parser.block_links, parser.conts),
        footnotes=parser.footnotes,
        contexts=parser.contexts,
        units=parser.units,
    )


def _decode(data: bytes) -> str:
    head = data[:2048].decode("ascii", errors="ignore").lower()
    match = re.search(r"charset=([a-z0-9_-]+)|encoding=[\"']([a-z0-9_-]+)", head)
    name = (match.group(1) or match.group(2)) if match else "utf-8"
    try:
        return data.decode(name, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def section_for(doc: ParsedDocument, offset: int) -> Section | None:
    """The innermost non-TOC section containing a character offset (for evidence rows)."""
    best: Section | None = None
    for section in doc.sections:
        if not section.toc and section.start <= offset < section.end:
            if best is None or section.start >= best.start:
                best = section
    return best
