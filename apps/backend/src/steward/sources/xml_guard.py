"""One DTD / entity guard for every XML document parsed from an outside source.

A byte search for ``<!DOCTYPE`` misses a document that is not UTF-8: in UTF-16 or UTF-32
every character is spread over several bytes, so the ASCII marker never appears in the raw
bytes, yet expat decodes the document (from its BOM or ``encoding`` declaration) and then
honours the DTD. EBCDIC-style single-byte encodings (``cp037``) hide it the same way. The
guard therefore searches the document under every reading expat could give it, and also
refuses a document that declares an encoding the guard cannot decode.

Expat's own amplification limits remain a second line of defence; this guard exists so the
documented refusal ("DTD or entity declarations are not accepted") actually holds.
"""

import re
import xml.etree.ElementTree as ET

_MARKERS = ("<!doctype", "<!entity")
_DECLARED = re.compile(rb"""^\s*<\?xml[^>]*?encoding\s*=\s*["']([A-Za-z0-9._:-]+)["']""")
_FIXED_READINGS = ("utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be", "cp037")
_BOMS = (
    (b"\xff\xfe\x00\x00", "utf-32"),
    (b"\x00\x00\xfe\xff", "utf-32"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
    (b"\xef\xbb\xbf", "utf-8-sig"),
)


class UnsafeXml(ValueError):
    """The document carries a DTD / entity declaration (or hides its encoding)."""


def _declared_encoding(data: bytes) -> str | None:
    # An ASCII-compatible prolog; for UTF-16/32 the prolog is not ASCII, so look at the
    # NUL-stripped bytes too.
    for view in (data[:512], data[:1024].replace(b"\x00", b"")):
        match = _DECLARED.match(view)
        if match:
            return match.group(1).decode("ascii")
    return None


def _readings(data: bytes) -> list[str]:
    """Every plausible text reading of the document."""
    readings: list[str] = [data.decode("latin-1")]
    stripped = data.replace(b"\x00", b"")
    if stripped != data:
        readings.append(stripped.decode("latin-1"))
    encodings: list[str] = list(_FIXED_READINGS)
    for bom, name in _BOMS:
        if data.startswith(bom):
            encodings.insert(0, name)
            break
    declared = _declared_encoding(data)
    if declared:
        encodings.insert(0, declared)
    for name in dict.fromkeys(encodings):
        try:
            readings.append(data.decode(name, errors="ignore"))
        except LookupError:
            raise UnsafeXml(f"XML declares an unsupported encoding {name!r}") from None
    return readings


def has_dtd_or_entity(data: bytes | str) -> bool:
    """True when any reading of the document contains a DOCTYPE or ENTITY declaration."""
    if isinstance(data, str):
        return any(m in data.lower() for m in _MARKERS)
    return any(m in reading.lower() for reading in _readings(data) for m in _MARKERS)


def reject_dtd(data: bytes | str, error: type[Exception] = UnsafeXml) -> None:
    """Raise `error` unless the document is free of DTD / entity declarations."""
    try:
        unsafe = has_dtd_or_entity(data)
    except UnsafeXml as exc:
        raise error(str(exc)) from exc
    if unsafe:
        raise error("XML with DTD or entity declarations is not accepted")


def safe_fromstring(data: bytes | str, error: type[Exception] = UnsafeXml) -> ET.Element:
    """`ET.fromstring` after the DTD guard; parse errors raise `error` too."""
    reject_dtd(data, error)
    try:
        return ET.fromstring(data)  # noqa: S314 - DTD/entity declarations refused above.
    except ET.ParseError as exc:
        raise error(f"malformed XML: {exc}") from exc
