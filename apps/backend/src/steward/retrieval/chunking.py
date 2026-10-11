"""Splits a document into parent sections and child passages.

A child (about 900 characters) is what search matches; its parent (the whole section, up to
`PARENT_MAX`) is what the model reads around it. Structure comes first: a heading starts a
new section, a table (consecutive lines of cells joined by " | ", as `documents.text` writes
them) is never cut mid-row and a split table repeats its header row, so a passage never shows
numbers without their column names. Plain text with no headings is one section per run of
paragraphs.
"""

import hashlib
import re
from dataclasses import dataclass, field

CHILD_TARGET = 900
CHILD_MAX = 1400
PARENT_MAX = 6000
MIN_CHILD = 40  # a shorter leftover joins its neighbour

_MD_HEADING = re.compile(r"^(#{1,6})\s+(\S.*)$")
_SEC_HEADING = re.compile(
    r"^(part\s+[ivx]+\b.*|item\s+\d{1,2}[a-c]?\s*[.:—-].*|item\s+\d{1,2}[a-c]?)$", re.I
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9“\"(])")


@dataclass
class Section:
    path: str
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip()


@dataclass(frozen=True)
class ChildChunk:
    text: str
    section_path: str
    parent: int  # index into Chunked.parents


@dataclass(frozen=True)
class ParentChunk:
    text: str
    section_path: str


@dataclass(frozen=True)
class Chunked:
    parents: list[ParentChunk]
    children: list[ChildChunk]


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _is_table_line(line: str) -> bool:
    return line.count(" | ") >= 1


def _heading(line: str) -> tuple[int, str] | None:
    """(level, title) when the line is a heading, else None."""
    stripped = line.strip()
    if not stripped or len(stripped) > 120:
        return None
    md = _MD_HEADING.match(stripped)
    if md:
        return len(md.group(1)), md.group(2).strip()
    if _SEC_HEADING.match(stripped):
        return (1 if stripped.lower().startswith("part") else 2), stripped
    letters = [c for c in stripped if c.isalpha()]
    if (
        len(letters) >= 4
        and len(stripped) <= 80
        and stripped.upper() == stripped
        and not stripped.endswith((".", ",", ";"))
        and not _is_table_line(stripped)
    ):
        return 3, stripped.title()
    return None


def split_sections(text: str, title: str = "") -> list[Section]:
    """Sections in reading order, each with the path of headings above it. Text before the
    first heading is a section named after the document."""
    stack: list[tuple[int, str]] = []
    sections: list[Section] = [Section(title)]
    for raw in text.replace("\r\n", "\n").split("\n"):
        found = _heading(raw)
        if found is not None:
            level, name = found
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, name))
            path = " > ".join(n for _, n in stack)
            sections.append(Section(f"{title} > {path}" if title else path))
            continue
        sections[-1].lines.append(raw.rstrip())
    return [s for s in sections if s.text]


def _blocks(lines: list[str]) -> list[list[str]]:
    """Paragraphs (separated by blank lines), with each table kept as one block."""
    blocks: list[list[str]] = []
    current: list[str] = []
    in_table = False
    for line in lines:
        if not line.strip():
            if current:
                blocks.append(current)
                current, in_table = [], False
            continue
        table = _is_table_line(line)
        if current and table != in_table:
            blocks.append(current)
            current = []
        current.append(line)
        in_table = table
    if current:
        blocks.append(current)
    return blocks


def _split_table(rows: list[str]) -> list[str]:
    """A table longer than CHILD_MAX, cut between rows with the header row repeated."""
    header, body = rows[0], rows[1:]
    pieces: list[str] = []
    current = [header]
    size = len(header)
    for row in body:
        if size + len(row) + 1 > CHILD_MAX and len(current) > 1:
            pieces.append("\n".join(current))
            current, size = [header], len(header)
        current.append(row)
        size += len(row) + 1
    pieces.append("\n".join(current))
    return pieces


def _split_text(block: str) -> list[str]:
    """A paragraph longer than CHILD_MAX, cut at sentence ends (or hard, if there are none)."""
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(block):
        while len(sentence) > CHILD_MAX:  # no sentence end inside: cut at a space
            cut = sentence.rfind(" ", 0, CHILD_MAX)
            cut = cut if cut > CHILD_MAX // 2 else CHILD_MAX
            head, sentence = sentence[:cut].strip(), sentence[cut:].strip()
            if current:
                pieces.append(current)
                current = ""
            pieces.append(head)
        if current and len(current) + len(sentence) + 1 > CHILD_TARGET:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def children_of(section: Section) -> list[str]:
    """Passages of a section: paragraphs packed up to CHILD_TARGET; tables stay whole."""
    units: list[str] = []
    for block in _blocks(section.lines):
        joined = "\n".join(block)
        if _is_table_line(block[0]) and len(joined) > CHILD_MAX:
            units.extend(_split_table(block))
        elif len(joined) > CHILD_MAX:
            units.extend(_split_text(" ".join(block)))
        else:
            units.append(joined)
    out: list[str] = []
    current = ""
    for unit in units:
        is_table = _is_table_line(unit.split("\n", 1)[0])
        if current and (is_table or len(current) + len(unit) + 2 > CHILD_TARGET):
            out.append(current)
            current = ""
        if is_table:
            out.append(unit)
        else:
            current = f"{current}\n\n{unit}".strip() if current else unit
    if current:
        out.append(current)
    # A tiny leftover joins the passage before it rather than standing alone.
    merged: list[str] = []
    for passage in out:
        if merged and len(passage) < MIN_CHILD and not _is_table_line(passage):
            merged[-1] = f"{merged[-1]}\n\n{passage}"
        else:
            merged.append(passage)
    return merged


def _parents(section: Section) -> list[tuple[str, list[str]]]:
    """A section as one or more parents of at most PARENT_MAX, each with its own children."""
    passages = children_of(section)
    groups: list[list[str]] = []
    size = 0
    for passage in passages:
        if not groups or (size + len(passage) > PARENT_MAX and groups[-1]):
            groups.append([])
            size = 0
        groups[-1].append(passage)
        size += len(passage) + 2
    return [(section.path, g) for g in groups]


def chunk(text: str, title: str = "", *, structured: bool = True) -> Chunked:
    """Parents and children of a document. `structured=False` (a news item, a note) makes the
    whole text one section and one child, with no parent: the child is its own context."""
    clean = text.strip()
    if not clean:
        return Chunked([], [])
    if not structured:
        return Chunked([], [ChildChunk(clean, title, -1)])
    parents: list[ParentChunk] = []
    children: list[ChildChunk] = []
    for section in split_sections(clean, title):
        for path, passages in _parents(section):
            parents.append(ParentChunk("\n\n".join(passages), path))
            children.extend(ChildChunk(p, path, len(parents) - 1) for p in passages)
    return Chunked(parents, children)
