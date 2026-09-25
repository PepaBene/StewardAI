"""Fetching a source document: bytes, type and hash, and the text the validator checks.

Web content is untrusted data. It is fetched here by code (never by the model during
extraction), size-capped, archived as the exact bytes, and handed to the extraction call
as a document.
"""

import hashlib
import io
from dataclasses import dataclass

import httpx
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from steward import __version__
from steward.documents.text import html_to_text, plain_to_text

PDF = "application/pdf"
HTML = "text/html"
TEXT = "text/plain"


class FetchError(Exception):
    pass


@dataclass(frozen=True)
class Document:
    url: str
    content_type: str  # PDF, HTML or TEXT
    data: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    @property
    def extension(self) -> str:
        return {PDF: "pdf", HTML: "html"}.get(self.content_type, "txt")

    def text(self) -> str:
        """The document as plain text: what snippets are checked against."""
        if self.content_type == PDF:
            return pdf_text(self.data)
        decoded = self.data.decode(_charset(self.data), errors="replace")
        return html_to_text(decoded) if self.content_type == HTML else plain_to_text(decoded)


def _charset(data: bytes) -> str:
    head = data[:2048].decode("ascii", errors="ignore").lower()
    for marker in ("charset=", "encoding="):
        at = head.find(marker)
        if at >= 0:
            value = head[at + len(marker) :].strip("\"' ").split('"')[0].split("'")[0]
            name = value.split(";")[0].split(">")[0].split()[0] if value else ""
            try:
                "".encode(name)
                return name
            except (LookupError, IndexError):
                break
    return "utf-8"


def pdf_text(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except (PdfReadError, ValueError, KeyError) as exc:
        raise FetchError(f"unreadable PDF: {exc}") from exc
    return plain_to_text("\n".join(pages))


def sniff_type(data: bytes, header: str | None, url: str) -> str:
    if data[:5] == b"%PDF-":
        return PDF
    header = (header or "").split(";")[0].strip().lower()
    if header in (HTML, "application/xhtml+xml"):
        return HTML
    head = data[:1024].lstrip().lower()
    if head.startswith((b"<!doctype html", b"<html", b"<?xml")) or b"<body" in head:
        return HTML
    if url.lower().endswith((".htm", ".html")):
        return HTML
    return TEXT


def default_headers(user_agent: str | None = None) -> dict[str, str]:
    return {"User-Agent": user_agent or f"StewardAI/{__version__} (personal portfolio tool)"}


async def fetch_document(
    http: httpx.AsyncClient, url: str, *, max_bytes: int, headers: dict[str, str] | None = None
) -> Document:
    try:
        async with http.stream("GET", url, headers=headers, follow_redirects=True) as response:
            if response.status_code != 200:
                raise FetchError(f"{url} returned HTTP {response.status_code}")
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise FetchError(f"{url} is larger than {max_bytes // 1_000_000} MB")
                chunks.append(chunk)
            data = b"".join(chunks)
            content_type = sniff_type(data, response.headers.get("content-type"), url)
    except httpx.HTTPError as exc:
        raise FetchError(f"fetching {url} failed: {exc}") from exc
    if not data:
        raise FetchError(f"{url} returned an empty body")
    return Document(url=str(url), content_type=content_type, data=data)
