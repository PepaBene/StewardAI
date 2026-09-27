"""Fetching a source document: bytes, type and hash, and the text the validator checks.

Web content is untrusted data. It is fetched here by code (never by the model during
extraction), size-capped, archived as the exact bytes, and handed to the extraction call
as a document.
"""

import asyncio
import hashlib
import io
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx
from pypdf import PdfReader
from pypdf.errors import DependencyError, PyPdfError

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

    async def text_async(self) -> str:
        """`text()` in a worker thread: PDF parsing is CPU-bound and would block the loop."""
        return await asyncio.to_thread(self.text)


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
    except (PyPdfError, DependencyError, ValueError, KeyError, RecursionError) as exc:
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


MAX_REDIRECTS = 5
_REDIRECTS = (301, 302, 303, 307, 308)

Resolver = Callable[[str], Awaitable[list[str]]]


async def resolve_host(host: str) -> list[str]:
    """Every address `host` resolves to."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def check_public_url(url: str, resolve: Resolver) -> None:
    """Refuse anything but http(s) to a host whose every address is public: a document URL
    comes from a web search, a holding profile or a redirect, and must not reach the
    worker's own network (loopback, private ranges, cloud metadata)."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise FetchError(f"{url}: only http and https URLs are fetched")
    host = parts.hostname
    if not host:
        raise FetchError(f"{url} has no host")
    try:
        addresses = [host] if _is_ip(host) else await resolve(host)
    except OSError as exc:
        raise FetchError(f"{url}: cannot resolve {host}: {exc}") from exc
    if not addresses:
        raise FetchError(f"{url}: {host} does not resolve")
    for address in addresses:
        try:
            public = _public(address)
        except ValueError as exc:
            raise FetchError(f"{url}: {host} resolves to {address!r}") from exc
        if not public:
            raise FetchError(f"{url}: {host} resolves to a non-public address ({address})")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return False
    return True


def _mocked(http: httpx.AsyncClient) -> bool:
    # Tests route requests to fake hosts through a MockTransport; nothing leaves the process.
    return isinstance(getattr(http, "_transport", None), httpx.MockTransport)


async def fetch_document(
    http: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int,
    headers: dict[str, str] | None = None,
    resolve: Resolver | None = None,
) -> Document:
    """GET `url` (following at most MAX_REDIRECTS redirects, each hop checked to be a public
    http(s) address). `resolve` overrides DNS resolution; with a mocked transport and no
    resolver the address check is skipped."""
    guard = resolve or (None if _mocked(http) else resolve_host)
    current = url
    try:
        for _ in range(MAX_REDIRECTS + 1):
            if guard is not None:
                await check_public_url(current, guard)
            async with http.stream(
                "GET", current, headers=headers, follow_redirects=False
            ) as response:
                if response.status_code in _REDIRECTS and response.headers.get("location"):
                    current = urljoin(current, response.headers["location"])
                    continue
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
                content_type = sniff_type(data, response.headers.get("content-type"), current)
                break
        else:
            raise FetchError(f"{url} redirects more than {MAX_REDIRECTS} times")
    except httpx.HTTPError as exc:
        raise FetchError(f"fetching {url} failed: {exc}") from exc
    if not data:
        raise FetchError(f"{url} returned an empty body")
    return Document(url=str(url), content_type=content_type, data=data)
