"""The evidence archive: Supabase Storage bucket `evidence`, via the Storage REST API.

Objects are keyed by the SHA-256 of their bytes (`ab/abcdef....html`), so the same page
fetched twice is stored once and a stored figure always points at the exact bytes it was
read from, even after the website changes. The bucket is private; the frontend gets
short-lived signed URLs from the API.
"""

import gzip
import hashlib
import hmac
import time
import urllib.parse
from typing import Protocol

import httpx

BUCKET = "evidence"

# Archived pages and filings are text: gzip takes them to a fifth or less of their size, which is
# what keeps the bucket inside the free tier. Anything else (PDFs, images) is stored as is.
_GZIP_MAGIC = b"\x1f\x8b"
_COMPRESSIBLE = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml",
    "+json",
    "+xml",
)


class StorageError(Exception):
    pass


def is_compressible(content_type: str) -> bool:
    kind = content_type.split(";", 1)[0].strip().lower()
    return any(kind.startswith(p) if p.endswith("/") else p in kind for p in _COMPRESSIBLE)


def unpack(data: bytes) -> bytes:
    """Stored bytes back to the original: gzip when it is gzip, otherwise untouched, so objects
    written before compression existed read the same way."""
    return gzip.decompress(data) if data[:2] == _GZIP_MAGIC else data


def sign_blob(secret: str, path: str, expires_at: int) -> str:
    return hmac.new(secret.encode(), f"{path}|{expires_at}".encode(), hashlib.sha256).hexdigest()


def verify_blob(secret: str, path: str, expires_at: int, signature: str) -> bool:
    return expires_at >= time.time() and hmac.compare_digest(
        sign_blob(secret, path, expires_at), signature
    )


class EvidenceStore(Protocol):
    async def put(self, path: str, data: bytes, content_type: str) -> None: ...

    async def get(self, path: str) -> bytes: ...

    async def signed_url(self, path: str, expires_in: int) -> str: ...


def evidence_path(sha256: str, extension: str) -> str:
    return f"{sha256[:2]}/{sha256}.{extension}"


class SupabaseStorage:
    def __init__(
        self,
        http: httpx.AsyncClient,
        supabase_url: str,
        secret_key: str,
        *,
        public_api_url: str | None = None,
    ) -> None:
        """With `public_api_url` set, text objects are stored gzipped and `signed_url` points at
        the API's /evidence/blob route, which serves the decompressed bytes; without it, objects
        are stored as they are and links are Supabase signed URLs, as before."""
        self._http = http
        self._secret = secret_key
        self._serve_base = public_api_url.rstrip("/") if public_api_url else None
        self._base = f"{supabase_url.rstrip('/')}/storage/v1"
        # New secret keys (sb_secret_...) are not JWTs: they go in `apikey` only. Legacy
        # service_role keys are JWTs and go in both headers.
        self._headers = {"apikey": secret_key}
        if not secret_key.startswith("sb_"):
            self._headers["Authorization"] = f"Bearer {secret_key}"

    def _object(self, path: str) -> str:
        return f"{BUCKET}/{urllib.parse.quote(path)}"

    async def put(self, path: str, data: bytes, content_type: str) -> None:
        if self._serve_base and is_compressible(content_type) and data[:2] != _GZIP_MAGIC:
            data = gzip.compress(data, compresslevel=6, mtime=0)
        try:
            response = await self._http.post(
                f"{self._base}/object/{self._object(path)}",
                content=data,
                headers={**self._headers, "Content-Type": content_type, "x-upsert": "false"},
            )
        except httpx.HTTPError as exc:
            raise StorageError(f"storage upload failed: {type(exc).__name__}") from exc
        if response.status_code in (200, 201):
            return
        # Same bytes, same key: already archived.
        if response.status_code in (400, 409) and "exists" in response.text.lower():
            return
        raise StorageError(f"storage upload failed: HTTP {response.status_code} {response.text}")

    async def get(self, path: str) -> bytes:
        try:
            response = await self._http.get(
                f"{self._base}/object/{self._object(path)}", headers=self._headers
            )
        except httpx.HTTPError as exc:
            raise StorageError(f"storage download failed: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise StorageError(f"storage download failed: HTTP {response.status_code}")
        return unpack(response.content)

    async def recompress(self, path: str, content_type: str) -> tuple[int, int] | None:
        """Replace an uncompressed text object with its gzip, in place. Returns (bytes before,
        bytes after), or None when it is already gzip or is not a text type."""
        if not is_compressible(content_type):
            return None
        try:
            response = await self._http.get(
                f"{self._base}/object/{self._object(path)}", headers=self._headers
            )
        except httpx.HTTPError as exc:
            raise StorageError(f"storage download failed: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise StorageError(f"storage download failed: HTTP {response.status_code}")
        raw = response.content
        if raw[:2] == _GZIP_MAGIC:
            return None
        packed = gzip.compress(raw, compresslevel=6, mtime=0)
        try:
            up = await self._http.post(
                f"{self._base}/object/{self._object(path)}",
                content=packed,
                headers={**self._headers, "Content-Type": content_type, "x-upsert": "true"},
            )
        except httpx.HTTPError as exc:
            raise StorageError(f"storage upload failed: {type(exc).__name__}") from exc
        if up.status_code not in (200, 201):
            raise StorageError(f"storage upload failed: HTTP {up.status_code} {up.text}")
        return len(raw), len(packed)

    async def signed_url(self, path: str, expires_in: int) -> str:
        if self._serve_base:
            expires_at = int(time.time()) + expires_in
            query = urllib.parse.urlencode(
                {"path": path, "exp": expires_at, "sig": sign_blob(self._secret, path, expires_at)}
            )
            return f"{self._serve_base}/evidence/blob?{query}"
        try:
            response = await self._http.post(
                f"{self._base}/object/sign/{self._object(path)}",
                json={"expiresIn": expires_in},
                headers=self._headers,
            )
        except httpx.HTTPError as exc:
            raise StorageError(f"signing failed: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise StorageError(f"signing failed: HTTP {response.status_code} {response.text}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise StorageError("signing response is not JSON") from exc
        signed = (
            (payload.get("signedURL") or payload.get("signedUrl"))
            if isinstance(payload, dict)
            else None
        )
        if not isinstance(signed, str):
            raise StorageError("signing response has no signedURL")
        return f"{self._base}{signed}" if signed.startswith("/") else signed


class MemoryStorage:
    """In-process store for tests and the golden-set run."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put(self, path: str, data: bytes, content_type: str) -> None:
        self.objects.setdefault(path, (data, content_type))

    async def get(self, path: str) -> bytes:
        if path not in self.objects:
            raise StorageError(f"no object {path}")
        return self.objects[path][0]

    async def signed_url(self, path: str, expires_in: int) -> str:
        if path not in self.objects:
            raise StorageError(f"no object {path}")
        return f"memory://{BUCKET}/{path}?expires_in={expires_in}"
